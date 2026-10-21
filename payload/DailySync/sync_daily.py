"""Daily Garmin sync: saved login only; no cloud upload or Garmin writes."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timedelta
import importlib.util
import json
import logging
import os
from pathlib import Path
import sys


def load_history(base):
    source = base / "historical_import.py"
    spec = importlib.util.spec_from_file_location("garmin_historical_import", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("historical_import.py is required in GarminSync")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def gather_summaries(history, archive):
    rows = []
    for path in (archive / "activities").glob("*/summary.json"):
        envelope = history.read_json(path)
        if envelope and envelope.get("status") == "ok" and isinstance(envelope.get("data"), dict):
            rows.append(envelope["data"])
    return sorted(rows, key=lambda row: str(row.get("startTimeLocal") or row.get("startTimeGMT") or ""))


def build_outputs(history, base, today, importer, scan_complete):
    archive = base / "history_v1" / "archive"
    rows = gather_summaries(history, archive)
    recent = [row for row in rows if (today - timedelta(days=41)).isoformat() <=
              str(row.get("startTimeLocal") or row.get("startTimeGMT") or "")[:10] <= today.isoformat()]
    summaries = []
    for row in recent:
        activity_id = str(int(row["activityId"]))
        detail = history.read_json(archive / "activities" / activity_id / "activity.json")
        summaries.append({"summary": history.shareable(row),
                          "reported_effort_and_feeling": history.reported_effort((detail or {}).get("data") or {}),
                          "detail_status": (detail or {}).get("status", "not_downloaded"),
                          "local_detail_folder": str(archive / "activities" / activity_id)})
    wellness = []
    for offset in range(6, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        wellness.append({"date": day, **{section: history.shareable(history.read_json(
            archive / "wellness" / day / (section + ".json"))) for section in ("sleep", "hrv")}})
    details = []
    for row in rows[-30:]:
        activity_id = str(int(row["activityId"]))
        detail = {"activityId": row["activityId"]}
        for section in ("summary", "activity", "laps", "typed_splits", "split_summaries", "hr_zones", "exercise_sets"):
            envelope = history.read_json(archive / "activities" / activity_id / (section + ".json"))
            if envelope is not None:
                detail[section] = history.shareable(envelope)
        details.append(detail)
    statuses = dict(Counter(item["status"] for item in importer.statuses.values()))
    complete = scan_complete and not statuses.get("failed")
    generated = history.utc_now()
    summary = {
        "schema": "garmin-daily/1.0", "generated_at_utc": generated,
        "local_date": today.isoformat(), "sync_complete": complete,
        "activity_scan_complete": scan_complete, "coverage_this_run": statuses,
        "archive_activity_count": len(rows), "recent_activity_days": 42,
        "activities": summaries, "daily_wellness": wellness,
        "recent_weight": history.shareable(history.read_json(archive / "account" / "recent_weight.json")),
        "personal_records": history.shareable(history.read_json(archive / "account" / "personal_records.json")),
        "notes": [
            "New and changed recent activities refresh; dates and records are Garmin's, not medical clearance.",
            "Last 7 local dates are refreshed for sleep/HRV, even if previously empty.",
            "Activity samples/original FIT files are not downloaded.",
            "Old edits outside the 7-day window are not automatically revisited.",
            "Source envelopes retain their fetched_at/status; check these before interpreting stale values.",
            "The local archive contains detailed laps/sets; latest_workouts_details.json contains the latest 30.",
            "Raw source units are retained; missing values are not zero. Free text can identify locations.",
            "This file is local and is not automatically uploaded to ChatGPT or Google Drive."
        ]
    }
    root = base / "DailySync" / "data"
    prefix = "" if complete else "partial_"
    history.atomic_json(root / (prefix + "latest_training.json"), summary)
    history.atomic_json(root / (prefix + "latest_workouts_details.json"), {
        "schema": "garmin-daily/1.0", "generated_at_utc": generated,
        "sync_complete": complete, "activities": details,
    })
    # One small snapshot per date; repeated successful runs replace that day's copy.
    history.atomic_json(root / "daily" / today.isoformat() / (prefix + "training_summary.json"), summary)
    return complete, statuses, generated


def sync(base, api, today=None, delay=2.5):
    history = load_history(base)
    today = today or date.today()
    root = base / "history_v1"
    data = base / "DailySync" / "data"
    data.mkdir(parents=True, exist_ok=True)
    old_status = history.read_json(data / "sync_status.json") or {}
    lock = root / ".import.lock"  # Shared with historical import: never overlap.
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        print("Archive is locked by another import. No sync was started.")
        return 2
    importer = history.Importer(api, root, delay=delay, retry_unavailable=True)
    scan_complete = False
    state = "failed"
    reason = None
    refreshed = 0
    try:
        print("Started:", history.utc_now(), "Refreshing 7-day window through", today, flush=True)
        cutoff = today - timedelta(days=6)
        previous_rows = gather_summaries(history, root / "archive")
        # With no baseline, daily mode must not silently become a full import.
        # After downtime, scan back to the newest prior record to catch new backlog.
        scan_floor = cutoff
        if previous_rows:
            newest = previous_rows[-1]
            previous_day = date.fromisoformat(str(newest.get("startTimeLocal") or newest.get("startTimeGMT"))[:10])
            scan_floor = min(cutoff, previous_day)
        for page in range(1000):
            entries = importer.fetch(f"daily_index/page_{page:04d}", "get_activities", page * 100, 100, fresh=True)
            if not isinstance(entries, list):
                raise history.StopImport("Activity index was not a list; sync cannot be marked complete.")
            if not entries:
                scan_complete = True
                break
            old_found = False
            for row in entries:
                if not isinstance(row, dict) or not row.get("activityId"):
                    raise history.StopImport("Unexpected activity record in index.")
                activity_id = str(int(row["activityId"]))
                stamp = str(row.get("startTimeLocal") or row.get("startTimeGMT") or "")[:10]
                try:
                    activity_date = date.fromisoformat(stamp)
                except ValueError:
                    raise history.StopImport("Activity has no usable date.") from None
                if activity_date > today:
                    continue
                target = root / "archive" / "activities" / activity_id
                existing = history.read_json(target / "summary.json")
                if activity_date < cutoff:
                    if activity_date < scan_floor:
                        old_found = True
                        continue
                    if existing is not None:
                        old_found = True
                        continue
                importer.save_summary(row)
                for section, method in (
                    ("activity", "get_activity"), ("laps", "get_activity_splits"),
                    ("typed_splits", "get_activity_typed_splits"),
                    ("split_summaries", "get_activity_split_summaries"),
                    ("hr_zones", "get_activity_hr_in_timezones"),
                ):
                    importer.fetch(f"activities/{activity_id}/{section}", method, activity_id, fresh=True)
                sport = str((row.get("activityType") or {}).get("typeKey", "")).lower()
                if any(word in sport for word in ("strength", "training", "fitness", "cardio", "crossfit")):
                    importer.fetch(f"activities/{activity_id}/exercise_sets", "get_activity_exercise_sets", activity_id, fresh=True)
                refreshed += 1
                print("Updated activity:", activity_id, stamp, flush=True)
            if old_found:
                scan_complete = True
                break
        if not scan_complete:
            raise history.StopImport("Activity scan exceeded page limit.")
        for offset in range(6, -1, -1):
            day = (today - timedelta(days=offset)).isoformat()
            importer.fetch(f"wellness/{day}/sleep", "get_sleep_data", day, transform=history.compact_sleep, fresh=True)
            importer.fetch(f"wellness/{day}/hrv", "get_hrv_data", day, transform=history.compact_hrv, fresh=True)
        # Separate rolling weight file avoids altering historical chunk boundaries.
        weight_key = f"weight_daily/{today.isoformat()}"
        importer.fetch(weight_key, "get_body_composition", cutoff.isoformat(), today.isoformat(), fresh=True)
        weight_envelope = history.read_json(root / "archive" / (weight_key + ".json"))
        history.atomic_json(root / "archive" / "account" / "recent_weight.json", weight_envelope)
        importer.fetch("account/personal_records", "get_personal_record", include_history=True, fresh=True)
        state = "finished"
    except KeyboardInterrupt:
        reason = "Interrupted; run again later."
        print(reason)
    except history.StopImport as error:
        reason = str(error)
        print(reason)
    except Exception as error:
        reason = type(error).__name__  # Do not expose raw responses or secrets.
        print("Sync failed:", reason)
    finally:
        try:
            complete, counts, generated = build_outputs(history, base, today, importer, scan_complete and state == "finished")
            if complete and counts.get("unavailable"):
                state = "finished_with_unavailable_sections"
            elif not complete:
                state = "failed"
            history.atomic_json(data / "sync_status.json", {
                "schema": "garmin-daily/1.0", "last_attempt_utc": generated,
                "last_successful_sync_utc": generated if complete else old_status.get("last_successful_sync_utc"),
                "status": state, "sync_complete": complete, "failure_reason": reason,
                "activities_refreshed": refreshed, "coverage": counts,
                "sections": importer.statuses,
                "review_file": str(data / ("latest_training.json" if complete else "partial_latest_training.json")),
            })
            print("Status:", state, counts)
            print("Review file:", data / ("latest_training.json" if complete else "partial_latest_training.json"))
        finally:
            lock.unlink(missing_ok=True)
    return 0 if complete else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--token-dir", type=Path, required=True, help="Private token folder configured by the starter kit")
    args = parser.parse_args(argv)
    base = args.base.resolve()
    daily = base / "DailySync"
    log_dir = daily / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / (date.today().isoformat() + ".log")
    with log_path.open("a", encoding="utf-8", buffering=1) as log, redirect_stdout(log), redirect_stderr(log):
        # Library warnings can contain request details. Print our safe status messages only.
        logging.disable(logging.CRITICAL)
        try:
            if not (base / "historical_import.py").is_file() or not (base / "history_v1" / "archive").is_dir():
                print("Existing historical_import.py and history_v1/archive are required in GarminSync.")
                return 1
            from garminconnect import Garmin
            api = Garmin(retry_attempts=0)
            try:
                api.login(str(args.token_dir.expanduser().resolve()))
            except Exception as error:
                history = load_history(base)
                path = daily / "data" / "sync_status.json"
                old = history.read_json(path) or {}
                history.atomic_json(path, {"last_attempt_utc": history.utc_now(),
                    "last_successful_sync_utc": old.get("last_successful_sync_utc"),
                    "status": "login_failed", "error_type": type(error).__name__,
                    "sync_complete": False, "action": "Reauthenticate locally; do not share tokens."})
                print("Saved login failed:", type(error).__name__, "Reauthenticate privately before retrying.")
                return 1
            return sync(base, api)
        except Exception as error:
            print("Startup/output error:", type(error).__name__)
            return 1


if __name__ == "__main__":
    sys.exit(main())
