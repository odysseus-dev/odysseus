# Odysseus provider page → 9router BFF

**Date:** 2026-09-20  
**Status:** Draft for review  
**Parents:**
- `docs/superpowers/specs/2026-09-08-openhands-agent-platform-cutover-design.md`
- `docs/superpowers/specs/2026-09-10-odysseus-openhands-operations-chat-design.md`
- `docs/superpowers/specs/2026-09-19-linux-vm-openhands-9router-deploy-design.md`
- `docs/plans/2026-09-10-001-refactor-9router-openhands-provider-boundary-plan.html`

This slice makes Odysseus Settings the product surface for connecting inference providers, with overlay 9router as the credential authority. It matches the chat cutover: Odysseus chrome, OpenHands runtime, 9router inference. It does not send operators to 9router’s dashboard as the primary connect UX.

## 1. Decision

Connect stays **inside Odysseus chrome**. Overlay 9router stores tokens, refresh, and upstream health. Odysseus stores only an opaque connection projection (`connection_id`, `status`, `entitlement`, `label`, `owner`).

The Settings **Add API Models** key/URL save path is retired for 9router-backed cloud providers. ChatGPT Subscription’s hop to `/dashboard/providers` is retired as the product path. The browser never needs a published 9router URL.

Live development and proof run on the Fusion Ubuntu overlay (`orchestration-vm`, `/home/agent/work/odysseus`). The Mac native 9router (0.5.75 on `127.0.0.1:20128`) stays untouched and is not the Odysseus inference or connect plane.

## 2. Goals

- One provider admin surface: this repository’s Odysseus Settings, same board as chat.
- 9router owns secrets; Odysseus never persists API keys, OAuth access/refresh tokens, or upstream base URLs as credentials.
- Unpublished overlay 9router remains unpublished; Odysseus reaches it on the Docker network.
- Mac host 9router and Odysseus overlay 9router stay isolated.
- Chat/model picker keeps curated 9router routes; it does not grow a paste-a-key path.

## 3. Non-goals

- Embedding 9router’s dashboard in an iframe, or treating Agent Canvas as a second provider board.
- Pointing Odysseus, Agent Server, OpenCode, Hermes, or the model-job worker at Mac 9router.
- Routing OpenHands or 9router through HHPE.
- Bumping overlay 9router 0.5.69 to host 0.5.75 in this slice.
- Local/custom OpenAI-compatible endpoints that are not 9router connections (deferred; not redesigned here).
- Copying tokens from the Mac 9router `DATA_DIR` into the guest, git, or Odysseus.
- Odysseus calling `/v1/chat/completions` or storing a 9router virtual inference key as a metadata credential.

## 4. Ownership

| Surface | Owns | Must not |
|---|---|---|
| Odysseus Settings + BFF | Catalog UX, start/poll/callback, opaque `ProviderAuthSession` projection | Persist secrets; talk to Mac `:20128`; open `/dashboard/providers` as the product path |
| Overlay 9router (Compose, unpublished) | Catalog, connect, tokens, refresh, upstream health | Be reached by the operator’s browser |
| Mac 9router 0.5.75 | Existing host tools | Act as overlay health or connect target |
| OpenHands / Canvas | Agent runtime / engineering UI | Provider credential admin |
| HHPE | Unrelated | Sit on the inference or connect path |

## 5. Development split

- **Edit** may happen in this Mac checkout.
- **Run, connect, and prove** happen on the guest overlay: compose `docker-compose.yml` + `docker-compose.openhands.yml` only, Odysseus `127.0.0.1:7000`, 9router unpublished. Reach 9router with `docker compose exec`, never Mac `127.0.0.1:20128`.
- Odysseus uses `NINE_ROUTER_METADATA_URL=http://9router:20128` (in-network). Do not point it at the host install.
- Unit tests that fake 9router may run on the Mac if they never open host `:20128`. Any test that talks to a real 9router runs on the guest after stack probe.
- Sync uses the existing rsync path (excludes `.git`, venvs, `.env`). Do not copy Mac 9router data onto the guest.

## 6. Connect BFF

Settings lists a redacted catalog from in-network `GET /api/providers`. Connect never opens `/dashboard/providers`.

**API key providers.** Operator pastes a key in Odysseus. Odysseus POSTs it to overlay 9router, reads opaque `connection_id` + status, persists the projection, drops the key. No `ModelEndpoint.api_key`, no `ProviderAuthSession` token columns on new writes.

**OAuth / subscription (including ChatGPT and Copilot).**

1. Odysseus starts connect in-network and returns an **upstream IdP** URL (or in-chrome device code), not a 9router dashboard URL.
2. Browser completes login at the IdP. Callback hits Odysseus.
3. Odysseus forwards the authorization code only. If the callback body contains tokens, discard them and treat that as a contract violation.
4. UI polls Odysseus until `usable` or `error`.

Copilot device code is shown in Odysseus; the resulting connection is still owned by 9router.

**Clients.** Keep `NineRouterMetadataClient` GET-only: `/api/health`, `/api/providers`, `/api/usage/`. Add a separate connect client with a tight allowlist for provider create and OAuth exchange. Forbidden on both clients: `/v1/*`, completions, using a virtual inference key as a metadata credential.

**0.5.69 sessionless connect.** Prove the overlay API from the guest with `compose exec` before writing UI. If there is no sessionless connect API, fail closed: do not fall back to Mac 9router, do not revive the dashboard hop as the product path, do not persist keys on Odysseus.

## 7. Settings UI

Replace the Add API Models card with a **9router connections** panel in the same Settings chrome.

- Rows: provider/label, opaque connection id if connected, status (`usable` / `pending` / `error` / disconnected). No keys, tokens, or upstream base URLs.
- Connect is in-page (IdP hop or one-shot key field that clears after submit).
- After connect, the row updates from Odysseus poll. Chat/model picker still uses `automatic` / `fast` / `balanced` / `best` plus opaque entitlement.
- Disconnect / reconnect go through Odysseus to 9router, then update or clear the projection.
- Drop Base URL + persistent API key + “Add endpoint” save for 9router-backed cloud providers. Drop proxy vs “API (direct) browser→provider” for those providers; inference stays 9router.

Existing Odysseus styling (provider picker, status line, admin card) stays. This is not an iframe of 9router.

## 8. Error handling

- Overlay 9router down or metadata miss: catalog unhealthy; Connect disabled; no silent hop to Mac `:20128`.
- Connect or OAuth exchange fails: inline error on the Settings row; no projection write; no key in Odysseus logs or DB.
- Callback with tokens or missing `connection_id`: discard secrets, HTTP 400, log contract violation.
- Missing sessionless connect on 0.5.69: guest probe fails honestly; product path stays fail-closed.
- Owner mismatch: connection ids are not usable across owners (existing AE5).
- Chat with no usable 9router connection: fail-closed credential miss; no local key fallback.

## 9. Testing

**Mac, no live 9router.** Extend projection/BFF tests: catalog redaction; API-key POST does not persist secrets; OAuth callback strips tokens; metadata client still forbids `/v1/*`; connect client allowlist; Settings no longer saves `ModelEndpoint` keys for 9router providers; ChatGPT start no longer returns `/dashboard/providers`.

**Guest overlay only.** After stack probe, one live connect through Odysseus Settings against unpublished 9router, then a token-successful completion probe. Never against host 0.5.75. Provider tests on the Mac native install are not a substitute.

## 10. Out of scope follow-ups

Local/custom non-9router endpoints; overlay image bump 0.5.69 → 0.5.75; egress-enforced sole-provider; Hermes on the Odysseus chooser.
