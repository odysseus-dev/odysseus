# Wiki Steward: integration status and activation (isolated; nothing live)

Branch `feature/wiki-steward`. Production, live agent configs, and the real wiki folder are untouched.

## Verification model (code: `src/wiki_steward.py`)

| Grade | What establishes it | Never |
|---|---|---|
| behavior-tested | a test **defined by a reviewer in `steward.json`** (argv, project, subjects, watched paths) runs at the source repo's HEAD in a scratch copy, exits 0, and is bound to the claim's `subject` | the agent cannot supply or alter the command |
| source-confirmed | `scope: source`; a file at HEAD contains the quoted text (4+ chars) **and the claim names that file**; or a commit that touched a named file and is named in the claim | general claims; bare commits; file existence without quoted text |
| externally-verified | `scope: external`; the quote is found on N distinct reviewer-allow-listed https hosts (no redirects, public addresses only); expires after `ttl_days` | one source; unlisted hosts; http |
| attested | everything else, including any claim backed only by a reachable commit | cannot become verified without a reviewer |
| stale | derived: a watched file changed, or the external check expired | cannot be revived by its old evidence |

Sensitive wording, any non-`fact` kind (decision, security, policy, architecture) -> `needs-review`; only a reviewer accepts. A reviewer-accepted attested claim stays labelled "ACCEPTED BY REVIEWER ... not independently verified".

## Reconciliation (`wiki_reconcile`, and automatically at MCP session start)

Mechanical, no LLM. Reports unrecorded commits, late/incomplete tasks without handoff, stale knowledge, unresolved conflicts, proposals waiting for verification, claims waiting for a test run, and what needs the reasoning bot. With `repair=true` it syncs commits, marks overdue open tasks `interrupted`, writes one idempotent handoff per task (even if two sessions race), and re-verifies waiting mechanical claims. Agents bracket work with `wiki_begin` / `wiki_record_task(task_id)`; a session that dies in between is recovered by the next session's startup pass.

## Real-client tests (`tests/wiki_steward/real_clients/`)

Isolated per invocation, no global config touched: Claude Code `--mcp-config <temp> --strict-mcp-config --no-session-persistence`; Codex `-c mcp_servers.wiki.*` overrides + `--ephemeral` + read-only sandbox; Hermes' own MCP client (`tools.mcp_tool_discovery`) in-process with a throwaway `HERMES_HOME`.

| Client | Result |
|---|---|
| Claude Code 2.1.317 (haiku) | PASS: all 8 prompted steps; claim graded source-confirmed; conflicting note held as conflict; wrong-project write refused; `wiki_decide` absent; task begun and completed; audit actor `claude-code` |
| Hermes (pinned release, stdio) | PASS: 12 tools registered as `mcp__wiki__*`; same steps; `agent` argument override refused; actor `hermes-preview` |
| Claude Code + Hermes at once | PASS: one wiki, no corruption, integrity clean, contradiction between the two detected |
| 6 raw MCP processes at once | PASS: all 5 replies each, no stderr |
| Codex 0.167.0 | NOT TESTED: its stored login fails ("refresh token was already used") before any tool call; it needs `codex login` by the user. Wiring (`-c mcp_servers.*`) is prepared but unproven. |

Defects found by the real clients and fixed: a missing or corrupt `steward.json` crashed the MCP server (Hermes then reports "lost its stdio subprocess"); now every call fails closed with a message and the server stays up. Separately my harness had resolved a rootless path differently per process (test-harness bug, not product).

## Trust boundary (explicit)

- Intended write path: only the steward process. Agents reach it through MCP tools that cannot decide, adopt, revert, or set their identity.
- **Same-user terminal access is outside this boundary.** An agent that can run shell commands as you can edit files in the wiki folder, run `git` there, or run reviewer commands through a pseudo-terminal. Mitigations that exist: the folder must not be in any agent's write roots, `integrity` flags uncommitted edits and commits not authored by the steward, a dirty wiki blocks all further writes, the audit log is hash-chained. A separate Windows account (or a sandbox) is the only real boundary.
- Wiki content returned to agents is labelled untrusted data.
- Do not add the wiki folder to the managed gateway's write roots. Under the runtime-grants design the wiki is never a project grant; only the steward writes it.

## Activation (needs your approval for each; none done)

1. Create the folder yourself (the file tool refuses the path): `New-Item -ItemType Directory F:\Projects\Odysseus-Wiki` in PowerShell, then `python scripts\wiki.py --root F:\Projects\Odysseus-Wiki init`, edit `steward.json` (projects -> repo paths, agents, `tests`, `external.hosts`), then `adopt "configure"` in a terminal.
2. Add one MCP entry per agent (Hermes `mcp_servers.wiki`, Claude Code `.mcp.json`/`claude mcp add`, Codex `[mcp_servers.wiki]`), each with its own `WIKI_AGENT`.
3. Add the begin / sync / record_task lines to each agent's instruction file.
4. Create the `wiki-bot` Hermes profile (only wiki tools).
5. Re-run Codex after `codex login`.
