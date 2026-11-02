# ChromaDB Volume Migration — Upgrading from pre-#6166 installs

> [!IMPORTANT]
> This guide is only for Docker-based installs that were deployed before PR #6166
> was merged. Native host installs are unaffected (they persist to `data/chroma/`
> on the host filesystem, which was always stored correctly).

---

## 1. What changed and why

Before PR #6166, the `chromadb-data` named volume was mounted at `/chroma/chroma` inside
the ChromaDB container. However, current `chromadb/chroma` images (version 1.0+) write their
database to `/data` — configured by the container image's internal `/config.yaml` (`persist_path: "/data"`).
Because the upstream ChromaDB image declares no `VOLUME` directive:

* **Any vector data written to `/data` accumulated exclusively in the container's writable layer.**
* The mounted `chromadb-data` named volume at `/chroma/chroma` remained completely empty.
* Any recreation or removal of the container (`docker compose down`, `docker compose up --force-recreate`, `docker rm`)
  permanently destroys that writable layer and all vector embeddings within it.

PR #6166 corrects the mount target across all Compose configurations to `chromadb-data:/data`.

Fresh installations will persist data directly to the named volume automatically. However, for
**existing running installations**, the vector store currently resides in the old container's
writable layer.

This guide provides a zero-data-loss, fail-closed operational procedure to quiesce, preserve,
back up, verify, seed, and validate that data before performing cutover.

---

## 2. Architecture and Safety Principles

1. **No Live `docker cp`:** ChromaDB uses SQLite (`chroma.sqlite3`) and binary HNSW segment index
   files (`data_level0.bin`, `header.bin`, `length.bin`, `link_lists.bin`). Taking a file copy while
   the database engine is running risks capturing torn SQLite pages or out-of-sync HNSW graph segments.
   Writers must be quiesced and ChromaDB gracefully stopped before copying files.
2. **Do Not Trust `docker rename` Alone:** Docker Compose tracks containers by internal metadata labels
   (`com.docker.compose.project`, `com.docker.compose.service`, `com.docker.compose.container-number`),
   **not** merely by container name. Renaming a container does not strip its labels; Compose commands
   can still discover, restart, or recreate it. To guarantee preservation of the writable layer, the
   stopped container must be committed to an immutable Docker image (`docker commit`).
3. **Never Overwrite Existing Volumes:** Named volumes must be positively verified as empty prior to seeding.
4. **Staging Validation Before Production Cutover:** The seeded named volume must be independently validated
   using a disposable ChromaDB instance on an isolated port before touching the production Compose deployment.
5. **No Rollback to `/chroma/chroma`:** Rolling back to pre-#6166 Compose files reintroduces the broken
   mount path where ChromaDB never looks. Rollback procedures must restore data into the corrected `/data` path
   or redeploy from the immutable backup image.

---

## 3. Step-by-Step Migration Procedure

### Step 0: Identify your project and volume names

Docker Compose prefixes container and volume names with the project name (usually the root directory name).
Identify yours before proceeding:

```bash
# Identify running ChromaDB container
docker ps --filter "ancestor=chromadb/chroma" --format "table {{.ID}}\t{{.Status}}\t{{.Names}}"

# Identify the named volume
docker volume ls --filter "name=chromadb"
```

Throughout this guide:
* `<project>` represents your Compose project (e.g. `odysseus` or `odysseus-maintainer-preview`).
* `<container>` represents the running ChromaDB container (e.g. `<project>-chromadb-1`).
* `<volume>` represents the named volume (e.g. `<project>_chromadb-data`).

Set shell variables for convenience:
```bash
PROJECT="<project>"
CONTAINER="${PROJECT}-chromadb-1"
VOLUME="${PROJECT}_chromadb-data"
```

---

### Step 1: Pre-Migration Collection Inventory (Baseline)

Establish a baseline of all existing collections and their embedding counts through the live ChromaDB API:

