"""Interactive controls for the Garmin Training Starter Kit.

No password persistence, AI calls, uploads, Garmin writes, or calendar writes.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from getpass import getpass
import importlib.util
import json
import logging
from pathlib import Path
import re
import subprocess
import sys

BASE = Path(__file__).resolve().parent


def module_at(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Required source file is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def settings(base=BASE):
    config = json.loads((base / "starter-kit.json").read_text(encoding="utf-8-sig"))
    if config.get("application") != "garmin-training-starter":
        raise ValueError("Unrecognized starter-kit configuration")
    if Path(config["install_root"]).resolve() != base.resolve():
        raise ValueError("Installation was moved. Rerun Setup for the new location.")
    tokens = Path(config["token_dir"]).resolve()
    if base.resolve() == tokens or base.resolve() in tokens.parents:
        raise ValueError("Token folder must be outside the training folder")
    return config


def login(config):
    from garminconnect import Garmin
    logging.disable(logging.CRITICAL)
    token_dir = Path(config["token_dir"])
    # Cached login first. A 429 never triggers another automatic login attempt.
    if token_dir.exists():
        try:
            Garmin(retry_attempts=0).login(str(token_dir))
            print("Existing Garmin session works. No password needed.")
            return 0
        except Exception as error:
            if "TooManyRequests" in type(error).__name__:
                print("Garmin rate limited login. Stop and wait before trying again.")
                return 1
            print("Saved login is unavailable; a private login is needed.")
    print("Enter your own Garmin Connect credentials below. Do not paste them into chat.")
    email = input("Garmin email: ").strip()
    password = getpass("Garmin password (typing is hidden): ")
    if not email or not password:
        print("Email and password are required. No login attempt was made.")
        return 1
    api = Garmin(email=email, password=password, retry_attempts=0,
                 prompt_mfa=lambda: input("Garmin verification code: ").strip())
    password = None
    try:
        token_dir.mkdir(parents=True, exist_ok=True)
        api.login(str(token_dir))
        # Explicit persistence avoids depending on a suppressed dump error upstream.
        api.client.dump(str(token_dir))
        print("Garmin connected. Session tokens saved privately outside your training folder.")
        return 0
    except KeyboardInterrupt:
        print("Login cancelled.")
        return 1
    except Exception as error:
        kind = type(error).__name__
        print("Login did not finish:", kind)
        if "TooManyRequests" in kind:
            print("Stop and wait before trying again. Do not repeatedly retry.")
        else:
            print("Check credentials/network; use Reconnect Garmin when ready. Raw responses are not printed.")
        return 1
    finally:
        api.password = None


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def historical_start(config, today=None):
    today = today or date.today()
    # Older configs without the preference use the new current-year default.
    value = config.get("history_since", today.replace(month=1, day=1).isoformat())
    if value is None or str(value).strip().upper() == "ALL":
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Historical start must be YYYY-MM-DD or ALL")
    start = date.fromisoformat(value)
    if start > today:
        raise ValueError("Historical start cannot be in the future")
    return start.isoformat()


def history_arguments(config, base=BASE, today=None):
    start = historical_start(config, today)
    arguments = [sys.executable, str(base / "historical_import.py"),
                 "--output", str(base / "history_v1"), "--token-dir", config["token_dir"]]
    if start:
        arguments.extend(["--since", start])
    return arguments


def print_status(config, base=BASE):
    daily = load_json(base / "DailySync/data/sync_status.json")
    historical = load_json(base / "history_v1/import_status.json")
    print("Training folder:", base)
    print("Daily schedule:", config["daily_time"], "in Windows local time; installed zone:", config["windows_timezone"])
    print("Historical start:", historical_start(config) or "ALL available history")
    if daily:
        print("Latest daily attempt:", daily.get("status", "unknown"))
        stamp = daily.get("last_successful_sync_utc")
        if stamp:
            try:
                dt = datetime.fromisoformat(stamp)
                print("Last successful download:", dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"))
                if (datetime.now(timezone.utc) - dt).total_seconds() > 36 * 3600:
                    print("Data is more than 36 hours old. Check network/task/login before treating it as current.")
            except (ValueError, TypeError):
                print("Last successful download:", stamp)
        else:
            print("A successful DAILY download is not yet recorded.")
        if daily.get("baseline_published_at_utc"):
            print("Initial overview was built from the completed historical baseline.")
        if daily.get("failure_reason") or daily.get("error_type"):
            print("Latest issue:", daily.get("failure_reason") or daily.get("error_type"))
        print("Coverage:", daily.get("coverage", {}))
    else:
        print("No daily sync status yet. Use Sync Now after Garmin login.")
    if historical:
        print("Historical import:", historical.get("status"), "| Activities:", historical.get("activity_count"))
    else:
        print("Historical baseline has not completed yet.")
    print("Current review:", base / "DailySync/data/latest_training.json")
    print("Workout details:", base / "DailySync/data/latest_workouts_details.json")
    print("No files are automatically sent to ChatGPT, a cloud drive, or the kit's author.")
    return 0


def checkin(base=BASE):
    print("Optional local check-in. Skip any item. No diagnosis or automatic coaching is generated.")
    print("These are personal health notes; do not share the installed training folder.")
    prompts = [
        ("pain", "Pain: location, 0-10 severity, and triggers"),
        ("soreness", "Muscle soreness"),
        ("fatigue", "Fatigue/energy"),
        ("sleep_feeling", "How rested do you feel?"),
        ("illness_stress", "Illness or unusual stress"),
        ("fueling", "Fueling/appetite concerns"),
        ("availability", "Time available / changes for today"),
    ]
    entry = {"recorded_at_utc": datetime.now(timezone.utc).isoformat(), "local_date": date.today().isoformat()}
    for key, prompt in prompts:
        answer = input(prompt + " [Enter to skip]: ").strip()
        entry[key] = answer or None
    if all(entry[key] is None for key, _ in prompts):
        print("Empty check-in was not saved.")
        return 0
    filename = datetime.now().strftime("%H%M%S-%f") + ".json"
    destination = base / "checkins" / date.today().isoformat() / filename
    history = module_at("checkin_history", base / "historical_import.py")
    history.atomic_json(destination, entry)
    print("Saved locally:", destination)
    return 0


def publish_baseline(base=BASE):
    """Build the first overview from cache; do not re-download an hour of history."""
    history = module_at("publish_history", base / "historical_import.py")
    daily = module_at("publish_daily", base / "DailySync/sync_daily.py")
    status = history.read_json(base / "history_v1/import_status.json") or {}
    if not str(status.get("status", "")).startswith("finished") or not status.get("activity_index_complete"):
        print("Historical baseline is incomplete; no complete overview was published.")
        return 1
    importer = history.Importer(None, base / "history_v1", delay=2.5)
    importer.statuses = status.get("sections", {})
    complete, counts, generated = daily.build_outputs(history, base, date.today(), importer, True)
    target = base / "DailySync/data/sync_status.json"
    old = history.read_json(target) or {}
    history.atomic_json(target, {
        "schema": "garmin-daily/1.0", "status": "baseline_ready" if complete else "baseline_partial",
        "last_attempt_utc": status.get("updated_at_utc"),
        "last_successful_sync_utc": old.get("last_successful_sync_utc"),
        "baseline_published_at_utc": generated,
        "baseline_download_completed_at_utc": status.get("updated_at_utc"),
        "sync_complete": complete, "coverage": counts,
        "note": "Overview generated from historical cache; first scheduled daily sync is still pending."
    })
    print("Initial overview published from cached history. The daily task will refresh it on schedule.")
    return 0 if complete else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["login", "history", "sync", "scheduled", "status", "checkin", "publish"])
    args = parser.parse_args(argv)
    try:
        config = settings()
        if args.command == "login":
            return login(config)
        if args.command == "status":
            return print_status(config)
        if args.command == "checkin":
            return checkin()
        if args.command == "publish":
            return publish_baseline()
        if args.command == "history":
            start = historical_start(config)
            print("Importing activities and daily sleep/HRV:", ("from " + start + " through today") if start else "all available history", flush=True)
            print("Ctrl+C saves progress. Resume uses your saved date preference.", flush=True)
            return subprocess.call(history_arguments(config))
        if not (BASE / "history_v1/archive").exists():
            (BASE / "history_v1/archive").mkdir(parents=True, exist_ok=True)
        daily = module_at("controlled_daily_sync", BASE / "DailySync/sync_daily.py")
        code = daily.main(["--base", str(BASE), "--token-dir", config["token_dir"]])
        if args.command == "sync":
            print_status(config)
        return code
    except KeyboardInterrupt:
        print("Cancelled. Existing files are preserved.")
        return 1
    except Exception as error:
        print("Control could not complete:", type(error).__name__)
        print("Check Status, the daily log, and README. No raw credential responses are printed.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
