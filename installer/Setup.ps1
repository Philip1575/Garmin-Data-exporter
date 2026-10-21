$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')
try {
    if ([Environment]::OSVersion.Version.Major -lt 10) { throw 'Windows 10 or Windows 11 is required.' }
    $kitRoot = Split-Path -Parent $PSScriptRoot
    Write-Host ''
    Write-Host 'GARMIN TRAINING STARTER KIT - WINDOWS PILOT v0.2' -ForegroundColor Cyan
    Write-Host 'Creates local training files, prompts privately for Garmin login, and schedules downloads.'
    Write-Host 'Uses an unofficial Garmin client. No AI account, API key, payment, or cloud storage is required for downloads.'
    Write-Host 'Internet is required. Initial history can take an hour or longer. Keep this window open.'
    Write-Host ''
    # Integrity check detects incomplete extraction/corruption, not publisher authenticity.
    $manifestPath = Join-Path $kitRoot 'manifest.json'
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    foreach ($entry in $manifest.files) {
        $file = Join-Path $kitRoot $entry.path
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw ('Package file is missing: ' + $entry.path + '. Extract the entire ZIP first.') }
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.sha256) { throw ('Package integrity check failed: ' + $entry.path) }
    }
    $defaultRoot = Join-Path $env:USERPROFILE 'GarminSync'
    if ((Test-Path -LiteralPath $defaultRoot) -and -not (Test-Path -LiteralPath (Join-Path $defaultRoot 'starter-kit.json'))) {
        $defaultRoot = Join-Path $env:USERPROFILE 'GarminSync-Starter'
        Write-Host 'An existing GarminSync setup was found. This kit will use a separate folder.'
    }
    $choice = Read-Host ('Install folder [Enter = ' + $defaultRoot + ']')
    $installRoot = if ([string]::IsNullOrWhiteSpace($choice)) { $defaultRoot } else { [IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($choice.Trim().Trim('"'))) }
    $installRoot = [IO.Path]::GetFullPath($installRoot).TrimEnd('\')
    $profileRoot = [IO.Path]::GetFullPath($env:USERPROFILE).TrimEnd('\') + '\'
    if (-not $installRoot.StartsWith($profileRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Choose a dedicated folder inside your Windows user folder.' }
    if ($installRoot.Contains('"')) { throw 'Folder names containing quotes are not supported.' }
    $markerPath = Join-Path $installRoot 'starter-kit.json'
    $existingConfig = $null
    if (Test-Path -LiteralPath $installRoot) {
        if (-not (Test-Path -LiteralPath $markerPath)) {
            $contents = @(Get-ChildItem -LiteralPath $installRoot -Force)
            if ($contents.Count -gt 0) { throw 'That folder already contains files and is not a starter-kit installation. Choose a new empty folder.' }
        } else {
            $existingConfig = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
            if ($existingConfig.application -ne 'garmin-training-starter') { throw 'Unrecognized installation marker; choose a new empty folder.' }
            Write-Host 'Existing starter-kit installation found. Data and login tokens will be preserved.'
        }
    }
    $accept = Read-Host 'Proceed with installing scripts, Python dependencies, and a daily task? [Y/n]'
    if ($accept -match '^[Nn]') { Write-Host 'Cancelled. No installation changes made.'; exit 0 }
    $clock = Read-Host 'Daily sync time in 24-hour LOCAL time [Enter = 12:00]'
    if ([string]::IsNullOrWhiteSpace($clock)) { $clock = '12:00' }
    $parsedClock = [datetime]::MinValue
    if (-not [datetime]::TryParseExact($clock, 'HH:mm', [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::None, [ref]$parsedClock)) { throw 'Enter a time such as 12:00 or 18:30. Run Setup again.' }
    $units = Read-Host 'Preferred display units: miles or km [Enter = miles]'
    if ([string]::IsNullOrWhiteSpace($units)) { $units = 'miles' }
    if ($units -notin @('miles','km')) { throw 'Units must be miles or km. Run Setup again.' }
    $historyDefault = ([datetime]::Today.ToString('yyyy') + '-01-01')
    if ($existingConfig -and $existingConfig.PSObject.Properties['history_since']) {
        $historyDefault = if ($existingConfig.history_since) { $existingConfig.history_since } else { 'ALL' }
    }
    Write-Host ('Default for a new setup is this year, starting ' + [datetime]::Today.ToString('yyyy') + '-01-01.')
    $historyStart = Read-HistoryStart $historyDefault
    New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
    $profileId = Get-ProfileId $installRoot
    $privateRoot = Join-Path $env:LOCALAPPDATA ('GarminTrainingStarter\Profiles\' + $profileId)
    New-Item -ItemType Directory -Path $privateRoot -Force | Out-Null
    # Tokens are outside the training workspace and restricted to this user and SYSTEM.
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule('SYSTEM', 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
    Set-Acl -LiteralPath $privateRoot -AclObject $acl
    $config = [ordered]@{ application='garmin-training-starter'; version='0.2.0'; install_root=$installRoot;
        token_dir=(Join-Path $privateRoot 'tokens'); task_name=('GarminTraining-' + $profileId);
        daily_time=$clock; windows_timezone=([TimeZoneInfo]::Local.Id); display_units=$units; history_since=$historyStart;
        created_at=(Get-Date).ToUniversalTime().ToString('o') }
    if ($existingConfig -and $existingConfig.created_at) { $config.created_at = $existingConfig.created_at }
    # Back up scripts before an explicit setup rerun; never copy the archive into backups.
    if ($existingConfig) {
        $backup = Join-Path $privateRoot ('script-backups\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
        foreach ($relative in @('historical_import.py','DailySync\sync_daily.py','tools.py','Controls.ps1','Common.ps1','AGENTS.md','starter-kit.json')) {
            $previous = Join-Path $installRoot $relative
            if (Test-Path -LiteralPath $previous -PathType Leaf) {
                $backupFile = Join-Path $backup $relative
                New-Item -ItemType Directory -Path (Split-Path -Parent $backupFile) -Force | Out-Null
                Copy-Item -LiteralPath $previous -Destination $backupFile
            }
        }
    }
    $payload = Join-Path $kitRoot 'payload'
    foreach ($relative in @('historical_import.py','tools.py','Controls.ps1','AGENTS.md','.gitignore','requirements.txt')) {
        Copy-Item -LiteralPath (Join-Path $payload $relative) -Destination (Join-Path $installRoot $relative) -Force
    }
    New-Item -ItemType Directory -Path (Join-Path $installRoot 'DailySync') -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $payload 'DailySync\sync_daily.py') -Destination (Join-Path $installRoot 'DailySync\sync_daily.py') -Force
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Common.ps1') -Destination (Join-Path $installRoot 'Common.ps1') -Force
    Copy-Item -LiteralPath (Join-Path $kitRoot 'START_HERE.html') -Destination (Join-Path $installRoot 'Help.html') -Force
    Copy-Item -LiteralPath (Join-Path $kitRoot 'README.md') -Destination (Join-Path $installRoot 'README.md') -Force
    Write-JsonFile $markerPath $config
    $virtualPython = Join-Path $installRoot '.venv\Scripts\python.exe'
    $foundPython = Test-Python313 $virtualPython
    if (-not $foundPython) {
        $foundPython = Find-Python313
        if (-not $foundPython) {
            $allowDownload = Read-Host 'Python 3.13 was not found. Download/install signed Python from python.org for this user? [Y/n]'
            if ($allowDownload -match '^[Nn]') { throw 'Setup paused. Install Python 3.13 from python.org and rerun Setup; created files are preserved.' }
            $foundPython = Install-PrivatePython313
        }
        & $foundPython -m venv (Join-Path $installRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the isolated Python environment.' }
    }
    Write-Host 'Installing pinned Garmin packages from PyPI...'
    & $virtualPython -m pip install --disable-pip-version-check -r (Join-Path $installRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check network access, then rerun Setup.' }
    & $virtualPython -c "from garminconnect import Garmin; import curl_cffi; print('Garmin packages ready')"
    if ($LASTEXITCODE -ne 0) { throw 'Garmin dependency validation failed.' }
    New-ToolShortcuts $installRoot
    $desktopChoice = Read-Host 'Add a Garmin Training shortcut to your desktop? [Y/n]'
    if ($desktopChoice -notmatch '^[Nn]') {
        $shell = New-Object -ComObject WScript.Shell
        $desktop = [Environment]::GetFolderPath('Desktop')
        $link = $shell.CreateShortcut((Join-Path $desktop 'Garmin Training.lnk'))
        $link.TargetPath = Join-Path $installRoot 'Garmin Tools'
        $link.Save()
    }
    Write-Host ''
    Write-Host 'PRIVATE GARMIN LOGIN - type credentials only in this local window.' -ForegroundColor Cyan
    & $virtualPython (Join-Path $installRoot 'tools.py') login
    if ($LASTEXITCODE -ne 0) { throw 'Login did not complete. Stop and read the message. Use Reconnect Garmin later; do not repeatedly retry a rate limit.' }
    $scheduled = $false
    try {
        $taskInfo = Register-GarminTask ([pscustomobject]$config)
        $scheduled = $true
        Write-Host ('Daily task installed for ' + $clock + ' (' + $config.windows_timezone + '). Next run: ' + $taskInfo.NextRunTime)
    } catch {
        Write-Host ('Daily task could not be registered: ' + $_.Exception.GetType().Name) -ForegroundColor Yellow
        Write-Host 'Manual shortcuts still work. Use Change Schedule to retry. Scheduling is NOT installed yet.'
    }
    $rangeLabel = if ($historyStart) { 'from ' + $historyStart + ' through today' } else { 'for all available dates' }
    $doHistory = Read-Host ('Import training history ' + $rangeLabel + ' now? [Y/n]')
    if ($doHistory -notmatch '^[Nn]') {
        & $virtualPython (Join-Path $installRoot 'tools.py') history
        if ($LASTEXITCODE -eq 0) {
            Write-Host 'History saved. Publishing the initial overview from cached records...'
            & $virtualPython (Join-Path $installRoot 'tools.py') publish
        } else {
            Write-Host 'History is incomplete. Completed sections are saved. Use Import or Resume History later.' -ForegroundColor Yellow
        }
    } else {
        Write-Host 'History deferred. Use Import or Resume History when you have time.'
    }
    Write-Host ''
    Write-Host ('Setup files ready: ' + $installRoot) -ForegroundColor Green
    if ($scheduled) { Write-Host 'Daily download is scheduled. You must be signed in, awake, and online. It does not wake the computer.' }
    Write-Host 'Use the Garmin Tools folder for Sync Now, Check Status, and the other controls.'
    Write-Host 'For AI coaching, open this training folder in local Codex. AGENTS.md explains the data; AI access is separate.'
    Write-Host 'Share the original starter-kit ZIP, never your installed training folder or private token folder.'
} catch {
    Write-Host ''
    Write-Host ('Setup stopped: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'Completed files remain in place. Do not delete training data or disable Windows security to troubleshoot.'
    exit 1
}
