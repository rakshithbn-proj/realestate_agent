# Stop the Atlas local stack: app first, then Postgres.
# ASCII-only (Windows PowerShell 5.1 mangles non-ASCII in .ps1).
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
$pgbin = Join-Path $root ".pgbin\pgsql\bin"

# 1. App - by recorded pid, then by port as a fallback
$stopped = $false
$pidFile = Join-Path $root ".run\uvicorn.pid"
if (Test-Path $pidFile) {
    $procId = (Get-Content $pidFile | Select-Object -First 1)
    if ($procId) {
        try { Stop-Process -Id ([int]$procId) -Force -ErrorAction Stop; $stopped = $true } catch {}
    }
    Remove-Item $pidFile -ErrorAction SilentlyContinue
}
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
    try { Stop-Process -Id $_.OwningProcess -Force -ErrorAction Stop; $stopped = $true } catch {}
}
Write-Host $(if ($stopped) { "App: stopped" } else { "App: was not running" })

# 2. Postgres
if (Get-NetTCPConnection -LocalPort 5432 -State Listen -ErrorAction SilentlyContinue) {
    & (Join-Path $pgbin "pg_ctl.exe") -D (Join-Path $root ".pgdata") -m fast stop | Out-Null
    Write-Host "Postgres: stopped"
} else {
    Write-Host "Postgres: was not running"
}
