# Backup and Restore Guide

This guide explains how to safely backup and restore the Odysseus `data/` directory.

## What's in `data/`?
All of your user state lives here:
- `app.db` - The SQLite database with users, sessions, settings, and schedules.
- `chroma/` - The vector database containing your parsed documents.
- `uploads/` and `mail-attachments/` - The physical files you've uploaded or received.

## How to Backup

You can use the included backup script:

```bash
chmod +x scripts/backup.sh
./scripts/backup.sh
```

This will safely snapshot the SQLite databases and compress everything into a timestamped `.tar.gz` archive in the `backups/` directory.

### Automated Backups (Cron)
If you are running on bare metal, you can add this to your crontab (`crontab -e`) to backup daily at 2am:
```text
0 2 * * * cd /path/to/odysseus && ./scripts/backup.sh >> /path/to/odysseus/logs/cron_backup.log 2>&1
```

### Docker Deployments
For Docker deployments with a named volume, we recommend backing up the volume using a temporary container:
```bash
docker run --rm -v odysseus_data:/data -v $(pwd)/backups:/backups alpine \
  tar -czf /backups/docker_backup_$(date +%Y%m%d).tar.gz -C /data .
```

## How to Restore

You can use the included restore script:

```bash
chmod +x scripts/restore.sh
./scripts/restore.sh backups/odysseus_backup_TIMESTAMP.tar.gz
```

This script will overwrite your existing `data/` folder and apply any necessary database migrations.

### Restoring in Docker
If you're using Docker:
1. Bring down the container: `docker compose down`
2. Extract the archive into your Docker volume using a temporary container:
```bash
docker run --rm -v odysseus_data:/data -v $(pwd)/backups:/backups alpine \
  sh -c "rm -rf /data/* && tar -xzf /backups/docker_backup_TIMESTAMP.tar.gz -C /data"
```
3. Bring the container back up: `docker compose up -d`
