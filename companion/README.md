# Companion bridge

A thin, additive layer so a LAN client (e.g. a phone) can discover what an
Odysseus server offers and pair to it, without duplicating any LLM logic.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/companion/ping` | session or token | cheap, auth-validated health check |
| GET | `/api/companion/info` | session or token | server identity + capability flags |
| GET | `/api/companion/models` | session or token | the **caller's own** model endpoints |
| GET | `/api/companion/sessions` | session or token | the **caller's own** sessions, annotated with live run state |
| GET | `/api/companion/sessions/{id}/stream` | session or token | live SSE for one session (replay buffer + live) |
| POST | `/api/companion/sessions/{id}/stop` | session or token | stop/interrupt a running session |
| GET | `/api/companion/pair` | **admin cookie** | pairing page (a form; never mints) |
| POST | `/api/companion/pair` | **admin cookie** | mint a one-time pairing token (`?format=json` for an in-app screen) |

`/models` scopes to the caller's real owner plus legacy null-owner shared rows
(same rule as `owner_filter`) and never returns API-key material.

## Sessions (remote control)

The `sessions/*` endpoints are a thin, owner-scoped layer over the existing
detached-run machinery (`src/agent_runs.py`) that the desktop UI already uses —
no new LLM logic. A paired phone (mobile app under `companion/mobile/`) uses
them to remote-control the PC: list the owner's sessions, watch one stream live,
and stop it.

- **Owner scoping is mandatory.** All three resolve the caller's real owner via
  `token_owner` / `_verify_session_owner` (which reads the bearer token's owner,
  not the sandboxed `api` pseudo-user). `GET /sessions` returns an **empty** list
  for an unresolved owner — never the global set, because
  `get_sessions_for_user(None)` returns *all* sessions.
- **Streaming reuses `agent_runs.subscribe`**, so a phone connecting mid-run
  replays everything so far then streams live, and disconnecting the SSE does
  **not** kill the run (it's detached). `POST /sessions/{id}/stop` is the
  explicit interrupt.
- **Reaching it from outside the LAN:** the endpoints are address-agnostic — a
  client pairs with whatever host/port reaches the server. For access beyond the
  LAN, prefer a private tunnel (e.g. Tailscale) over exposing ports to the
  public internet; see `THREAT_MODEL.md`.

## Pairing CSRF posture

Minting happens **only on POST**. The session cookie is `SameSite=Lax`
(`routes/auth_routes.py`), so a browser will not send it on a cross-site POST —
the same protection `POST /api/tokens` relies on. A `GET` would be unsafe (Lax
cookies ride top-level GET navigations), so `GET /pair` only renders a form.
Minting invalidates the auth middleware's token cache, so a freshly minted token
works on the next request without a restart.

The pairing/scoping rules live in small, tested units (`token_owner`,
`owner_can_see`, `mint_pairing_token`, `pairing.*`) — see
`tests/test_companion_readonly.py` and `tests/test_companion_pairing.py`.
