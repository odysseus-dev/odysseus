# MCP Gateway Pool

Status: implemented (`src/mcp_gateway.py`, `routes/mcp_gateway_routes.py`)
Tests: `tests/test_mcp_gateway_pool.py`

## Problem

Odysseus already speaks MCP over stdio, SSE, and Streamable HTTP
(`src/mcp_manager.py`), and admins can configure a single MCP server URL. That
covers "connect to one server". It does not cover "treat several
interchangeable gateway endpoints as one logical server".

An MCP API gateway fronts a pool of upstream MCP stacks. When one upstream
degrades, the gateway keeps returning `200` and starts failing individual
calls. A single hardcoded gateway URL turns that partial outage into a total
one, and there is no operator lever short of editing the database by hand.

## Model

A gateway is a **pool** of base URLs:

```
ODYSSEUS_GATEWAY_URLS=https://htp.sh/ai,https://backup.example.com/ai
```

Requests pick an endpoint round-robin and fail over on error. Routing state
(cooldowns, dead flags, counters) persists to `data/gateway_state.json`.

Gateway URLs are treated as **servers that publish their own route map**. A
gateway serves a JSON manifest at its root; the pool reads it rather than
hardcoding paths, so a gateway that adds or renames an endpoint needs no code
change here.

## Failure handling

Adapted from the state machine in the `ocmesh` CLI (model rotation), which
already proves out the cooldowns/dead-list approach locally:

| Outcome | Effect |
|---|---|
| Success | Clear cooldown + strikes, un-mark dead, update latency average |
| `5xx`, `429` | Exponential backoff: `base * 2^(strikes-1)`, capped |
| `5xx` >= 502 | Mark **dead** — removed from rotation until revived |
| `4xx` (not 429) | **No** health penalty — the caller's fault, endpoint is fine |
| Connection/timeout | Exponential backoff |

`4xx` deliberately does not cool an endpoint down: a malformed tool argument
should not eject a healthy gateway from the pool.

## Selection

`acquire()` walks the currently-routable set in sorted order using a rotating
cursor. Cooling and dead endpoints are skipped entirely rather than retried, so
a degraded gateway stops receiving traffic instead of failing calls.

Raises `GatewayExhausted` when nothing is routable — callers must handle this
rather than assume an endpoint exists.

## Concurrency

Odysseus can run multiple workers against one data directory, so the state file
is guarded by an in-process `threading.Lock` **and** an `fcntl` file lock.

The merge happens on **entry** to the transaction, before the caller's writes:

```python
with pool._mutate():        # lock -> load -> yield
    pool._record_fail(ep, "boom")   # write
#                            -> unlock -> write
```

Reloading on exit instead would restore stale on-disk values over the pending
mutation. That bug silently discarded every recorded failure, so backoff never
escalated past its first step and `revive()` reported success while leaving
`dead=True` on disk. Both are pinned by regression tests.

## Security

Gateway URLs are operator-supplied and fetched server-side — the same shape as
the SSRF gap recorded in `THREAT_MODEL.md` (#1039). Rather than adding to it,
`_validate_base_url` requires:

- **HTTPS only** (these carry bearer tokens)
- **Public address space only** — loopback, RFC1918, link-local, and reserved
  ranges are rejected after DNS resolution, blocking use as an internal pivot
  (e.g. `169.254.169.254`)

Private gateways require an explicit `ODYSSEUS_GATEWAY_ALLOW_PRIVATE=1`.

All admin routes are gated by `require_admin`, because adding a gateway changes
where privileged tool calls are sent.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ODYSSEUS_GATEWAY_URLS` | *(empty)* | Comma-separated pool members |
| `ODYSSEUS_GATEWAY_COOLDOWN` | `60` | Backoff base, seconds |
| `ODYSSEUS_GATEWAY_COOLDOWN_MAX` | `3600` | Backoff ceiling, seconds |
| `ODYSSEUS_GATEWAY_TIMEOUT` | `30` | Per-request timeout, seconds |
| `ODYSSEUS_GATEWAY_ALLOW_PRIVATE` | *(off)* | Permit non-public gateway hosts |

## API

| Route | Purpose |
|---|---|
| `GET /api/mcp/gateways` | Pool membership + routing state |
| `POST /api/mcp/gateways` | Add endpoint (`{base_url, discover}`) |
| `GET /api/mcp/gateways/health` | Aggregate health + `routable` flag |
| `GET /api/mcp/gateways/{i}/manifest` | Advertised route map (`?refresh=`) |
| `POST /api/mcp/gateways/{i}/revive` | Clear cooldown and dead flag |
| `DELETE /api/mcp/gateways/{i}` | Remove endpoint |

`/health` is declared before the indexed routes so it is never parsed as an
index.

## Known gaps

- **No circuit-breaker half-open state.** A dead endpoint is revived only by
  operator action; it does not self-probe back into rotation.
- **Not wired into `McpManager`.** The pool is currently reachable over HTTP
  routes but is not yet a selectable transport in the MCP server UI, so it does
  not yet serve agent tool calls. See below.
- **Manifests are per-endpoint, not pooled.** Two gateways advertising
  different route maps can resolve the same key to different paths.
- **Dead is sticky across restarts.** `data/gateway_state.json` restores the
  dead list, so an endpoint marked dead stays out of rotation after a restart
  until revived. Deliberate (survives deploys) but operators must know.

## Edge proxy — `workers/mcp-gateway-proxy`

A Cloudflare Worker running the same selection and failure rules, for callers
that should not reach the gateway pool directly. 27 tests.

- **Deploy target: `workers.dev` only.** It deliberately binds **no route on
  `htp.sh`**, which serves live production (`still-firefly-7b67ebk` on `/`,
  `hackdeplanet` Pages on `crime.htp.sh`). A route here would take over that
  traffic. Add one deliberately, never implicitly.
- **`GATEWAY_POOL` is origin-only** (`https://htp.sh`, not `https://htp.sh/ai`).
  `htp.sh/ai` advertises absolute paths (`/ai/tools`), so the origin belongs in
  the pool and the advertised path is the request path.
- Per-isolate in-memory state. A cooldown outliving isolate eviction would need
  a KV write per failure; a revived isolate re-learns within a few requests.
- `GET /__gateway/health` mirrors the Python pool's status shape.

### URL joining

`buildTarget()` builds the upstream URL from scratch rather than mutating
`new URL(request.url)`. Mutating leaks the proxy's own port into the upstream
host, so the Worker dials `htp.sh:8787` and hangs until timeout; concatenating
blindly also doubles a shared prefix (`/ai` + `/ai/tools` -> `/ai/ai/tools`).
When the incoming path already starts with the base path, the base is treated
as an origin prefix rather than concatenated.

### Forwarded headers

`Host`, `Content-Length`, and `Accept-Encoding` are stripped. Forwarding `Host`
breaks virtual-host routing upstream; the other two describe the inbound
connection and mismatch the rewritten target.

## Next step to make this live

To have agent tool calls actually flow through the pool, add a `gateway`
transport to `McpManager.connect_server` that resolves a tool name to a
manifest path and dispatches via `GatewayPool.request`. The pool, its state
machine, and the admin surface are already in place for that.