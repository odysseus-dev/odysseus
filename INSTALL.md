# Installing the Memory Platform into Odysseus

This is a **working, additive integration** — nothing in Odysseus is replaced.
The platform (`memory_platform/`) attaches hybrid recall, the immutable core
(drift-protected blocks), the integrity chain, socratic growth, sleep/
consolidation, and the brain view to Odysseus's existing `MemoryManager` +
`MemoryVectorStore`.

## Files in this package

| File | Destination | What it does |
|---|---|---|
| `memory_platform/` (40 modules) | `<odysseus>/memory_platform/` | The full memory platform |
| `scripts/odysseus_adapter.py` | `src/odysseus_adapter.py` | The integration layer (boots store, drift ledger, integrity, recall) |
| `routes_memory_graph_routes.py` | `routes/memory/graph_routes.py` | `/api/memory-brain/overview` + `/sleep` + `/pressure` |
| `brain.js` | `static/js/brain.js` | The Brain view renderer (interactive node-link graph) |
| `patch_odysseus_gui.py` | (run once) | Adds the Brain tab + sleep ledger to the memory modal |
| `measure_fair.py` | (dev tool) | The recall benchmark (11 vs 9, same data) |

## Dependencies

The platform's hybrid store uses **sqlite-vec** (dense vectors) + SQLite FTS5
(BM25), fused by RRF. sqlite-vec is required for full functionality and is a
declared dependency:

```bash
pip install "sqlite-vec>=0.1.9"
```

(The canary suite treats it as essential; the store also degrades to BM25-only
recall if dense vectors are unavailable, so the app still boots either way.)

## Steps

### 1. Copy the engine + platform

```bash
cp -r memory_platform <odysseus>/
cp scripts/odysseus_adapter.py <odysseus>/src/
cp routes_memory_graph_routes.py <odysseus>/routes/memory/graph_routes.py
cp brain.js <odysseus>/static/js/
```

### 1b. Pin FastAPI (important — verified requirement)

The default `fastapi` (0.141.x / starlette 1.6.x) has a broken `include_router`
for prefixed routers — sub-routes are silently dropped. Verified working on
FastAPI 0.115.14:

```bash
pip install "fastapi>=0.115,<0.116" "starlette<0.42"
```

### 2. Register the adapter + brain route in `app.py`

Add after the memory router is included (around line 672):

```python
from src.odysseus_adapter import install_memory_platform
_platform = install_memory_platform(memory_manager, memory_vector)
if _platform.get("hybrid_recall") is not None:
    memory_vector.search = _platform["hybrid_recall"].search

from routes.memory.graph_routes import setup_graph_routes
app.include_router(setup_graph_routes(
    memory_manager, memory_vector,
    sleep_engine=(_platform or {}).get("sleep"),
))
```

On boot the adapter:
- opens the platform's own hybrid store (sqlite-vec + FTS5),
- snapshots the five core blocks into the drift ledger (immutability anchor),
- wires the worthiness gate + claim audit,
- attaches hybrid recall, socratic growth, and sleep/consolidation.

### 3. Add the Brain tab to the UI

```bash
python3 patch_odysseus_gui.py <odysseus>
```

(Idempotent — safe to re-run; backs up `index.html` and `memory.js`.)

### 4. Restart and verify

```bash
docker compose up -d --build   # or uvicorn app:app
```

- Boot log confirms the full chain: `Odysseus memory platform attached:
  {platform_store, drift_ledger, drift_ok, worthiness, claim_audit,
  hybrid_recall, associations, socratic, sleep, auto_sleep}`.
- `GET /api/memory-brain/overview` → persona, identity, association nodes,
  neurons, sleep receipts + pressure. **Verified 200** with a live store.
- `POST /api/memory-brain/sleep` merges near-duplicates, prunes stale, promotes
  used, and appends a receipt. **Verified** against a live store.
- The Brain tab shows the interactive graph + sleep ledger + pressure gauge.
- **Self-proving canaries** (portable, env-isolated):

  ```bash
  cd memory_platform && ./canary-manager.sh   # 5/5 checks, asserts completeness
  ```
