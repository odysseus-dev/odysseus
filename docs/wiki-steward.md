# Wiki Steward (built in isolation; nothing installed, nothing deployed)

One private Git-backed wiki (`F:\Projects\Odysseus-Wiki`, to be created with `wiki.py init`), one steward, many agents, one write path.

## Architecture

- **`src/wiki_steward.py`**: stdlib + git, no daemon, no LLM. All writes go through one transaction: lock file, refuse if the wiki has out-of-band changes, write, append a hash-chained audit line, one commit authored `wiki-steward` with `Wiki-Steward:` and `Wiki-Actor:` trailers. Any failure resets the tree. Reads take no lock and no commit.
- **Entry points** (same library): `scripts/wiki_mcp.py` (stdio MCP server, started by the agent's own client for the length of its session) and `scripts/wiki.py call <method>` (shell). Hermes (`mcp_servers`), Claude Code and Codex all support stdio MCP, so no per-agent code. Identity comes from `WIKI_AGENT` in that agent's MCP entry, not from tool arguments (tested).
- **Reasoning model**: not part of the library. The "Wiki Bot" is a Hermes profile `wiki-bot` that has only the wiki tools. It is started on demand (Webapp session or `delegate_task`) when `wiki_conflicts` lists work that needs judgement. It cannot promote by assertion: it can only call `wiki_verify` with *new evidence* (commit, or file + expected text), which the library re-checks mechanically.

## Data model (files, one record each, so concurrent writers rarely touch the same file)

`claims/<project>/<id>.md`, `tasks/<project>/<id>.md`, `handoffs/<agent>/<id>.md`, `state/cursors/`, `audit/audit.jsonl`, `steward.json` (projects -> source repo, agents -> allowed projects and operations, reviewers). Each record is a JSON header plus the human text. A claim records source agents, project, status, evidence with check results, `verified_commit`, `verified_at`, and the watched files with their Git blob ids.

## Operations

| Wanted | Method | Cost |
|---|---|---|
| `wiki.search` | BM25 over verified claims, project-scoped, freshness computed live | no LLM, about 135 ms for 300 claims (measured) |
| `wiki.propose` | secret scan, dedupe (exact or near-duplicate with the same subject), conflict check, mechanical verification, then promote / hold | one commit, about 0.4 s |
| `wiki.verify` | re-run checks, optionally add evidence | no LLM |
| `wiki.record_task` / `sync` | idempotent task log; `sync` turns new source commits into task logs | no LLM |
| `wiki.handoff` / `inbox` / `ack` | handoff record carries the stale/conflicting/needs-review claims at that moment | no LLM |
| `wiki.status` | claim counts, verified facts, stale ids, recent tasks, open handoffs | no LLM |
| `wiki.conflicts` | contradictions, outdated, needs-review, unverified, and *possible* contradictions (similar text, one negated; a candidate for the reasoning bot) | no LLM |

## Promotion rules (code, tested)

- **Verified automatically**: kind `fact`, at least one mechanical evidence item passes (a commit that exists and is reachable from HEAD, or a file at HEAD that exists and contains the expected text), none fails, not sensitive, no conflict.
- **Needs review**: any kind other than `fact` (decision, security, policy, architecture), or sensitive wording or watched path (security, permission, auth, credential, token, approval, sandbox, grant, revoke, policy). Only a reviewer can accept.
- **Conflict**: another live claim with the same project and subject and different text. It never displaces the existing one, even with mechanical evidence. Resolution: a reviewer, or the wiki bot if the claim is non-sensitive, has mechanical evidence, and every claim it conflicts with is **stale**.
- **Provisional**: no mechanical evidence (notes, attestations) or failing evidence. Never returned as verified; shown only with `include_provisional` and labelled NOT VERIFIED.
- Rejected before storage: text that looks like a credential; evidence paths that are absolute, contain `..`, or name `.env`, keys, `auth.json`, vault and similar.

## Freshness (the recommended extra)

When a claim is verified the steward records the Git blob id of each watched file at HEAD (files named in file-evidence, `watch_paths`, or by default the files the evidence commit changed). Search, status, conflicts and handoffs compare against the current HEAD: a changed or deleted file makes the entry **stale** and every result says so. `recheck` can refresh an entry only when content-bound evidence still holds (the file still contains the expected text). A claim backed only by a commit ("fixed at abc123") cannot be re-proven mechanically, so it becomes `stale` and goes to the reasoning bot or a reviewer. Tested: the cookbook-fix example, including a conflicting newer claim replacing the stale one.

## How agents invoke it, and automatic end-of-task updates

- **Hermes / Claude Code / Codex**: add one MCP entry per agent (`command: python scripts/wiki_mcp.py`, env `WIKI_ROOT`, `WIKI_AGENT=<name>`). Tools: `wiki_search`, `wiki_propose`, `wiki_verify`, `wiki_record_task`, `wiki_sync`, `wiki_handoff`, `wiki_status`, `wiki_conflicts`, `wiki_inbox`, `wiki_ack`. Reviewer operations are not exposed.
- **End of task, no LLM and no daemon**: the instruction file each agent already reads (CLAUDE.md / AGENTS.md / the Hermes skill) says "finish with `wiki_sync`, then `wiki_record_task` if the summary adds something". `sync` is idempotent and derives the log from Git, so it also works from a Claude Code `Stop` hook, a Codex `notify` command, or a post-commit hook in a project repository, but installing any of those changes an agent setting or a project repository and needs your approval. The pinned Hermes has no session-end hook (only `post_tool_call` and the LLM/stream hooks), so for Hermes the instruction plus `wiki_sync` is the mechanism.
- **Start of task**: `wiki_inbox`, `wiki_status`, `wiki_search`. Handoffs show what is stale or contested so a new agent does not trust it blindly.

## Tested (15 tests, `tests/wiki_steward/`; plus mutation checks)

Two simulated agents submitting conflicting claims (verified claim vs. note-only claim vs. mechanically evidenced claim); freshness after a code change; recheck limits; bot resolving a conflict only when the old claim is stale; sensitive claims needing review and agents unable to promote; unverifiable and bad evidence; secrets refused with nothing written; deduplication; authorization and project scoping; task, sync, handoff, inbox, ack; out-of-band edit blocks writes until a reviewer adopts it; a foreign commit is detected; 8 concurrent writers; a failed write leaves no trace and revert is reversible; CLI and MCP entry points (unknown tool and an `agent` argument are refused). Mutation checks: removing the freshness, conflict, secret-scan or clean-tree rule each makes a specific test fail. Not tested: real Hermes/Claude/Codex clients speaking to the MCP server, Windows paths with Btrfs, very large wikis (hundreds of thousands of claims would need a persisted index).

## Limits (stated plainly)

1. **Same-user limit.** An agent with a terminal runs as you and could edit the wiki folder or run reviewer commands in a pseudo-terminal. The design makes that visible, not impossible: the folder must not be in any agent's write roots (it will not be, under the workspace-grant design), `integrity` flags uncommitted edits and commits not made by the steward, a dirty wiki blocks all further writes, the audit chain is hash-linked, and reviewer commands refuse without a terminal. A separate Windows account would be the real boundary.
2. Agent identity is attested through the MCP entry, not authenticated.
3. `test` evidence is not run by the steward in this version: it is accepted as attestation (the claim stays provisional unless commit or file evidence also passes). Running allow-listed test commands is a possible later step.
4. Claims that need judgement are only queued, so the wiki bot must be started (by you or an agent) to process them.
5. The wiki and the source repos stay separate: the steward only reads the source repos named in `steward.json`, with `git` read commands, and writes only to its own folder. No remote, no push.

## To use it (needs your approval; none done)

1. Create `F:\Projects\Odysseus-Wiki` (it must be a writable root for the steward process only), `python scripts/wiki.py --root <that> init`, edit `steward.json`, `adopt`.
2. Add the MCP entries per agent (this changes agent configuration).
3. Add the end-of-task lines to the instruction files.
4. Create the `wiki-bot` Hermes profile.
