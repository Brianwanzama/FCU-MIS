#!/usr/bin/env bash
# Nightly backup: the database and the uploaded files. Keeps the last 14 days.
# Copy /var/backups/fcu-mis somewhere OFF this server too (see docs/DEPLOY_DROPLET.md).
set -euo pipefail

DEST=/var/backups/fcu-mis
KEEP_DAYS=14
STAMP=$(date +%Y-%m-%d_%H%M)

mkdir -p "$DEST"
umask 077

# shellcheck disable=SC1091
set -a; . /etc/fcu-mis/env; set +a

pg_dump "$DATABASE_URL" | gzip > "$DEST/db-$STAMP.sql.gz"
tar -czf "$DEST/files-$STAMP.tar.gz" -C /var/lib/fcu-mis .

find "$DEST" -type f \( -name 'db-*.sql.gz' -o -name 'files-*.tar.gz' \) -mtime +"$KEEP_DAYS" -delete

echo "Backup written to $DEST ($STAMP)"