```bash
# List all collections and record counts
curl -s http://127.0.0.1:8100/api/v2/tenants/default_tenant/databases/default_database/collections \
  | python3 -c '
import json, sys, urllib.request
data = json.load(sys.stdin)
print(f"Total collections found: {len(data)}")
for col in data:
    c_id = col["id"]
    name = col["name"]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:8100/api/v2/tenants/default_tenant/databases/default_database/collections/{c_id}/count") as resp:
            count = resp.read().decode().strip()
    except Exception as e:
        count = f"error: {e}"
    print(f"Collection: {name} (ID: {c_id}) -> Count: {count}")
' | tee pre_migration_inventory.txt
```

Save `pre_migration_inventory.txt` securely. This baseline is used in Step 7 and Step 9 to verify
zero data loss.

---

### Step 2: Quiesce Writers and Gracefully Stop ChromaDB

To ensure transactional and referential consistency across SQLite and HNSW segment files:

1. Stop application writers (Odysseus) to prevent any in-flight requests:
   ```bash
   docker compose stop odysseus
   ```
2. Gracefully stop the ChromaDB service. This permits SQLite to checkpoint the Write-Ahead Log (WAL)
   and flushes all in-memory vector index segments to disk:
   ```bash
   docker compose stop chromadb
   ```
3. Confirm that the container status is `Exited`:
   ```bash
   docker ps -a --filter "name=${CONTAINER}" --format "table {{.ID}}\t{{.Status}}\t{{.Names}}"
   ```
   *Do not proceed if the container is still running.*

---

### Step 3: Preserve the Original Container as an Immutable Image

Because Docker Compose discovers containers via service labels, running `docker compose up` later
could recreate and destroy the original container's writable layer.

Preserve the complete writable layer into an immutable Docker image:
```bash
BACKUP_IMAGE="${PROJECT}-chromadb-backup:pre-migration-$(date +%Y%m%d-%H%M%S)"
docker commit "${CONTAINER}" "${BACKUP_IMAGE}"
echo "Committed container writable layer to: ${BACKUP_IMAGE}"
```

Verify the image was created:
```bash
docker images "${BACKUP_IMAGE}"
```

This image is your ultimate insurance: even if all containers and volumes are deleted, your
data remains safely archived in Docker's image store.

---

### Step 4: Take a Cold File Backup

With the container stopped, copy the `/data` directory out to a dedicated host backup directory:

```bash
BACKUP_DIR="./chromadb-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "${BACKUP_DIR}"

docker cp "${CONTAINER}:/data/." "${BACKUP_DIR}/"
echo "Cold backup created at: ${BACKUP_DIR}"
```

---

### Step 5: Verify Backup Integrity

Never proceed to destructive cutover without verifying both SQLite and vector index structures.

1. **Verify SQLite Database File:**
   ```bash
   test -s "${BACKUP_DIR}/chroma.sqlite3" || { echo "ERROR: chroma.sqlite3 is missing or 0 bytes"; exit 1; }
   ```
2. **Execute SQLite PRAGMA Integrity Check:**
   ```bash
   INTEGRITY=$(sqlite3 "${BACKUP_DIR}/chroma.sqlite3" "PRAGMA integrity_check;")
   if [ "${INTEGRITY}" != "ok" ]; then
       echo "ERROR: SQLite integrity check failed: ${INTEGRITY}"
       exit 1
   fi
   echo "SQLite integrity check: OK"
   ```
3. **Verify Collections Table Structure:**
   ```bash
   sqlite3 "${BACKUP_DIR}/chroma.sqlite3" "SELECT id, name FROM collections;"
   ```
4. **Verify HNSW Index Binary Files:**
   Vector representations are not stored inside SQLite; they are kept in segment directories under `/data`:
   ```bash
   find "${BACKUP_DIR}" -type f -name "*.bin"
   ```
   Ensure `.bin` files (`data_level0.bin`, `header.bin`, etc.) exist for indexed collections.

