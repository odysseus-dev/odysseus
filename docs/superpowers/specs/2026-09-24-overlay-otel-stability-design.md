# Overlay stability: OpenTelemetry, Collector, Tempo, Grafana, Prometheus, Langfuse

**Date:** 2026-09-24  
**Status:** Draft for review  
**Parents:**
- `docs/superpowers/specs/2026-09-19-linux-vm-openhands-9router-deploy-design.md`
- `docs/superpowers/specs/2026-09-20-odysseus-provider-page-9router-bff-design.md`
- `docs/plans/2026-09-20-settings-clarity-slice-c-d.md`

Odysseus is the only product board. OpenHands is the Native runtime. Overlay 9router stays unpublished and owns cloud secrets. This slice does **not** add chat, Settings, or connect features. It makes the overlay **debuggable and verifiable without a developer-in-the-loop**, except for inference quality, Hugging Face / OpenHands *choices*, and project direction.

Empty Native bubbles (leftover session URL, `openai/auto`, redacted sidecar 401) were invisible because Odysseus had no correlated session lifecycle. `/api/health` and `openhands_probe.py stack` only prove processes.

## 1. Decision

Instrument Odysseus (and Odysseus→OpenHands / Odysseus→9router HTTP) with **OpenTelemetry**. Export **only** through an unpublished **Collector**. The Collector strips secrets and fans out:

| Backend | Job |
|---|---|
| **Grafana Tempo** | Trace store |
| **Grafana** | Waterfall (Explore traces) + Prometheus (Explore metrics) |
| **Prometheus** | Time series: error rate, latency, probe pass/fail, leftover cloud row count |
| **Langfuse** (self-hosted) | LLM session lens on the same OTLP (`gen_ai.*`) |

**Not in this stack:** Jaeger, OpenLIT app, Traceloop Cloud, Langfuse Cloud, Tempo *without* Grafana, a second Odysseus.

**OpenLLMetry** is libraries only (httpx / OpenAI-compat), not a UI. It emits OTLP into the same Collector.

Synthetics walk the real Native pipe and return `trace_id` + `session_id`. Debug is Grafana/Langfuse on that id, not a phone screenshot.

Slice D leftover **cloud `ModelEndpoint` purge** ships in this same work: startup purge plus a Prometheus gauge that must stay 0.

## 2. Goals

- One correlated tree per Native turn: `session.create` → `overlay.bind` → `openhands.settings` → `openhands.create` → `chat` → `openhands.idle`.
- W3C `traceparent` across Odysseus HTTP, OpenHands client, 9router httpx.
- `gen_ai.conversation.id` = Odysseus session id. OpenHands conversation id is a span attribute, not a second product id.
- Collector deny-list: `Authorization`, `Cookie`, `x-9r-cli-token`, virtual keys, `api_key`, `enc:` payloads.
- No `gen_ai.input.messages` / output bodies by default.
- Grafana, Tempo, Prometheus, Langfuse, Collector unpublished on the overlay network (same rule as 9router). Operator browser uses Odysseus on Tailscale. Observability is guest `127.0.0.1` bind or `docker compose exec`, not a public board.
- Headless gate: exit 0 only when structural invariants hold and a one-word Native pipe returns assistant text, with a `trace_id` in the JSON.
- Developer-in-the-loop stays for model quality, Hugging Face, Native vs OpenCode, Agent vs Chat, ChatGPT IdP login.

## 3. Non-goals

- Replacing OpenHands Agent Server, Canvas, or Odysseus chat/Settings.
- Publishing Grafana or Langfuse on Tailscale as a product URL.
- Capturing prompts, completions text, or 9router virtual keys in traces.
- Alertmanager / paging (later).
- Grafana dashboards as a ship requirement (Explore is enough).
- Instrumenting 9router’s Node process in this slice (Odysseus-side client spans cover Native chat).
- OpenLIT UI, Jaeger, host-wide OTel, HHPE.

## 4. Ownership

| Surface | Owns | Must not |
|---|---|---|
| Odysseus | Span creation around session/chat/OpenHands client; `/metrics` or OTLP metrics; synthetic runner inside the image | Store cloud keys; become a trace UI |
| OpenHands | Runtime. Odysseus client wraps calls | New credential admin |
| Collector | Process, redact, fan-out OTLP | Be skipped by app exporters |
| Tempo | Trace storage | Serve a second product UI |
| Grafana | Trace waterfall + PromQL Explore | Replace Odysseus |
| Prometheus | Scrape Collector + Odysseus metrics | Explain a single empty bubble by itself |
| Langfuse | LLM observation UI on OTLP | Cloud; prompt store by default |
| Overlay 9router | Inference secrets | OTLP backend |

## 5. Where this is built

Linux VM overlay (`orchestration-vm`, `/home/agent/work/odysseus`). Observability lives in **`docker-compose.observability.yml`**, always passed with overlay `up` together with `docker-compose.yml` and `docker-compose.openhands.yml`. Do not fold Grafana/Langfuse into the OpenHands overlay file. `docker compose ls` for this project lists those three files. HHPE relay remains forbidden.

