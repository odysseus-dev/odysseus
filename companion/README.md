# Companion bridge (read-only)

A thin, additive layer so a LAN client can discover what an Odysseus server
offers, without duplicating any LLM logic. Reachable with either a logged-in
cookie session or a Bearer `ody_` API token (auth is enforced globally by
`AuthMiddleware`).

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/companion/ping` | cheap, auth-validated health check |
| GET | `/api/companion/info` | server identity + capability flags |
| GET | `/api/companion/models` | the **caller's own** model endpoints |

`/models` scopes to the caller's real owner (the token's owner for bearer
callers) plus legacy null-owner shared rows, the same rule as `owner_filter`. It
never returns API-key material. The owner rule lives in two pure, tested helpers
(`token_owner`, `owner_can_see`) — see `tests/test_companion_readonly.py`.

This module is intentionally read-only. Pairing/token-minting, token-owner
session attribution, and any mutation endpoints are proposed in separate PRs.
