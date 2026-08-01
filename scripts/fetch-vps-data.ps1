# Pull the VPS database down to this machine so analysis and tuning happen
# where development happens. The VPS keeps collecting throughout — this is a
# read-only snapshot, it never writes to or pauses the server.
#
#   scripts\fetch-vps-data.ps1 -VpsHost atlas@1.2.3.4
#   scripts\fetch-vps-data.ps1 -VpsHost atlas@1.2.3.4 -IntoLocal   # also restore
#
# Requires ssh + scp on PATH (built into Windows 10/11).
# ASCII-only on purpose (Windows PowerShell 5.1 mangles non-ASCII in .ps1).
param(
    [Parameter(Mandatory = $true)][string]$VpsHost,
    [string]$RemotePath = "~/atlas",      # where docker-compose.yml lives on the VPS
    # Compose service name of the Atlas database. 'atlas-db' matches
    # deploy/compose-snippet.yml (merged into an existing stack); use 'db' for
    # the repo's standalone docker-compose.yml.
    [string]$DbService = "atlas-db",
    [string]$DbUser = "atlas",
    [string]$DbName = "atlas",
    # Restore into the LOCAL portable Postgres after downloading. Destructive
    # to the local DB, so it is opt-in.
    [switch]$IntoLocal,
    [string]$LocalDb = "atlas_vps"
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$outDir = Join-Path $root "backups"
New-Item -ItemType Directory -Force $outDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$remoteTmp = "/tmp/atlas_$stamp.sql.gz"
$local = Join-Path $outDir "atlas_vps_$stamp.sql.gz"

Write-Host "Dumping on $VpsHost ..."
# pg_dump runs INSIDE the db container; the port is bound to localhost only, so
# there is nothing to connect to from outside - this is the supported path.
ssh $VpsHost "cd $RemotePath && docker compose exec -T $DbService pg_dump -U $DbUser $DbName | gzip > $remoteTmp"
if ($LASTEXITCODE -ne 0) { throw "remote pg_dump failed" }

Write-Host "Downloading ..."
scp "${VpsHost}:${remoteTmp}" $local
if ($LASTEXITCODE -ne 0) { throw "scp failed" }
ssh $VpsHost "rm -f $remoteTmp"

$size = "{0:N1} MB" -f ((Get-Item $local).Length / 1MB)
Write-Host "Saved: $local ($size)"

if (-not $IntoLocal) {
    Write-Host ""
    Write-Host "To load it locally later:"
    Write-Host "  scripts\fetch-vps-data.ps1 -VpsHost $VpsHost -IntoLocal"
    exit 0
}

$pgbin = Join-Path $root ".pgbin\pgsql\bin"
if (-not (Test-Path (Join-Path $pgbin "psql.exe"))) {
    throw ".pgbin\pgsql\bin not found - cannot restore locally"
}
if (-not (Get-NetTCPConnection -LocalPort 5432 -State Listen -ErrorAction SilentlyContinue)) {
    throw "local Postgres is not running - start it with scripts\start.ps1 first"
}

# Restore into a SEPARATE database, never over the local dev one: the local DB
# holds its own collection history, and clobbering it would destroy the only
# copy of days collected here before the VPS existed.
Write-Host "Restoring into local database '$LocalDb' ..."
& (Join-Path $pgbin "psql.exe") -h 127.0.0.1 -U postgres -d postgres -v ON_ERROR_STOP=1 `
    -c "DROP DATABASE IF EXISTS $LocalDb;" -c "CREATE DATABASE $LocalDb;"
if ($LASTEXITCODE -ne 0) { throw "could not recreate $LocalDb" }

# gunzip via the bundled zlib-less route: use tar/gzip from Windows if present,
# else fall back to .NET GZipStream.
$plain = [System.IO.Path]::ChangeExtension($local, $null).TrimEnd('.')
$in = [System.IO.File]::OpenRead($local)
$gz = New-Object System.IO.Compression.GZipStream($in, [System.IO.Compression.CompressionMode]::Decompress)
$out = [System.IO.File]::Create($plain)
$gz.CopyTo($out); $out.Close(); $gz.Close(); $in.Close()

& (Join-Path $pgbin "psql.exe") -h 127.0.0.1 -U postgres -d $LocalDb -q -f $plain
if ($LASTEXITCODE -ne 0) { throw "restore failed" }
Remove-Item $plain -Force

Write-Host ""
Write-Host "Restored into local database '$LocalDb'."
Write-Host "Point Atlas at it for analysis without touching your local history:"
Write-Host "  `$env:DATABASE_URL='postgresql+psycopg://postgres@127.0.0.1:5432/$LocalDb'"
Write-Host "  .venv\Scripts\python -m atlas.cli gate"
