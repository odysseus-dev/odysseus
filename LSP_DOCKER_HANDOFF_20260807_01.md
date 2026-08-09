# LSP Docker Activation — Handoff

**Created:** 2026-08-07T16:57:54Z<br>
**Scope:** Dockerized Odysseus LSP MCP only.<br>
**State:** `PARTIALLY_VERIFIED / LIVE_CONNECTION_VERIFIED / SEMANTIC_CLOSEOUT_PENDING`.

## Current live state

| Item | Observed state |
|---|---|
| Odysseus service | Running Docker container `86b02519adf8a487ffe6be7f1d98946ec9d8df7d2979cd199ff686e05b11fb47` |
| Image | `sha256:c2fb085dad45b173c093f8067485b1ff95639ba1fc6ac4c44380c4ea718f0d65` |
| Loopback service | `GET http://127.0.0.1:7000/api/health` returned `{"status":"healthy",...}` at 2026-08-07T16:57:55Z |
| Dashboard | `127.0.0.1:7001` remains loopback-bound; prior check returned its expected `302` authentication redirect |
| LSP record | ID `396e33c2-e4a5-4fae-b60d-b1c1ca5ed0f7`; enabled; Linux container entrypoint `/opt/odysseus-lsp/node_modules/@treedy/lsp-mcp/dist/index.js` |
| Live MCP connection | `data/logs/app.log` recorded `MCP server connected: LSP Code Intelligence (Pinned) ... - 101 tools via stdio` at 2026-08-07T16:54:00Z |
| Live process | `node /opt/odysseus-lsp/node_modules/@treedy/lsp-mcp/dist/index.js` runs as the `odysseus` user under the current container |
| Fixture mount | `tests/fixtures/lsp_docker` is mounted at `/workspace/lsp_fixture` read-only |

## Implemented changes

1. **Reproducible image runtime**
   - Added `docker/lsp-runtime/package.json` and lockfile.
   - Exact runtime roots: `@treedy/lsp-mcp@0.2.8`, `pyright@1.1.411`, `@treedy/pyright-mcp@1.1.8`, and `@treedy/typescript-lsp-mcp@0.1.7`.
   - Docker installs the exact lockfile into `/opt/odysseus-lsp` with `npm ci --ignore-scripts --no-audit --no-fund`.

2. **Runtime network lock**
   - Added `docker/lsp-runtime/npx`, which intercepts the upstream LSP package's `@latest` backend requests and dispatches only the pinned Python or TypeScript backend.
   - It delegates `npx --version` only and rejects undeclared package requests with exit `64`.
   - The live container proved the four installed runtime versions and `SHIM_UNDECLARED_PACKAGE=REJECTED`.

3. **Safe MCP configuration update**
   - Added the supported `odysseus-mcp update` command for an existing server's explicit `args`, `env`, and `is_enabled` fields.
   - Input JSON is validated; unknown IDs fail closed; output redacts all environment values.
   - The existing LSP record was updated using this CLI while Odysseus was stopped, then Odysseus was recreated.

4. **Read-only semantic fixture**
   - Added Python and TypeScript fixtures plus `package.json` and `tsconfig.json`.
   - Added a single Compose mount: `./tests/fixtures/lsp_docker:/workspace/lsp_fixture:ro,z`.

5. **Build repair**
   - The first image rebuild exposed a stale Chrome package pin (`151.0.7922.71-1`) that was no longer present in Google's repository.
   - Verified repository metadata supplied `151.0.7922.108-1`; Dockerfile and its focused regression test were updated to that exact version.
   - Second build completed successfully.

## Evidence achieved

### Focused source tests

```text
23 passed, 1 warning in 1.02s
```

This included the MCP CLI update tests, Docker hardening tests, MCP dependency pin test, and built-in MCP cache tests.

### Offline container acceptance

A temporary container used the newly built image, mounted only the fixture, and ran with `--network none`.

- Main MCP initialized with `INIT_TOOL_COUNT=101`.
- Python backend started from pinned `pyright-mcp@1.1.8`.
- TypeScript backend started from pinned `typescript-lsp-mcp@0.1.7`.
- Python diagnostics reported the intentional `undefined_symbol` error.
- TypeScript diagnostics reported `Type 'string' is not assignable to type 'number'.`
- No runtime registry resolution was possible because the temporary container had no network.

