# Deploying Odysseus on Railway (OpenRouter backend + clawagent tab)

This guide covers running Odysseus as a single Railway service (one container
from the repo `Dockerfile`), using OpenRouter as the only model provider, and
enabling the built-in **clawagent** tab.

Railway runs **one container per service** from a Dockerfile/image — it does
**not** run `docker compose`. The bundled Compose stack (app, ChromaDB,
SearXNG, ntfy) must therefore be split into separate Railway services (Tier 2)
or left out (Tier 1).

> Ship Tier 1 first, confirm a real HTTP 200, then add Tier 2 services. Isolate
> failures per layer instead of deploying everything at once.

---

## What the repo already does for Railway

These were verified against this repo so you don't have to patch them:

- **No `VOLUME` directive** in the `Dockerfile`. Railway rejects Dockerfile
  `VOLUME` lines; persistence is an attached Railway Volume (below). Nothing to
  strip.
- **Port binding honors `$PORT`.** The image `CMD` is
  `sh -c "exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-7000}"`, so it
  binds `0.0.0.0` on Railway's injected `$PORT` automatically and falls back to
  `7000` for local `docker run` / Compose. No start-command override is
  required. (`APP_BIND`/`APP_PORT` are Compose host-publish vars and do **not**
  affect Railway routing — don't rely on them.)

---

## Tier 1 — app only (must boot first)

**Service `odysseus`:** Source = this repo (Dockerfile build).

**Volume:** attach a Railway Volume mounted at `/app/data`. Mandatory — `data/`
holds `app.db`, `settings.json`, `auth.json`, uploads, and the chroma dir.
Without it, the admin account and all settings reset on every redeploy.

**Start command:** leave default (the image already binds `$PORT`). If you
prefer an explicit override, `uvicorn app:app --host 0.0.0.0 --port $PORT`.

**Environment variables:**

```
AUTH_ENABLED=true
LOCALHOST_BYPASS=false
SECURE_COOKIES=true
ALLOWED_ORIGINS=https://<your-app>.up.railway.app
```

`SECURE_COOKIES=true` because Railway terminates HTTPS at its edge and the
container speaks plain HTTP (the "served through HTTPS by a trusted proxy"
case). `ALLOWED_ORIGINS` must be the exact Railway public origin.

Leave `DATABASE_URL` at its default (SQLite on the volume) unless you have
confirmed Postgres works. SQLite-on-volume is the safe default.

**First login:** the temporary admin password is printed in the deploy logs
(`admin` user unless `ODYSSEUS_ADMIN_USER` is set). Log in, change it
immediately in Settings, and disable open signup in `data/auth.json`.

**Acceptance:**
- `GET https://<your-app>.up.railway.app/login` → HTTP 200.
- Admin login works and the temp password is rotated.

---

## OpenRouter backend (no local models)

After first login: **Settings → Models → add provider** (OpenAI-compatible):

- Base URL: `https://openrouter.ai/api/v1`
- API key: your OpenRouter key
- Add the model slugs you route.

**Acceptance:** one chat message returns a real completion; provider status
green.

Expected degraded at this point: Deep Research (needs SearXNG) and vector
memory (needs ChromaDB). Memory runs DEGRADED rather than crashing — fine for
Tier 1.

---

## Tier 2 — memory + research (add only after Tier 1 passes)

Two more Railway services on **private** networking (no public domain):

1. **chromadb** — image `chromadb/chroma`, attach a small volume.
2. **searxng** — image `searxng/searxng`. Compose's secret-init step does not
   run on Railway, so set `SEARXNG_SECRET` yourself and provide a settings file
   via env/volume.

Point `odysseus` at them with Railway internal hostnames:

```
CHROMADB_HOST=<chroma-service>.railway.internal
CHROMADB_PORT=8000
SEARXNG_INSTANCE=http://<searxng-service>.railway.internal:8080
```

**Acceptance:** odysseus logs show ChromaDB connected (no DEGRADED line); a Deep
Research run returns real sources.

---

## clawagent tab

The fork ships a built-in **clawagent** sidebar entry that frames the live
clawagent Mission Control at `/clawagent`.

### Odysseus side (done in code; you just set the env var)

Set on the `odysseus` service:

```
CLAWAGENT_URL=https://<clawagent-mission-control>.up.railway.app
```

- Must be an `http(s)` URL. Only this exact origin is added to the `/clawagent`
  page's CSP `frame-src`; the rest of the app keeps `frame-src 'self'`.
- When unset, the tab shows a setup notice instead of an iframe.

### clawagent side (you must configure its backend)

The clawagent app must permit being framed by the Odysseus origin. On
clawagent's Express backend, set:

```
Content-Security-Policy: frame-ancestors https://<your-odysseus>.up.railway.app
```

and remove any `X-Frame-Options: DENY`/`SAMEORIGIN`. Cross-origin framing
partitions storage/cookies, so confirm any clawagent auth/session it relies on
tolerates the partitioned frame context. Direct `fetch()` calls from inside the
frame (e.g. to `api.anthropic.com` with a user key) are not cookie-bound and
keep working.

**Acceptance:** the clawagent tab renders the live Mission Control inside
Odysseus and an in-frame network request returns HTTP 200 (not blocked by
CSP/X-Frame-Options).

### Developing clawagent from the Odysseus agent

Shell/file tools are admin-gated. As the admin account, from the agent shell
tool `git clone` `jmiller18899-lab/clawagent` into the workspace; the agent can
then edit, run, and open PRs against it. Keep the working copy under `/app/data`
if you want edits to survive redeploys, or treat it as ephemeral scratch and
push to GitHub as the source of truth. Optionally register a clawagent MCP
server via admin MCP management for direct tool access.

**Acceptance:** the agent produces a real commit SHA or PR URL against
`jmiller18899-lab/clawagent`.

---

## Security checklist

- `AUTH_ENABLED=true`, `LOCALHOST_BYPASS=false`, `SECURE_COOKIES=true` — set,
  not assumed.
- Only the `odysseus` web entrypoint is public. ChromaDB, SearXNG, ntfy, and
  clawagent internal ports stay on Railway private networking.
- Open signup disabled in `data/auth.json`; only your account is admin; no demo
  accounts.
- No `.env`, `data/`, keys, or tokens committed (`git status --short` before any
  push). Rotate any key ever pasted into a chat/screenshot/log.
- Consider Cloudflare Access / Tailscale in front of the Railway domain for a
  private access layer.
