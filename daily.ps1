# Daily forward log in one command (Windows PowerShell).
#
#   .\daily.ps1          (from the project folder)
#   or the full path to this file from anywhere.
#
# Steps: switch on the Python environment, log today's model prices, commit the new log,
# pick up any updates from GitHub, and push. On Fridays it also scores the week so far.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
# Keep a copy of everything this run prints, so an automatic run can be checked afterwards.
Start-Transcript -Path "$PSScriptRoot\last_run.txt" | Out-Null

function Fail($msg) {
    Write-Host ""
    Write-Host "STOPPED: $msg" -ForegroundColor Red
    Write-Host "Nothing after this step ran. Copy the messages above and paste them to Claude."
    Stop-Transcript | Out-Null
    exit 1
}

# 1. Python environment (the .venv folder can sit next to this folder or inside it)
$venvs = @("$PSScriptRoot\..\.venv\Scripts\Activate.ps1", "$PSScriptRoot\.venv\Scripts\Activate.ps1")
$venv = $venvs | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $venv) { Fail "Could not find the .venv folder. Expected it at ..\.venv or .\.venv" }
& $venv
Write-Host "Environment on: $venv" -ForegroundColor DarkGray

# 2. Log today's prices
Write-Host ""
Write-Host "== Logging today's model prices ==" -ForegroundColor Cyan
python forward_log.py
if ($LASTEXITCODE -ne 0) { Fail "forward_log.py did not finish" }

# 3. Commit the new log file(s)
Write-Host ""
Write-Host "== Saving to GitHub ==" -ForegroundColor Cyan
git add forward_log
git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host "No new log file to commit (nothing was logged)." -ForegroundColor Yellow
} else {
    $today = Get-Date -Format "yyyy-MM-dd"
    git commit -m "forward log $today"
    if ($LASTEXITCODE -ne 0) { Fail "git commit failed" }
}

# 4. Pick up any updates, then push
git pull --rebase
if ($LASTEXITCODE -ne 0) { Fail "git pull --rebase failed" }
git push
if ($LASTEXITCODE -ne 0) { Fail "git push failed" }

# 5. Fridays: score the logged forecasts that have settled
if ((Get-Date).DayOfWeek -eq "Friday") {
    Write-Host ""
    Write-Host "== Friday: scoring the forward log ==" -ForegroundColor Cyan
    python score_forward.py
}

Write-Host ""
Write-Host "Done. Today's log is on GitHub." -ForegroundColor Green
Stop-Transcript | Out-Null
