# Companion bridge

A thin, additive layer so a LAN client (e.g. a phone) can discover what an
Odysseus server offers and pair to it, without duplicating any LLM logic.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/companion/ping` | session or token | cheap, auth-validated health check |
| GET | `/api/companion/info` | session or token | server identity + capability flags |
| GET | `/api/companion/models` | session or token | the **caller's own** model endpoints |
| GET | `/api/companion/pair` | **admin cookie** | pairing page (a form; never mints) |
| POST | `/api/companion/pair` | **admin cookie** | mint a one-time pairing token (`?format=json` for an in-app screen) |
| POST | `/api/companion/research/start` | session or token | launch a Deep Research run (the caller's own) |
| GET | `/api/companion/research/active` | session or token | the caller's currently-running runs |
| GET | `/api/companion/research/stream/{id}` | session or token | SSE progress for one run |
| POST | `/api/companion/research/cancel/{id}` | session or token | cancel one of the caller's runs |
| POST | `/api/companion/research/result/{id}` | session or token | read a run's report + sources (no clear) |

`/models` scopes to the caller's real owner plus legacy null-owner shared rows
(same rule as `owner_filter`) and never returns API-key material.

## Deep Research launcher

`/api/companion/research/*` mirrors the stock `/api/research/*` endpoints but
re-scopes every run to the token's **real owner** (`token_owner`). The stock
routes resolve a bearer caller to the sandboxed pseudo-user `api`, so a run
started there would be owned by `api` — invisible in the owner's web-UI library
and gated by `api`'s privileges. Ownership is enforced on every read/cancel
(`research_owns`, a 404-not-403 gate), so a caller only ever touches their own
runs. No extra `companion` scope is required: research is a chat-class generation
capability and is mounted only when the app passes a `research_handler`.

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
