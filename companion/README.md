# Companion bridge

A thin, additive layer so a LAN client (e.g. a phone) can discover what an
Odysseus server offers and pair to it, without duplicating any LLM logic.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/companion/ping` | session or token | cheap, auth-validated health check |
| GET | `/api/companion/info` | session or token | server identity + capability flags |
| GET | `/api/companion/models` | session or token | the **caller's own** model endpoints |
| GET | `/api/companion/notes` | **`companion`** scope | the caller's own notes (read-only) |
| GET | `/api/companion/tasks` | **`companion`** scope | the caller's own scheduled tasks (read-only) |
| GET | `/api/companion/memory` | **`companion`** scope | the caller's own memories (read-only) |
| GET | `/api/companion/pair` | **admin cookie** | pairing page (a form; never mints) |
| POST | `/api/companion/pair` | **admin cookie** | mint a one-time pairing token (`?format=json` for an in-app screen) |

`/models` scopes to the caller's real owner plus legacy null-owner shared rows
(same rule as `owner_filter`) and never returns API-key material.

## Pairing CSRF posture

Minting happens **only on POST**. The session cookie is `SameSite=Lax`
(`routes/auth_routes.py`), so a browser will not send it on a cross-site POST —
the same protection `POST /api/tokens` relies on. A `GET` would be unsafe (Lax
cookies ride top-level GET navigations), so `GET /pair` only renders a form.
Minting invalidates the auth middleware's token cache, so a freshly minted token
works on the next request without a restart.

## The `companion` data scope

`notes` / `tasks` / `memory` expose private user data, so they require **more**
than a plain `chat` token: a bearer caller must carry the explicit `companion`
scope (a cookie session always may). A `chat`-scoped token can chat but cannot
read your notes or memory. Every view is owner-scoped (same `owner_filter` rule
as `/models`) and strictly read-only.

The pairing/scoping rules live in small, tested units (`token_owner`,
`owner_can_see`, `has_companion_scope`, `mint_pairing_token`, `pairing.*`) — see
`tests/test_companion_readonly.py`, `tests/test_companion_pairing.py`, and
`tests/test_companion_data.py`.
