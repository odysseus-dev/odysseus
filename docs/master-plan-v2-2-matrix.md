# Master Plan v2.2: implementation matrix (Sonnet lane)

State as of 2026-11-15 close. Odysseus production: see the commit that contains this file (earlier `db79d11c`, rollback tag `pre-e3-activation` = `4f5b68d0`). Hermes release `c6b93a58d456` (eight patches) is live and the gateway is healthy after the user's restart; rollback release `d17f92d164c3`. Public GitHub `dev`: `f9933beb` (fast-forward from `51bf85da`, sanitized, LongCat cookbook commits preserved). Sonnet modernization workstream closed apart from the user-only items in section 6.

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
| Eighth patch (`webapp-transcript-clarity`, SHA-256 `ba662bd1…8c8b`) | Deployed, gateway healthy. Verified in a component harness and by unit tests. Live visual acceptance awaits the user's check (`docs/e3-activation-runbook.md` §3). |
| §E5 approvals | No workspace scope for commands or MCP (not enforceable). cwd and resolved paths are not on the approval payload (deferred). Terminal and MCP run as the user; nothing here is a sandbox. |
| §E3 research progress | The existing live summary line only; the gateway has no research query, source or step fields (deferred). |
| `hermes-security` | Kept enabled. It runs scans and keeps its own scan state; it does **not** restrict file, shell or MCP access, registers no pre-tool hook, and is not a guard. |
| Local SearXNG (`127.0.0.1:8888`) | Verified working: search returned results through Hermes. DuckDuckGo stays disabled (CAPTCHA on 6 of 6 test queries); Google CSE and Brave work. Some engines are rate limited (Qwant denied, Mojeek and Startpage intermittently). |
| `odysseus-local-extract` (extract backend) | Installed and enabled. Reviewed, 12 tests, loopback and metadata addresses refused. A fresh uncached page was extracted through it and the runtime log shows `Web extract via odysseus-local-extract` with no external fallback. Plain HTTP fetch: no JavaScript rendering. |
| Applied configuration | Read back from the live config: REA removed from the default profile (folder kept; dedicated profile optional), `memory.write_approval` and `skills.write_approval` true, checkpoints on (10 snapshots, 200 MB, 7 days), alyn `sampling.enabled: false`, `web.search_backend: searxng`, `web.extract_backend: odysseus-local-extract`, keyless fallback and rescue off. |
| SearXNG launcher fixes (`searxng-launcher-fix` `a2320ad`) | Tested in isolation (the old `.cmd` fails with "Illegal characters in path", the fixed one runs; exited-PID race handled). **Awaiting manual installation** with `scripts/install-searxng-launcher-fix.ps1`. Engines and settings untouched. |
| Checkpoints | Once on: a snapshot before the first file-tool write or detected-destructive terminal command each turn, bounded to 10 snapshots, 200 MB, 7 days. Ordinary terminal changes, MCP actions, databases, files over 10 MB and anything outside the working directory are not rolled back. Windows destructive spellings (`del`, `Remove-Item`, `move`, …) are not detected yet (separate agent-side decision). |

## 3. Deliberately deferred
Research progress card, approval cwd/resolved-path extension (no ninth patch), Electron shell, OpenViking, JEV/OpenRouter, nine-agent fleet, LAN/phone intake, REA analysis engines, custom SearXNG MCP, Craft Anything, a Windows-native AppContainer/MXC sandbox, Docker/WSL2 isolation pilot (design only in `docs/hermes-windows-isolation.md`).

## 4. Owned by the LongCat workstream
Windows runtime and portability, launchers, service lifecycle, installer/update/rollback, Windows smoke tests, Cookbook/Windows port. Three LongCat cookbook commits (`a1aa1b7b`, `5cbbb38c`, `4f5b68d0`) landed in production while this work ran; they do not overlap and were kept.

## 5. Still genuinely missing
- Researcher / Builder / Reviewer profiles with `output_schema` verdicts (§8).
- Read-only knowledge automation: nightly proposer, capped `status/BRIEF.md`, regenerated `environment/` pages (§G).
- Cost measurement harness and cache micro-benchmark (§H).
- Windows destructive-command spellings for checkpoints (earlier RFC finding S4: an agent-side patch to the pinned Hermes, a separate decision).
- Plan-mode profile, per-stack test skills (§9), Hermes backup automation and `hermes update --plan` in the update procedure (§12).
- `odysseus-backup.py create` failed twice with "Permission denied" on a locked file and left two incomplete snapshots (`snapshot-20261115-180933-500e1a`, `snapshot-20261115-181645-dcd479`, no manifest, about 4.6 GB each). A targeted backup (`deploy-e3-20261115-194046`) was used instead. The locked file is not identified. **Unresolved maintenance issue.** Both snapshots are left untouched pending separate authorization; the full backup suite has not been rerun.

## 6. User-only items remaining
- Live Desktop visual check of the eighth patch (checklist: `docs/e3-activation-runbook.md` §3).
- Run `scripts/install-searxng-launcher-fix.ps1` (dry run first, then `-Apply`).
- Optional: create the dedicated `rea` profile; decide the two incomplete backup snapshots and the locked-file failure.

## 7. Intentionally deferred
Ninth patch (approval cwd/resolved paths, research progress card, Windows destructive-command spellings) and any OS-level sandbox.
