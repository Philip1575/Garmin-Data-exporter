param([ValidateSet('Sync','Status','History','HistoryRange','Login','CheckIn','Schedule','Disable','Enable','Uninstall','Folder')][string]$Mode='Status')
$ErrorActionPreference = 'Stop'
try {
    . (Join-Path $PSScriptRoot 'Common.ps1')
    $config = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'starter-kit.json') -Raw | ConvertFrom-Json
    $python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    $tools = Join-Path $PSScriptRoot 'tools.py'
    switch ($Mode) {
        'Sync' { & $python $tools sync; if ($LASTEXITCODE -ne 0) { Write-Host 'Sync was not successful. Check the status and daily log.' -ForegroundColor Yellow } }
        'History' {
            & $python $tools history
            if ($LASTEXITCODE -eq 0) { & $python $tools publish }
        }
        'Login' { & $python $tools login }
        'HistoryRange' {
            $current = ([datetime]::Today.ToString('yyyy') + '-01-01')
            if ($config.PSObject.Properties['history_since']) {
                $current = if ($config.history_since) { $config.history_since } else { 'ALL' }
            }
            $start = Read-HistoryStart $current
            $config | Add-Member -NotePropertyName history_since -NotePropertyValue $start -Force
            Write-JsonFile (Join-Path $PSScriptRoot 'starter-kit.json') $config
            Write-Host 'Saved. Use Import or Resume History to import that range. Existing archived records were not deleted.'
        }
        'CheckIn' { & $python $tools checkin }
        'Status' {
            & $python $tools status
            $task = Get-ScheduledTask -TaskName $config.task_name -ErrorAction SilentlyContinue
            if ($task) {
                $info = Get-ScheduledTaskInfo -TaskName $config.task_name
                Write-Host ('Scheduled task: ' + $task.State + ' | Next: ' + $info.NextRunTime + ' | Last result code: ' + $info.LastTaskResult)
                Write-Host 'Task result 0 means success; 267009 means currently running. Check data status separately.'
            } else { Write-Host 'Daily task is NOT installed. Use Change Schedule to install it.' }
        }
        'Schedule' {
            $answer = Read-Host ('New daily LOCAL time HH:mm [Enter = ' + $config.daily_time + ']')
            if (-not [string]::IsNullOrWhiteSpace($answer)) {
                $parsed = [datetime]::MinValue
                if (-not [datetime]::TryParseExact($answer, 'HH:mm', [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::None, [ref]$parsed)) { throw 'Enter a time such as 12:00 or 18:30.' }
                $config.daily_time = $answer
            }
            $config.windows_timezone = [TimeZoneInfo]::Local.Id
            $info = Register-GarminTask $config
            Write-JsonFile (Join-Path $PSScriptRoot 'starter-kit.json') $config
            Write-Host ('Schedule installed: ' + $config.daily_time + ' local. Next run: ' + $info.NextRunTime)
        }
        'Disable' { Disable-ScheduledTask -TaskName $config.task_name | Out-Null; Write-Host 'Daily downloads disabled. Your data remains saved.' }
        'Enable' { Enable-ScheduledTask -TaskName $config.task_name | Out-Null; Write-Host 'Daily downloads enabled.' }
        'Uninstall' {
            $answer = Read-Host 'Remove this scheduled task only? Training data, Python, and login tokens will remain. Type REMOVE to continue'
            if ($answer -eq 'REMOVE') { Unregister-ScheduledTask -TaskName $config.task_name -Confirm:$false; Write-Host 'Task removed. No training data or tokens were deleted.' }
            else { Write-Host 'Cancelled.' }
        }
        'Folder' { Invoke-Item -LiteralPath $PSScriptRoot }
    }
} catch {
    Write-Host ('Could not complete: ' + $_.Exception.Message) -ForegroundColor Red
}
if ($Mode -ne 'Folder') { Read-Host 'Press Enter to close' | Out-Null }
