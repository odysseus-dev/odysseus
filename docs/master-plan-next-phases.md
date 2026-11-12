# Master plan, next phases (planning only; nothing here is implemented or deployed)

Date 2026-11-12. Production: Odysseus `:7000` at `ed0bea07`, `ODYSSEUS_HERMES_BACKEND=webapp`, managed gateway `:9130`, old WebUI `:8787` kept as rollback. Nothing is pushed. Security status of the hand-over: `docs/evidence/navigation/security-verification.md`.

## 1. What already exists in the pinned Webapp (agent `3b421578`, `apps/desktop`)

Checked in the managed release source (`%LOCALAPPDATA%\Odysseus\hermes\managed\webapp\releases\427ff712100d\src`). "Seen live" means I used it in a real browser earlier this session.

| Wanted | In the pinned Webapp | Evidence | Gap |
|---|---|---|---|
| Project registry | Yes, per-profile `projects.db` behind `/api/profiles/projects/*`; fields: name, slug, folders, primary path, color, icon, board, archived | `store/projects.ts`, `types/hermes.ts` `ProjectInfo` | none for the registry itself |
| Create / register / select | Yes: create, rename, add-folder dialog (multi-folder pick, name from folder), enter/go-to project, "Open folder..." | `app/chat/sidebar/project-dialog.tsx`, `store/projects.ts` | the browser folder picker is the server-side one (its `..` bug is fixed in our patch, live re-test pending) |
| Project dashboard | Partly: a sidebar overview with the 3 latest sessions per project and "show more" pages of 50 | `projects/overview-row.tsx`, `model.ts` (`PROJECT_PREVIEW_COUNT=3`) | no dashboard page (cards with branch, changes, last activity, active runs) |
| Recent / pinned | Sessions: yes (recency sort, pins). **Projects: not pinnable** (no field) | `session-pin-sync.ts`, `workspace-groups.ts` | pin flag for projects |
| Resume per project | Yes: entered project lanes, route resume, resume latches, virtual session list | `use-entered-project-sessions.ts`, `use-route-resume.ts` | none known |
| Workspace, branch, changes | Yes: composer branch/worktree row, base-branch picker, worktree dialog, Git pane with diff/stage/commit (seen live), diff counts | `use-composer-branch.ts`, `worktree-dialog.tsx`, `components/chat/diff-lines.tsx` | no always-visible "workspace + branch + N changes + **write allowed?**" strip |
| Model / profile per project | Model menu and profile switcher exist; projects belong to a profile; **no model field on a project** | `model-menu-panel.tsx`, `profile-switcher.tsx` | per-project model (store it Odysseus-side or use one profile per project) |
| Approval dialogs | Yes: once / session / always / deny cards; approval mode manual / smart / off per profile in the status bar | `components/assistant-ui/tool/approval.tsx`, `shell/approval-mode-menu.tsx` | none |
| Revocation of "always" approvals | Yes: Settings > Safety & Approvals edits `command_allowlist` | `settings/config-subpages.ts` | not discoverable from the approval card |
| **Write permission per project** | **No.** Writes are bounded by `HERMES_WRITE_SAFE_ROOT`, an environment variable of the gateway process (read per call from the environment in `agent/file_safety.py`), plus our route guard and never-list. Registering a project does **not** change it (good, and required) | `agent/file_safety.py:226`, `hermes_cli/web_route_guard.py` | **no UI to authorize / list / revoke write roots; a change needs a gateway restart** |
| Context window gauge | Yes: status-bar chip with a breakdown popover and a compact action | `shell/context-usage-panel.tsx` | it sits in the status bar, not beside the composer |
| Reasoning vs tools vs answer | Yes: reasoning blocks and pill, tool rows with result summaries, approval activity, activity timer | `lib/reasoning-blocks.ts`, `thread/turn-activity.ts`, `lib/tool-result-summary.ts` | needs a visual review with real data, not code |
| Session history, search, navigation | Yes: server-side session search, virtual list, switcher, command palette, find bar | `use-server-session-search.ts`, `command-palette/`, `find-bar.tsx` | check at scale (13 sessions, 11,289 messages migrated) |
| Responsive UI | Touch styles and pane shell exist | `touch-styles.test.ts`, `components/pane-shell/` | NOT TESTED at narrow widths |

