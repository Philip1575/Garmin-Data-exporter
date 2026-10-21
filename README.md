# Garmin Data Exporter — Windows pilot v0.2

A personal Python project by Philip Szczechowicz, built with Codex and AI assistance.

**Start here:** [How it works](HOW_IT_WORKS.md) · [Synthetic example](examples/illustrative-output.json) · [Validation limits](RELEASE_NOTES.md).

To get the runnable source, choose **Code → Download ZIP** on this repository and extract the whole archive. No private training records are included.

An installable, local, read-only Garmin training-data workspace. No coding is
required. This is an independent personal pilot, not an official Garmin product,
a medical device, or a hosted AI coaching service.

## Download and run

1. Right-click the ZIP and choose **Extract All**. Do not run it from inside the ZIP.
2. Open the extracted repository folder (for example, `Garmin-Data-exporter-main`).
3. Double-click **Setup.cmd**. Review this README first. Windows may ask you to
   confirm running downloaded scripts; the kit is unsigned. Do not disable
   Windows security or school/work device policies. If blocked, ask the device
   administrator or use an unrestricted personal computer.
4. Follow the local prompts: folder, daily time, display units, historical start, optional Python
   download, Garmin login/MFA, optional desktop shortcut, historical import.
5. Keep the setup window open while the historical download runs. It can take
   an hour or longer for months of training. Ctrl+C interrupts and saves progress;
   use **Import or Resume History** later to continue. Don't start two imports.

All folders are created automatically. The default is `%USERPROFILE%\GarminSync`.
If that contains an unrelated existing setup, a separate `GarminSync-Starter`
folder is offered; an unrelated nonempty folder is never overwritten.

## What Setup does

The historical date prompt defaults to **January 1 of the current year** on a
new installation. Press Enter to accept, enter a custom `YYYY-MM-DD`, or type
`ALL` for all available history. Future/invalid dates are rejected. This range
applies to historical activities AND daily sleep/HRV/weight requests through
today. The choice is saved, so resuming does not silently expand the range.
On a setup rerun, an existing saved choice is offered as the default instead.
Use **Change History Range** later, followed by **Import or Resume History**, to
change it. Previously downloaded older records are retained, not deleted.
The regular rolling daily refresh is unchanged and does not use the historical
start as a restriction on its recent seven-day window.

- Verifies packaged file hashes to detect corruption/incomplete extraction.
  This integrity list is not a digital signature or proof of publisher identity.
- Reuses Python 3.13 if available. Otherwise, with your agreement, downloads
  Python 3.13.16 for Intel/AMD x64 or ARM64 directly from python.org, verifies a
  valid Python Software Foundation Authenticode signature, and installs it for
  the current user. It does not add Python to PATH or require an admin install.
- Creates an isolated `.venv` and installs `garminconnect==0.3.19` and
  `curl_cffi==0.16.3` from PyPI. Transitive dependencies are resolved by pip;
  this is not a fully locked, offline installation.
- Creates scripts, training folders, AI-reading instructions, and a controls menu.
- Prompts for Garmin email/password/MFA privately in the local window. Passwords
  are not written to config, logs, shortcuts, or exported training files.
- Saves session tokens outside the training workspace under
  `%LOCALAPPDATA%\GarminTrainingStarter\Profiles\<profile-id>\tokens`, with a
  directory ACL limited to the Windows user and SYSTEM. This is not encryption
  against programs running as that user or an administrator.
- Registers an individually named Windows scheduled task at the chosen **local
  wall-clock time**. It needs this user signed in and the PC awake/online, permits
  battery operation, prevents overlapping scheduled runs, and enables catch-up.
  It does not wake the PC. Missed-run catch-up may not start immediately.
- Offers the historical baseline. After a successful import, creates the initial
  overview from cache with status `baseline_ready`. That is distinguished from
  an actual daily sync; the next scheduled run or **Sync Now** refreshes it.

If task registration fails (for example, due to a managed-device restriction),
Setup explicitly reports that scheduling is NOT installed; manual controls can
still work. Use **Change Schedule** to retry. No Windows password is stored.

## Controls — no commands to remember

Open the **Garmin Tools** folder inside your installation, or the optional
**Garmin Training** desktop shortcut:

| Control | Action |
| --- | --- |
| Sync Now | Refresh recent data and show the resulting status |
| Check Status | Show freshness, history coverage, and scheduled-task state |
| Import or Resume History | Download/resume all available historical training |
| Reconnect Garmin | Validate a saved session or prompt privately for login |
| Daily Check-in | Save optional pain/fatigue/sleep/fueling/context notes locally |
| Change Schedule | Choose a new HH:mm local time and install/update this task |
| Change History Range | Save this-year, custom-date, or all-history selection |
| Disable / Enable Daily Sync | Pause or resume recurring downloads |
| Remove Scheduled Task | Remove the task after confirmation; keep data/tokens |
| Open Training Folder | Open the installed workspace |

Check-ins are local text inputs; they do not message ChatGPT or generate advice.
Empty check-ins are not saved. Multiple check-ins on a date are stored separately.

## Folder layout