Live proof is guest-only, same as Native chat. Mac Docker is not the overlay. Host shim: SSH to `orchestration-vm` then compose exec (same pattern as `run_overlay_native_chat_probe.sh`).

## 6. Session lifecycle (white-box)

Span names (OTel GenAI where applicable):

| Span | When | Required attributes |
|---|---|---|
| `POST /api/session` (FastAPI auto) | Session create | `http.status_code` |
| `overlay.bind` | `overlay_session_bind` result | `odysseus.overlay` bool; `url.full` redacted to host+path; `gen_ai.conversation.id` |
| `invoke_agent Native` | `legacy_bridge` / OpenHands create-or-resume | `gen_ai.operation.name=invoke_agent`; `gen_ai.agent.name=Native`; OpenHands conv id |
| `openhands.settings` | GET `/api/settings` | status; `odysseus.sidecar_restored` bool — never the key |
| `openhands.create` | POST `/api/conversations` | status; `gen_ai.request.model` |
| `chat {model}` | 9router `/v1/chat/completions` via OpenHands or probe | `gen_ai.operation.name=chat`; `gen_ai.provider.name=9router`; `gen_ai.request.model`; `http.status_code` |
| `openhands.idle` | Poll until terminal status | `odysseus.execution_status` (`finished` / `error` / …) |

Errors recorded on the span (`status=ERROR`, exception type). Empty assistant + `finished` is still a span event `odysseus.assistant.empty` so Langfuse/Grafana can filter it.

Synthetic turns set `odysseus.synthetic=true`.

## 7. Collector

OTLP gRPC 4317 and HTTP 4318, unpublished.

Processors (order): attributes delete / redact keys matching deny-list (case-insensitive substring: `authorization`, `api_key`, `cookie`, `x-9r-cli-token`, `virtual_key`); batch; export.

Exporters:

- OTLP → Tempo
- OTLP/HTTP → Langfuse (`/api/public/otel`) with Langfuse keys from overlay env file **not** Odysseus SQLite
- Prometheus exporter (or `prometheusremotewrite`) so Prometheus scrapes processed metrics

Apps (`odysseus`, `odysseus-model-jobs`) set `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318` only. They do not know Tempo/Langfuse URLs.

## 8. Prometheus (why it exists)

Traces explain **one** Native turn. Prometheus answers **whether the overlay is still broken**.

Required series (names may match OTel metric naming after Collector):

- `odysseus_overlay_native_probe_success` (0/1 last run)
- HTTP 5xx / 401 counts on OpenHands client and 9router client spans (or Odysseus RED metrics)
- `odysseus_model_endpoints_cloud_rows` (must be 0 after purge)
- Process `up` for odysseus, 9router, openhands-agent-server, collector, tempo, prometheus, langfuse

No Grafana dashboard ship gate. Explore is enough.

## 9. Synthetics (black-box)

`scripts/overlay_stability_probe.py` (guest, inside `odysseus` or compose network):

1. Compose project files are overlay-only (no `docker-compose.relay.yml`).
2. Health: Odysseus `/api/health`, 9router `/api/health`, Agent Server `/health`.
3. Native settings model is `openai/cx/…`, base `http://9router:20128/v1`, not `openai/auto`.
4. Sidecar key file non-empty (boolean only).
5. Catalog GET 200; pick skips `gpt-6-astra` / `-review`.
6. `cloud_rows == 0`.
7. One-word completions + OpenHands `finished` with text (stack pipe, not model eval).
8. Print JSON `{ok, trace_id, session_id, steps[]}` exit 0/1.

Mac: `scripts/run_overlay_stability_probe.sh` SSHs to the guest. Do not `docker compose` against Mac `docker.sock`.

The Native-chat-only script becomes a step of this probe, not a second ritual.

ChatGPT device login is **out** of synthetics.

## 10. Leftover cloud purge

`purge_leftover_cloud_model_endpoints` on `init_db`: delete public-cloud `ModelEndpoint` rows; keep LAN/loopback/docker leftover; rebind those sessions to overlay 9router `automatic`; clear `default_endpoint_id` pointing at purged ids. Metric `odysseus_model_endpoints_cloud_rows` after purge.

## 11. Testing

- Unit: Collector config deny-list fixtures (example attributes stripped); overlay bind; purge keep-local; probe JSON schema without live 9router.
- Guest live: stability probe exit 0; Grafana can fetch the printed `trace_id`; Langfuse shows a `chat` generation for that synthetic with no prompt body and no key.
- Do not treat missing live overlay as pass.

## 12. Risks

- Langfuse self-host adds DB (Postgres). Keep it overlay-internal; pin images; no Tailscale Serve.
- OpenLLMetry must not enable Traceloop telemetry or prompt capture.
- FastAPI + OTel yield-dependencies: use async instrumentation path (known ContextVar detach bug on sync generators).
- Grafana/Langfuse auth: basic or env password on guest loopback only; not Odysseus users.

## 13. Out of order

Do not add more Odysseus product features until this overlay’s stability probe is exit 0 on the guest and a synthetic `trace_id` is visible in Grafana Tempo via Grafana.
