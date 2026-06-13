# Backup & Restore

Odysseus ships a built-in CLI tool, `scripts/odysseus-backup`, for creating and restoring snapshots of your `data/` directory. This guide covers all subcommands, flags, and recommended automation patterns.

> **Warning:** `restore` is destructive — it overwrites `data/` in place. Always verify a backup before restoring to production.

---

## What Gets Backed Up

The `data/` directory contains everything Odysseus needs to run:

| Path | Contents |
|---|---|
| `data/*.db` | SQLite database (conversations, memory, settings) |
| `data/.app_key` | Fernet encryption key |
| `data/vault/` | Encrypted credentials and secrets |
| `data/memory/` | Long-term memory indexes |
| `data/rag/` | RAG document indexes |
| `data/uploads/` | Personal file uploads |

### Excluded by default

The following paths are skipped unless explicitly included:

- `data/deep_research/` — large research run artifacts (use `--include-research` to include)
- `data/mail-attachments/` — cached IMAP attachment extractions (use `--include-attachments` to include)

---

## Subcommands

### `snapshot` — Create a backup

Creates a gzip-compressed tarball of `data/`. The SQLite database is copied using `sqlite3 .backup` so the snapshot is consistent even while the app is running.

```bash
# Default: saves to backups/<YYYY-MM-DDTHH:MM:SS>.tar.gz
scripts/odysseus-backup snapshot

# Custom output path
scripts/odysseus-backup snapshot --out /mnt/nas/odysseus-backup.tar.gz

# Include large optional directories
scripts/odysseus-backup snapshot --include-research --include-attachments
```

**Flags:**

| Flag | Description |
|---|---|
| `--out PATH` | Write the tarball to a custom path instead of `backups/` |
| `--include-research` | Also include `data/deep_research/` |
| `--include-attachments` | Also include `data/mail-attachments/` |
| `--pretty` | Format JSON output |

---

### `list` — List available backups

Lists all tarballs stored in the `backups/` directory.

```bash
scripts/odysseus-backup list

# Pretty-printed output
scripts/odysseus-backup list --pretty
```

---

### `verify` — Check tarball integrity

Performs an integrity check on a tarball without restoring it. Run this before any restore operation.

```bash
scripts/odysseus-backup verify backups/2026-06-13T10:00:00.tar.gz
```

**Arguments:**

| Argument | Description |
|---|---|
| `PATH` | Path to the tarball to verify |

---

### `restore` — Restore from a backup

Overwrites the current `data/` directory with the contents of a tarball. **This is irreversible.** The `--yes` flag is required as an explicit confirmation.

```bash
scripts/odysseus-backup restore backups/2026-06-13T10:00:00.tar.gz --yes
```

**Arguments & Flags:**

| Argument / Flag | Description |
|---|---|
| `PATH` | Path to the tarball to restore from |
| `--yes` | Required — confirms destructive overwrite of `data/` |

> **Tip:** Always run `verify` before `restore`.

---

## Directory Paths by Deployment

### Docker

When running via Docker Compose, `data/` is bind-mounted from the host. Run the backup script from the host machine, pointing at the mounted volume path:

```bash
# From the project root on the host
scripts/odysseus-backup snapshot --out /opt/odysseus-backups/$(date +%F).tar.gz
```

Alternatively, exec into the container:

```bash
docker compose exec odysseus scripts/odysseus-backup snapshot
```

### Native Python

Run the script directly from the project root:

```bash
python scripts/odysseus-backup snapshot
```

---

## Automating Backups

### Cron + local storage

Add a crontab entry to snapshot daily at 2 AM:

```cron
0 2 * * * cd /opt/odysseus && scripts/odysseus-backup snapshot >> /var/log/odysseus-backup.log 2>&1
```

### Cron + remote copy via `scp`

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /opt/odysseus
OUT="backups/$(date +%Y-%m-%dT%H:%M:%S).tar.gz"
scripts/odysseus-backup snapshot --out "$OUT"
scp "$OUT" user@backup-server:/remote/odysseus-backups/
```

### Cron + S3 via `s3cmd`

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /opt/odysseus
OUT="backups/$(date +%Y-%m-%dT%H:%M:%S).tar.gz"
scripts/odysseus-backup snapshot --out "$OUT"
s3cmd put "$OUT" s3://my-bucket/odysseus-backups/
```

---

## Migration Between Hosts

1. On the **source** host, create a snapshot:
   ```bash
   scripts/odysseus-backup snapshot --out odysseus-migration.tar.gz
   ```
2. Transfer the file to the **destination** host:
   ```bash
   scp odysseus-migration.tar.gz user@new-host:/opt/odysseus/
   ```
3. On the **destination** host, verify then restore:
   ```bash
   cd /opt/odysseus
   scripts/odysseus-backup verify odysseus-migration.tar.gz
   scripts/odysseus-backup restore odysseus-migration.tar.gz --yes
   ```

---

## Reference

```
Usage: odysseus-backup <subcommand> [options]

Subcommands:
  snapshot              Create a backup tarball of data/
    --out PATH          Custom output path
    --include-research  Include data/deep_research/
    --include-attachments Include data/mail-attachments/
    --pretty            Pretty-print output

  list                  List entries in backups/
    --pretty            Pretty-print output

  verify PATH           Check tarball integrity
    --pretty            Pretty-print output

  restore PATH          Overwrite data/ from a tarball
    --yes               Required confirmation flag
    --pretty            Pretty-print output
```
