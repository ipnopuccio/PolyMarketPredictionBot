#!/usr/bin/env bash
# backup.sh — Backup PostgreSQL database for btc-bot-v2
# Usage: ./scripts/backup.sh [output_dir]
set -euo pipefail

BACKUP_DIR="${1:-./backups}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FILENAME="trading_bot_${TIMESTAMP}.sql.gz"

PG_HOST="${PG_HOST:-localhost}"
PG_PORT="${PG_PORT:-5432}"
PG_USER="${PG_USER:-tradingbot}"
PG_DB="${PG_DB:-trading_bot}"

mkdir -p "$BACKUP_DIR"

echo "Backing up $PG_DB to $BACKUP_DIR/$FILENAME ..."

if command -v docker &>/dev/null && docker compose ps postgres --format '{{.Status}}' 2>/dev/null | grep -qi "up"; then
    # Backup via docker compose exec
    docker compose exec -T postgres pg_dump -U "$PG_USER" "$PG_DB" | gzip > "$BACKUP_DIR/$FILENAME"
else
    # Direct pg_dump
    PGPASSWORD="${DB_PASSWORD:-tradingbot}" pg_dump -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" "$PG_DB" | gzip > "$BACKUP_DIR/$FILENAME"
fi

SIZE=$(du -h "$BACKUP_DIR/$FILENAME" | cut -f1)
echo "Done: $BACKUP_DIR/$FILENAME ($SIZE)"

# Prune backups older than 30 days
PRUNED=$(find "$BACKUP_DIR" -name "trading_bot_*.sql.gz" -mtime +30 -delete -print | wc -l)
if [ "$PRUNED" -gt 0 ]; then
    echo "Pruned $PRUNED backup(s) older than 30 days."
fi
