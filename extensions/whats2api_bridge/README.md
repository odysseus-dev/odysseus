# Whats2API Bridge — OXS extension

> Persist [whats2api.com](https://whats2api.com) (hosted WhatsApp gateway)
> inbound events in Odysseus and read them back via MCP. The agent drives
> WhatsApp through the **remote** `mcp__whats2api__*` tools; this extension
> is a **read layer + cache** on top.

This extension is bundled with Odysseus. Drop-in. No `pip install`. No
container rebuilds. Settings → Extensions → **Whats2API Bridge** → enable.

## What you get

- **4 read tools** the agent calls as `mcp__oxs_whats2api_bridge__<name>`
  to query the local cache:
  - `list_messages` — recent inbound/outbound, filter by connection, jid,
    direction, since, text
  - `get_message` — single message by id, full raw_json
  - `list_events` — every whats2api event captured by the webhook
  - `list_connections_local` — cached connection states from `connection.update`

- **Inbound webhook** at `/api/ext/whats2api_bridge/webhook/<token>`
  that consumes the whats2api event fan-out (`messages.upsert`,
  `connection.update`, `groups.upsert`, `presence.update`, `call`, ...),
  persists everything to local SQLite, and fires the corresponding
  `whats2api.*` event on the OXS bus.

- **Admin routes** for the dashboard, settings, and CRUD over the local
  tables (messages, events, connections).

- **Panel + static UI** at `/ext-static/whats2api_bridge/index.html` —
  a dark-mode dashboard showing recent inbound events and a status
  endpoint that does not leak the token.

**For sending, listing, creating, starting, stopping connections, and
all other write actions, use the remote `mcp__whats2api__*` tools** that
come from connecting `https://api.whats2api.com/mcp` as an MCP server in
Odysseus. This extension does not re-implement them.

## 30-second setup

1. Sign up at <https://whats2api.com> and generate an API token in
   the dashboard (Dashboard → API Tokens). The token is shown only
   once.
2. In Odysseus, go to **Settings → MCP Servers** and add the whats2api
   MCP server: `https://api.whats2api.com/mcp/<your_token>`. The 24
   `mcp__whats2api__*` tools appear immediately.
3. Enable the **Whats2API Bridge** OXS extension. Set its `api_token`
   in settings (Settings → Extensions → Whats2API Bridge). Enable.
4. The admin UI shows the inbound webhook URL — copy it. In the
   whats2api dashboard, configure your connection's *Webhook* to that
   URL (HTTP POST, all events).
5. Use the remote tools to create and start a connection:
   ```
   mcp__whats2api__create_connection({...})
   mcp__whats2api__start_connection({"connection_id": "..."})
   ```
6. Scan the QR with the WhatsApp app.

That's it. Inbound messages now appear as `whats2api.message.received`
events on the OXS bus, get persisted in
`whats2api_bridge__messages`, and are queryable via
`mcp__oxs_whats2api_bridge__list_messages`.

## Architecture

```
┌──────────────┐  HTTPS  ┌───────────────┐
│ WhatsApp app │◄───────►│ whats2api.com │
└──────────────┘         └──────┬────────┘
                                │ webhook
                                ▼
              ┌──────────────────────────────────┐
              │ /api/ext/whats2api_bridge/      │
              │         webhook/<token>          │
              │ (OXS inbound, HMAC + rate-limit) │
              └──────────────┬───────────────────┘
                             │ _persist_message / _persist_event
              ┌──────────────▼──────────────┐
              │ SQLite:                     │
              │  whats2api_bridge__messages │
              │  whats2api_bridge__events   │
              │  whats2api_bridge__connections │
              └──────────────┬──────────────┘
                             │ 4 read tools
              ┌──────────────▼──────────────┐
              │ mcp__oxs_whats2api_bridge__* │
              │  list_messages / get_message│
              │  list_events                │
              │  list_connections_local     │
              └─────────────────────────────┘

Writes / live state go through the REMOTE MCP (not this extension):
   mcp__whats2api__send_message(...)
   mcp__whats2api__list_connections(...)
   mcp__whats2api__create_connection(...)
   ... (24 tools, source of truth: api.whats2api.com)
```

## Why split the write/read?

The remote `mcp__whats2api__*` server is the source of truth — same
auth, same rate limits, same data, no extra layer. Wrapping each call
in an OXS in-process tool would just duplicate the remote surface and
add an extra token. The bridge exists for what the remote cannot do:
**persist** inbound events locally, expose a queryable history, and
fire OXS events so other agents can react to incoming messages.

## Trust model

The extension is **in-process** with the rest of Odysseus and:

- declares `network_outbound: ["https://api.whats2api.com"]` so it
  cannot reach any other host (used only for connection-state cache
  refresh on `connection.update`);
- stores the API token under `oxs.extensions.whats2api_bridge.*`
  (admin-only writes; not exposed by the public status route);
- uses the standard OXS webhook (32-byte URL token + per-extension
  HMAC secret + 10 req/s rate limit);
- never persists message bodies unless `log_inbound_events` is true;
  the default is true — operators can flip it.

See `SPEC.md` for the surface, event taxonomy, and operational
notes; `extension.json` for the canonical schema.

## Files

```
extensions/whats2api_bridge/
├── README.md       # this file
├── SPEC.md         # spec: events, tools, schema, ops
├── extension.json  # OXS manifest
├── plugin.py       # register(host) entry point
└── ui/
    └── index.html  # static dashboard (served at /ext-static/...)
```
