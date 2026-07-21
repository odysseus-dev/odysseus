# Persistent Memory (Atlas overlay)

Session memory for agent ops. See vendor handbook for initialization protocol:

- [`../../atlas_framework/atlas_framework/CLAUDE.md`](../../atlas_framework/atlas_framework/CLAUDE.md) — Memory Protocol section

## Layout

- `MEMORY.md` — curated long-term facts (create on first use)
- `logs/` — daily session logs (`YYYY-MM-DD.md`)

Runtime Odysseus memory also lives in `data/` and `services/memory/` — this folder is for **agent overlay** continuity.