- `.venv/`: isolated Python environment.
- `starter-kit.json`: preferences, task name, and token-folder path, not tokens.
- `historical_import.py`, `tools.py`, `Controls.ps1`, `Common.ps1`: tools.
- `AGENTS.md`: generic instructions for on-demand local Codex reviews.
- `history_v1/archive/`: detailed cached source records, one per endpoint/activity.
- `history_v1/history_for_review.json`: original historical baseline export.
- `DailySync/data/latest_training.json`: 42-day current overview and seven days of wellness.
- `DailySync/data/latest_workouts_details.json`: latest 30 workouts including laps/sets.
- `DailySync/data/daily/YYYY-MM-DD/training_summary.json`: small daily overview snapshots.
- `DailySync/data/sync_status.json`: last attempt, actual last successful daily sync, gaps.
- `DailySync/logs/YYYY-MM-DD.log`: one log per day.
- `checkins/YYYY-MM-DD/`: optional local personal context.
- `Garmin Tools/`: clickable shortcuts.

Daily snapshots do not nest inside one another and do not duplicate the full
archive. Reruns on one date replace that day's overview. Detailed files are
updated in place. Dated files/logs accumulate; automatic pruning is not enabled.
Partial downloads use `partial_` outputs and do not replace latest successful
review files. A historical baseline export does not automatically rebuild daily.

## Data coverage

Historical import: Garmin activities within the selected start-date range and their detail/lap/typed
split/split-summary/HR-zone responses; exercise sets for applicable strength,
fitness, cardio, or training labels; daily sleep/HRV summaries from the chosen
start through today (earliest activity if ALL); recorded body-composition/weight history; Garmin personal
records with history. No athlete profile or injury diagnosis is generated.

Daily: activity index and new records, recent seven local dates of workouts
(including edits, ratings, laps, and sets), seven days of sleep/HRV and weight,
and personal records. After downtime, new records back to the newest previously
archived activity can be caught up. Without a historical baseline, daily mode
stays recent rather than silently importing the whole account. Old backdated
imports/edits outside the scan window are not guaranteed to be found.
Missing dates/values are not zeros. Metrics depend on the device,
recording, permissions, and Garmin's response. Empty/unavailable/failed states
are distinct. A successful HTTP response can still lack individual metrics.

No second-by-second workout traces, original FIT files, continuous daily
stress/Body Battery, historical training-readiness series, official race-result
lookup, automatic calendar matching, or nutritional diagnosis is included.
Raw endpoint units are retained. RPE and feeling are preserved separately.
Some source responses contain precise locations and account metadata. Coordinate/
identifier fields are filtered in review copies, but free text can still identify
locations/people. Review before sharing anything.

## AI use

Open the installed training folder as a workspace in local Codex, then ask:

> Read AGENTS.md, check sync freshness, and summarize my current training.

An AI account/application is separate and may have its own costs or limits.
Downloads themselves do not call an AI model. Folder instructions do not grant
phone/browser chats local access or automatically transfer conversation context.
When AI reads local files, information may be sent to that AI provider under its
own settings. This kit itself does not upload to ChatGPT, Drive, or the author.

## Troubleshooting

- **Login/rate limit:** wait rather than repeatedly retrying. Use Reconnect Garmin.
  The unofficial library may internally try multiple authentication strategies.
- **No fresh data:** sync the watch to Garmin Connect, then use Sync Now. Inspect
  both data timestamps and task state. A sleeping/offline/signed-out PC cannot sync.
- **Unfinished historical import:** use Import or Resume History. Completed
  sections are reused; it does not redownload everything on a normal resume.
- **Lock exists:** ensure no historical/daily import is running. Only then remove
  `history_v1\.import.lock` if a force-terminated process left it behind.
- **Missing Python download/dependency wheel:** installer/dependency versions or
  Windows support may have changed. Do not substitute an arbitrary installer;
  update/test the kit against official Python/PyPI sources.
- **School/work restrictions:** do not bypass device-management policy. Manual
  tools may work without a scheduled task; otherwise use a personal computer.
- **Moved installation:** don't drag the installed folder elsewhere. Rerun Setup
  for the destination; paths in config/tasks/shortcuts must be consistent.
- **Time-zone change:** task time follows Windows local time. Use Change Schedule
  after travel/time-zone changes if the stored zone or intended time differs.
- **Disable/remove:** disable or remove the scheduled task through the controls
  before manually deleting installed files. No automatic recursive deletion is
  provided. Login tokens and the per-user Python runtime remain unless separately
  removed by the user. Do not delete shared Python used by other applications.

## Share safely / pilot limitations

Share **the original starter-kit ZIP**, never an installed training directory,
`.venv`, data, logs, check-ins, `starter-kit.json`, or private token folder. The
source package contains no user's training history, credentials, or calendar data.
The original author's existing task/workspace is not modified merely by building
or sharing this kit. Running Setup is an explicit new installation action.

Supported target: Windows 10/11, 64-bit Intel/AMD or ARM64. No macOS/Linux installer.
Offline logic and PowerShell syntax can be tested here; the full path on a
fresh Windows PC, network downloads, account-specific MFA, shortcuts, permissions,
and scheduling still require a real pilot. No unattended production reliability
or Garmin approval is claimed. Versions may need maintenance.

Uses unofficial/private Garmin Connect endpoints via a third-party client.
Public distribution/commercial deployment needs review of Garmin integration
requirements, upstream dependency licenses, and privacy/security obligations.
This kit is not a substitute for professional medical advice or event clearance.

Sources:

- https://docs.python.org/3/using/windows.html
- https://www.python.org/downloads/windows/
- https://github.com/cyberjunky/python-garminconnect/tree/0.3.19
- https://github.com/lexiforest/curl_cffi
- Inspiration: https://github.com/scruff2/CODEX-SKILLS

Generated starter-kit source is provided under the included MIT license.
Third-party dependencies retain their own licenses; they are downloaded separately.
