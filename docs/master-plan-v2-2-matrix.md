# Master Plan v2.2: implementation matrix (Sonnet lane)

State as of 2026-11-15 evening. Production Odysseus `db79d11c` (rollback tag `pre-e3-activation` = `4f5b68d0`). Hermes component: release `c6b93a58d456` (eight patches) activated in the component config; the gateway process keeps serving `d17f92d164c3` until the user restarts it. Previous release for rollback: `d17f92d164c3`.

"Verified" = a test, a lab run or a live check recorded here or in `DEV_STATE.md`. Items carried over from `DEV_STATE.md` were not re-run today.

## 1. Completed and verified
| Item | Evidence |
|---|---|
| Unified navigation and authentication (§E8, P0-F) | DEV_STATE 2026-11-13; `docs/desktop-lab-results.md` (T1/T2/T4, logout and BFCache probes) |
| Projects registry, dashboard, native project import, resume plugin (§M, P0-B) | DEV_STATE 2026-11-13/14; 45 dashboard tests; isolated-lab flows |
| Workspace write grants: grant, revoke, audit (§E5 file-tool side, P0-C) | `docs/hermes-webapp-grants.md`, dashboard, composer control center |
| Composer context strip, context meter, compaction info, cost labels, AI Usage Center (§E1/E2) | usage-center patch deployed 2026-11-15 |
| G2 interrupted-tool recovery, managed-update protection, gateway lifecycle, release rollback | DEV_STATE 2026-11-14/15 |
| Wiki Steward integration | `docs/wiki-steward.md`; tools used in every task here |
| Activity vs answer hierarchy, grouped and collapsed activity, no empty Thinking rows (§E3) | upstream renderer; lab at 375/768/1440 px |
| Failed calls stay visible in folded runs, run duration (§E3 gaps) | eighth patch: unit tests (mutation-checked) and lab before/after |
| Command approval scopes stated honestly (§E5) | eighth patch: Once / This session / Always (global) / Deny, "Flagged by Hermes" reason, not-a-sandbox note |
| Component test fixture | 62 passing component tests after the fixture fix (it was stale; production code unchanged) |
| Seven-patch and older rollback recognition | component tests; staged release `patch_state` = applied for the eight-patch set |

## 2. Completed with documented limitations
| Item | Limitation |
|---|---|
| Eighth patch (`webapp-transcript-clarity`, SHA-256 `ba662bd1…8c8b`) | Verified in a component harness with fixture messages and by unit tests. Not yet seen in the live Desktop (needs your sign-in): checklist in `docs/e3-activation-runbook.md`. |
| §E5 approvals | No workspace scope for commands or MCP (not enforceable). cwd and resolved paths are not on the approval payload (deferred). Terminal and MCP run as the user; nothing here is a sandbox. |
| §E3 research progress | The existing live summary line only; the gateway has no research query, source or step fields (deferred). |
| `hermes-security` | Kept enabled. It runs scans and keeps its own scan state; it does **not** restrict file, shell or MCP access, registers no pre-tool hook, and is not a guard. |
| Local SearXNG (`127.0.0.1:8888`) | Working. DuckDuckGo is disabled because it CAPTCHAs (6 of 6 test queries); weights staged for Google CSE and Brave. Some engines are rate limited (Qwant denied, Mojeek and Startpage intermittently). |
| `odysseus-local-extract` (extract backend) | Reviewed and tested (12 tests, live pages, loopback and metadata addresses refused). Not installed or enabled until `scripts/apply-e3-config.py --apply` is run by you. Plain HTTP fetch: no JavaScript rendering. |
| Staged configuration (REA off by default, alyn sampling off, write approvals, bounded checkpoints, SearXNG + extract, no remote fallback) | Prepared as a dry-run-first script and reviewed; **not applied**: writes to the Hermes home and SearXNG settings are outside my write grants. |
| Checkpoints | Once on: a snapshot before the first file-tool write or detected-destructive terminal command each turn, bounded to 10 snapshots, 200 MB, 7 days. Ordinary terminal changes, MCP actions, databases, files over 10 MB and anything outside the working directory are not rolled back. Windows destructive spellings (`del`, `Remove-Item`, `move`, …) are not detected yet. |

## 3. Deliberately deferred
Research progress card, approval cwd/resolved-path extension (no ninth patch), Electron shell, OpenViking, JEV/OpenRouter, nine-agent fleet, LAN/phone intake, REA analysis engines, custom SearXNG MCP, Craft Anything, a Windows-native AppContainer/MXC sandbox, Docker/WSL2 isolation pilot (design only in `docs/hermes-windows-isolation.md`).

## 4. Owned by the LongCat workstream
Windows runtime and portability, launchers, service lifecycle, installer/update/rollback, Windows smoke tests, Cookbook/Windows port. Three LongCat cookbook commits (`a1aa1b7b`, `5cbbb38c`, `4f5b68d0`) landed in production while this work ran; they do not overlap and were kept.

## 5. Still genuinely missing
- Applying the staged configuration (your run of `apply-e3-config.py`, then the gateway and SearXNG restarts) and creating the `rea` profile in the Desktop.
- Live Desktop verification of the eighth patch.
- Researcher / Builder / Reviewer profiles with `output_schema` verdicts (§8).
- Read-only knowledge automation: nightly proposer, capped `status/BRIEF.md`, regenerated `environment/` pages (§G).
- Cost measurement harness and cache micro-benchmark (§H).
- Windows destructive-command spellings for checkpoints (earlier RFC finding S4: an agent-side patch to the pinned Hermes, a separate decision).
- Plan-mode profile, per-stack test skills (§9), Hermes backup automation and `hermes update --plan` in the update procedure (§12).
- `odysseus-backup.py create` failed twice with "Permission denied" on a locked file and left two incomplete snapshots (`snapshot-20261115-180933-500e1a`, `snapshot-20261115-181645-dcd479`, no manifest, about 4.6 GB each). A targeted backup (`deploy-e3-20261115-194046`) was used instead. The locked file is not identified.