**Extension mechanism:** a real plugin system (status-bar, pane, title-bar and settings-page slots, app routes, and a `host` API for sessions, new chat and composer), the one `odysseus-return` already uses. A write-authorization panel and a project dashboard can ship as plugins, **not** as patches to upstream UI. Verify the app-route and pane contracts in `sdk/index.ts` and `app/contrib/surfaces.*` before the first design (not read in depth yet).

**Conclusion:** nearly every item on the Projects wishlist exists upstream. Genuine development is small: (a) write authorization, (b) a project dashboard page, (c) pinned projects and per-project model, (d) a context-gauge placement tweak. Do not rebuild registry, sessions, search, approvals, gauge or Git UI.

## 2. Design constraint that drives item (a)

Registration must never authorize writes. Today it does not, and the only authorization channel is the component command `hermes-webapp-component.py roots` (exact top folder of one repository; refuses credential folders, the Odysseus install, drive roots). Because `HERMES_WRITE_SAFE_ROOT` is read from the gateway's environment, changing it means restarting the managed gateway. Proposed flow, nothing built: a plugin panel lists the project's folders with a "write access: off / on" state; turning it on shows the exact path, filesystem (warn on Btrfs), the Git worktree's real Git directory and common directory, and requires an explicit confirmation; Odysseus applies it through the component (already validated) and restarts the gateway **only when idle** (the Services menu already refuses with active runs); revoke does the same. A worktree's Git directory outside the authorized folder is shown and must be authorized as well or the commit is refused (the gap recorded in `hermes-production-cutover.md`).

## 3. SearXNG (what exists)

- Instance: native, `C:\Users\Protagonist\Apps\searxng-windows`, listening on `127.0.0.1:8888`; a JSON search returned 38 results (brave, google cse).
- Odysseus: already configured: `data/settings.json` has `search_provider=searxng`, `search_url=http://127.0.0.1:8888`, safe search strict. (`.env` still says `localhost:8080`; the settings value wins. Something else listens on 8080; not investigated.)
- Hermes: the pinned agent has a `searxng` web provider (`tools/web_tools.py`) enabled by `SEARXNG_URL`, selectable with `web.search_backend`. **Needed:** set `SEARXNG_URL=http://127.0.0.1:8888` (not a secret) in the managed gateway home's `.env` and `search_backend: searxng` in its config. That is a production configuration change: validate first in the preview gateway by calling the tool function directly (no model needed), then ask.

## 4. Revised priorities and dependencies

| # | Task | Depends on | Notes |
|---|---|---|---|
| 1 | **Shared wiki + handoff** | none | documentation and conventions only; unblocks 3 and the Sonnet/LongCat split |
| 2 | **SearXNG for Hermes** | none | preview validation first; production config change needs approval |
| 3 | **Researcher -> Builder -> Reviewer** | 1, 2; 4a for the Builder's write root | three Hermes profiles, handoff through the wiki and Git branches, no orchestrator |
| 4 | **Projects UI** (a write authorization, b dashboard, c pins + per-project model, d gauge) | a: component roots rules (exist). b/c/d: plugin SDK check | (a) first: it is the safety-relevant one |
| 5 | **Permission hardening, isolation, automated tests** | 4a for the new surface | candidates: install the ask-policy plugin (24 tests, not installed), a Windows sandbox/separate account decision, fold `security_verify.py` and the Firefox probes into a scripted regression |
| 6 | **Final integration + deployment** | 1-5 and LongCat's port | merge order, re-baseline `config/release-baseline.json`, one release check, user-approved deploy and push |

### Researcher -> Builder -> Reviewer (minimal)

