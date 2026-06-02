# Odysseus Mobile companion bridge

A thin, additive LAN layer so the [`odysseus-mobile`](https://github.com/mahdi-salmanzade/odysseus-mobile)
Expo app can pair to this server and drive it over your local network. It adds
**no** new LLM logic — the phone authenticates with a standard `ody_` API token
and uses the existing chat/session endpoints.

## Endpoints (`/api/companion/*`)

Every route accepts a bearer `ody_` token or an admin/owner cookie session
(except `/pair`, which is browser-only). Reads and writes are scoped to the
token's real owner (plus null-owner shared rows), not the sandboxed `api`
pseudo-user — that scoping is the whole reason the bridge exists.

| Method | Path | Purpose |
|---|---|---|
| GET | `/ping` | Pairing health check — confirms host/port/token |
| GET | `/info` | Server identity, capability flags, and an endpoint map |
| GET | `/models` | **Owner-scoped** LLM models (read-only; never returns key material) |
| GET | `/notes` | List the owner's notes |
| POST | `/notes` | Create a note (text or checklist) |
| DELETE | `/notes/{note_id}` | Delete a note |
| POST | `/notes/{note_id}/pin` | Toggle a note's pinned flag |
| POST | `/notes/{note_id}/items/{index}/toggle` | Toggle one checklist item |
| GET | `/tasks` | List the owner's scheduled tasks |
| GET | `/memory` | List the owner's memories |
| POST | `/memory` | Add a memory (`text`, optional `category`) |
| DELETE | `/memory/{memory_id}` | Delete a memory |
| GET | `/pair` | **Admin cookie only** — browser pairing page: mints a token + shows a QR |

> **Models:** use `/api/companion/models`, **not** the stock `/api/models`.
> The latter scopes to `get_current_user`, which for a bearer token is the
> sandboxed `api` pseudo-user that owns no endpoints — so it comes back empty.
> The companion route scopes to the token's real owner instead.

For chat and sessions the phone uses the stock API directly, with
`Authorization: Bearer ody_…`:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/sessions` | List sessions |
| POST | `/api/session` | Create a session |
| GET | `/api/history/{session_id}` | Load a session's message history |
| POST | `/api/chat_stream` | Streaming chat (SSE) |
| POST | `/api/chat/stop/{session_id}` | Stop an in-flight stream |

## Pairing

**Option A — browser (recommended, instant):** while logged in as admin, open

```
http://localhost:<port>/api/companion/pair
```

…where `<port>` is **7000** for the Docker default, or **7860** for the native
macOS quick start (`start-macos.sh` — macOS AirPlay Receiver holds 7000). Scan
the QR with the app. The token is minted in-process and the auth cache is
invalidated immediately, so it works on the next request — no restart.

**Option B — terminal:**

```bash
python scripts/pair_mobile.py
```

Prints host/port/token + an ASCII QR. If the server is already running, a token
minted this way isn't recognized until the token cache refreshes (restart the
server) — prefer Option A in that case.

## Exposing on the LAN

The phone must reach this server, so bind to the LAN (not just loopback):

- Docker: set `APP_BIND=0.0.0.0` in `.env`, then `docker compose up -d`.
- Native: launch with `--host 0.0.0.0`.

Keep `AUTH_ENABLED=true`. The pairing token is a real credential — revoke it
anytime in **Settings → API tokens**. For anything beyond a trusted home LAN,
put the server behind HTTPS (see the main README).
