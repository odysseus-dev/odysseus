# LSP Docker Activation — Current Handoff

**Created:** 2026-08-07<br>
**Chat scope:** LSP Docker activation for Odysseus only.<br>
**Status:** `LIVE_CONNECTION_VERIFIED / SEMANTIC_CLOSEOUT_PENDING`.

## What is live now

- Odysseus runs as Docker only on `127.0.0.1:7000`.
- Current container: `86b02519adf8a487ffe6be7f1d98946ec9d8df7d2979cd199ff686e05b11fb47`.
- Current image: `sha256:c2fb085dad45b173c093f8067485b1ff95639ba1fc6ac4c44380c4ea718f0d65`.
- `GET /api/health` returned `{"status":"healthy",...}`.
- Dashboard remains on `127.0.0.1:7001`; its prior response was the expected `302` authentication redirect.
- The existing LSP MCP record `396e33c2-e4a5-4fae-b60d-b1c1ca5ed0f7` is enabled and now uses:
  - command: `node`
  - argument: `/opt/odysseus-lsp/node_modules/@treedy/lsp-mcp/dist/index.js`
  - Linux-only pinned/offline environment values, redacted by the supported CLI.
- Live application log evidence confirms: `LSP Code Intelligence (Pinned) ... 101 tools via stdio`.
- The live LSP Node process is present under the Odysseus container.

## Completed implementation

1. Added a Docker-internal, lockfile-installed LSP runtime under `/opt/odysseus-lsp`.
2. Pinned exact primary packages:
   - `@treedy/lsp-mcp@0.2.8`
   - `pyright@1.1.411`
   - `@treedy/pyright-mcp@1.1.8`
   - `@treedy/typescript-lsp-mcp@0.1.7`
3. Added an offline-only `npx` dispatcher that maps the upstream `@latest` backend requests to the approved pinned local packages and rejects undeclared requests.
4. Added a read-only Python/TypeScript fixture mount at `/workspace/lsp_fixture`.
5. Added and tested `odysseus-mcp update` to safely update explicit `args`, `env`, and `is_enabled` fields of the existing record; no raw SQLite edit was used.
6. Updated the stale Chrome build pin to the currently available exact package version `151.0.7922.108-1`; the Docker hardening test was updated with it.

## Verified evidence

- Focused source suite: `23 passed, 1 warning` before the final probe-only edits.
- Docker Compose configuration validated.
- Rebuilt Odysseus image successfully.
- Isolated container acceptance with `--network none` passed for:
  - MCP initialization: `101` tools.
  - Python Pyright backend startup: `1.1.8`.
  - TypeScript backend startup: `0.1.7`.
  - Python intentional undefined-symbol diagnostic.
  - TypeScript intentional string-to-number diagnostic.
  - Unknown package dispatcher rejection.
- Fixture source hashes remained unchanged after those offline diagnostics.
- Live in-container checks confirmed all four exact package versions and read-only fixture access.

## Rollback

| Path | SHA-256 | State |
|---|---|---|
| `G:\AIW\05_BAK\ODY_LSP_DOCKER_PRE_ACTIVATION_20260807_165042_02\app.db` | `303e42a612dbf04925289974e9c1ae46fcda6063d382f0a73105e56779200567` | SQLite `integrity_check=ok`; original LSP record disabled with its Windows host path |

The earlier `_01` backup directory is empty: its destination-path attempt failed before a database file was created. It was preserved; nothing was deleted.

## Exact outstanding work

Do **not** claim full semantic completion yet.

1. The current source probe was extended to test `definition`, `references`, and `rename` after the live image build.
2. The first new definition check used a coordinate that returned `No definition found at this position`; this is a test-fixture coordinate issue, not a runtime failure.
3. The next bounded action is to correct the probe to find the symbol coordinate deterministically, then run:
   - Python definition;
   - Python references;
   - Python rename as workspace-edit/dry-run only;
   - post-test fixture hashes.
4. Rebuild/recreate only `odysseus` once the finalized probe passes so its image exactly matches source.
5. An authenticated live manager semantic call remains unverified because `/api/mcp/servers` correctly returns `401`; no credentials or authentication settings were read, changed, or bypassed.
6. Then run final focused tests, create a final LSP receipt, route the learning entry through the approved DCA path, and add the stable Obsidian architecture note.

## Resume authority

```text
GO — resume only LSP Docker semantic closeout. Preserve the live loopback Odysseus service, dashboard, provider settings, credentials, ports, and unrelated containers. Fix the bounded probe coordinate, prove definition/references/rename-dry-run with network disabled, re-hash the read-only fixture, rebuild/recreate only odysseus to bind the final probe source, and finish receipts/DCA learning/vault closeout. Do not bypass Odysseus authentication or touch Dashboard/Bridge.
```
