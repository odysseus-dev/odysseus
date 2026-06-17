# Chat Gateway

Lets a user converse with the Odysseus agent (the full tool/RAG/memory agent,
the same one the web UI drives) from messaging platforms. Opt-in and off by
default: it does nothing unless `data/chat_gateway.yaml` exists and enables it.

## Design

A thin per-platform **adapter** handles transport only (connect, listen, send).
A single shared **runner** turns each inbound message into an agent run via
`src.agent_loop.stream_agent_loop` and returns the reply. Adapters contain no
agent logic, so every platform gets the same agent and tools.

```
platform  ->  adapter.listen()  ->  runner  ->  stream_agent_loop  ->  adapter.send()  ->  platform
```

Adapters run as background asyncio tasks started from the app's existing
lifespan startup (`app.state._startup_tasks`); there is no extra process.

## Bundled adapters

Self-hostable, local-first platforms:

| Platform | Transport | Extra dependency |
|---|---|---|
| Mattermost | WebSocket | `websockets` (optional) |
| Matrix | `/sync` long-poll | none (httpx, core) |
| IRC | raw TCP (asyncio) | none |
| SimpleX | local `simplex-chat` CLI WebSocket | `websockets` (optional) |

Third-party/cloud platforms (Telegram, Discord, Slack, and so on) are not
bundled; copy `src/chat_gateway/adapters/_skeleton.py` to add one.

## Configuration

Copy `chat_gateway.example.yaml` to `data/chat_gateway.yaml` and edit. Per
platform: `enabled`, credentials, `require_mention` (channels need a mention;
DMs are always answered), an optional `channels` allowlist, optional
`free_response_channels` (channels that reply without a mention), and per-platform
toolset gating (`all` | `allow` | `deny`). Set `owner` to the Odysseus user the
agent acts as.

Credentials are read from config/env only and live in the gitignored
`data/chat_gateway.yaml`; never commit them.

## Adding a platform

1. Copy `adapters/_skeleton.py` to `adapters/<platform>.py` and rename the class.
2. Set `platform = "<key>"` (must match the config block name).
3. Implement `connect()` (auth, set `self._bot_user_id`), `listen()` (receive
   loop building `IncomingMessage` and calling `await self._dispatch(msg)`), and
   `send()`. Optionally implement the typing-indicator hooks.
4. Register it in `adapters/__init__.py` (one line in `ADAPTERS`).
5. Add a config block under `platforms:` in `data/chat_gateway.yaml`.

Prefer `httpx` (core) or stdlib `asyncio` transports; add `websockets`
(optional) only if the platform needs a WebSocket. Avoid heavy SDKs.

## Notes and limitations

- Gateway sessions are normal Odysseus sessions (named `platform:channel_id`),
  so conversations appear in the web UI. The bridge is one-directional:
  messages typed in the web UI are not relayed back out to the platform.
- Matrix: encrypted rooms are detected and warned about, but not read. End-to-end
  encryption would require the heavy mautrix/olm stack and is intentionally left
  out of the lean core; it is a possible future option.
- IRC: outbound text is CRLF/NUL-sanitised (command-injection guard), split on
  byte length, and rate-limited.
