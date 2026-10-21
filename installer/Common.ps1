$ErrorActionPreference = 'Stop'

function Write-JsonFile([string]$Path, $Value) {
    $parentPath = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $parentPath -Force | Out-Null
    $content = $Value | ConvertTo-Json -Depth 12
    [System.IO.File]::WriteAllText($Path, $content, (New-Object System.Text.UTF8Encoding($false)))
}

function Get-ProfileId([string]$Root) {
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = $algorithm.ComputeHash([Text.Encoding]::UTF8.GetBytes($Root.ToLowerInvariant()))
        return ([BitConverter]::ToString($bytes).Replace('-', '').Substring(0, 12).ToLowerInvariant())
    } finally { $algorithm.Dispose() }
}

function Read-HistoryStart([string]$DefaultChoice) {
    while ($true) {
        Write-Host 'Historical range: a start date imports activities AND daily sleep/HRV from that date through today.'
        $answer = Read-Host ('Start date YYYY-MM-DD, or ALL for all history [Enter = ' + $DefaultChoice + ']')
        if ([string]::IsNullOrWhiteSpace($answer)) { $answer = $DefaultChoice }
        $answer = $answer.Trim()
        if ($answer -ieq 'ALL') { return $null }
        $parsedDate = [datetime]::MinValue
        if ([datetime]::TryParseExact($answer, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::None, [ref]$parsedDate)) {
            if ($parsedDate.Date -le [datetime]::Today) { return $parsedDate.ToString('yyyy-MM-dd') }
        }
        Write-Host 'Enter a valid date no later than today, for example 2026-01-01, or ALL.' -ForegroundColor Yellow
    }
}

function Test-Python313([string]$Executable, [string[]]$PrefixArguments = @()) {
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $null }
    $oldPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $answer = & $Executable @PrefixArguments -c 'import sys; print(sys.executable) if sys.version_info[:2] == (3,13) else sys.exit(1)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $answer) { return ([string]@($answer)[-1]).Trim() }
    } catch { return $null } finally { $ErrorActionPreference = $oldPreference }
    return $null
}

function Find-Python313 {
    # PYTHON_MANAGER_AUTOMATIC_INSTALL is disabled just while probing aliases.
    $savedAutomatic = $env:PYTHON_MANAGER_AUTOMATIC_INSTALL
    $env:PYTHON_MANAGER_AUTOMATIC_INSTALL = 'false'
    try {
        foreach ($alias in @('py.exe', 'python3.13.exe', 'python.exe')) {
            $command = Get-Command $alias -ErrorAction SilentlyContinue
            if ($command) {
                $prefix = @()
                if ($alias -eq 'py.exe') { $prefix = @('-3.13') }
                $result = Test-Python313 $command.Source $prefix
                if ($result) { return $result }
            }
        }
        $pythonRoots = @(
            (Join-Path $env:LOCALAPPDATA 'Python'),
            (Join-Path $env:LOCALAPPDATA 'Programs\Python'),
            (Join-Path $env:LOCALAPPDATA 'GarminTrainingStarter\Python313')
        )
        foreach ($candidateRoot in $pythonRoots) {
            if (-not (Test-Path -LiteralPath $candidateRoot)) { continue }
            $candidates = Get-ChildItem -LiteralPath $candidateRoot -Filter python.exe -File -Recurse -ErrorAction SilentlyContinue
            foreach ($candidate in $candidates) {
                $result = Test-Python313 $candidate.FullName
                if ($result) { return $result }
            }
        }
    } finally { $env:PYTHON_MANAGER_AUTOMATIC_INSTALL = $savedAutomatic }
    return $null
}

