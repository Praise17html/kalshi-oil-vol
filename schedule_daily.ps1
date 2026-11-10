# One-time setup: run daily.ps1 automatically every weekday (Windows Task Scheduler).
#
#   .\schedule_daily.ps1                 set it up for 1:20 pm (run again any time to change it)
#   .\schedule_daily.ps1 -At "2:00pm"    a different time
#   .\schedule_daily.ps1 -Remove         turn the automatic run off
#
# It runs while you are logged in to this laptop. If the laptop is off or asleep at that time,
# it runs as soon as it wakes. Each run's output is saved in last_run.txt in this folder.
param([switch]$Remove, [string]$At = "1:20pm")

$name = "Kalshi forward log"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Write-Host "Removed the automatic run '$name'." -ForegroundColor Yellow
    exit 0
}

$script = Join-Path $PSScriptRoot "daily.ps1"
if (-not (Test-Path $script)) { Write-Host "daily.ps1 not found next to this file." -ForegroundColor Red; exit 1 }

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`"" -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Host "Done: '$name' will run daily.ps1 every weekday at $At." -ForegroundColor Green
Write-Host "Check any run afterwards in last_run.txt, or on GitHub (a new file in forward_log)."
