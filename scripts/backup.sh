#!/bin/sh
# Nightly Postgres dump (plan §7: backups + tested restore).
# VPS cron:  30 2 * * *  cd /path/to/atlas && sh scripts/backup.sh
# Restore test (run monthly, seriously):
#   docker compose exec -T db psql -U atlas -d atlas_restore_test < backups/<file>.sql
set -eu

# Cron runs with a bare environment — pick up the same credentials compose
# uses, so a non-default POSTGRES_USER/DB doesn't silently break the dump.
if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi

BACKUP_DIR="${BACKUP_DIR:-./backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date +%Y%m%d_%H%M%S)"
# Service name of the Atlas database. 'atlas-db' when merged into an existing
# multi-service compose (deploy/compose-snippet.yml); 'db' for the repo's
# standalone docker-compose.yml. Dumping the WRONG service would silently back
# up someone else's database under an atlas_ filename.
DB_SERVICE="${ATLAS_DB_SERVICE:-atlas-db}"
DB_USER="${ATLAS_POSTGRES_USER:-${POSTGRES_USER:-atlas}}"
DB_NAME="${ATLAS_POSTGRES_DB:-${POSTGRES_DB:-atlas}}"

mkdir -p "$BACKUP_DIR"
docker compose exec -T "$DB_SERVICE" pg_dump -U "$DB_USER" "$DB_NAME" \
    | gzip > "$BACKUP_DIR/atlas_$STAMP.sql.gz"

find "$BACKUP_DIR" -name 'atlas_*.sql.gz' -mtime "+$KEEP_DAYS" -delete
echo "backup written: $BACKUP_DIR/atlas_$STAMP.sql.gz"
