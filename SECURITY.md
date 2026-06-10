# Security Policy

Odysseus is a self-hosted AI workspace with privileged local capabilities. Please do not run it as a public, unauthenticated service.

## Supported Versions

Security fixes are handled on the default branch until formal releases are cut.

## Deployment Guidance

- Keep `AUTH_ENABLED=true` for any network-accessible deployment.
- Keep `LOCALHOST_BYPASS=false` outside local development.
- Set `SECURE_COOKIES=true` when Odysseus is served through HTTPS by a trusted reverse proxy or private access gateway.
- Back up `data/.app_key` alongside `data/app.db` — it is the Fernet master key for all encrypted database secrets. See README for the `APP_KEY` env var alternative.
- Use HTTPS when exposing the app beyond localhost.
- Put the authenticated Odysseus web/API entrypoint behind a trusted reverse proxy or private access layer such as Cloudflare Access, Tailscale, or a VPN.
- Keep ChromaDB, SearXNG, ntfy, Ollama, vLLM, llama.cpp, databases, and raw model/provider APIs internal-only.
- Protect `.env`, `data/`, `logs/`, uploads, generated media, backups, auth/session files, database files, API keys, and model/provider tokens.
- Disable open signup unless you intentionally want new accounts.
- Keep demo/test users non-admin, and remove them entirely on serious deployments.
- Give admin accounts strong passwords and enable 2FA where possible.
- Leave high-risk agent tools restricted to admins: shell, Python, file read/write, email send/read, MCP, app API, task/skill/memory management, settings, tokens, and model serving.
- Rotate API keys, webhook secrets, and Odysseus API tokens if they appear in logs, screenshots, demos, or shared chats.
- Treat shell, model-serving, MCP, email, calendar, and vault features as privileged admin functionality.
- Common internal-only ports are Odysseus `7000`, SearXNG `8080`, ntfy `8091`, ChromaDB `8100`, Ollama `11434`, and local model/provider APIs such as `8000-8020`.

## Password Reset Token Delivery (COMP-P5-001 / OBS-P5-01 — Design Decision)

Odysseus has no built-in email (SMTP) for password reset. The reset token is
instead printed directly to the **server console** (stdout) by design. The
operator reads it there and pastes it into the browser reset form. The audit
log records that a reset was requested but intentionally **omits the token** so
it is never forwarded to log aggregators.

**If you ship server stdout/stderr to a log-aggregation system** (Splunk,
Datadog, Loki, CloudWatch, etc.) you should disable the console-delivery
channel and implement your own out-of-band mechanism:

```
RESET_TOKEN_TO_CONSOLE=false
```

When this variable is set to `false`, the token is **not** printed to the
console. You must then deliver it through your own channel (e.g. a webhook,
email relay, or pager integration) — without a delivery channel the reset
flow will not complete. The `audit_event` record `password_reset_request`
continues to fire (without the token value) so you know a reset was triggered.

The default value of `RESET_TOKEN_TO_CONSOLE` is `true` (console delivery
active), which is correct for the single-operator / local-network deployment
model Odysseus targets.

## Sensitive File Storage

The following files in `data/` hold secrets and must be kept private. They are excluded from version control via `.gitignore`.

| File | Contents | Required mode |
|---|---|---|
| `data/sessions.json` | Plaintext session bearer tokens (64 hex chars, 7-day TTL). Any holder of a token has full authenticated access. | 0600 |
| `data/auth.json` | User accounts and bcrypt password hashes. | 0600 |
| `data/.app_key` | Fernet master key used to encrypt IMAP/SMTP passwords at rest. | 0600 |
| `data/vault.json` | Vaultwarden `BW_SESSION` key. Grants access to the configured Bitwarden vault. | 0600 |

Never commit these files to version control. If any token is exposed, rotate immediately: delete `data/sessions.json` to invalidate all sessions, regenerate `data/.app_key` and re-encrypt stored credentials, and rotate the Bitwarden session.

## Publishing A Fork

Before pushing a public fork, run:

```bash
git status --short
git check-ignore -v .env data/auth.json data/app.db logs/compound.log odysseus.db
git grep -n -I -E "(sk-[A-Za-z0-9_-]{20,}|xox[baprs]-|AIza[0-9A-Za-z_-]{20,}|Bearer [A-Za-z0-9._~+/-]{20,})" -- . ':!static/lib/**' ':!package-lock.json'
```

Only `.env.example`, docs, source, tests, and static assets should be committed. Never commit live `.env` values, `data/` contents, local databases, uploaded files, generated media, logs, backups, auth/session files, API keys, model/provider tokens, password hashes, or personal documents.

## Reporting

Please report vulnerabilities privately via GitHub security advisories if available, or by opening a minimal issue that does not disclose exploit details.
