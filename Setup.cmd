@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\Setup.ps1"
if errorlevel 1 echo Setup did not finish. Read the message above before retrying.
pause
