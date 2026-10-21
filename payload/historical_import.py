"""Read-only Garmin historical import. Python 3.13; garminconnect 0.3.19.

Run with the existing GarminSync virtual environment. No password collection,
calendar writes, GPS sample downloads, or Garmin write operations.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
import sys
import time

SCHEMA = "garmin-history/1.0"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
    temporary.replace(path)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def empty(value):
    return value is None or value == {} or value == []


def shareable(value):
    """Remove personal identifiers/location fields from the analysis copy.

    Free-text workout names/notes can still contain personal information.
    The local archive retains the original payloads.
    """
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            lower = key.lower()
            if any(part in lower for part in (
                "latitude", "longitude", "polyline", "owner", "userprofile",
                "displayname", "fullname", "imageurl", "userroles", "token",
                "email", "uuid", "deviceid", "serialnumber", "geolocation",
            )):
                continue
            result[key] = shareable(item)
        return result
    if isinstance(value, list):
        return [shareable(item) for item in value]
    return value


def reported_effort(value, path=""):
    """Keep original rating fields and paths; never invent scale mappings."""
    found = {}
    if isinstance(value, dict):
        for key, item in value.items():
            current = f"{path}.{key}" if path else key
            if re.search(r"perceived|effort|feel|self.?eval|rpe", key, re.I):
                found[current] = item
            elif isinstance(item, (dict, list)):
                found.update(reported_effort(item, current))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.update(reported_effort(item, f"{path}[{index}]"))
    return found


def race_marked(summary, detail):
    candidates = [summary.get("eventType"), detail.get("eventType"),
                  (detail.get("metadataDTO") or {}).get("eventTypeDTO")]
    for item in candidates:
        if isinstance(item, dict) and str(item.get("typeKey", "")).lower() in {"race", "competition"}:
            return True
    return False


class StopImport(Exception):
    pass


class Importer:
    def __init__(self, api, root, delay=2.5, retry_unavailable=False):
        self.api = api
        self.root = root
        self.delay = delay
        self.retry_unavailable = retry_unavailable
        self.requests = 0
        self.cached = 0
        self.statuses = {}

    def remember(self, key, envelope):
        self.statuses[key] = {
            name: envelope[name] for name in ("status", "fetched_at", "error_type", "http_status")
            if name in envelope
        }
        return envelope.get("data")

    def fetch(self, key, method, *args, transform=None, fresh=False, **kwargs):
        path = self.root / "archive" / (key + ".json")
        previous = read_json(path)
        if previous and not fresh:
            state = previous.get("status")
            if state in {"ok", "empty"} or (state == "unavailable" and not self.retry_unavailable):
                self.cached += 1
                return self.remember(key, previous)
        time.sleep(self.delay)
        self.requests += 1
        envelope = {"schema": SCHEMA, "fetched_at": utc_now(), "method": method}
        try:
            value = getattr(self.api, method)(*args, **kwargs)
            if transform is not None:
                value = transform(value)
            envelope.update(status="empty" if empty(value) else "ok", data=value)
        except Exception as error:
            error_type = type(error).__name__
            response = getattr(error, "response", None)
            code = getattr(response, "status_code", None)
            envelope.update(status="failed", error_type=error_type, http_status=code)
            if "TooManyRequests" in error_type or code == 429:
                atomic_json(path, envelope)
                self.remember(key, envelope)
                raise StopImport("Garmin rate limited the import. Wait before resuming.") from None
            if "Authentication" in error_type or code == 401:
                atomic_json(path, envelope)
                self.remember(key, envelope)
                raise StopImport("Saved login was rejected. Reauthenticate privately, then resume.") from None
            if "NotFound" in error_type or code == 404:
                envelope["status"] = "unavailable"
            print(f"  {envelope['status']}: {key} ({error_type})", flush=True)
        atomic_json(path, envelope)
        return self.remember(key, envelope)

    def save_summary(self, summary):
        activity_id = str(int(summary["activityId"]))
        envelope = {"schema": SCHEMA, "status": "ok", "fetched_at": utc_now(), "data": summary}
        atomic_json(self.root / "archive" / "activities" / activity_id / "summary.json", envelope)
        self.remember(f"activities/{activity_id}/summary", envelope)

    def discover(self, since, until):
        """Refresh the small index, then reuse saved endpoint responses."""
        activities = {}
        for page in range(1000):
            rows = self.fetch(f"index/page_{page:04d}", "get_activities", page * 100, 100, fresh=True)
            if not isinstance(rows, list):
                raise StopImport("Activity index failed or returned an unexpected shape; run again to resume.")
            if not rows:
                return list(activities.values())
            older = False
            for item in rows:
                if not isinstance(item, dict) or not item.get("activityId"):
                    raise StopImport("Unexpected activity record; index cannot be marked complete.")
                stamp = str(item.get("startTimeLocal") or item.get("startTimeGMT") or "")[:10]
                try:
                    day = date.fromisoformat(stamp)
                except ValueError:
                    raise StopImport("An activity is missing its date; inspect the local index.") from None
                if since and day < since:
                    older = True
                    continue
                if day > until:
                    continue
                activities[str(item["activityId"])] = item
                self.save_summary(item)
            print(f"Indexed {len(activities)} activities", flush=True)
            if older:
                return list(activities.values())
        raise StopImport("Index exceeded the safety page limit; archive saved.")

    def export(self, activity_ids, start, end, index_complete, run_status):
        records = []
        for activity_id in activity_ids:
            record = {}
            for section in ("summary", "activity", "laps", "typed_splits", "split_summaries", "hr_zones", "exercise_sets"):
                value = read_json(self.root / "archive" / "activities" / activity_id / (section + ".json"))
                if value is not None:
                    record[section] = value
            summary = (record.get("summary") or {}).get("data") or {}
            detail = (record.get("activity") or {}).get("data") or {}
            record["reported_effort_and_feeling"] = reported_effort({"summary": summary, "activity": detail})
            record["marked_as_race"] = race_marked(summary, detail)
            records.append(shareable(record))
        daily = []
        day = start
        while day <= end:
            row = {"date": day.isoformat()}
            for section in ("sleep", "hrv"):
                payload = read_json(self.root / "archive" / "wellness" / day.isoformat() / (section + ".json"))
                if payload is not None:
                    row[section] = shareable(payload)
            if len(row) > 1:
                daily.append(row)
            day += timedelta(days=1)
        weights = []
        for path in sorted((self.root / "archive" / "weight").glob("*.json")):
            if start.isoformat() <= path.stem[:10] <= end.isoformat():
                weights.append(shareable(read_json(path)))
        personal_records = read_json(self.root / "archive" / "account" / "personal_records.json")
        counts = dict(Counter(value["status"] for value in self.statuses.values()))
        payload = {
            "schema": SCHEMA, "generated_at_utc": utc_now(),
            "garminconnect_version": version("garminconnect"),
            "range": {"start": start.isoformat(), "end": end.isoformat(), "basis": "Garmin local calendar dates"},
            "status": run_status, "activity_index_complete": index_complete,
            "coverage": counts, "activities": records, "daily_wellness": daily,
            "weight_history": weights, "personal_records": shareable(personal_records),
            "notes": [
                "Not a full account backup. No sample traces or original FIT files are included.",
                "Race flags are Garmin labels, not independently verified official results.",
                "Missing effort/feeling values are unknown; original scales are not remapped.",
                "Endpoint success does not prove every metric or date is present.",
                "Missing days/measurements must not be treated as zero or interpolated.",
                "Daily sleep and HRV use Garmin calendarDate; compare with workout local dates.",
                "Units and raw field names are retained from each Garmin endpoint, not standardized.",
                "Identifiers and coordinate fields are filtered from this copy; inspect free text before sharing.",
                "No athlete profile, training recommendation, or sleep/performance inference has been generated."
            ]
        }
        filename = "history_for_review.json" if run_status.startswith("finished") else "history_partial_for_review.json"
        atomic_json(self.root / filename, payload)
        atomic_json(self.root / "import_status.json", {
            "schema": SCHEMA, "updated_at_utc": utc_now(), "status": run_status,
            "activity_index_complete": index_complete, "activity_count": len(records),
            "coverage": counts, "requests_this_run": self.requests,
            "cached_sections_this_run": self.cached, "sections": self.statuses,
        })


def compact_sleep(data):
    if not isinstance(data, dict) or not data:
        return data
    # Exclude sleep movement, oxygen, respiration, and other overnight sample arrays.
    keep = {key: value for key, value in data.items() if key == "dailySleepDTO" or
            not isinstance(value, (dict, list))}
    return keep


def compact_hrv(data):
    if not isinstance(data, dict) or not data:
        return data
    return {key: value for key, value in data.items() if key != "hrvReadings"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path.home() / "GarminSync" / "history_v1")
    parser.add_argument("--token-dir", type=Path, default=Path.home() / ".garminconnect")
    parser.add_argument("--since", type=date.fromisoformat, help="Optional YYYY-MM-DD; default all available activities")
    parser.add_argument("--until", type=date.fromisoformat, default=date.today())
    parser.add_argument("--delay", type=float, default=2.5, help="Seconds between calls; minimum 2")
    parser.add_argument("--retry-unavailable", action="store_true", help="Retry cached 404 sections")
    args = parser.parse_args(argv)
    if args.delay < 2 or (args.since and args.since > args.until):
        parser.error("Use delay >= 2 seconds and since <= until")
    # Refuse to put exported health data in or around the credentials directory.
    output, tokens = args.output.expanduser().resolve(), args.token_dir.expanduser().resolve()
    if output == tokens or tokens in output.parents or output in tokens.parents:
        parser.error("Output must be separate from the login-token directory")
    output.mkdir(parents=True, exist_ok=True)
    lock = output / ".import.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(descriptor)
    except FileExistsError:
        print("An import lock exists. If no importer is running, delete .import.lock and rerun.")
        return 1
    importer = None
    activity_ids = []
    start = args.since or args.until
    index_complete = False
    status = "interrupted"
    try:
        from garminconnect import Garmin
        print("Logging in with saved local tokens; no password is collected.", flush=True)
        api = Garmin(retry_attempts=0)
        api.login(str(tokens))
        importer = Importer(api, output, args.delay, args.retry_unavailable)
        summaries = importer.discover(args.since, args.until)
        index_complete = True
        activity_ids = sorted({str(int(item["activityId"])) for item in summaries})
        if not summaries and not args.since:
            raise StopImport("No activities found. Supply --since YYYY-MM-DD to import wellness without activities.")
        if summaries:
            earliest = min(date.fromisoformat(str(item.get("startTimeLocal") or item["startTimeGMT"])[:10]) for item in summaries)
            start = args.since or earliest
        for index, summary in enumerate(summaries, 1):
            activity_id = str(int(summary["activityId"]))
            print(f"Activity {index}/{len(summaries)}: {activity_id}", flush=True)
            base = f"activities/{activity_id}/"
            for section, method in (
                ("activity", "get_activity"), ("laps", "get_activity_splits"),
                ("typed_splits", "get_activity_typed_splits"),
                ("split_summaries", "get_activity_split_summaries"),
                ("hr_zones", "get_activity_hr_in_timezones"),
            ):
                importer.fetch(base + section, method, activity_id)
            sport = str((summary.get("activityType") or {}).get("typeKey", "")).lower()
            if any(word in sport for word in ("strength", "training", "fitness", "cardio", "crossfit")):
                importer.fetch(base + "exercise_sets", "get_activity_exercise_sets", activity_id)
        day = start
        while day <= args.until:
            stamp = day.isoformat()
            print("Daily sleep and HRV:", stamp, flush=True)
            importer.fetch(f"wellness/{stamp}/sleep", "get_sleep_data", stamp, transform=compact_sleep)
            importer.fetch(f"wellness/{stamp}/hrv", "get_hrv_data", stamp, transform=compact_hrv)
            day += timedelta(days=1)
        day = start
        while day <= args.until:
            end = min(day + timedelta(days=27), args.until)
            importer.fetch(f"weight/{day.isoformat()}_{end.isoformat()}", "get_body_composition", day.isoformat(), end.isoformat())
            day = end + timedelta(days=1)
        importer.fetch("account/personal_records", "get_personal_record", include_history=True)
        states = [value["status"] for value in importer.statuses.values()]
        status = "finished_with_gaps" if any(state in {"failed", "unavailable"} for state in states) else "finished"
    except KeyboardInterrupt:
        print("Stopped. Completed sections are saved; rerun the same command to resume.")
    except StopImport as error:
        print(str(error))
    except Exception as error:
        # Do not print request bodies, tokens, or potentially sensitive exception messages.
        print("Import stopped:", type(error).__name__, "(details omitted to protect credentials)")
    finally:
        try:
            if importer:
                importer.export(activity_ids, start, args.until, index_complete, status)
                filename = "history_for_review.json" if status.startswith("finished") else "history_partial_for_review.json"
                print("Analysis file:", output / filename)
                print("Status:", status, dict(Counter(value["status"] for value in importer.statuses.values())))
            print("Local archive:", output)
        finally:
            lock.unlink(missing_ok=True)
    return 0 if status in {"finished", "finished_with_gaps"} else 1


if __name__ == "__main__":
    sys.exit(main())