Reuse what exists: Hermes profiles ("Bots"/profiles), the `delegation` tools and the kanban plugin; no new orchestrator.
- **Researcher:** web search (SearXNG), read-only tools, no terminal, no write root. Writes findings as `wiki/handoff/<date>-research-<topic>.md` (it needs a write root for the wiki folder only).
- **Builder:** the project's worktree as its only write root; approval mode manual or smart; writes `wiki/handoff/<date>-build-<topic>.md` (what changed, commit, how to test).
- **Reviewer:** read-only plus Git diff and test runs in a scratch location; writes a verdict note; never edits the code.
Handoff = a note plus a branch name; the human (or the next agent when asked) moves it on.

### Shared wiki (minimal)

A separate Git repository of plain Markdown, at a path **you** choose (proposal: `F:\Odysseus-Wiki`, outside the production tree, not on Btrfs). Structure: `STATE.md` (one page: who works on what, branch, last commit), `contracts/` (interfaces between agents), `decisions.md` (dated, one line each), `handoff/` (one note per handoff). Read access for both agents through the normal file tools; write access granted per agent as a write root of that folder only. Hermes can also read it through its existing `llm-wiki` skill; Odysseus through its notes tools. The wiki is advisory and never becomes an authority for permissions or secrets.

## 5. Proposed division: Sonnet and LongCat

LongCat's current work is not visible in this repository (no branch or worktree in `git worktree list`; the only trace is the old WebUI session "Windows/Linux compatibility..." that read `containment.py`, `routes/hwfit_routes.py`, `routes/codex_routes.py`). I did not touch it and will not guess its branch. **Needed from you:** LongCat's branch and worktree path, or tell it to use `F:\Odysseus\integration-backups\longcat-winport` on `feature/windows-port-longcat`, based on `ed0bea07`.

| Owner | Files (exclusive) |
|---|---|
| **Sonnet** (Hermes integration, Projects) | `src/hermes_*.py`, `src/application_modes.py`, `static/js/application-modes.js`, `integrations/hermes-mode/**`, `scripts/hermes-*.py`, `tests/application_modes/**`, `tests/desktop-lab/**`, `docs/hermes-*.md`, `docs/evidence/**`, `config/hermes-*.json`, `config/production-cutover.json`, the wiki conventions |
| **LongCat** (Windows port of Odysseus core) | `src/containment.py`, `src/process_*.py`, `src/bg_jobs.py`, `core/platform_compat.py`, non-Hermes `routes/**` and `scripts/**`, tests outside `tests/application_modes/` (the 525 baseline failures), `fcntl` scripts |
| **Shared, change only through a contract note** | `app.py` (proxy install), `core/auth.py` (revoke listener), `requirements*.txt`, `launch-windows.ps1`, `config/release-baseline.json` |

Contracts to write first (in the wiki, `contracts/`): (1) the process-ownership API used by the managed component (`src/process_ownership.py` `start_token` / `verify`, `process_lifecycle`): signatures stay stable; (2) the release-baseline protocol: LongCat reports newly passing tests in a handoff note and Sonnet re-baselines once, so the file has one writer; (3) Windows-only skip markers; (4) branch and worktree rules: one worktree and one feature branch per agent, based on the current release commit, no force-push, nobody edits `F:\Odysseus` except an approved deploy, merges into the release branch one at a time with a rebase and a focused test run.

## 6. What can run at the same time

| Together | Why it is safe |
|---|---|
| Wiki (1) + SearXNG preview validation (2) + Researcher/Builder/Reviewer design (3, design only) | disjoint files (a new repository, a lab home, a design doc) |
| Any Sonnet task + LongCat's Windows port | disjoint file ownership; only the shared files above need a note |
| Projects write authorization (4a) + dashboard (4b) | different plugin files, but both load in the same Webapp: develop in separate worktrees, merge sequentially |
| **Not together:** 4a and 5 (hardening) | both change `hermes_webapp_component.py` and the route guard; do them in order |
| **Not together:** any production change and a release check or deploy | one gate at a time |

## 7. Remaining risks (security)

