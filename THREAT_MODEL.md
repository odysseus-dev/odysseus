# Threat Model

Odysseus is a **self-hosted AI workspace with privileged local access**. This document states the trust boundary so contributors can reason about security decisions without reading through the full auth and middleware stack.

## Trust Boundary

Odysseus is designed for **trusted users on a private network**, not public exposure. The README describes it as "treat it like an admin console" — that framing is accurate. A logged-in admin can execute shell commands, read and write files, send email, and control model serving. This is intentional. The threat model does not try to prevent admins from doing these things. It does try to prevent:

- Unauthenticated access
- Non-admins reaching admin-only capabilities
- The AI agent acting on instructions injected through untrusted content (web results, emails, fetched pages, memories)
- Internal services (ChromaDB, Ollama, SearXNG, etc.) being reachable from outside the host

## Roles and Capabilities

| Capability | Admin | Non-admin (default) |
|---|---|---|
| Chat with agent | ✓ | ✓ |
| Browser tool | ✓ | ✓ |
| Documents | ✓ | ✓ |
| Research mode | ✓ | ✓ |
| Image generation | ✓ | ✓ |
| Memory management | ✓ | ✓ |
| Shell / Python execution | ✓ | ✗ |
| File read / write | ✓ | ✗ |
| Email send / read | ✓ | ✗ |
| MCP tools | ✓ | ✗ |
| Calendar management | ✓ | ✗ |
| Token / webhook management | ✓ | ✗ |
| Model serving | ✓ | ✗ |
| Vault | ✓ | ✗ |
| Settings | ✓ | ✗ |

Non-admin defaults are in `core/auth.py:DEFAULT_PRIVILEGES`. Tool enforcement is in `src/tool_security.py:NON_ADMIN_BLOCKED_TOOLS`. Any tool whose name starts with `mcp__` is also blocked for non-admins. Admins always get full access regardless of stored privilege values.

## Authentication

- **Sessions:** bcrypt passwords, 7-day session tokens stored atomically in `data/sessions.json` via `core/atomic_io.py`.
- **2FA:** TOTP with 8 single-use backup codes. Verified after password check, before session issuance.
- **Reserved usernames:** request sentinels and the Default/Local storage owner cannot be registered or renamed into. Defined in `core/auth.py:RESERVED_USERNAMES`.
  - `internal-tool` is security-critical: `core/middleware.py:require_admin` treats any request where `request.state.current_user == "internal-tool"` as the in-process tool loopback and grants admin unconditionally. A real account with that name would silently pass every `require_admin` check.
- **Orphan sessions:** `validate_token` re-checks that the user record still exists on every call. A deleted user's cookie is dropped on next request rather than continuing to authenticate.

## Internal Tool Loopback

Agent tool calls reach admin-gated HTTP routes over an in-process HTTP loopback. The mechanism:

1. At app startup, `core/middleware.py` generates a random `INTERNAL_TOOL_TOKEN` via `secrets.token_hex(32)`. It is never persisted and never sent to clients.
2. Loopback requests carry `X-Odysseus-Internal-Token: <token>` or have `request.state.current_user` already set to `"internal-tool"` by the auth middleware.
3. `require_admin` recognises either signal and grants access without checking the session user.

The agent may be running in a non-admin user's session, but tool dispatch first calls `src/tool_security.py:owner_is_admin_or_single_user` to verify the session owner is an admin before issuing any loopback call. Non-admin users cannot invoke admin tools even via the agent.

## Prompt-Injection Hardening

External content that reaches the LLM is treated as untrusted via `src/prompt_security.py`:

- `untrusted_context_message(label, content)` wraps the content in a `user`-role message with a header block instructing the model not to follow instructions inside it. Content goes in as data, not as a system instruction.
- `UNTRUSTED_CONTEXT_POLICY` is a system-prompt preamble that states the same policy at the top of every session where untrusted data may appear.

**Untrusted surfaces that must go through this wrapper:** web search results, fetched URLs, emails (read), saved memories, skill text, notes, and any tool output sourced from outside the server. Injecting untrusted content directly into the system role is a security bug.

### Agent Run Authority

Model output requests an action; it does not authorize one. `src/tool_capabilities.py` classifies each built-in tool's effects and result integrity, while `src/agent_run_policy.py` combines those fixed classifications with the thread's server-owned security mode:

- **Ask:** public and workspace observation can proceed, but private reads, writes, code execution, egress, external side effects, UI effects, admin changes, destructive actions, and unknown tools require an exact approval.
- **Sandbox (default):** code execution stays inside the workspace sandbox. Network egress, external side effects, admin changes, and destructive actions always require an exact approval. Once external untrusted context has influenced the run, any later high-impact action also requires approval.
- **Full access:** an admin or intentional single-user deployment may explicitly opt into direct execution with that user's normal OS permissions. This is never the default, and route, agent-loop, and dispatcher gates reject it for non-admin users.

An approval is an opaque, expiring, one-use server record bound to the owner, session, origin run, exact tool name and input, workspace, security mode, effect classification, and external-context state. The browser submits only the opaque approval ID and the user's approve/deny decision. On approval, the server executes its sealed copy before the next model turn; natural-language confirmation and a model-repeated or modified command carry no authority.

## Security Headers

`core/middleware.py:SecurityHeadersMiddleware` sets headers on every response:

- `X-Frame-Options: DENY` + `frame-ancestors 'none'` on all routes except tool-render iframes (which are sandboxed at the HTML level).
- `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer` everywhere.
- **CSP:** nonce-based `script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net`. `style-src 'unsafe-inline'` is intentionally kept — `static/index.html` ships inline `<style>` blocks and JS modules set `style=""` attributes at runtime. Inline styles do not execute script so the risk is visual-only. Removing this requires templating the HTML files and auditing all JS-set style attributes.

## Known Gaps

These are open, acknowledged, and contributor help is welcome:

1. **Linux sandbox portability and explicit Full Access.** Agent `bash`, Python, tmux, and detached background commands default to a Bubblewrap profile with a private network namespace, cleared environment, private temp/home, one writable workspace, credential-path overlays, read-only `.git` metadata, and generous per-process rlimits. Internet-enabled process execution is limited to the trusted HTTP(S) egress broker; raw container networking is never shared with either mode. Odysseus performs the actual capability probes under the service user before process execution. A failed probe blocks only process tools and never downgrades automatically. An administrator may temporarily enable **Full Access** only after a warning plus typed confirmation. Full Access grants the process the Odysseus operating-system user's filesystem view while retaining the sandbox's private PID/network policy; an already-running Full Access process retains its launch-time authority until it exits or is killed. The process boundary mounts a fresh procfs scoped to its private PID namespace. Hard workspace-disk quotas and aggregate agent-pool/per-instance CPU, memory, and PID ceilings are not established in this slice; current limits are per process and the aggregate design is tracked separately.

2. **SSRF via `/api/v1/chat` `base_url` parameter.** A chat-scoped API token can supply an arbitrary `base_url`; the server forwards the LLM request to that host without validating the scheme or address. PR #1039 fixes this.

3. **`src/search/` partial consolidation.** `src.search.core` and `src.search.providers` correctly alias `services.search` via `sys.modules` replacement. `analytics`, `cache`, `content`, `query`, and `ranking` are still independent copies that can drift. The SSRF regression tests in `tests/test_webhook_ssrf_resilience.py` test `src.webhook_manager` directly (separate from search), so the safety net there is intact. See #1058.

4. **Token scopes are coarse.** There is no way to grant a session a subset of the owning user's privileges. Companion/mobile tokens carry either `chat` or `admin` scope with no per-capability granularity.
