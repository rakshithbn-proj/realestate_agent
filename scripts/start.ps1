# Start the Atlas local stack (Docker-less Windows): portable Postgres + app.
# Runs the app in the BACKGROUND and returns; stop it with scripts\stop.ps1.
# ASCII-only on purpose (Windows PowerShell 5.1 mangles non-ASCII in .ps1).
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root                      # so .env / .pgdata / alembic resolve
$pgbin = Join-Path $root ".pgbin\pgsql\bin"
$runDir = Join-Path $root ".run"
New-Item -ItemType Directory -Force $runDir | Out-Null

if (-not (Test-Path (Join-Path $pgbin "initdb.exe"))) {
    throw ".pgbin\pgsql\bin not found - extract the PostgreSQL 16 windows-x64-binaries zip to .pgbin\ (see README)"
}
if (-not (Test-Path (Join-Path $root ".pgdata"))) {
    & (Join-Path $pgbin "initdb.exe") -D .pgdata -U postgres -A trust -E UTF8 --no-locale
}

# 1. Postgres
if (Get-NetTCPConnection -LocalPort 5432 -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Postgres: already running"
} else {
    Start-Process -WindowStyle Hidden (Join-Path $pgbin "pg_ctl.exe") -ArgumentList `
        '-D', '.pgdata', '-w', '-l', '.pgdata\server.log', `
        '-o', '"-p 5432 -c listen_addresses=127.0.0.1"', 'start'
    foreach ($i in 1..30) {
        & (Join-Path $pgbin "pg_isready.exe") -h 127.0.0.1 -p 5432 -q
        if ($LASTEXITCODE -eq 0) { break }
        Start-Sleep -Seconds 1
    }
    if ($LASTEXITCODE -ne 0) { throw "Postgres did not come up - check .pgdata\server.log" }
    Write-Host "Postgres: started"
}

# 2. Migrations (alembic reads DATABASE_URL from .env)
& (Join-Path $root ".venv\Scripts\alembic.exe") upgrade head | Out-Null
Write-Host "Migrations: up to date"

# 3. App (background). The app reads .env for DATABASE_URL + ATLAS_API_TOKEN.
if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "App: already running on http://127.0.0.1:8000"
} else {
    $proc = Start-Process (Join-Path $root ".venv\Scripts\uvicorn.exe") `
        -ArgumentList 'atlas.main:app', '--host', '127.0.0.1', '--port', '8000' `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runDir "uvicorn.out.log") `
        -RedirectStandardError  (Join-Path $runDir "uvicorn.err.log")
    $proc.Id | Out-File -Encoding ascii (Join-Path $runDir "uvicorn.pid")
    $ready = $false
    foreach ($i in 1..30) {
        try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health -TimeoutSec 2 | Out-Null; $ready = $true; break }
        catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ready) { throw "App did not become ready - check .run\uvicorn.err.log" }
    Write-Host "App: started (pid $($proc.Id))"
}

# Print the bearer token from .env (read at runtime, not hardcoded)
$m = Select-String -Path (Join-Path $root ".env") -Pattern '^\s*ATLAS_API_TOKEN\s*=\s*(.+?)\s*$' | Select-Object -First 1
$token = if ($m) { $m.Matches.Groups[1].Value } else { "(set ATLAS_API_TOKEN in .env)" }
Write-Host ""
Write-Host "Atlas is up:   http://127.0.0.1:8000/docs"
Write-Host "Bearer token:  $token"
Write-Host "Stop it with:  scripts\stop.ps1"
