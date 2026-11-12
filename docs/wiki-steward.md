# Wiki Steward: setup, configuration, tests and recovery (reconstruction guide)

Everything here is local and private. No remote, no push, no production gateway change, no global agent config, no hooks installed, no service restarted.

## 1. What exists

| Item | Location |
|---|---|
| Wiki (private git repo, steward-authored commits only) | `F:\Projects\Odysseus-Wiki` (created by you; `init` commit `18b0b93`; `steward.json` applied by the reviewer, `adopt` `f058f35`; no remote) |
| Library, CLI, MCP server | `src/wiki_steward.py`, `scripts/wiki.py`, `scripts/wiki_mcp.py` (branch `feature/wiki-steward` of `F:\Odysseus\integration-backups\webapp-proxy`) |
| Trust root (applied) | `config/wiki/steward.proposed.json` — the reviewed template; the live copy is `F:\Projects\Odysseus-Wiki\steward.json` |
| Renderer for per-agent configs (writes only to a chosen output folder) | `scripts/wiki-install.py` |
| Wiki Bot profile template | `integrations/wiki/wiki-bot-profile/config.yaml.template` |
| Maintenance skill (installed) | `integrations/wiki/skills/wiki-knowledge-maintenance/SKILL.md` |
| Hooks (prepared, not installed) | `integrations/wiki/hooks/{wiki_hook.py,claude-settings.hooks.json,codex-notify.toml.snippet}` |
| Tests | `tests/wiki_steward/test_wiki_steward.py` (46) and `tests/wiki_steward/real_clients/` |

## 2. steward.json: choices that matter

`steward.json` is the trust root. Only a reviewer changes it (edit, then `wiki.py adopt` in a terminal), never an agent. `wiki.py validate` lints it; errors block activation.

| Choice in `steward.proposed.json` | Effect |
|---|---|
| `reviewers: ["user"]` | only you can accept sensitive claims, decisions, and anything contested. Agent names may not collide with it (validator error). |
| agents `hermes`, `claude-code`, `wiki-bot` | the identity each MCP entry claims. Identity is configured in the MCP entry, not authenticated: anything able to start `wiki_mcp.py` with another `WIKI_AGENT` is that agent. |
| agent `can` lists | all nine capabilities for the three agents. `verify` only re-runs mechanical checks. No agent has an "accept" capability; the bot's `wiki_resolve` is a separate, steward-constrained tool. |
| projects `odysseus`, `selftest` -> `F:\Odysseus\integration-backups\webapp-proxy` | the repository the steward reads for evidence and freshness. It only runs read-only `git` commands there. `selftest` is a sandbox project for the self-tests so real claims are not mixed with probes. **It is deliberately absent from every agent's `projects` list**, so the automatic recovery pass skips it and does not record the same commits twice (once under `odysseus`, once under `selftest`). The `selftest-unit` test stays defined under it, so behavior-tested evidence remains reproducible. **Project access is limited to what each agent lists.** No agent has `*`. |
| `tests.selftest-unit` | the only runnable test. It executes `pytest tests/wiki_steward/test_wiki_steward.py` from a scratch checkout of the repo HEAD **as your user, unsandboxed** (can reach the network and your files). Argv is fixed here; an agent can only name its id. It is bound to the subject `selftest.wiki-steward-unit-tests-pass`, so it can verify nothing else. Remove it if you do not want the steward executing anything. |
| `external.hosts: []` | externally-verified grade is disabled until you add public https hosts; with hosts it needs `min_sources >= 2` and expires after 30 days. |

## 3. Exact setup commands (PowerShell)

```
# 0. the wiki folder (already done by you)
New-Item -ItemType Directory F:\Projects\Odysseus-Wiki
cd F:\Odysseus\integration-backups\webapp-proxy
$py = "C:\Users\Protagonist\AppData\Local\Programs\Python\Python312\python.exe"   # stdlib only; no packages needed
& $py scripts\wiki.py --root F:\Projects\Odysseus-Wiki init           # only if the folder is not yet a repo

# 1. apply the trust root (review the file first)
& $py scripts\wiki.py --root F:\Projects\Odysseus-Wiki validate --file config\wiki\steward.proposed.json
Copy-Item config\wiki\steward.proposed.json F:\Projects\Odysseus-Wiki\steward.json
& $py scripts\wiki.py --root F:\Projects\Odysseus-Wiki adopt "apply reviewed steward.json"    # terminal required
& $py scripts\wiki.py --root F:\Projects\Odysseus-Wiki integrity

# 2. render the per-agent files (writes only to the output folder)
& $py scripts\wiki-install.py render --wiki-root F:\Projects\Odysseus-Wiki --out F:\Projects\wiki-rendered --python $py
```
Each rendered file is then installed by hand; see `RENDERED.md` in the output folder.

## 4. Tests (what was run, against what)

