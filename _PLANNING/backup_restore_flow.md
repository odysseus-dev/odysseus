# LID Planning: Backup/Restore Guide and Helper Flow for `data/`

## Landscape
All critical user data in Odysseus—including the SQLite database (`app.db`), ChromaDB vector store, user uploads, memory files, and settings—is stored locally in the `data/` directory. Currently, to back up their instance, a user must manually copy this directory. This approach is error-prone: copying a live SQLite database or ChromaDB instance while it is being written to can result in data corruption. Furthermore, there is no official documentation or helper utility to guide users through safely migrating or restoring their instances, which is a common pain point for self-hosted software.

## Initiative
Create a standardized, safe, and automated backup and restore flow for the `data/` directory. The initiative will provide tools to safely snapshot the databases and package the data, ensuring zero corruption. We will also provide clear, step-by-step documentation for users running Odysseus on bare metal, Docker, and macOS/Windows to perform routine backups and disaster recovery.

## Deliverable
- **Backup Helper Script**: A new CLI script (e.g., `scripts/backup.sh` and/or a python equivalent `python -m scripts.backup`) that safely locks/snapshots the SQLite DB, gracefully handles the ChromaDB state, and creates a timestamped `.tar.gz` or `.zip` archive of the `data/` directory.
- **Restore Helper Script**: A corresponding `restore.sh` script to safely extract a backup archive, overwrite the current `data/` directory, and apply any necessary database migrations.
- **Comprehensive Documentation**: A new guide at `docs/backup_restore.md` detailing the backup/restore process, including cron examples for automated backups and specific instructions for Docker volume management during the process.
- **Admin UI Hook**: (Future/Stretch) Consider adding a "Download Backup" button in the Admin Settings UI that triggers the backup script and serves the archive.