1. Same-user execution: the terminal pane, approved agent commands, MCP servers, and any local program can read `.claude`, `.codex`, the gateway token and the files the gateway can reach. Only a separate Windows account or a sandbox closes it.
2. The gateway port serves `/api/status` unauthenticated (paths, pid, install id) to local processes; protected routes need a token.
3. The 30-second ticket is a bearer in the address bar and browser history (spent after use).
4. No `Secure` cookies (http on loopback).
5. Write authorization is only a CLI today; until the panel exists, projects stay read-only by default, which is the safe default.
6. The old WebUI on `:8787` still runs with its own password and the user-level `HERMES_WRITE_SAFE_ROOT`; it is a rollback path and an unmanaged surface.
7. Claude OAuth refresh races between the old and managed gateways and `claude.exe` (one fresh `claude login` in the worst case).
8. The full release baseline has 525 known failures plus 9 environment-dependent ones; regressions hide inside that noise until LongCat's port shrinks it.

## 8. Decisions needed before implementation

1. Wiki location (proposal `F:\Odysseus-Wiki`) and that I may create it.
2. LongCat's branch and worktree, or approval of the proposed names.
3. Whether the managed gateway's `.env` may get `SEARXNG_URL` (after preview validation).
4. Order inside item 4: write-authorization panel first (recommended), then dashboard.
5. Whether to install the ask-policy plugin (approval floor for paths) in the managed gateway.

## 9. Added task: Wiki panel in HermesGUI / Projects (later; not started)

Goal: see the shared wiki's state from the Projects side without leaving it. Read-only first; review actions later.

- **Where it lives:** a Webapp plugin (status-bar chip + a pane), like `odysseus-return`, not a patch to the upstream UI. Data comes from a small Odysseus endpoint that calls the Wiki Steward library (`status`, `conflicts`, `inbox`, `reconcile` read-only); the panel keeps no copy of wiki state and never writes to the wiki folder.
- **Status chip (per project):** verified / stale / contested / needs-review counts, open handoffs, last reconcile result, wiki integrity (green, or red with the reason when `integrity` reports uncommitted edits or foreign commits).
- **Pane sections:** (1) Handoffs addressed to the current agent, with the automatic "Session interrupted" ones flagged; (2) Conflicts (both claims side by side, grade, evidence, who said it); (3) Stale knowledge (what changed, since which commit); (4) Needs review (sensitive/decision claims); (5) Recent task log.
- **Review actions (second step, human only):** accept / reject buttons for needs-review and conflict claims call `Wiki.decide` as the reviewer after an explicit confirmation in Odysseus, never from the agent's own chat or MCP session. They show the evidence grade and the exact text that will become authoritative.
- **Depends on:** the Wiki Steward being live; the Projects write-authorization panel (item 4a) for the shared "effective permissions" chip so the two chips look consistent.
- **Does not need building:** search, grading, conflict detection, freshness, reconciliation (all exist in `src/wiki_steward.py`).
- **Risks to design for:** the endpoint runs in the Odysseus process, so a bug there must not be able to write the wiki; keep it to the read-only library calls plus the one reviewer endpoint behind the Odysseus session and the grant gate; show wiki text as untrusted (escape it; never render it as markdown with links or HTML).

- **Backup action (added):** a "Back up wiki" control in the same pane. The wiki has no remote by design, so this is a local, verified snapshot: run `integrity` first (refuse if it reports problems), write a `git bundle` of the wiki plus `steward.json` to a folder **outside** the wiki and outside any agent's write roots, then verify the bundle (`git bundle verify`) and the audit chain, and show the result. Options: manual button, optional schedule (Windows scheduled task owned by Odysseus), keep the last N snapshots. Restore is a reviewer-only, terminal-guided procedure (clone from the bundle, run `integrity`, `adopt` if intended); the GUI never overwrites a live wiki. The snapshot contains claims, task logs and handoffs, so it is stored with the same care as the wiki (same-user readable; encrypted-backup approach is the same open item as for the Odysseus full backups).
