#!/bin/bash
# Backup management DB to a timestamped SQL file
BACKUP_DIR="$(dirname "$0")/backups"
mkdir -p "$BACKUP_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FILE="$BACKUP_DIR/management_${TIMESTAMP}.sql.gz"

docker compose exec -T db pg_dump -U mgmt management | gzip > "$FILE"
echo "Backup saved to $FILE ($(du -h "$FILE" | cut -f1))"
