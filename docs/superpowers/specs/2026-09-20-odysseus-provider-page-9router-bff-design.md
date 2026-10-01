# Odysseus provider page → 9router BFF

**Date:** 2026-09-20  
**Status:** Draft for review  
**Parents:**
- `docs/superpowers/specs/2026-09-08-openhands-agent-platform-cutover-design.md`
- `docs/superpowers/specs/2026-09-10-odysseus-openhands-operations-chat-design.md`
- `docs/superpowers/specs/2026-09-19-linux-vm-openhands-9router-deploy-design.md`
- `docs/plans/2026-09-10-001-refactor-9router-openhands-provider-boundary-plan.html`

This slice makes Odysseus Settings the product surface for connecting inference providers. The **in-stack 9router** (Compose overlay on the Linux VM) is the credential authority. Odysseus chrome, OpenHands runtime, stack 9router inference. Operators do not administer providers in 9router’s own dashboard.

The overlay is already deployed on the Fusion Ubuntu VM. This feature is developed **on that machine**, against that stack, then later repackaged and redeployed to prove the tree. It does not use a host-wide 9router.

## 1. Decision

Connect stays **inside Odysseus chrome**. Stack 9router stores tokens, refresh, and upstream health. Odysseus stores only an opaque connection projection (`connection_id`, `status`, `entitlement`, `label`, `owner`).

The Settings **Add API Models** key/URL save path is retired for 9router-backed cloud providers. ChatGPT Subscription’s hop to `/dashboard/providers` is retired as the product path. The browser never needs a published 9router URL.

Stack 9router is an **isolated Compose service** whose role is being shaped to sit behind Odysseus (BFF, unpublished, in-network only). That is a different job than a host-wide 9router used as a general inference proxy.

## 2. Goals

- One provider admin surface: this repository’s Odysseus Settings, same board as chat.
- Stack 9router owns secrets; Odysseus never persists API keys, OAuth access/refresh tokens, or upstream base URLs as credentials.
- Stack 9router stays unpublished; Odysseus reaches it on the Docker network (`NINE_ROUTER_METADATA_URL=http://9router:20128`).
- Chat/model picker keeps curated 9router routes; it does not grow a paste-a-key path.
- Implementation, live connect, and proof happen on the already-running Linux VM overlay before a packaging/redeploy check.

## 3. Non-goals

- Embedding 9router’s dashboard in an iframe, or treating Agent Canvas as a second provider board.
- Using a host-wide 9router (any machine) as this stack’s connect or inference plane.
- Routing OpenHands or 9router through HHPE.
- Local/custom OpenAI-compatible endpoints that are not stack-9router connections (deferred).
- Odysseus calling `/v1/chat/completions` or storing a 9router virtual inference key as a metadata credential.

## 4. Ownership

| Surface | Owns | Must not |
|---|---|---|
| Odysseus Settings + BFF | Catalog UX, start/poll/callback, opaque `ProviderAuthSession` projection | Persist secrets; open `/dashboard/providers` as the product path |
| Stack 9router (Compose, unpublished) | Catalog, connect, tokens, refresh, upstream health | Be reached by the operator’s browser; act as a host-wide proxy |
| OpenHands / Canvas | Agent runtime / engineering UI | Provider credential admin |
| HHPE | Unrelated | Sit on the inference or connect path |

## 5. Where this is built

The Linux VM overlay (`orchestration-vm`, `/home/agent/work/odysseus`, compose `docker-compose.yml` + `docker-compose.openhands.yml`) is the **development environment**. Odysseus is `127.0.0.1:7000`. Stack 9router is unpublished; reach it with `docker compose exec`.

- Change, run, connect, and debug on the guest checkout against the running overlay.
- After the feature works there, package and redeploy that tree onto the same overlay to confirm a clean ship. Daily work is not “edit elsewhere and rsync to test.”
- Fake/unit tests that never open a live 9router may run in CI; any live 9router test uses this stack’s unpublished service only.

## 6. Connect BFF

Settings lists a redacted catalog from in-network `GET /api/providers`. Connect never opens `/dashboard/providers`.

**API key providers.** Operator pastes a key in Odysseus. Odysseus POSTs it to stack 9router, reads opaque `connection_id` + status, persists the projection, drops the key. No `ModelEndpoint.api_key`, no `ProviderAuthSession` token columns on new writes.

**OAuth / subscription (including ChatGPT and Copilot).**

1. Odysseus starts connect in-network and returns an **upstream IdP** URL (or in-chrome device code), not a 9router dashboard URL.
2. Browser completes login at the IdP. Callback hits Odysseus.
3. Odysseus forwards the authorization code only. If the callback body contains tokens, discard them and treat that as a contract violation.
4. UI polls Odysseus until `usable` or `error`.

Copilot device code is shown in Odysseus; the resulting connection is still owned by stack 9router.

**Clients.** Keep `NineRouterMetadataClient` GET-only: `/api/health`, `/api/providers`, `/api/usage/`. Add a separate connect client with a tight allowlist for provider create and OAuth exchange. Forbidden on both clients: `/v1/*`, completions, using a virtual inference key as a metadata credential.

**Sessionless connect on the pinned overlay image.** Prove the in-stack API with `compose exec` before writing UI. If there is no sessionless connect API, fail closed: do not revive the dashboard hop as the product path, do not persist keys on Odysseus, do not reach outside this Compose project for another 9router.

## 7. Settings UI

Replace the Add API Models card with a **9router connections** panel in the same Settings chrome.

- Rows: provider/label, opaque connection id if connected, status (`usable` / `pending` / `error` / disconnected). No keys, tokens, or upstream base URLs.
- Connect is in-page (IdP hop or one-shot key field that clears after submit).
- After connect, the row updates from Odysseus poll. Chat/model picker still uses `automatic` / `fast` / `balanced` / `best` plus opaque entitlement.
- Disconnect / reconnect go through Odysseus to stack 9router, then update or clear the projection.
- Drop Base URL + persistent API key + “Add endpoint” save for 9router-backed cloud providers. Drop proxy vs “API (direct) browser→provider” for those providers; inference stays stack 9router.

Existing Odysseus styling (provider picker, status line, admin card) stays. This is not an iframe of 9router.

## 8. Error handling

- Stack 9router down or metadata miss: catalog unhealthy; Connect disabled; no substitute 9router.
- Connect or OAuth exchange fails: inline error on the Settings row; no projection write; no key in Odysseus logs or DB.
- Callback with tokens or missing `connection_id`: discard secrets, HTTP 400, log contract violation.
- Missing sessionless connect on the overlay image: guest probe fails honestly; product path stays fail-closed.
- Owner mismatch: connection ids are not usable across owners (existing AE5).
- Chat with no usable stack-9router connection: fail-closed credential miss; no local key fallback.

## 9. Testing

**Fakes (no live 9router).** Catalog redaction; API-key POST does not persist secrets; OAuth callback strips tokens; metadata client still forbids `/v1/*`; connect client allowlist; Settings no longer saves `ModelEndpoint` keys for 9router providers; ChatGPT start no longer returns `/dashboard/providers`.

**Live, Linux VM overlay only.** After stack probe on the guest, one connect through Odysseus Settings against unpublished stack 9router, then a token-successful completion probe. Redeploy-from-package is a confirmation step after that path works, not a substitute for developing on the VM.

## 10. Out of scope follow-ups

Local/custom non-9router endpoints; overlay 9router image bump; egress-enforced sole-provider; Hermes on the Odysseus chooser.