## Preserved rollback point

| Artifact | SHA-256 | Verification |
|---|---|---|
| `G:\AIW\05_BAK\ODY_LSP_DOCKER_PRE_ACTIVATION_20260807_165042_02\app.db` | `303e42a612dbf04925289974e9c1ae46fcda6063d382f0a73105e56779200567` | SQLite `integrity_check=ok`; LSP record was disabled and retained its original Windows host path |

The earlier `_01` backup directory is empty because its first attempted native-Python destination path used an MSYS `/g/...` path and failed before database output was created. It was preserved; nothing was deleted.

## Remaining closeout work — not executed because the user requested this handoff

1. Rebuild the image once more. The source-only addition that prints semantic tool schemas in `docker/lsp-runtime/verify_lsp_runtime.py` was made after the currently running image was built. It does not change runtime behavior, but source/image identity must be restored before final closeout.
2. Run the finalized offline probe and add the following semantic checks using the now-observed schemas:
   - `definition(file, line, column)` for `defined_symbol`;
   - `references(file, line, column, page_size)` for `defined_symbol`;
   - `rename(file, line, column, newName)` and assert a workspace-edit/dry-run only;
   - re-hash both fixture source files afterward.
3. Perform the same semantic calls through the authenticated live Odysseus MCP manager if a currently authenticated Odysseus UI/browser session is available. Direct HTTP access to `/api/mcp/servers` correctly returns `401`; no credentials or authentication settings were read, changed, or bypassed.
4. Run complete post-change focused tests and `docker compose config --quiet`.
5. Create the final evidence receipt, DCA learning proposal/entry, and durable Obsidian architecture note.

## Exact current source hashes

| Path | SHA-256 |
|---|---|
| `Dockerfile` | `5d22b5798202111d310b5edabf403969c8c371fa356703db25b6eb1d9bb22bba` |
| `docker-compose.yml` | `e8a452d44146d2bda094438165e03886dc1aa40609a5ca02842913bfb53b8c0d` |
| `scripts/odysseus-mcp` | `71f213b1acf11f6e8c9d6780c3897faf8ebf7d7f0b1d06417dd36373ad584f33` |
| `docker/lsp-runtime/package.json` | `a25c6e7061ac94444fe27f410845619c4898619e9b449602a8311776fc72d1d3` |
| `docker/lsp-runtime/package-lock.json` | `4b0a825759e39cb19cdaa421b8cd2e6dd8bc5f28d0dcab7ac8af2d7fc84fbe53` |
| `docker/lsp-runtime/npx` | `12bb24b17b000a13cbfc779e1528009c8ac859ccc3e8a5f0e4a849fd89012157` |
| `docker/lsp-runtime/verify_lsp_runtime.py` | `4a9269a4629ceb284dec606ebf074bf5c3581bf7f7a77e6355f055f15b374b02` |
| `tests/cli/test_mcp_cli_update.py` | `ef0dfbd6104545e1f3d6df1830a49f694b2a2f3414c0afa863b20e566236ad81` |
| `tests/fixtures/lsp_docker/python_fixture.py` | `1c7e843c74ca8640effac58523a5161693b76959ce8f8ba1edb396a93a3e37cc` |
| `tests/fixtures/lsp_docker/ts_fixture.ts` | `30114e022627e8b16e09148fe6382b5721500bb07cb68e4946eb0ad56bf074b9` |
| `data/app.db` at handoff observation | `70cab8c8dbec0ba2c888f23424cd783a6d2ea085d3c25961236c3da1e966bcf6` |

## Resume authority

```text
GO — resume only the LSP Docker closeout described in LSP_DOCKER_HANDOFF_20260807_01.md. Rebuild only odysseus to bind the current probe source, run its offline semantic definition/references/rename-dry-run checks with no network, re-hash fixtures, then use an already-authenticated Odysseus session if available to exercise the same tools through the live manager. Do not bypass auth, alter providers/ports/credentials, touch Dashboard/Bridge, or recreate any Compose service other than odysseus. Complete only the final receipts, DCA learning path, and Obsidian architecture note after real verification.
```