---

### Step 6: Verify Destination Volume is Empty and Seed Safely

The named volume `${VOLUME}` must be empty before seeding. Under no circumstances should
migration files be merged into a populated volume.

1. **Positive Empty Check:**
   ```bash
   VOL_FILES=$(docker run --rm -v "${VOLUME}:/vol:ro" alpine ls -A /vol)
   if [ -n "${VOL_FILES}" ]; then
       echo "ERROR: Named volume ${VOLUME} already contains files: ${VOL_FILES}"
       echo "Aborting migration to prevent overwriting existing volume data."
       exit 1
   fi
   echo "Volume ${VOLUME} is empty and ready for seeding."
   ```
2. **Seed the Volume:**
   Use a disposable Alpine container to populate the volume with exact ownership and permissions:
   ```bash
   docker run --rm \
     -v "${VOLUME}:/target" \
     -v "$(realpath "${BACKUP_DIR}"):/source:ro" \
     alpine sh -c "cp -a /source/. /target/ && ls -la /target/"
   ```
3. **Verify Seeded Contents:**
   ```bash
   docker run --rm -v "${VOLUME}:/vol:ro" alpine ls -la /vol
   ```

---

### Step 7: Staging Validation in an Isolated Disposable Container

Before modifying your production Compose stack, validate that ChromaDB boots successfully from the
seeded volume and serves all collections.

1. **Start Disposable Staging Container:**
   Run a temporary ChromaDB instance bound to an isolated loopback port (e.g. `127.0.0.1:18199`):
   ```bash
   docker run -d --name "${PROJECT}-chromadb-staging" \
     -p 127.0.0.1:18199:8000 \
     -v "${VOLUME}:/data" \
     chromadb/chroma:latest

   # Wait for staging ChromaDB to boot
   sleep 4
   curl -s http://127.0.0.1:18199/api/v2/heartbeat || { echo "Staging container failed to respond"; exit 1; }
   ```
2. **Compare Collection Counts with Baseline:**
   ```bash
   curl -s http://127.0.0.1:18199/api/v2/tenants/default_tenant/databases/default_database/collections \
     | python3 -c '
   import json, sys, urllib.request
   data = json.load(sys.stdin)
   print(f"Staging collections verified: {len(data)}")
   for col in data:
       c_id = col["id"]
       name = col["name"]
       with urllib.request.urlopen(f"http://127.0.0.1:18199/api/v2/tenants/default_tenant/databases/default_database/collections/{c_id}/count") as resp:
           count = resp.read().decode().strip()
       print(f"  {name}: {count} records")
   '
   ```
   *Confirm that the output matches `pre_migration_inventory.txt` from Step 1.*

3. **Tear Down Staging Container:**
   ```bash
   docker rm -f "${PROJECT}-chromadb-staging"
   ```

---

### Step 8: Apply Updated Compose Files and Perform Production Cutover

Once the seeded volume is proven healthy:

1. Update your repository to the revision containing PR #6166:
   ```bash
   git pull
   ```
2. Verify the mount line in your Compose file:
   ```bash
   grep -E 'chromadb-data:/data' docker-compose.yml
   ```
3. Bring up the updated ChromaDB container:
   ```bash
   docker compose up -d chromadb
   ```
   Docker Compose will detect the updated mount configuration, replace the stopped container,
   and attach `${VOLUME}` directly to `/data`.
4. Bring up the application stack:
   ```bash
   docker compose up -d odysseus
   ```

---

### Step 9: Post-Migration Production Verification

1. **Confirm Mount Point:**
   ```bash
   docker inspect "${CONTAINER}" --format '{{range .Mounts}}{{.Name}} -> {{.Destination}}{{"\n"}}{{end}}'
   ```
   Expected output:
   ```
   <project>_chromadb-data -> /data
   ```
