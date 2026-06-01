# Security Policy

Odysseus is a self-hosted AI workspace with privileged local capabilities. Please do not run it as a public, unauthenticated service.

## Supported Versions

Security fixes are handled on the default branch until formal releases are cut.

## Deployment Guidance

- Keep `AUTH_ENABLED=true`.
- Use HTTPS when exposing the app beyond localhost.
- Put the app behind a trusted reverse proxy or private network.
- Protect `.env`, `data/`, logs, uploaded files, generated media, and database files.
- Disable open signup unless you intentionally want new accounts.
- Keep demo/test users non-admin, and remove them entirely on serious deployments.
- Give admin accounts strong passwords and enable 2FA where possible.
- Leave high-risk agent tools restricted to admins: shell, Python, file read/write, email send/read, MCP, app API, task/skill/memory management, settings, tokens, and model serving.
- Rotate API keys, webhook secrets, and Odysseus API tokens if they appear in logs, screenshots, demos, or shared chats.
- Treat shell, model-serving, MCP, email, calendar, and vault features as privileged admin functionality.

## Admin Blast Radius
Odysseus's agent tools are powerful by design. If an admin account is compromised (or if a rogue/hallucinating model is given unrestricted access), the consequences include:
- **Full Host Access**: The `bash` and `python` tools run code as the OS user executing the Odysseus process.
- **Arbitrary File Access**: The agent can read, modify, or delete any file the OS user can access (e.g., SSH keys, other applications' data, `/etc`).
- **Network Pivoting**: The agent can use shell tools (e.g., `curl`, `nmap`) to scan or attack other hosts on your internal network.

## Hardening Guide
To run Odysseus with least-privilege principles and mitigate the above risks:
1. **Unprivileged Docker**: Always run Odysseus in a Docker container rather than bare metal.
2. **Read-Only Mounts**: If you mount host directories for the agent to read, mount them as `ro` (read-only).
3. **Drop Capabilities**: Use `cap_drop: - ALL` in your Docker Compose file to strip root capabilities from the container.
4. **Disable High-Risk Tools**: If you only use Odysseus for chat or simple web research, go to Admin Settings -> Tools and disable `bash`, `python`, `write_file`, and MCP servers.
5. **Network Isolation**: Put Odysseus on a separate VLAN or use Docker networks to prevent it from reaching sensitive internal services.

## Publishing A Fork

Before pushing a public fork, run:

```bash
git status --short
git check-ignore -v .env data/auth.json data/app.db logs/compound.log odysseus.db
git grep -n -I -E "(sk-[A-Za-z0-9_-]{20,}|xox[baprs]-|AIza[0-9A-Za-z_-]{20,}|Bearer [A-Za-z0-9._~+/-]{20,})" -- . ':!static/lib/**' ':!package-lock.json'
```

Only `.env.example`, docs, source, tests, and static assets should be committed. Never commit live `data/` contents, local databases, uploaded files, generated media, logs, backups, API keys, password hashes, or personal documents.

## Reporting

Please report vulnerabilities privately via GitHub security advisories if available, or by opening a minimal issue that does not disclose exploit details.
