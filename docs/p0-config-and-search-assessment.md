# P0 configuration and search assessment (read-only, nothing applied)

2026-11-15. Live files were only read. Secrets were not printed. Every change below is a proposal that needs your approval, and none of it is part of the eighth-patch code release.

## Live Hermes gateway state (home `...\Odysseus\hermes\state\hermes-gateway`)
| Setting | Now |
|---|---|
| `approvals.mode` | `smart` |
| `terminal.backend` | `local` (commands run as the user) |
| `plugins.enabled` | agent-importers, claude-subscription-directsdk-experimental, disk-cleanup, hermes-security, rea, security-guidance, odysseus-workspace-grants, odysseus-wiki-context |
| profiles | none (default only) |
| `mcp_servers` | wiki, node_repl, alyn (`trust: untrusted`, no `sampling` key), hugging_face (disabled). No `rea` entry, so no duplicate of the plugin's own `mcp.json`. |
| `memory.write_approval`, `skills.write_approval`, `checkpoints`, `web` | unset |

## REA
- It is enabled in the one existing profile, so every coding session can reach it. Its 207 tool schemas stay behind tool search, so the cost is small; the risk is the dynamic tools, which Hermes does not prompt for.
- Config is per profile (`plugins.enabled` lives in that profile's `config.yaml`, and each profile has its own `plugins/` folder). So the wanted shape is supported: remove `rea` from the default profile's `plugins.enabled`, and enable it only in a dedicated profile that has its own copy of `plugins/rea`.
- Not tested: creating the profile needs the `hermes` CLI, which is barred in this session, and a gateway restart. Steps for you to run: create profile `rea`, copy `plugins/rea` into it, enable it there, then remove it from default. Never run `rea-agents setup --client hermes`; it overwrites the reviewed pin.
- Disabling REA in default does not delete the plugin folder or the REA evidence work.

## hermes-security (0.1.3, locally patched for skill descriptions)
- It registers nine `security_scan_*` tools, a `/security` command, skills, and three hooks (`on_session_end`, `subagent_start`, `subagent_stop`) that only update its own scan database and may inject a resume message.
- It registers no pre-tool hook, denies nothing, and touches no write roots or approvals. It does not conflict with the grants plugin, the route guard or approvals, and it is also not a guard. Its own README calls controlled validation a convenience, not a sandbox.
- Recommendation: keep enabled provisionally, as you said. If you want the smaller surface, it can move to the same dedicated-profile pattern.

## alyn MCP
- It stays `trust: untrusted`. `sampling` is not set, and the reference says sampling is on by default with limits, so a server could request completions on your model budget.
- Proposed (not applied): `mcp_servers.alyn.sampling.enabled: false`. No data-exposure review is needed to turn it off; enabling it is the thing that would need one.
- The plan also says no remote MCP in production. The entry points at a remote service, so consider disabling it entirely.

## memory.write_approval and skills.write_approval
- `tools/write_approval.py`: when true, memory writes (MEMORY.md and USER.md) and skill writes (SKILL.md and files) are staged under `<home>\pending\{memory,skills}` for review instead of being committed. In an interactive CLI memory asks inline; elsewhere it stages. Review with `/memory pending` and `/skills pending`.
- It covers the foreground agent and `background_review` forks. The Wiki Steward writes through its own `wiki` MCP tools, not these paths, so it is unaffected.
- Prompt volume is low: only actual memory and skill writes, which are rare.
- Effect on me: my `memory` and `skill_manage` calls would stage and need your approval before they take effect.
- Recommendation: turn both on. Proposed (not applied): `memory.write_approval: true`, `skills.write_approval: true`.

## Checkpoints
- Opt-in snapshot of the working directory once per turn before the first `write_file`, `patch` or destructive terminal command; `/rollback` restores. Shadow store `<home>\checkpoints\store` (shared git repo, deduplicated across projects) sits on C:, not F:.
- Defaults are already bounded: 20 snapshots per project, 500 MB total (oldest dropped round-robin), files over 10 MB skipped, 7-day retention, auto-prune.
- It reads the working directory (including the Btrfs volumes) to hash it, and writes only to C:. C: has 42 GB free (82% used).
- Limits: terminal commands are not undone unless flagged destructive; the Windows destructive spellings are missing (the earlier RFC's S4).
- Recommendation: enable with a lower cap, for example `enabled: true`, `max_total_size_mb: 200`, `max_snapshots: 10`. Proposed, not applied.

## Approval mode
`approvals.mode: smart` is unchanged. Hard deny rules were not reviewed.

## SearXNG at 127.0.0.1:8888
- Running (process `searx\webapp.py` from `C:\Users\Protagonist\Apps\searxng-windows`), bound to loopback, `/healthz` 200, `format=json` 200, limiter off. A sample query returned 36 results from brave (23) and google cse (20); `wikidata` reported "too many requests".
- Your stated preference (DuckDuckGo and Google CSE over Brave) is not what the live settings say. `settings.yml` disables every `duckduckgo` engine (the shipped example does too, and today's log shows DuckDuckGo CAPTCHA/disconnect warnings), enables `brave`, and sets no weights. I changed nothing. Decision for you: re-enable DuckDuckGo anyway (it may CAPTCHA), or accept Google CSE plus Brave, or set weights.

## Why `web:` is empty and `web_extract` fails
- `config.yaml` has no `web:` section, and the Hermes home has no `SEARXNG_URL` (that variable exists only in the Odysseus `.env`). So Hermes never learned about SearXNG.
- With nothing pinned, Hermes autodetects (`tools/web_tools.py::_autodetect_backend`). Running its own resolver read-only against this home gave autodetect = `openai-native` (available because a Codex login exists), search = `perplexity`, extract = `firecrawl`, keyless = `firecrawl`. In the session I saw, `web_extract` landed on `openai-native`, which is search-only, hence "cannot extract URL content".
- The keyless and Nous-gateway routes send queries and URLs to third-party services, which conflicts with the local-first rule.
- No Hermes web backend extracts locally: the extractors are Exa, Firecrawl, Keenable, Parallel and Tavily, all external. SearXNG searches only, by design.

## Proposed fix (staged, not applied)
1. Hermes `.env`: `SEARXNG_URL=http://127.0.0.1:8888`.
2. `config.yaml`: `web.search_backend: searxng`, `web.extract_backend: odysseus-local-extract`.
3. Copy `integrations/hermes-mode/plugins/odysseus-local-extract` into the home's `plugins/` and add it to `plugins.enabled`.
4. Restart the gateway (yours), then test one search and one extract.

`odysseus-local-extract` is new, own code, extract only. It fetches a page from this machine and returns readable text. It reuses Hermes' own private-address check (and re-checks every redirect), accepts http/https only, caps pages at 2 MB, has no JavaScript, and sends nothing to any third party. It is not installed or enabled. Tests: 7 pass (`tests/hermes_local_extract`, run with the Hermes runtime), including refusal of loopback, a redirect to a refused address, size and content-type limits, and per-URL errors in input order. A live fetch of `example.com` returned the page and `127.0.0.1:8888` was refused.

Rollback: remove the three config lines and the plugin folder, restart.
