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

mkdir -p "$BACKUP_DIR"
docker compose exec -T db pg_dump -U "${POSTGRES_USER:-atlas}" "${POSTGRES_DB:-atlas}" \
    | gzip > "$BACKUP_DIR/atlas_$STAMP.sql.gz"

find "$BACKUP_DIR" -name 'atlas_*.sql.gz' -mtime "+$KEEP_DAYS" -delete
echo "backup written: $BACKUP_DIR/atlas_$STAMP.sql.gz"