2. **Confirm Production Collection Counts:**
   ```bash
   curl -s http://127.0.0.1:8100/api/v2/tenants/default_tenant/databases/default_database/collections \
     | python3 -c '
   import json, sys, urllib.request
   data = json.load(sys.stdin)
   print(f"Production collections: {len(data)}")
   for col in data:
       c_id = col["id"]
       name = col["name"]
       with urllib.request.urlopen(f"http://127.0.0.1:8100/api/v2/tenants/default_tenant/databases/default_database/collections/{c_id}/count") as resp:
           count = resp.read().decode().strip()
       print(f"  {name}: {count} records")
   '
   ```
3. **Application Verification:**
   Log in to Odysseus, navigate to **Settings -> Memory**, and confirm that memory items,
   tool embeddings, and RAG document queries operate normally.

---

## 4. Rollback Procedures

### Scenario A: Failure During Staging (Before Compose Cutover)

If staging verification in Step 7 fails or reveals discrepancies:
1. Remove the staging container:
   ```bash
   docker rm -f "${PROJECT}-chromadb-staging"
   ```
2. Clear the failed destination volume:
   ```bash
   docker run --rm -v "${VOLUME}:/target" alpine sh -c "rm -rf /target/*"
   ```
3. Restart the original production container. Because the original container was only stopped (never recreated),
   its writable layer remains completely intact:
   ```bash
   docker compose start chromadb
   docker compose start odysseus
   ```
4. Verify production status on port 8100.

---

### Scenario B: Failure After Compose Cutover

If an issue occurs after `docker compose up -d`:

> [!CAUTION]
> **Do not `git checkout` the pre-#6166 Compose file!**
> In the pre-#6166 Compose file, `chromadb-data` was mounted to `/chroma/chroma`.
> ChromaDB 1.0+ persists to `/data`, so checking out the old Compose file will cause
> ChromaDB to ignore the restored volume and boot with an empty database.

To restore service:
1. **Option 1 — Restore from Verified Host Backup into the `/data` Volume:**
   If the container failed due to data corruption during startup:
   ```bash
   docker compose stop chromadb
   docker run --rm -v "${VOLUME}:/target" alpine sh -c "rm -rf /target/*"
   docker run --rm \
     -v "${VOLUME}:/target" \
     -v "$(realpath "${BACKUP_DIR}"):/source:ro" \
     alpine cp -a /source/. /target/
   docker compose up -d chromadb
   ```
2. **Option 2 — Fallback to Committed Backup Image:**
   If Compose cannot start ChromaDB with the volume:
   ```bash
   docker compose stop chromadb
   docker run -d --name "${PROJECT}-chromadb-fallback" \
     -p 127.0.0.1:8100:8000 \
     "${BACKUP_IMAGE}"
   ```
   This immediately restores the exact pre-migration container state.

---

## 5. Post-Migration Cleanup (Explicit Authorization)

Retain the host backup directory (`${BACKUP_DIR}`) and the backup Docker image (`${BACKUP_IMAGE}`)
for at least 7 days until routine backups have succeeded.

Once you are completely confident in system stability, manually clean up:
```bash
# Remove host backup
rm -rf ./chromadb-backup-*

# Remove backup Docker image
docker rmi "${BACKUP_IMAGE}"
```

---

## 6. Re-indexing (Disaster Recovery Only)

If an installation has already destroyed its pre-#6166 container writable layer (e.g. prior to following
this guide), the vector embeddings cannot be restored from disk. In this scenario:

* **RAG Documents:** Re-upload files under **Settings -> Memory -> RAG**. Odysseus automatically re-chunks
  and re-indexes documents upon upload.
* **Semantic Memories:** Text memory records reside authoritatively in `data/app.db` (SQLite). Vector representations
  are re-indexed automatically when Odysseus connects to a healthy ChromaDB instance.
* **Tool Index:** Rebuilt automatically by Odysseus upon first query.

Re-indexing is a disaster recovery fallback, not a substitute for preserving existing database state.
