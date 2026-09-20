# Linux VM OpenHands + 9router deploy design

**Date:** 2026-09-19  
**Status:** Draft for review  
**Parents:**
- `docs/superpowers/specs/2026-09-08-openhands-agent-platform-cutover-design.md`
- `docs/superpowers/specs/2026-09-10-odysseus-openhands-operations-chat-design.md`
- `docs/plans/2026-09-10-001-refactor-9router-openhands-provider-boundary-plan.html`

This slice makes the OpenHands overlay **deployable and testable** on the large Linux VM on this machine. It does not add providers in tests first. It does not route OpenHands or 9router through HHPE.

## 1. Decision

Odysseus remains the **canonical production frontend**. The dashboard already modified for the OpenHands + 9router workflow is the board operators open. Agent Canvas runs **in parallel** as the engineering UI for the same Agent Server conversations. There are not two Odysseus boards.

Deploy and first live provider connection happen on the Fusion Ubuntu VM (`Ubuntu 64-bit` / guest `hhpe-forge` / SSH host `orchestration-vm`). The Mac host keeps its existing native 9router (0.5.75 on `127.0.0.1:20128`) untouched. Overlay inference uses the **unmodified 9router in Compose**, not the Mac install and not HHPE.

Providers are added only after that overlay is up and the stack probe is healthy. Token-successful completions are still unproven; routing already fails closed with a 9router credential miss when no upstream is connected.

## 2. Goals

- One Odysseus UI: this repository’s OpenHands + 9router dashboard, not a second product board.
- Canvas available beside it via the existing “Open in Agent Canvas” URL; not embedded; not a second chat product.
- Bring `docker-compose.yml` + `docker-compose.openhands.yml` up on the Linux VM and prove health with the existing probe/contract tests.
- Isolate Mac 9router from overlay 9router so the host install is not a port or version collision.
- Connect providers on the VM overlay 9router after deploy, so live native / OpenCode / Hermes acceptance can get tokens without changing the Mac install.
- Leave HHPE relay and other HHPE adapter compose files out of this stack. 9router and OpenHands do not enter through HHPE.

## 3. Non-goals

- Starting the overlay on macOS Docker Desktop.
- Pointing Agent Server, OpenCode, Hermes, or the model-job worker at the Mac 9router.
- Attaching or preserving the HHPE Odysseus relay as part of this deploy.
- Running the vendored VM Odysseus (`external/vendors/odysseus` on port 7000) in parallel with this board.
- Adding or expanding provider-connection tests before a deployable overlay exists.
- Bumping overlay 9router from the pinned 0.5.69 image to host 0.5.75 in this slice.
- Egress-enforced sole-provider, Hermes on the Odysseus chooser, Task 18 deletion, or modifying 9router / Canvas source.
- GPU Cookbook serving; the VM has no NVIDIA devices in the last inspection.

## 4. Surfaces and ownership

| Surface | Role | This slice |
|---|---|---|
| Odysseus dashboard (this repo) | Canonical production frontend | Replace the VM’s current Odysseus board; keep OpenHands + 9router workflow |
| Agent Canvas | Engineering UI | Run in parallel on its published port; same Agent Server |
| Overlay 9router | Provider authority for this stack | Compose service; providers added here after health |
| Mac 9router 0.5.75 | Existing host install | Untouched; not the overlay inference plane |
| HHPE relay / adapter | Unrelated topology | Do not include; do not send OpenHands or 9router through it |
| OpenHands Agent Server / Automation | Canonical agent runtime | Overlay services; ports unpublished except Canvas |

Interactive work: user submits on Odysseus → OpenHands executes → runtime calls overlay 9router → Odysseus projects. Canvas may open the same conversation. Bounded jobs use the overlay worker → overlay 9router. Domain MCP stays Odysseus; it is not HHPE and not 9router.

## 5. Target environment

