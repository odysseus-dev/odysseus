# PDV integration boundary

This checkout is an isolated, modified copy of Odysseus for native-Windows
evaluation. It must remain outside PDV repositories. The pinned upstream state
is recorded in `PDV_UPSTREAM_SNAPSHOT.json`.

## License boundary

Odysseus is licensed `AGPL-3.0-or-later`. Preserve `LICENSE`,
`ACKNOWLEDGMENTS.md`, copyright notices, Git history, and the canonical upstream
URL. If this modified service is conveyed or made available for remote network
interaction, provide the complete corresponding source for the exact running
version, including these integration changes and build/run scripts. An API
metadata response or upstream URL alone is not a substitute for corresponding
source availability.

The source builder inventories every tracked file plus an explicit integration
file list, records per-file and license hashes, fails on excluded tracked files
or high-confidence credential/private-key content, and excludes exact
runtime/secret paths. Arbitrary untracked source-like notes are not admitted by
extension; an intentional additional file requires `--include` and receives the
same path and secret screening.

Do not copy Odysseus source into PDV. Connect over the authenticated loopback
HTTP boundary. If PDV needs a concept implemented natively, perform a separate
license review and clean-room design from a permissively licensed original
source where available.

## Runtime boundary

- Native Windows only for this baseline; Docker and GPU paths are excluded.
- Bind only `127.0.0.1`; selected port is `7000`.
- Ports `11435` and `11436` are reserved and rejected by the lifecycle wrapper.
- Keep `AUTH_ENABLED=true` and `LOCALHOST_BYPASS=false`.
- Existing Odysseus admin authorization remains authoritative. No adapter
  bypass, shared-secret header, or anonymous health route is added.
- `ODYSSEUS_PDV_ADAPTER_KEY_FILE` references a pre-provisioned, ACL-restricted,
  non-empty key file. Neither its path nor contents are returned by PDV routes.
- Runtime data is isolated under ignored `data/pdv-integration-v1/`.
- No paid provider, model-provider credential, GPU fallback, or external model
  endpoint is required by the baseline.

## Adapter contract

Use an authenticated admin session or `Authorization: Bearer <ody_...>` token
owned by an Odysseus admin.

- `GET /api/pdv/health` wraps native readiness and fails closed with HTTP 503
  unless database/data readiness, loopback binding, adapter-key reference,
  source metadata, a live governed Execution OS `health.status` round-trip, and
  a connected seven-tool `pdv_control` MCP child are all valid.
- `GET /api/pdv/source` returns canonical repository, pinned upstream commit,
  branch, and license metadata. It never returns local filesystem paths.

Run a non-mutating preflight:

```powershell
pwsh -NoProfile -File .\scripts\pdv_verify_native_windows_baseline.ps1 `
  -RepositoryRoot . `
  -ExpectedUpstreamCommit 25c9e735ef5ce605f47f8f666ac6689056d2c10c `
  -Json

pwsh -NoProfile -File .\scripts\pdv_windows_lifecycle.ps1 `
  -Action Check `
  -RepositoryRoot . `
  -AdapterKeyFile $env:ODYSSEUS_PDV_ADAPTER_KEY_FILE `
  -ExecutionOsUrl http://127.0.0.1:4310 `
  -Port 7000 `
  -Json
```

`Start` launches a hidden Uvicorn process and records its PID. `Stop` terminates
only a PID whose executable and command line match this checkout's Uvicorn
instance.
