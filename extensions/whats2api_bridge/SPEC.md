# SPEC — `whats2api_bridge`

> OXS extension that bridges [whats2api.com](https://whats2api.com) into
> Odysseus. Manifest lives in `extension.json`; this document is the
> single source of truth for the runtime contract (tools, events,
> storage, settings, ops).

## 1. Identity

| Field           | Value                                                                  |
| --------------- | ---------------------------------------------------------------------- |
| `id`            | `whats2api_bridge` (no hyphen — storage layer requires `[a-z0-9_]`)    |
| `entry_point`    | `plugin:register`                                                      |
| `oxs_version`   | `>=1.0.0,<2.0.0`                                                       |
| `version`       | `0.1.0`                                                                |
| `python_deps`   | `[]` (uses only stdlib + `httpx`, both already in `requirements.txt`)  |

## 2. Capabilities

Safe tier (auto-granted on enable):

| Capability      | Use                                                             |
| ---------------- | --------------------------------------------------------------- |
| `data`           | Own tables `whats2api_bridge__connections/messages/events`      |
| `tools`          | Reserved for future non-MCP tools (none today)                  |
| `routes`         | Public `/ext-api/whats2api_bridge/{status,local_data,mcp_integrations,settings}` for the static UI |
| `mcp_tools`      | 16 tools (see §4)                                               |
| `webhooks_in`    | Inbound `/api/ext/whats2api_bridge/webhook/<token>`             |
| `webhooks_out`   | Reserved — no outbound webhooks fire today                      |
| `events_sub`     | `oxs.rescan` (log only)                                         |
| `events_fire`    | All `whats2api.*` events (see §5)                               |
| `ui_panel`       | Adds a panel entry to the Odysseus sidebar                      |
| `static`         | Serves `ui/` at `/ext-static/whats2api_bridge/`                 |
| `settings_read`  | Reads own settings namespace                                    |

Medium tier (allowlisted):

| Permission                | Allowlist                              |
| ----------------------- | -------------------------------------- |
| `network_outbound`      | `https://api.whats2api.com`            |
| `filesystem_data_subpath`| `cache` (reserved — used in a later release) |

No privileged permissions.

## 3. Settings (admin-only writes)

| Key                        | Type    | Required | Default                          | Notes                                                                       |
| -------------------------- | ------- | -------- | -------------------------------- | --------------------------------------------------------------------------- |
| `api_token`                | string  | yes      | —                                | 64-hex API token from the whats2api dashboard. `minLength=8`.                |
| `base_url`                 | string  | yes      | `https://api.whats2api.com`      | Override only for proxies. Must match `^https://...`.                       |
| `default_connection_id`    | string  | no       | `""`                             | Connection to pick when a tool call omits `connection_id`.                   |
| `request_timeout_seconds`  | integer | no       | `30`                             | `1 ≤ x ≤ 300`                                                               |
| `retention_days`           | integer | no       | `7`                              | Reserved — pruning job not shipped in v0.1.                                 |
| `log_inbound_events`       | boolean | no       | `true`                           | Persist every inbound event to `whats2api_bridge__events`.                   |

The token is **never** returned by the public `/ext-api/.../status`
endpoint; that route only reports presence (`api_token_set: true|false`).

Both `on_install` and `on_enable` defensively re-apply the defaults above
so pre-existing installs (whose `on_install` predated these defaults)
are upgraded in place. The defaults never overwrite a user-set value.

## 4. MCP tools

All tools are registered on the namespaced MCP server
`oxs_whats2api_bridge` and exposed to the agent as
`mcp__oxs_whats2api_bridge__<tool_name>`.

### 4.1 Connection lifecycle

| Tool                  | Args                                                                                           | Returns                              |
| --------------------- | ---------------------------------------------------------------------------------------------- | ------------------------------------ |
| `list_connections`    | (none)                                                                                         | whats2api `GET /connections`         |
| `get_connection`      | `connection_id`                                                                                | whats2api `GET /connections/:id`     |
| `create_connection`    | `name` (required), `engine` (`baileys`/`whatsmeow`, default pass-through), `webhook_url?`        | whats2api `POST /connections`        |
| `patch_connection`    | `connection_id`, plus any of `name`, `webhook_url`                                              | whats2api `PATCH /connections/:id`   |
| `remove_connection`   | `connection_id`                                                                                | whats2api `DELETE /connections/:id`  |
| `start_connection`    | `connection_id`                                                                                | whats2api `POST /connections/:id/start` |
| `stop_connection`     | `connection_id`                                                                                | whats2api `POST /connections/:id/stop`  |
| `refresh_connections` | (none)                                                                                         | alias for `list_connections`         |
| `health`              | (none)                                                                                         | `{healthy, status_code?, count?}`    |

### 4.2 Messaging

| Tool             | Required args                                                     | Optional                       |
| ---------------- | ----------------------------------------------------------------- | ------------------------------ |
| `send_message`   | `to` (remote_jid), `text`                                         | `quoted`, `mentions`           |
| `send_reaction`  | `key` (object — message key from a prior event)                    | `text` (empty removes)         |
| `mark_read`      | `chat` (remote_jid), `ids` (list of message ids)                  | —                              |
| `send_media`     | `to`, `kind` (`image|video|audio|document|ptt`), `url`, `mimetype` | `caption`, `file_name`         |
| `send_presence`  | —                                                                 | `presence`, `to`               |
| `is_on_whatsapp` | `phones` (list of E.164 numbers)                                   | —                              |
| `call_method`    | `method`                                                           | `params` (default `[]`)        |

`send_message` and the other messaging tools resolve `connection_id` in
this order: arg `connection_id` / `connectionId` / `connection` →
`default_connection_id` setting. If neither is set, the tool returns
`{ok: false, error: "connection_id is required: …"}` without making a
network call.

### 4.3 Result envelope

Every tool returns a single dict with the shape:

```
{
  "ok":   true | false,
  "data": <whats2api response>            # when ok=true
  "error": "<human-readable message>",    # when ok=false
  "status_code": <int>,                   # when the upstream rejected us
  "body": "<truncated upstream body>"     # when the upstream rejected us
}
```

The agent's tool runner maps this to MCP `exit_code`:

- `ok=true` → `exit_code: 0`, output = `data`.
- `ok=false` → `exit_code: 1`, error = `error` (plus status_code/body).

## 5. Inbound webhook → OXS events

### 5.1 URL & auth

The extension installs a single webhook at:

```
POST /api/ext/whats2api_bridge/webhook/<token>
```

- `<token>` is a 32-byte URL-safe token. OXS's
  ``host.register_webhook`` generates a fresh one on every enable, which
  would silently invalidate the URL the operator configured in
  ``whats2api.com`` on the first container restart. To keep the URL
  stable, the bridge pins the token to disk at
  ``<data>/whats2api_bridge/inbound_webhook_token`` (mode ``0600``)
  on every enable and rewrites the just-installed record's ``token_hash``
  to match — so a restart loads the same token from disk instead of
  rolling a new one. Delete the file (and re-enable) to force rotation.
  Shown **once** in the admin UI.
- The optional header `X-whats2api_bridge-Signature: sha256=<hmac>` is
  verified against a per-extension HMAC secret (constant-time).
- Rate limit is 10 req/s/token (429 beyond that).
- The handler runs in an asyncio task and returns 202 immediately.
- A wrong/missing token returns **404** (not 401) — we don't leak the
  existence of the endpoint.

### 5.2 Body shape

whats2api sends a POST whose body is a **map of `eventName → payload`**:

```json
{
  "messages.upsert":     { "type": "notify", "messages": [ ... ] },
  "connection.update":   { "state": "open", "connectionId": "..." },
  "groups.upsert":       [ ... ],
  "presence.update":     { ... }
}
```

The handler iterates over every event in the body, persists it to
`whats2api_bridge__events`, and fires the mapped OXS event.

### 5.3 Event taxonomy

whats2api fans out every Baileys event from the `ev` bus. The handler
recognises three payload shapes for message-class events:

1. `messages.upsert` → `{type, messages: [...]}` — full envelope per
   message, `key.id` becomes the messages-table primary key.
2. `Message` → full envelope `{key, message, pushName,
   messageTimestamp}` — same as above.
3. `Message` → **bare content** `{conversation, messageContextInfo,
   ...}` (no `key`, no `messageTimestamp`). The handler synthesises a
   deterministic id (``synth:<sha1[:24]>``) and writes a row with
   `message_type = "partial"`. If a per-message `Info` event arrives in
   the same body (whats2api always emits it right before the bare
   `Message`), the row is enriched with the real id, chat jid, sender
   jid, push name, ISO timestamp and message type — the synthetic id
   and `partial` marker are replaced and the row is indistinguishable
   from one written via `messages.upsert`.

| whats2api event             | OXS event                          | Payload                                                                                     |
| --------------------------- | ---------------------------------- | ------------------------------------------------------------------------------------------- |
| `messages.upsert`           | `whats2api.message.received`       | `{connection_id, user_id, message: {id, remote_jid, from_me, push_name, text, ts, type}}`    |
| `messages.update`           | `whats2api.message.update`         | `{connection_id, user_id, event_name, data}`                                                |
| `messages.delete`           | `whats2api.message.delete`         | `{connection_id, user_id, event_name, data}`                                                |
| `messages.reaction`         | `whats2api.message.reaction`       | `{connection_id, user_id, event_name, data}`                                                |
| `messages.media-update`     | `whats2api.message.media`          | `{connection_id, user_id, event_name, data}`                                                |
| `message-receipt.update`    | `whats2api.message.receipt`        | `{connection_id, user_id, event_name, data}`                                                |
| `connection.update`         | `whats2api.connection.update`      | `{connection_id, user_id, event_name, data}`                                                |
| `chats.upsert`              | `whats2api.chat.upsert`            | `{connection_id, user_id, event_name, data}`                                                |
| `chats.update`              | `whats2api.chat.update`            | `{connection_id, user_id, event_name, data}`                                                |
| `chats.delete`              | `whats2api.chat.delete`            | `{connection_id, user_id, event_name, data}`                                                |
| `contacts.upsert`           | `whats2api.contact.upsert`         | `{connection_id, user_id, event_name, data}`                                                |
| `contacts.update`           | `whats2api.contact.update`         | `{connection_id, user_id, event_name, data}`                                                |
| `groups.upsert`             | `whats2api.group.update`           | `{connection_id, user_id, event_name, data}`                                                |
| `groups.update`             | `whats2api.group.update`           | `{connection_id, user_id, event_name, data}`                                                |
| `group-participants.update` | `whats2api.group.participants`     | `{connection_id, user_id, event_name, data}`                                                |
| `presence.update`           | `whats2api.presence.update`        | `{connection_id, user_id, event_name, data}`                                                |
| `blocklist.set`             | `whats2api.blocklist.set`          | `{connection_id, user_id, event_name, data}`                                                |
| `blocklist.update`          | `whats2api.blocklist.update`       | `{connection_id, user_id, event_name, data}`                                                |
| `call`                      | `whats2api.call`                   | `{connection_id, user_id, event_name, data}`                                                |
| —                           | `whats2api.message.sent`           | `{connection_id, to, text, result}` (fired after a `send_message` MCP call succeeds)        |
| —                           | `whats2api.bridge.enabled`         | `{info: "whats2api_bridge online"}` (fired on enable)                                       |

### 5.4 Verification

whats2api sets three headers on every webhook (per
[/docs/events](https://whats2api.com/docs/events)):

- `whats2api: true`
- `userId: <user id>`
- `connectionId: <originating connection id>`

The handler reads them via case-insensitive header lookup, drops the
events into the `events` table regardless, and **only fires OXS events
when `whats2api: true` is present** — protects against misrouted /
spoofed sources.

## 6. Storage

Tables (created on enable via `storage.apply_data_schema`):

- `whats2api_bridge__connections`
  - `id TEXT PRIMARY KEY`
  - `name, engine, phone, status, owner_id TEXT`
  - `raw_json TEXT`
  - `updated_at INTEGER NOT NULL`
  - indexes on `(status)`, `(owner_id)`

- `whats2api_bridge__messages`
  - `id TEXT PRIMARY KEY` (WhatsApp message id, dedupes on retry; rows
    from per-message fan-out events that arrive without a `key` get a
    synthetic id of the form ``synth:<sha1[:24]>`` so re-deliveries hit
    the unique constraint instead of duplicating)
  - `connection_id TEXT NOT NULL`
  - `direction TEXT NOT NULL` (`in` / `out`)
  - `remote_jid, chat_jid, sender_jid, push_name TEXT` (empty for partial
    rows from bare-content `Message` fan-outs — we have no envelope)
  - `from_me TEXT` (`"0"` / `"1"`)
  - `text, message_type TEXT` (`message_type` is `"partial"` for rows
    derived from a bare-content `Message` event)
  - `ts INTEGER NOT NULL`
  - `raw_json TEXT`
  - indexes on `(connection_id, ts)`, `(chat_jid, ts)`, `(direction)`
  - unique `(id)`

- `whats2api_bridge__events`
  - `id INTEGER PRIMARY KEY AUTOINCREMENT`
  - `event_name TEXT NOT NULL`
  - `connection_id, user_id TEXT`
  - `received_at INTEGER NOT NULL`
  - `raw_json TEXT` (truncated to 65 KB)
  - indexes on `(event_name, received_at)`, `(connection_id, received_at)`

All `execute_raw` queries are restricted to `SELECT`/`WITH` and
scoped to the `whats2api_bridge__` prefix (OXS storage wall).

## 7. Operational notes

- **Token rotation**: the inbound webhook token is pinned to disk at
  ``<data>/whats2api_bridge/inbound_webhook_token`` and survives restarts.
  To rotate (e.g. suspected compromise): delete the file, disable+enable
  the extension. The next request will be served under the new token —
  update ``whats2api.com`` accordingly.
- **Outbound API token rotation**: edit `api_token` in settings,
  disable+enable the extension. The webhook URL/token is NOT affected
  by this (separate credential).
- **Pruning**: not shipped in v0.1. The `events` table grows linearly
  with incoming traffic; v0.2 should add a TTL job keyed on
  `retention_days`.
- **Media caching**: declared as `filesystem_data_subpath: ["cache"]`
  for a follow-up release that adds `download_media`.
- **Multi-engine**: the extension is engine-agnostic — it always
  passes the `method` string verbatim to `/connections/request`. The
  Baileys wire shapes used in `send_message` / `send_media` are the
  most common path; the generic `call_method` tool is the escape
  hatch for hypermeow's protojson shapes.
- **Static UI**: `index.html` is a single-page dashboard with five tabs
  (Live / Local Data / MCP Integrations / Settings / Docs). It hits
  four routes under `/ext-api/whats2api_bridge/` to read state and
  write back changes (settings, MCP integration CRUD). The status
  route returns presence flags + recent events but never the token.
  The local_data route serves paginated reads of the extension's
  own tables. The settings route exposes only the user-toggleable
  fields; the API token is write-only and never returned over the
  wire. To rotate or read the token, the operator uses the standard
  OXS admin UI.
- **Disable ≠ uninstall**: disabling unwinds every registration but
  keeps the tables on disk. Uninstall drops the tables.

## 8. Testing

`tests/test_extension_events_whats2api_bridge.py` exercises:

- Manifest validation (id, capabilities, schema, hashes).
- Discovery + enable + disable cycle.
- 16 MCP tools registered with correct schemas.
- Inbound webhook: parses `messages.upsert`, persists rows in
  `messages` + `events`, fires the OXS event.
- Invalid `whats2api` header: persists but does NOT fire OXS events.
- Rate limit: 11th call in 1 s returns 429.
- `call_method` and `send_message` return friendly errors when neither
  arg nor default connection_id is set.

A real whats2api account is not required — the tests only verify the
local bridge; the REST client is exercised via `monkeypatch` on
`httpx.AsyncClient`.