- **VM:** VMware Fusion Ubuntu 64-bit, NAT `172.16.170.128` when running, 8 vCPU, ~8 GiB RAM, Docker Engine 29.x on the guest.
- **Access:** SSH `orchestration-vm` as `agent`; Docker socket is in group `oldmac-vm`; `sudo -n docker` works for `agent`.
- **Last observed occupant:** a base Odysseus compose (web, Chroma, SearxNG, plus an HHPE relay file) on `127.0.0.1:7000` from a vendored tree. That board is superseded for this workflow. The relay file is not part of the replacement compose.
- **Host Mac:** Docker Desktop off; native 9router listening on loopback 20128. Do not start overlay 9router on the Mac.

The VM may be powered off between sessions. Power it on before deploy. First deploy uses loopback binds on the guest (`APP_BIND=127.0.0.1`, Canvas bind `127.0.0.1`) and reachability via SSH/`docker compose exec`, not a public bind.

## 6. Deploy shape

One Compose project from **this** repository checkout on the VM (not the vendored HHPE copy):

1. Checkout or sync this branch onto the guest in a dedicated directory owned by the operator who can run Docker.
2. Copy `.env.example` → `.env`; keep auth on; do not publish Agent Server or Automation.
3. Install pinned OpenCode and Hermes Linux binaries into `data/openhands-runtime-bin` with `scripts/install_openhands_runtime_bin.py`.
4. Build `odysseus-odysseus:latest` from this tree so overlay services that `pull_policy: never` that image are this board, not a stale vendor image.
5. `docker compose -f docker-compose.yml -f docker-compose.openhands.yml up -d --wait --pull never`.
6. `python3 scripts/openhands_probe.py stack` then contract/reachability tests. Do not connect providers until those pass.

Stop the old 7000 Odysseus **project** before binding the new one to 7000 so there is a single board. Do not compose in `docker-compose.relay.yml` or any HHPE adapter overlay.

Rollback is: previous guest Odysseus compose (without claiming it as the OpenHands + 9router board) plus pinned images in `deploy/openhands/versions.env`. Overlay 9router data stays under the guest `APP_DATA_DIR` 9router directory; it is not the Mac `DATA_DIR`.

## 7. Provider timing

Live three-runtime evidence already shows requests hitting overlay 9router and failing with no active credentials (observed provider miss: `kiro`). That is sufficient to refuse more “add a provider in tests” work on the Mac.

After stack health on the VM:

1. Connect upstream providers **inside overlay 9router** on the guest (9router-hosted auth; Odysseus stores only opaque connection identity if product UX is used).
2. Re-run live native / OpenCode / Hermes probes expecting tokens, still with no direct-provider fallback.
3. Leave the Mac 9router install as-is so host tools keep their existing credentials.

Do not copy provider tokens from the Mac install into Odysseus, Agent Server env, or git.

## 8. Error handling

- Overlay 9router down or unhealthy: Agent Server and worker fail closed; no `stream_llm` interactive fallback; no silent Mac-9router hop.
- Port 7000 still held by the old project: do not start a second Odysseus; stop or rebind the old project first.
- Missing `/opt/oh-bin` pins: ACP live tests skip or fail honestly; install pins before claiming ACP acceptance.
- Guest Docker permission: use the account that can talk to the engine (`oldmac-vm` or `agent` with `sudo -n docker`); do not weaken the socket to world-writable.
- Host 9router remains up: that is expected; it is not a health signal for the overlay.

## 9. Testing

- Compose config quiet + `up --wait --pull never` on the guest.
- `scripts/openhands_probe.py stack` (and acceptance only after providers exist).
- `tests/integration/openhands/test_stack_contract.py` and `test_9router_reachability.py` against that compose.
- Live three-runtime tests after providers: still assert 9router, still forbid direct OpenAI/Anthropic/ChatGPT markers.
- No new HHPE relay tests. No new Mac-host overlay bring-up as the acceptance path.

## 10. Out of product identity

HHPE registry, relay, Forgejo, NATS, and other guest services stay whatever they are. This slice does not make Odysseus an HHPE ingress for models. OpenHands and 9router stay on the Odysseus overlay network.
