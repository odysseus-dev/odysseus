# laya decision-engine integration

[laya](https://github.com/NandhaKishorM/laya) (Apache-2.0) is a fast, local,
non-autoregressive **"System 1" decision engine**: it answers *typed* questions
(`choice` / `score` / `noul` probability) about a piece of text in a single
forward pass (~33 ms), with calibrated confidence. It does **not** generate text,
call tools, or take actions — it only classifies.

Odysseus uses it (optionally) for read-only, advisory decisions:

- **Model routing** — small vs frontier model per request.
- **Prompt guardrails** — jailbreak / injection / secret-leak detection, on top of
  the existing prompt-level hardening in `src/prompt_security.py`.
- **Content moderation** — toxicity / harassment scoring.
- **Request triage** — intent / urgency / churn for assistant & email flows.

## Design

- **Sidecar, not in-process.** laya runs as a `laya-serve` container; Odysseus
  talks to it over HTTP. This keeps laya's heavy `torch`/`transformers` stack out
  of the Odysseus image and lets the engine scale/restart independently.
- **Off by default.** With `LAYA_ENABLED=false` (the default) Odysseus never
  contacts laya and behaves exactly as before.
- **Fail-open.** The service layer (`services/laya/`) never raises into a request
  handler: if laya is disabled, unreachable, slow, or returns something unusable,
  Odysseus falls back to its existing behavior. laya can only ever *add* a
  decision, never block the app by being absent.
- **No side effects → safe retries.** laya classifies text and returns
  probabilities, so transient failures (timeouts, 5xx) are retried; 4xx are not.
- **Admin-gated surface.** Operational endpoints (health now; monitoring later)
  require admin, like `/api/db/stats`.

## Enabling it

1. Start the sidecar with the overlay (internal-only; not published to the host):

   ```bash
   docker compose -f docker-compose.yml -f compose.laya.yaml up -d --build
   ```

2. Set a shared bearer token and turn the flag on in `.env`:

   ```bash
   LAYA_API_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(32))")
   LAYA_ENABLED=true
   ```

3. Confirm connectivity (admin session required):

   ```bash
   curl -s localhost:7000/api/laya/health   # {"enabled":true,"reachable":true,...}
   ```

First boot downloads checkpoints from Hugging Face into the `laya-hf-cache`
volume, so the sidecar's healthcheck has a long start period.

## Configuration

| Env var | Read by | Default | Meaning |
|---|---|---|---|
| `LAYA_ENABLED` | Odysseus | `false` | Master switch. |
| `LAYA_URL` | Odysseus | `http://127.0.0.1:8000` | Sidecar base URL (`http://laya:8000` under compose). |
| `LAYA_API_KEY` | both | – | Bearer token; set the same value on both. |
| `LAYA_TIMEOUT` | Odysseus | `5.0` | Per-request timeout (s). |
| `LAYA_RETRIES` | Odysseus | `2` | Transient-only retries. |
| `LAYA_PRELOAD` / `LAYA_MODELS` / `LAYA_DEVICE` / `LAYA_MAX_CONCURRENT` / `LAYA_REVISION` | laya sidecar | see `compose.laya.yaml` | Engine tuning; pin weights with `LAYA_REVISION`. |

## Capabilities & execution loop

Four read-only, advisory capabilities, all on `LayaService`: `route`, `guard`,
`moderate`, `triage`. Each runs the same loop: build a typed question set
(`questions.py`) → call the sidecar with bounded transient retry → **deterministic
validation** (`validate.py`: structure + range + offered-option checks; anything
off raises and the call is treated as *no decision*) → interpret
(`decisions.py`) → **audit** (`LayaRun` table) → return a `LayaDecision`. A caller
acts only when `decision.ok and decision.acted`; in shadow mode `acted` is always
False.

## Audit log

Every attempt writes a `laya_runs` row: capability, owner, shadow/acted,
reachable, model, decision JSON, confidence, latency, error — plus a **redacted**
input (short preview + length + SHA-256; the raw text is never stored). Writes are
best-effort and never break a request. Thresholds and redaction live in
`policy.py` (`LAYA_ROUTE_MIN_CONFIDENCE`, `LAYA_GUARD_THRESHOLD`,
`LAYA_MODERATION_THRESHOLD`).

## Status

- **M1 (done):** sidecar + overlay, `services/laya/` client+service, admin-gated
  `/api/laya/health`. No request-path behavior changes.
- **M2 (done):** decision service (`route/guard/moderate/triage`) + deterministic
  validation + `LayaRun` audit table; **`route` wired into `/api/chat` in shadow
  mode** (fire-and-forget, observe-only, zero added latency).
- **M3 (done):** admin monitoring screen at **`/laya/admin`** + admin API
  (`/api/laya/status|runs|pause|resume|run`); runtime pause switch.
- **M4 (done):** first real activation — the **prompt guardrail on the chat path**,
  behind `LAYA_GUARD_MODE`. Wired into both `/api/chat` and `/api/chat_stream`
  (no bypass). Still off by default.
- **Next:** activate `moderate`, then design a tier→model mapping so `route` can
  graduate from shadow to advisory acting.

## Guard activation (`LAYA_GUARD_MODE`)

Gates the prompt guardrail (jailbreak / injection / prompt-leak) on the chat path:

| Mode | Behavior | Blocks users? |
|---|---|---|
| `off` (default) | not run — zero behavior change | no |
| `warn` | runs on each message, audits flagged ones (`acted=true`), logs a warning | **no** |
| `block` | additionally **rejects** a flagged message with HTTP 400 and a clear message | yes (opt-in) |

**Fail-open in every mode:** a disabled, paused, slow, or erroring laya never
blocks a chat — `block` only rejects on a genuine flagged verdict. Blocks are
never silent (the user gets a message) and are always audited + visible in
`/laya/admin`. `block` is the project's one **fail-closed** switch and is strictly
opt-in. When active, each chat message costs one laya round-trip (bounded by
`LAYA_TIMEOUT`, fail-open on timeout), so size the sidecar / timeout accordingly.