function Install-PrivatePython313 {
    $architecture = $env:PROCESSOR_ARCHITECTURE
    if ($env:PROCESSOR_ARCHITEW6432) { $architecture = $env:PROCESSOR_ARCHITEW6432 }
    switch ($architecture.ToUpperInvariant()) {
        'ARM64' { $suffix = 'arm64' }
        'AMD64' { $suffix = 'amd64' }
        default { throw 'This pilot supports 64-bit Windows on Intel/AMD or ARM64 only.' }
    }
    $runtimeRoot = Join-Path $env:LOCALAPPDATA 'GarminTrainingStarter\Python313'
    $downloadFolder = Join-Path $env:LOCALAPPDATA 'GarminTrainingStarter\Downloads'
    New-Item -ItemType Directory -Path $downloadFolder -Force | Out-Null
    $installerPath = Join-Path $downloadFolder ('python-3.13.16-' + $suffix + '.exe')
    $downloadUrl = 'https://www.python.org/ftp/python/3.13.16/python-3.13.16-' + $suffix + '.exe'
    Write-Host 'Downloading Python 3.13.16 from python.org (about 30 MB)...'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $downloadUrl -OutFile $installerPath -UseBasicParsing
    $signature = Get-AuthenticodeSignature -LiteralPath $installerPath
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
        throw 'The Python installer signature could not be verified. Nothing was installed. Check https://www.python.org/downloads/windows/.'
    }
    Write-Host 'Installing Python for this user. No system PATH changes or admin installation.'
    $arguments = '/quiet InstallAllUsers=0 PrependPath=0 Include_launcher=0 Include_test=0 Include_doc=0 Include_tcltk=0 Include_pip=1 Shortcuts=0 TargetDir="' + $runtimeRoot + '"'
    $process = Start-Process -FilePath $installerPath -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -notin @(0, 3010)) { throw ('Python installation failed with code ' + $process.ExitCode) }
    $result = Test-Python313 (Join-Path $runtimeRoot 'python.exe')
    if (-not $result) { throw 'Python did not become available. Restart Windows if requested, then run Setup again.' }
    return $result
}

function Register-GarminTask($Config) {
    $existing = Get-ScheduledTask -TaskName $Config.task_name -ErrorAction SilentlyContinue
    $controller = Join-Path $Config.install_root 'tools.py'
    if ($existing) {
        $matchingActions = @($existing.Actions | Where-Object { $_.Arguments -like ('*' + $controller + '*') })
        if ($matchingActions.Count -eq 0) { throw 'A different scheduled task has the same name. No task was changed.' }
    }
    $pythonWindowless = Join-Path $Config.install_root '.venv\Scripts\pythonw.exe'
    $action = New-ScheduledTaskAction -Execute $pythonWindowless -Argument ('"' + $controller + '" scheduled') -WorkingDirectory $Config.install_root
    $clock = [datetime]::ParseExact($Config.daily_time, 'HH:mm', [Globalization.CultureInfo]::InvariantCulture)
    $trigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours($clock.Hour).AddMinutes($clock.Minute))
    $userIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $userIdentity -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2) -Hidden
    $definition = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description ('Local Garmin download at ' + $Config.daily_time + ' in Windows local time. No AI calls or cloud uploads.')
    Register-ScheduledTask -TaskName $Config.task_name -InputObject $definition -Force | Out-Null
    $task = Get-ScheduledTask -TaskName $Config.task_name
    if ($task.State -eq 'Disabled') { throw 'The scheduled task is disabled.' }
    return Get-ScheduledTaskInfo -TaskName $Config.task_name
}

function New-ToolShortcuts([string]$Root) {
    $shell = New-Object -ComObject WScript.Shell
    $menuRoot = Join-Path $Root 'Garmin Tools'
    New-Item -ItemType Directory -Path $menuRoot -Force | Out-Null
    $labels = [ordered]@{
        'Sync Now'='Sync'; 'Check Status'='Status'; 'Import or Resume History'='History';
        'Reconnect Garmin'='Login'; 'Daily Check-in'='CheckIn'; 'Change Schedule'='Schedule'; 'Change History Range'='HistoryRange';
        'Disable Daily Sync'='Disable'; 'Enable Daily Sync'='Enable';
        'Remove Scheduled Task'='Uninstall'; 'Open Training Folder'='Folder'
    }
    foreach ($label in $labels.Keys) {
        $shortcut = $shell.CreateShortcut((Join-Path $menuRoot ($label + '.lnk')))
        $shortcut.TargetPath = Join-Path $PSHOME 'powershell.exe'
        $shortcut.Arguments = '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $Root 'Controls.ps1') + '" -Mode ' + $labels[$label]
        $shortcut.WorkingDirectory = $Root
        $shortcut.Save()
    }
}
