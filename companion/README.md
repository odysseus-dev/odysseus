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
| GET | `/api/companion/system/update-check` | **admin** | is a newer release available? (read-only) |
| GET | `/api/companion/system/db-export` | **admin** | download a consistent SQLite snapshot |

`/models` scopes to the caller's real owner plus legacy null-owner shared rows
(same rule as `owner_filter`) and never returns API-key material.

## System endpoints (issue #1157)

Two admin operations for keeping a self-hosted instance current and getting your
data out. Both are **admin-gated** via `require_admin`: a bearer/chat token comes
through as the sandboxed pseudo-user `api` (never an admin), so these are reachable
from a browser logged in as admin — *not* from a paired chat token. That's
deliberate: a DB snapshot is effectively a full data dump, and triggering updates
is privileged.

- **`GET /system/update-check`** — compares the running `APP_VERSION` against the
  latest release at `UPDATE_CHECK_URL` (the upstream GitHub repo by default;
  override with `ODYSSEUS_REPO` or `ODYSSEUS_UPDATE_CHECK_URL`). Read-only and
  side-effect free — it never touches the container. A network failure degrades to
  `{reachable: false, error: …}` rather than a 500.
- **`GET /system/db-export`** — streams a point-in-time copy of `app.db` taken with
  SQLite's online `.backup` API (safe while the server is running). SQLite only; a
  non-SQLite `DATABASE_URL` returns 400. The Fernet key (`data/.app_key`) needed to
  decrypt encrypted columns is **not** included.

### Applying an update — `scripts/odysseus-update`

The actual container update is a CLI (the HTTP layer never shells out to Docker, so
the Docker socket is never exposed on the LAN):

```
odysseus-update check                       # current vs latest release
odysseus-update apply                       # DRY-RUN: prints the plan
odysseus-update apply --yes                 # snapshot → pull → up -d --build
odysseus-update apply --service odysseus --no-backup
```

`apply` is a dry-run unless `--yes`. It snapshots `data/` via `odysseus-backup`
first, then `docker compose pull` + `up -d --build` (the odysseus image is
`build:`-based, so it's rebuilt locally and the container recreated). It does
**not** `git pull` — update the checkout yourself first to pick up new source,
since this workspace carries the companion overlay.

The helpers live in tested units (`parse_version`, `update_available`,
`resolve_sqlite_path`, `safe_sqlite_snapshot`) — see `companion/system.py` and
`tests/test_companion_system.py`.

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
