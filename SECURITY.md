# Security Policy

Odysseus is a self-hosted AI workspace with privileged local capabilities. Please do not run it as a public, unauthenticated service.

## Supported Versions

Security fixes are handled on the default branch until formal releases are cut.

## Deployment Guidance

- Keep `AUTH_ENABLED=true` for any network-accessible deployment.
- Keep `LOCALHOST_BYPASS=false` outside local development.
- Set `SECURE_COOKIES=true` when Odysseus is served through HTTPS by a trusted reverse proxy or private access gateway.
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

## Encrypting Secrets At Rest (Optional)

By default, secret values (API keys, admin password seed, internal tokens, etc.) live in `.env` as plaintext. The file is gitignored, but any process that reads it (container exec, host backup, snapshot) reveals every secret. For deployments where that's a concern, Odysseus supports [SOPS](https://github.com/getsops/sops) with age keys to encrypt secrets at rest.

**Scope:** the built-in SOPS hook lives in the Docker entrypoint (`docker/entrypoint.sh`). Native startups (`uvicorn app:app` directly, systemd, launchd) do **not** automatically pick it up — they read `.env` / process environment as usual. Native users who want SOPS today have to wrap their own launch — `sops exec-env` takes the command as a single shell-string (not argv passthrough), so the working form is `sops exec-env --same-process secrets.env 'uvicorn app:app --host 0.0.0.0 --port 7000'`. The `--same-process` flag makes uvicorn the signal target so systemd/launchd stop signals reach it directly. A first-class native hook is out of scope for this PR. Cross-service secrets (e.g. `SEARXNG_SECRET`, consumed by the separate `searxng` container) are also out of scope here — see the issue tracker for the cross-service design.

This is **opt-in** — the feature is only active when an encrypted `secrets.env` is present at container start. Existing deployments behave exactly as before.

### Setup

1. Install [`age`](https://github.com/FiloSottile/age) on your host and generate a key:

   ```bash
   mkdir -p ~/.config/sops/age
   age-keygen -o ~/.config/sops/age/keys.txt
   age-keygen -y < ~/.config/sops/age/keys.txt   # prints your age public key
   ```

2. Paste the printed public key into `.sops.yaml` (replace the `age1REPLACE...` placeholder).

3. Copy the example, fill in real secret values, then encrypt in place:

   ```bash
   cp secrets.env.example secrets.env
   $EDITOR secrets.env           # fill in real values
   sops -e -i secrets.env        # encrypt in place — file is now safe to commit
   ```

   To edit later: `sops secrets.env` opens `$EDITOR` with decrypted contents and re-encrypts on save.

4. Provide the encrypted `secrets.env` AND the age **private** key to the container as runtime volume mounts (not baked into the image). Add this to `docker-compose.override.yml`:

   ```yaml
   services:
     odysseus:
       volumes:
         - ./secrets.env:/app/secrets.env:ro
         - ~/.config/sops/age/keys.txt:/run/secrets/sops-age-key:ro
       environment:
         - SOPS_AGE_KEY_FILE=/run/secrets/sops-age-key
   ```

At container start, the entrypoint runs `sops exec-env` so secrets enter the wrapped process as env vars JIT — plaintext never touches the container filesystem. `setup.py` (which seeds the admin user from `ODYSSEUS_ADMIN_PASSWORD`) runs inside the same wrap, so the seed reaches it. If `secrets.env` is present but **not** SOPS-encrypted, the entrypoint refuses to start (it's almost always a packaging mistake).

### What to put in `secrets.env` vs `.env`

Only true secrets belong in `secrets.env`. Non-secret configuration stays in `.env` because it benefits from plaintext diffs and PR review:

| `.env` (plaintext, gitignored) | `secrets.env` (SOPS-encrypted, mounted at runtime) |
|---|---|
| `APP_PORT`, `LLM_HOST`, `SEARXNG_INSTANCE`, `ALLOWED_ORIGINS`, `CHROMADB_BIND`, `LOCALHOST_BYPASS` | `OPENAI_API_KEY`, `ODYSSEUS_ADMIN_PASSWORD`, `ODYSSEUS_INTERNAL_TOKEN`, MCP OAuth client secrets, IMAP passwords |

`SEARXNG_SECRET` is intentionally absent: it's consumed by a different container (`searxng`), which the in-container `sops exec-env` cannot reach. See the issue tracker for the cross-service design.

To migrate: move only the secret lines out of your existing `.env` into `secrets.env`, encrypt, and delete those lines from `.env`.

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
