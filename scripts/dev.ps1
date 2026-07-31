# Local dev stack on the Docker-less Windows box: portable Postgres from
# .pgbin + migrations + uvicorn with reload. Ctrl+C stops uvicorn; Postgres
# keeps running (stop it with:  .pgbin\pgsql\bin\pg_ctl -D .pgdata stop).
$ErrorActionPreference = "Stop"
$pgbin = Join-Path $PSScriptRoot "..\.pgbin\pgsql\bin"

if (-not (Test-Path (Join-Path $pgbin "initdb.exe"))) {
    throw ".pgbin\pgsql\bin not found - extract the PostgreSQL 16 windows-x64-binaries zip to .pgbin\ (see README)"
}
if (-not (Test-Path .pgdata)) {
    & (Join-Path $pgbin "initdb.exe") -D .pgdata -U postgres -A trust -E UTF8 --no-locale
}

if (-not (Get-NetTCPConnection -LocalPort 5432 -State Listen -ErrorAction SilentlyContinue)) {
    # No -Wait: pg_ctl exits once the server is ready, but Start-Process -Wait
    # would wait on the postmaster child too and hang forever.
    Start-Process -WindowStyle Hidden (Join-Path $pgbin "pg_ctl.exe") -ArgumentList `
        '-D', '.pgdata', '-w', '-l', '.pgdata\server.log', `
        '-o', '"-p 5432 -c listen_addresses=127.0.0.1"', 'start'
    foreach ($i in 1..30) {
        & (Join-Path $pgbin "pg_isready.exe") -h 127.0.0.1 -p 5432 -q
        if ($LASTEXITCODE -eq 0) { break }
        Start-Sleep -Seconds 1
    }
    if ($LASTEXITCODE -ne 0) { throw "Postgres did not come up - check .pgdata\server.log" }
}

$env:DATABASE_URL = "postgresql+psycopg://postgres@127.0.0.1:5432/postgres"
if (-not $env:ATLAS_API_TOKEN) { $env:ATLAS_API_TOKEN = "dev-token" }

.\.venv\Scripts\alembic upgrade head
Write-Host "`nAtlas dev: http://127.0.0.1:8000/docs  (token: $env:ATLAS_API_TOKEN)`n"
.\.venv\Scripts\uvicorn atlas.main:app --host 127.0.0.1 --port 8000 --reload
