# One full day of Atlas ingestion: RERA -> portals -> sweep + legal tagging.
# Designed to be run unattended by Task Scheduler (see install-daily-task.ps1),
# so it is self-contained: it starts portable Postgres if it is not already up,
# applies migrations, runs the daily sequence, and logs everything.
# ASCII-only on purpose (Windows PowerShell 5.1 mangles non-ASCII in .ps1).
param(
    # Generous by default: crash recovery after an unclean shutdown can take
    # minutes on a cold disk. Overrunning the wait costs a whole gate day.
    [int]$PgStartTimeoutSec = 300
)
$ErrorActionPreference = "Stop"
# Read the child process's UTF-8 output as UTF-8; otherwise the console codepage
# mangles non-ASCII in log messages (an em dash arrived as 'ù').
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root                      # so .env / .pgdata / alembic resolve
$pgbin = Join-Path $root ".pgbin\pgsql\bin"
$logDir = Join-Path $root ".run"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir ("daily-" + (Get-Date -Format "yyyy-MM-dd") + ".log")

function Write-Log($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Write-Host $line
    Add-Content -Path $log -Value $line -Encoding utf8
}

# Run a native exe, tee its output into the log, and return its exit code.
# Two Windows PowerShell 5.1 traps this exists to avoid:
#  - Python's logging writes to STDERR, and '2>&1' on a native command wraps
#    every line in an ErrorRecord; under $ErrorActionPreference='Stop' that
#    turns an ordinary INFO log line into a terminating error mid-run.
#  - $LASTEXITCODE must be read immediately after the call, before any other
#    command (Write-Log included) overwrites it.
function Invoke-Logged($exe, [string[]]$argList) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $exe @argList 2>&1 | ForEach-Object {
            # .ToString() rather than Out-String: Out-String renders an
            # ErrorRecord with PowerShell's full NativeCommandError decoration
            # (CategoryInfo/FullyQualifiedErrorId) and hard-wraps at the console
            # width, splitting URLs across lines. These logs are the record for
            # diagnosing a broken streak, so keep them one-line-per-event.
            $text = if ($_ -is [System.Management.Automation.ErrorRecord]) {
                $_.ToString()
            } else { [string]$_ }
            foreach ($line in ($text -split "`r?`n")) {
                if ($line.Trim()) { Write-Log $line.TrimEnd() }
            }
        }
        return $LASTEXITCODE
    } finally { $ErrorActionPreference = $prev }
}

Write-Log "=== daily run starting ==="

# The plan requires jobs in Asia/Kolkata explicitly. Task Scheduler fires on
# LOCAL time, so a machine on another timezone would silently run at the wrong
# hour - warn loudly rather than pretend the schedule is correct.
$tz = (Get-TimeZone).Id
if ($tz -ne "India Standard Time") {
    Write-Log "WARNING: machine timezone is '$tz', not India Standard Time - scheduled hour is NOT Asia/Kolkata"
}

$startedPg = $false
try {
    if (-not (Test-Path (Join-Path $pgbin "pg_isready.exe"))) {
        throw ".pgbin\pgsql\bin not found - extract the PostgreSQL 16 binaries (see README)"
    }
    if (Get-NetTCPConnection -LocalPort 5432 -State Listen -ErrorAction SilentlyContinue) {
        Write-Log "Postgres: already running (leaving it up)"
    } else {
        Write-Log "Postgres: starting"
        Start-Process -WindowStyle Hidden (Join-Path $pgbin "pg_ctl.exe") -ArgumentList `
            '-D', '.pgdata', '-w', '-l', '.pgdata\server.log', `
            '-o', '"-p 5432 -c listen_addresses=127.0.0.1"', 'start'
        # Be patient: after an unclean shutdown (laptop slept / lost power)
        # Postgres runs crash recovery first, and on Windows the data-directory
        # fsync alone has been measured at ~34s here. A short wait fails in
        # exactly the situation this unattended job exists to survive, so allow
        # several minutes and let pg_isready decide when it is really up.
        $deadline = (Get-Date).AddSeconds($PgStartTimeoutSec)
        $ready = $false
        while ((Get-Date) -lt $deadline) {
            & (Join-Path $pgbin "pg_isready.exe") -h 127.0.0.1 -p 5432 -q
            if ($LASTEXITCODE -eq 0) { $ready = $true; break }
            Start-Sleep -Seconds 2
        }
        if (-not $ready) {
            throw "Postgres did not come up within ${PgStartTimeoutSec}s - check .pgdata\server.log"
        }
        $startedPg = $true
        Write-Log "Postgres: started"
    }

    $migrateExit = Invoke-Logged (Join-Path $root ".venv\Scripts\alembic.exe") @('upgrade', 'head')
    if ($migrateExit -ne 0) { throw "alembic upgrade head failed (exit $migrateExit)" }

    Write-Log "ingestion: running 'atlas.cli daily'"
    $py = Join-Path $root ".venv\Scripts\python.exe"
    $ingestExit = Invoke-Logged $py @('-m', 'atlas.cli', 'daily')

    Invoke-Logged $py @('-m', 'atlas.cli', 'gate') | Out-Null

    if ($ingestExit -ne 0) {
        Write-Log "=== daily run FINISHED WITH ERRORS (exit $ingestExit) ==="
    } else {
        Write-Log "=== daily run finished cleanly ==="
    }
    exit $ingestExit
}
catch {
    Write-Log "FATAL: $($_.Exception.Message)"
    exit 1
}
finally {
    # Only stop Postgres if THIS script started it - never yank the DB out from
    # under a dev session that already had it running.
    if ($startedPg) {
        Write-Log "Postgres: stopping (this run started it)"
        & (Join-Path $pgbin "pg_ctl.exe") -D .pgdata -w stop | Out-Null
    }
}