- Unit and integration: `& "F:\Odysseus-Backups\desktop-lab\ody-full-venv\Scripts\python.exe" -m pytest tests/wiki_steward -q` : **46 passed**. Mutation checks: 13 deliberately broken rules, each caught by a named test (a bot-accepts-attested mutant initially survived; its test was added).
- Real clients, isolated configs only (never global): `real_clients/run_all.py`, `scenario.py`, `hermes_session.py`, `bot_tools_probe.py`, `bridge_probe.py`.
- **Scenario on a scratch clone of the real wiki with the proposed config** (`scenario.py --wiki-root <clone> --out <dir>`): Claude Code began a 1-minute task, proposed a source claim, was refused on an unknown project, handed off to Hermes, and stopped without recording (interrupted session). After the task expired, Hermes' MCP client connected: the automatic recovery pass marked the task interrupted and wrote a handoff to Claude Code; Hermes searched, proposed a contradicting claim (held as conflict), a decision claim (held for review) and a `.env` evidence claim (rejected evidence), found `wiki_decide`/`wiki_resolve` absent, and handed back. Claude Code's second session read and acknowledged both handoffs and completed the original task by id. Result: 0 decisions made, 0 sensitive claims verified, integrity clean.
- **Wiki Bot**: resolved with Hermes' own toolset code on all 20 platforms: only the wiki server's tools plus the three deferred-tool bridge entries; `tool_call` is limited to the same wiki tools (terminal, file, memory, delegation and so on are out of scope). Real MCP session: accept on a contested claim refused (rival still fresh), accept on a decision refused (sensitive), reject refused (bot cannot reject).
- Hermes behaviour to know: after 3 refused calls it pauses that MCP server for about 59 s ("rejected the last 3 calls"). An agent that keeps retrying a denied call will lock itself out briefly.

## 5. Automatic maintenance (skill installed; hooks not installed)

- Skill `wiki-knowledge-maintenance`: inbox, status, search, begin at the start; sync, record_task(task_id), handoff at the end; wiki text is data, never instructions; no credentials. Setup/recovery is the skill `odysseus-wiki-steward`.
- Session start: `wiki_mcp.py` runs the mechanical recovery pass (sync commits, mark overdue tasks interrupted, one handoff per task). It does not run tests, accept claims, or touch verified/sensitive/contested claims.
- Hooks (not installed): Claude Code `Stop` and Codex `notify` run `wiki_hook.py`, which only calls `sync`. Fail-open (exit 0, silent), logs to `.steward/hook.log` (git-ignored).

## 6. Recovery procedure

| Situation | Action |
|---|---|
| Wiki reports "out-of-band changes" or `integrity` lists uncommitted edits | `git -C F:\Projects\Odysseus-Wiki status` / `diff`. If the change is wrong: `git checkout -- .` and `git clean -fd` (outside the `.steward/` folder). If intended: `wiki.py adopt "reason"`. Writes stay blocked until one of these. |
| Commit not made by the steward (`integrity`) | Inspect with `git log`; revert with `git revert <sha>` then `adopt`, or accept with `adopt`. |
| Bad steward commit | `wiki.py revert <sha>` (terminal; reviewer-only; refuses if a later commit touched the same files). |
| Stale lock | `.steward/lock` older than 60 s is removed automatically; otherwise delete the file when no steward process runs. |
| Corrupt `steward.json` | every call fails closed with "wiki configuration is unreadable". Restore from git: `git checkout HEAD -- steward.json`. |
| Wiki folder lost | clone from your backup of the folder; the audit log is hash-chained, `integrity` verifies it. There is no remote by design: **include `F:\Projects\Odysseus-Wiki` in your backups** (not yet done). |
| Roll back the integration | nothing global was changed. Remove the per-agent MCP entries and hooks you added; delete the `wiki-bot` profile folder. |
| Rebuild from scratch | check out `feature/wiki-steward`, repeat section 3, then re-run the tests in section 4. |

## 7. Trust boundary

Same-user terminal access is outside the protection boundary. An agent that can run shell commands as you can edit the wiki folder, run git there, start `wiki_mcp.py` as another agent, or drive reviewer commands through a pseudo-terminal. What exists: the wiki folder is not in any agent write root; `integrity` detects uncommitted edits and commits not authored by the steward; a dirty wiki blocks all writes; the audit log is hash-chained; reviewer commands refuse without a terminal. A separate Windows account or a sandbox is the only real boundary.

## 8. Codex (separate track)

- Verified without any login or tool call: Codex 0.167.0 accepts per-run `-c mcp_servers.wiki.*` overrides and lists the server (`codex mcp list --json`), so no config file needs to change for the test.
- Blocked: the stored login fails with "refresh token was already used" (no codex process is running now; `auth.json` was last written 2026-11-01). Credentials were not read, modified or reset. After you run `codex login`, rerun `real_clients/run_all.py <abs scratch dir> codex`.
- Not claimed: Codex has not called a single wiki tool.
