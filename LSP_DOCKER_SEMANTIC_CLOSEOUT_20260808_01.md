# LSP Docker Semantic Closeout

**Observed:** 2026-08-08T05:28:32+12:00<br>
**Scope:** Odysseus Docker LSP semantic closeout only.<br>
**State:** `LIVE_CONNECTION_VERIFIED / OFFLINE_SEMANTIC_CLOSEOUT_VERIFIED`.

## Result

The pinned Docker LSP runtime is rebuilt, recreated, enabled, and healthy on the loopback Odysseus service. The finalized offline acceptance probe passed with Docker networking disabled and proves Python and TypeScript diagnostics plus definition, references, and rename-preview behavior.

## Root cause and repair

The original semantic probe used a coordinate search that could select `use_defined_symbol` instead of the intended `defined_symbol` call. A deterministic one-based Python call position is now pinned at `python_fixture.py:6:12`.

The corrected coordinate exposed a second root cause: the pinned `@treedy/pyright-mcp@1.1.8` rename-preview implementation prefers `rg --line-number --column`; without `ripgrep`, its grep fallback emits a three-field result that the rename parser does not convert into edits. The image now includes `ripgrep`, preserving the pinned npm packages and allowing the provider's primary, column-aware rename-preview path to return two workspace edits.

## Final evidence

- `docker run --rm --network none ... /opt/odysseus-lsp/verify_lsp_runtime.py`: exit `0`.
  - MCP initialization: `101` tools.
  - Python diagnostic: intentional `undefined_symbol` error detected.
  - Python definition: fixture call `6:12` resolves to definition `1:5`.
  - Python references: exactly `2` references.
  - Python rename preview: `defined_symbol` → `renamed_symbol`, `2` occurrences, preview only.
  - TypeScript diagnostic: intentional string-to-number type error detected.
  - TypeScript definition and rename preview: `2` planned locations, preview only.
- Focused canonical tests: `20 passed, 3 warnings` (`0.78s`). Warnings are existing SQLAlchemy/Starlette deprecations and the current host pytest `asyncio_mode` configuration warning.
- `docker compose config --quiet`: pass.
- Live health: `GET http://127.0.0.1:7000/api/health` returned `{"status":"healthy",...}`.
- Live process: `node /opt/odysseus-lsp/node_modules/@treedy/lsp-mcp/dist/index.js` present.
- Read-only configuration query confirms MCP record `396e33c2-e4a5-4fae-b60d-b1c1ca5ed0f7` is named `LSP Code Intelligence (Pinned)`, uses `node` with the pinned LSP entrypoint, and has `is_enabled=1`.
- Exact in-container package versions: `@treedy/lsp-mcp=0.2.8`, `@treedy/pyright-mcp=1.1.8`, `@treedy/typescript-lsp-mcp=0.1.7`, `pyright=1.1.411`.
- Fixture mount remains read-only; source fixture hashes are unchanged.

## Final live identity

| Field | Value |
|---|---|
| Container | `2b22ea8da7b8964f9851a1087278a4e9490d073f4ac026cde8c7e625ea6e6ad9` |
| Image | `sha256:1deb93bf43b88de04237578b66343e986848b88f7219ef8320672ee7915cee8a` |
| Service | `ody-odysseus-1` on `127.0.0.1:7000` |
| LSP probe source | `docker/lsp-runtime/verify_lsp_runtime.py` SHA-256 `11e776d33322b4f13ac0c458649139bbc2b3ed26bee2ec63077713b1fb06f77e` |
| Dockerfile | SHA-256 `2257d7b51f6306e67299fc3c275df3a067bbadb90542530dca81afe148627a67` |
| Python fixture | SHA-256 `1c7e843c74ca8640effac58523a5161693b76959ce8f8ba1edb396a93a3e37cc` |
| TypeScript fixture | SHA-256 `30114e022627e8b16e09148fe6382b5721500bb07cb68e4946eb0ad56bf074b9` |

## Boundaries retained

- No dashboard, bridge, provider settings, credentials, or authentication settings were read, changed, or bypassed.
- No semantic rename was applied: both rename checks returned previews only, and the fixture mount was verified read-only.
- No network was available to the final semantic acceptance container.
- The authenticated manager API semantic call remains intentionally untested because its authentication boundary remains in force.
- A transient SQLite-lock message was observed during the first post-recreate warm-up. The service was subsequently recreated once after the root-cause repair and returned healthy; no unrelated database/session behavior was changed under this LSP-only authority.

## Durable-memory closeout

`verify/VERIFY_LOG.md` cannot be updated through DCA because the active allowlist permits only `memory/` and `registry/`. `memory/LEARNING.md` already has two pending full-file proposals in a linear chain. A further learning entry must be generated as their exact successor, preserving their decoded full content and successor hash; it was not staged in this closeout rather than risk a stale full-file overwrite. A separate, non-conflicting architecture-note proposal is staged through the DCA inbox; it is queued only and is not represented as applied until a DCA receipt exists.
