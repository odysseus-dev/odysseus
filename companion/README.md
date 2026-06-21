# Companion bridge

A thin, additive layer so a LAN client (e.g. a phone) can discover what an
Odysseus server offers and pair to it, without duplicating any LLM logic.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/companion/ping` | session or token | cheap, auth-validated health check |
| GET | `/api/companion/info` | session or token | server identity + capability flags |
| GET | `/api/companion/models` | session or token | the **caller's own** model endpoints |
| POST | `/api/companion/messages/send` | session or token | queue an iMessage/SMS for a paired phone to send |
| GET | `/api/companion/messages/outbox` | session or token | paired phone polls owner-scoped queued messages |
| POST | `/api/companion/messages/{id}/status` | session or token | paired phone marks a queued message `sent`/`failed` |
| POST | `/api/companion/messages/inbound` | session or token | paired phone/Shortcut records an inbound message |
| GET | `/api/companion/pair` | **admin cookie** | pairing page (a form; never mints) |
| POST | `/api/companion/pair` | **admin cookie** | mint a one-time pairing token (`?format=json` for an in-app screen) |

`/models` scopes to the caller's real owner plus legacy null-owner shared rows
(same rule as `owner_filter`) and never returns API-key material.

## iMessage/SMS without a Mac

Apple's local Messages automation APIs are only available on macOS, so Docker,
Linux, Windows, and cloud Odysseus servers cannot send iMessage directly. The
companion message endpoints use a cross-platform relay instead:

1. Odysseus queues an outbound message in `/api/companion/messages/send`.
2. A paired iPhone/iPad companion (or Shortcut) polls
   `/api/companion/messages/outbox` with its chat-scoped token.
3. The device sends via Messages and acknowledges the item through
   `/api/companion/messages/{id}/status`.
4. The device can post received messages to `/api/companion/messages/inbound`.

All rows are owner-scoped to the session user or API token owner, so a paired
phone only sees its own queued messages.

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
