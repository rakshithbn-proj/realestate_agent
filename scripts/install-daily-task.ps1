# Register (or refresh) the Windows Scheduled Task that runs scripts\daily.ps1
# once a day, so the Phase-1 gate's 7-day clock keeps ticking on this machine
# while the VPS is being provisioned. Runs as the current user, no admin rights
# needed. Remove it later with:  Unregister-ScheduledTask -TaskName Atlas-Daily
# ASCII-only on purpose (Windows PowerShell 5.1 mangles non-ASCII in .ps1).
param(
    [string]$Time = "06:00",            # local time; machine should be IST
    [string]$TaskName = "Atlas-Daily"
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$script = Join-Path $root "scripts\daily.ps1"
if (-not (Test-Path $script)) { throw "not found: $script" }

$tz = (Get-TimeZone).Id
if ($tz -ne "India Standard Time") {
    Write-Warning "Machine timezone is '$tz'. Task Scheduler fires on LOCAL time, so '$Time' will NOT be $Time Asia/Kolkata."
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$script`"" `
    -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
# StartWhenAvailable is the important one: a laptop that was asleep at 06:00
# runs the job as soon as it wakes, instead of silently skipping the day and
# breaking the streak.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed existing task '$TaskName'"
}
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Atlas daily ingestion (RERA + portals + sweep/tag)" | Out-Null

Write-Host "Registered '$TaskName' - daily at $Time ($tz)"
Write-Host "Run it now:    Start-ScheduledTask -TaskName $TaskName"
Write-Host "Check status:  Get-ScheduledTaskInfo -TaskName $TaskName"
Write-Host "Logs:          .run\daily-<date>.log"
Write-Host "Gate progress: .venv\Scripts\python -m atlas.cli gate"
