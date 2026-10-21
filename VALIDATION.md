# Validation — v0.2.0 Windows pilot

Validated against Python 3.13 and garminconnect 0.3.19 on October 18, 2026.

## Passed

- 23 offline tests against the packaged source: current-year/custom/ALL start dates,
  invalid/future dates, historical resume and pagination,
  lap/effort preservation, identifier filtering, empty vs missing values, shared
  import locks, daily refresh, failed/429 imports retaining previous success,
  deferred-history behavior, downtime catch-up, external token routing, hidden
  password-prompt path, no repeat login after a rate limit, local check-ins,
  and baseline publication distinguished from a daily download.
- All PowerShell scripts parsed without syntax errors.
- Python source parsed without syntax errors.
- Python 3.13 detection exercised against an existing runtime.
- Distinct installation paths produced distinct profile IDs.
- All eleven Windows control shortcuts were created in a temporary test workspace;
  a shortcut's target arguments were checked. No real user task was changed.
- Package contains only the explicitly selected source/docs/tests. No personal
  training files, actual login tokens, actual config, logs, or environments.
- SHA-256 manifest covers the packaged files. It detects corruption, not origin.

## Still needs a fresh-PC pilot

- Downloading and verifying the official Python installer, per-user installation,
  and dependency downloads on a computer without a compatible Python runtime.
- Full first-time Garmin login and account-specific MFA/anti-bot behavior.
- Registration, missed-run catch-up, battery operation, and automatic execution
  of this starter kit's new task on another Windows account/computer.
- x64 and ARM64 fresh-install paths, managed-device restrictions, and Windows
  download/security prompts. No signing certificate is bundled.

Earlier personal versions of the historical and daily download scripts were
successfully used against a real Garmin account, but that does not establish
end-to-end validation of this new combined installer.

Do not call this production-ready or Garmin-approved. Pilot with one consenting
friend, use Check Status to verify actual output, and share only the source ZIP.

## Run the offline tests after installing dependencies

From the extracted source folder, use the installed environment's Python:

```powershell
& "$HOME\GarminSync\.venv\Scripts\python.exe" -m unittest discover -s tests -p 'test_*.py'
```

Substitute the actual installation folder if different. These tests do not use
the user's real Garmin credentials, real training data, or scheduled tasks.
