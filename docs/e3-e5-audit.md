# §E3 / §E5 audit: Hermes Desktop transcript and approvals

Audited 2026-11-15 against managed release `d17f92d164c3` (pin 3b42157858 plus seven patches). Evidence classes: **code** (read from source), **unit** (vitest), **lab** (real renderer in Firefox against fixture messages), **doc** (public documentation read today). Production was not touched.

## §E3 Thinking vs final answer

| Requirement | State | Evidence |
|---|---|---|
| Final answer visually separate from activity | Works | lab: answer in full-contrast prose, scaffold rows muted (375/768/1440) |
| Compact expandable tool activity, call and result as one row | Works | code `tool/fallback.tsx` `ToolEntry` |
| Grouped calls ("Explored 2 files, ran 2 commands"), live ticker | Works | code `tool/run-summary.ts`, `run-ticker.tsx`; unit `tool-group.test.tsx` |
| Completed runs fold; manual expand; state persists | Works, also after reload | lab: expanded state survives a page reload at all three widths; `store/tool-view.ts` keeps the last 240 |
| No empty Thinking rows | Works | lab: whitespace-only reasoning draws no disclosure |
| Approvals stay visible | Works | lab: card present at all widths, with and without a folded run |
| Reduced motion, `aria-expanded` | Works | code |
| Resume recomputes grouping identically | Works for the checked case | lab: fresh mount of a completed message gives the same grouping as the first load. Real FTS5-resumed sessions were not exercised (needs a gateway). |
| Errors never collapsed | **Fixed** (was a gap) | before: failed call hidden, 0 failure rows. after: 1 visible failure row. |
| Elapsed on the folded summary | **Fixed** (was a gap) | before: none. after: `28s` shown for a 28 s run; no chip under 2 s. |
| Pinned research progress card | **Deferred, not implemented** | see below |
| Responsive layout | No horizontal overflow at 375/768/1440 | lab: `scrollWidth <= innerWidth` for all 18 captures |

Not verified: behaviour in the full Desktop shell with a live gateway, and composer/Usage Center regression in a browser. Those files are not touched by the patch (`tool/fallback.tsx`, `tool/approval.tsx` and their tests only); the thread and tool suites pass.

### Research progress card
Events the gateway emits (`tui_gateway/contracts/events.py`): `tool.start` (args), `tool.complete` (result), `tool.progress` (MCP `notifications/progress`: message and percent, only from servers that report it), `subagent.*`, `tool.generating`. There is no research-specific event and no query, source-count or step n/N field; `web_search` and `web_extract` are ordinary tool calls. Counting sources would mean parsing result bodies, which is provider-specific and unreliable. The existing live summary line ("Searching “…”", "Opened 3 pages") already updates in place. Deferred until a real field exists.

## §E5 Permissions and approvals

| Scope | Backend reality | UI |
|---|---|---|
| Once | enforced, one call | Run |
| This session | enforced for that command pattern in the session | Allow for… > Allow this session |
| Always | **global** command pattern saved to the Hermes config; every workspace, every later session | Allow for… > Always allow (all workspaces)…, with a confirm dialog that says so |
| Deny | enforced | Reject |
| This workspace | **does not exist** for commands or MCP. Folder scope exists only for file-tool write grants (a separate system with its own grant/revoke and audit). | not offered |

`tui_gateway/server.py::_approval_request_payload` builds `choices` as once, then session and always (unless smart-denied or the request disallows them), then deny. The wire payload carries `command`, `description`, `allow_session`, `allow_permanent`, `smart_denied`, `edit`, `request_id`, and no cwd.

Danger classification: an approval is only raised when the gateway's own detector (`detect_dangerous_command`, or a plugin rule) flagged the call, and `description` is that detector's reason. The card now shows it as "Flagged by Hermes: …" next to a real command. No frontend classifier was added. When there is no description, no reason is shown.

Card text states that approval is a confirmation, not a sandbox: an approved command or tool runs with full Windows permissions, and "This session"/"Always" remember the command and grant no folder access.

Left out on purpose: working directory and resolved target paths (not on the wire; would need an additive gateway change, a separate decision) and default focus on Reject (the card cannot rank severity, and every request here is already flagged).

## Reference comparison

Local REA material reused, no new reverse engineering: `REA-EVIDENCE-SUMMARY.md` (Claude Desktop v45.2.1, static graph, partial coverage; string-level facts are not REA findings) and `claude-code-reliability-comparison.md` (documented/artifact/inferred labels). Public docs were read live on 2026-11-15; no source of the other projects was cloned or run.

| Reference | Existing Hermes equivalent | Genuine gap | Worth adapting? | Risk |
|---|---|---|---|---|
| Claude Code permissions (allow/ask/deny rules per user, project, local; its docs say Bash rules do not match inside `sh -c` and point to sandboxing for real enforcement) | once/session/always/deny, deny rules, smart approvals | Scoped rule files per project | No. Adopt its honesty: rules are not a boundary. Done in the card text. | Low |
| Claude Code, our REA/RFC findings G1 (compaction thrash), G2 (interrupted tool calls), G8 (permission mode on resume) | G2 shipped (resume outcomes patch); G8 mostly covered | G1 refill-loop guard is agent-side, not UI | Later, agent-side, separate task | Medium |
| Claude Code plan mode | `/goal`, approvals; no read-only plan mode | A "Plan" profile with read-only toolsets | P1 via profile config, no code | Low |
| Codex Windows sandbox (`mxc`, `elevated`, `unelevated`; docs: unelevated has weaker network isolation and no denied-read paths) | Terminal runs as the user | OS enforcement | Not as UI. Master plan track 2 (Docker/WSL2 pilot). Never imply it today. | High if faked |
| OpenHands confirmation policy + security analyzer (risk levels, `ConfirmRisky`) | gateway danger detector + smart approvals | A graded risk level | Only if the backend exposes one. No frontend classifier. | Medium |
| OpenCode permissions (allow/ask/deny by action and resource) | odysseus-ask-policy plugin (off by decision) | Per-resource rules UI | No, plugin stays off | Medium |
| Aider (auto-commit per change, `/undo`, repo map) | checkpoints and `/rollback` (opt-in, off in this profile), worktrees | One-click undo of the last agent change | P1 candidate: enable and surface checkpoints. Config + small UI. | Low |
| T3 Code (MIT, very active; threads, git, diff review) | Projects, sessions, Desktop git pane | Inline diff review card | Not reviewed in depth here. P1 candidate for a targeted look. | Unknown |
| Vibe Kanban | Hermes Kanban, worktrees | none | No. Company shut down 2026-04-10; community maintained, going local-only. Design reference only. | Dependency |
| Orca and other earlier references | not recoverable from the notes available | | Not guessed | |

## Findings outside the patch
- The managed gateway config has `rea` and `hermes-security` enabled in the default profile; the plan wants REA per-profile, off by default. Schemas stay behind tool search, so the cost is small, but the dynamic REA tools are not prompted by Hermes. Needs your decision.
- The `alyn` MCP entry is present with `trust: untrusted`; the plan says no remote MCP in production and sets `sampling.enabled: false` on untrusted servers. No `sampling` key is set. Needs your decision.
- `web:` config is empty (no search or extract backend). `web_extract` fails in this session with "search-only backend, set web.extract_backend". SearXNG and extraction are the separate later task.
- `memory.write_approval`, `skills.write_approval` and `checkpoints` are unset.
- 26 tests in `tests/application_modes/test_webapp_component.py` fail identically before and after this work (fixture copies only two of the patches and builds a fake repo without the managed marker). Pre-existing; not changed.
