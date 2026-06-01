#!/usr/bin/env bash
# restore.sh - Restore the Odysseus data directory from a backup archive

set -e

if [ -z "$1" ]; then
    echo "Usage: ./scripts/restore.sh <backup_file.tar.gz>"
    exit 1
fi

BACKUP_FILE="$1"
DATA_DIR="data"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "Error: Backup file '$BACKUP_FILE' not found."
    exit 1
fi

if [ ! -d "scripts" ]; then
    echo "Error: Please run this script from the project root."
    exit 1
fi

echo "WARNING: This will overwrite your current data/ directory."
read -p "Are you sure you want to continue? [y/N] " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Restore cancelled."
    exit 1
fi

echo "Stopping services is recommended before restoring. Proceeding..."

# Create a backup of the current state just in case
if [ -d "$DATA_DIR" ]; then
    TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
    SAFE_BACKUP="data_before_restore_$TIMESTAMP.bak"
    echo "Backing up current data/ to $SAFE_BACKUP..."
    mv "$DATA_DIR" "$SAFE_BACKUP"
fi

echo "Extracting backup..."
tar -xzf "$BACKUP_FILE"

echo "Applying migrations if necessary..."
if [ -f "venv/bin/alembic" ]; then
    source venv/bin/activate
    alembic upgrade head || echo "No migrations to apply or alembic not configured."
else
    echo "Alembic not found in venv. Skipping database migrations."
fi

echo "Restore completed successfully!"
