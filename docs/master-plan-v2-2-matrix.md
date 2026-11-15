# Master Plan v2.2: implementation matrix (Sonnet lane)

State as of 2026-11-15. "Verified" means checked by a test or a live run recorded in `DEV_STATE.md` or this branch; items taken from `DEV_STATE.md` were not re-run today. Production release `d17f92d164c3`, Odysseus `1fc9575b`.

## Complete and verified
| Item | Evidence |
|---|---|
| Unified navigation and authentication (§E8, P0-F) | DEV_STATE 2026-11-13; lab T1/T2/T4 and logout/BFCache probes in `docs/desktop-lab-results.md` |
| Projects registry, dashboard, native project import, resume plugin (§M core, P0-B) | DEV_STATE 2026-11-13/14; 45 dashboard tests; isolated-lab flows |
| Workspace write grants: grant, revoke, audit, effective-access wording (§E5 file-tool side, P0-C) | `docs/hermes-webapp-grants.md`, dashboard phase 2, composer control center |
| Approval scopes and honesty about them (§E5) | this branch: once/session/always(global)/deny, no workspace scope for commands |
| Composer context strip, context meter, compaction info, cost labels, AI Usage Center (§E1/E2, P0-D second half) | Usage-center patch deployed 2026-11-15 |
| G2 interrupted-tool recovery, managed-update protection, gateway lifecycle and release rollback | DEV_STATE 2026-11-14/15 |
| Wiki Steward integration | `docs/wiki-steward.md`, tools in this session |
| Activity-vs-answer hierarchy, collapsing, no empty Thinking rows, errors visible, run duration (§E3, P0-D first half) | upstream renderer plus the eighth patch; lab at 375/768/1440 |

## Implemented, not yet visually verified in the full app
- Eighth patch in the real Desktop shell with a live gateway (verified in a component harness only). Needs activation and your browser check.
- Approval card wording in the real approval flow (unit and harness only).

## Partially complete
| Item | What is missing |
|---|---|
| §E5 approval dialog | cwd and resolved paths (not on the wire); no workspace scope (not enforceable) |
| §E3 long-research card | no event fields; summary line only |
| P0-E security configuration | approvals `smart`, terminal backend `local`, ask-policy off by decision. Not set: `memory.write_approval`, `skills.write_approval`, deny-rule review, MCP `sampling.enabled: false` for `alyn`. `rea` and `hermes-security` enabled in the default profile. All need your decision. |
| Workspace selector, Ctrl+K palette, pinned sessions (§E4/E6) | not audited this round; upstream Desktop provides some of it |
| Plan mode | no read-only "Plan" profile |
| Test and review workflow (§9) | hermes-security plugin enabled; no per-stack test skills recorded |
| Checkpoints / undo | feature exists upstream, unset here |

## Missing
- SearXNG as the Hermes search backend and a separate extraction backend (`web:` empty; `web_extract` currently fails). Separate later task per v2.2 sequencing.
- Researcher/Builder/Reviewer profiles with `output_schema` reviewer verdicts (§8).
- Docker/WSL2 isolation pilot for Builder/automation (§D3 track 2). `docs/hermes-windows-isolation.md` is design only.
- Read-only knowledge automation: nightly proposer, `status/BRIEF.md` cap, regenerated `environment/` pages (§G).
- Cost measurement harness and cache micro-benchmark (§H).
- Hermes backup automation and `hermes update --plan` in the update procedure (§12). Run only with your approval, and never `hermes` CLI in-session per standing rule.

## Deliberately deferred
Electron shell, OpenViking, JEV/OpenRouter, nine-agent fleet, LAN/phone intake, REA analysis profile engines, custom SearXNG MCP, Craft Anything, Windows-native AppContainer/MXC sandbox.

## Owned by the LongCat workstream
Windows runtime and portability, launchers and service lifecycle, installer/update/rollback, Windows smoke tests, Cookbook/Windows port. Not reopened.

## Real blockers before calling the Sonnet portion done
1. Activate the staged release and look at it in the real Desktop (your approval; gateway restart is yours).
2. Decide the P0-E configuration items above.
3. SearXNG plus extraction backend task.
4. Optional backend change for cwd/resolved paths on approval requests (separate patch, your call).
