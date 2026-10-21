# Local Garmin training workspace

For training questions, first read `DailySync/data/sync_status.json` and
`DailySync/data/latest_training.json`. Check last successful download and source
timestamps. If `status` is `baseline_ready`, the overview comes from historical
cache, not a completed daily sync. If data is over 36 hours old or the latest
attempt failed, say so. Do not assume the watch has uploaded its newest workout.

Read `starter-kit.json` for display-unit preferences and installation/timezone
settings. `history_since` is the chosen historical start date; null means all
available history. A narrower range does not delete older archived records.
A Windows timezone identifier is not necessarily an IANA identifier; use the
actual local timezone for date/time displays and Monday-Sunday weeks.
Do not assume the user's name, race goal, fitness, injury status, or availability.
Ask for missing context before building a plan or changing calendar events.

## Data sources

- `DailySync/data/latest_training.json`: 42-day overview and seven-day wellness.
- `DailySync/data/latest_workouts_details.json`: latest 30 workouts with laps/sets.
- `history_v1/archive/activities/<activityId>/`: detailed historical source records.
- `history_v1/archive/wellness/YYYY-MM-DD/`: sleep and HRV summaries.
- `history_v1/archive/weight/`, `weight_daily/`, and `account/`: weights/records.
- `history_v1/history_for_review.json`: original historical baseline; does not
  automatically rebuild daily. Prefer current archive records for updated data.
- `DailySync/data/daily/YYYY-MM-DD/training_summary.json`: small dated snapshots.
- `checkins/YYYY-MM-DD/*.json`: optional local user check-ins, when present.
- `DailySync/logs/YYYY-MM-DD.log`: troubleshooting.

Parse JSON and inspect targeted records; don't dump whole archives into context.
Missing values are unknown, not zero. `directWorkoutFeel=0` is a valid rating;
higher means feeling better. Raw `directWorkoutRpe` is 10 times conventional
0-10 effort. Keep original fields. Confirm each endpoint's units before converting
weight/strength data. Validated activity-summary distance/time/speed are metres,
seconds, and metres/second. Separate virtual cycling and treadmill records.

Check parent/child multisport duplication and duplicate activity IDs. Treadmill
lap totals may differ from calibrated activity totals. HR-zone boundaries can
change. Garmin race labels and PRs aren't verified official results. Daily
imports exclude second-by-second traces and original FIT files.

Interpret volume, intensity, recovery, and check-ins together. Do not infer
injury/nutrient diagnoses or clearance to exercise from watch data. Historical
peak volume does not prove current tolerance. Sleep/performance associations
aren't proof of causation. Distinguish recorded activity from actual activity;
unrecorded sessions are not automatically rest days. No automatic calendar
matching, online check-in form, or personalized coaching agent is installed.

## Operation and privacy

This kit schedules local data downloads only. It does not run AI, upload files,
send messages, or change calendars. ChatGPT/Codex access is separate and depends
on the session. Do not pretend this file grants local access to phone/browser
sessions or automatically transfers context between chats.

When explicitly asked to refresh, run `.venv/Scripts/python.exe tools.py sync`
from this folder, wait for completion, and reread sync status. Do not run a
network download on every message. Respect `history_v1/.import.lock`; never
start overlapping imports or remove a lock while an importer is running.
Stop after rate limiting/login failure instead of repeatedly retrying.

Never open, print, upload, copy, or commit credentials or token-cache contents.
Tokens live outside this folder; login tools can use them without displaying
them. Local raw records and check-ins can contain personal health/location data.
Do not upload archives or publish an installed folder without explicit consent.
Keep `.venv`, data, configs, logs, check-ins, and tokens out of shared source ZIPs.
Do not overwrite scripts, alter tasks, or change calendar events just to answer
a training question. Preserve the user's instructions and source data.
