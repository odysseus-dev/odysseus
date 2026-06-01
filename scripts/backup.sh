#!/usr/bin/env bash
# backup.sh - Safely backup the Odysseus data directory

set -e

# Default paths
DATA_DIR="data"
BACKUP_DIR="backups"

# Ensure we are in the project root
if [ ! -d "$DATA_DIR" ]; then
    echo "Error: data/ directory not found. Please run this script from the project root."
    exit 1
fi

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/odysseus_backup_$TIMESTAMP.tar.gz"
TEMP_DIR=$(mktemp -d)

echo "Starting backup process..."

# Copy the entire data directory to a temporary location
echo "Copying data directory..."
cp -r "$DATA_DIR" "$TEMP_DIR/data"

# Safely snapshot SQLite databases, overwriting the raw copies in the temp directory
echo "Snapshotting SQLite databases safely..."
if command -v sqlite3 >/dev/null 2>&1; then
    if [ -f "$DATA_DIR/app.db" ]; then
        sqlite3 "$DATA_DIR/app.db" ".backup '$TEMP_DIR/data/app.db'"
    fi
    if [ -f "$DATA_DIR/scheduled_emails.db" ]; then
        sqlite3 "$DATA_DIR/scheduled_emails.db" ".backup '$TEMP_DIR/data/scheduled_emails.db'"
    fi
else
    echo "Warning: sqlite3 command not found. SQLite databases will be copied as-is, which might result in corruption if the server is actively writing."
fi

# Clean up any transient fastembed/TTS caches that don't need backup
rm -rf "$TEMP_DIR/data/fastembed_cache" "$TEMP_DIR/data/tts_cache"

# Create the archive
echo "Creating archive: $BACKUP_FILE"
tar -czf "$BACKUP_FILE" -C "$TEMP_DIR" data

# Cleanup
rm -rf "$TEMP_DIR"

echo "Backup completed successfully!"
echo "Backup saved to: $BACKUP_FILE"
