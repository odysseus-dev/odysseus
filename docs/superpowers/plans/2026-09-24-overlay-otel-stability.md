# Overlay OTel Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Overlay Native chat can be proven and debugged without a phone: leftover cloud rows gone, OTel session tree in Grafana Tempo, LLM lens in Langfuse, time series in Prometheus, all via unpublished Collector.

**Architecture:** Odysseus emits OTLP only to `otel-collector:4318`. Collector redacts secrets and fans out to Tempo, Langfuse, and a Prometheus scrape endpoint. Grafana Explore is the waterfall + PromQL UI. Synthetics return `trace_id`. OpenHands stays the Native runtime; Odysseus stays the only product board.

**Tech Stack:** OpenTelemetry Python SDK, `opentelemetry-instrumentation-fastapi`, `opentelemetry-instrumentation-httpx`, OpenLLMetry httpx/openai instrumentations with Traceloop export disabled, otel/opentelemetry-collector-contrib, grafana/tempo, grafana/grafana, prom/prometheus, self-hosted Langfuse v3 (official compose deps), FastAPI Odysseus, overlay Compose on `orchestration-vm`.

## Global Constraints

- Guest overlay: SSH `orchestration-vm`, `/home/agent/work/odysseus`. Mac Docker is not the overlay.
- Compose files for this project: `docker-compose.yml` + `docker-compose.openhands.yml` + `docker-compose.observability.yml`. Never `docker-compose.relay.yml` / HHPE.
- Grafana, Tempo, Prometheus, Langfuse, Collector unpublished (guest `127.0.0.1` only). Odysseus Tailscale URL stays the product. Do not Serve Grafana/Langfuse.
- Apps set `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318` only. No Tempo/Langfuse URLs in Odysseus env.
- Collector deny-list (case-insensitive substring): `authorization`, `api_key`, `cookie`, `x-9r-cli-token`, `virtual_key`. Drop `enc:` values.
- No `gen_ai.input.messages` or completion bodies. Never log 9router virtual keys.
- No Jaeger, no OpenLIT UI, no Traceloop Cloud, no Langfuse Cloud.
- ChatGPT IdP login is out of synthetics. DITL stays for model quality, HF, Native vs OpenCode, Agent vs Chat.
- Do not add more Odysseus product features until guest stability probe exit 0 and Grafana shows that `trace_id`.
- Commit `feat:` / `test:` / `fix:` / `docs:`. Do not push unless asked.
- Pin images with digest or immutable tag in `deploy/observability/versions.env`. No `:latest`.

## File map

- Create: `services/observability/redact.py` — deny-list used by tests and documented as Collector contract
- Create: `services/observability/otel.py` — tracer provider, OTLP HTTP, no-op if endpoint empty
- Create: `services/observability/metrics.py` — `cloud_rows` + probe success gauges
- Create: `deploy/observability/otel-collector.yaml`
- Create: `deploy/observability/tempo.yaml`
- Create: `deploy/observability/prometheus.yml`
- Create: `deploy/observability/grafana/datasources.yaml`
- Create: `deploy/observability/versions.env`
- Create: `docker-compose.observability.yml`
- Create: `scripts/overlay_stability_probe.py`
- Create: `scripts/run_overlay_stability_probe.sh`
- Create: `tests/test_otel_redact.py`
- Create: `tests/test_overlay_stability_probe.py`
- Modify: `core/database.py` — `_purge_leftover_cloud_model_endpoints` (already drafted)
- Modify: `routes/model_routes.py` — `purge_leftover_cloud_model_endpoints`
- Modify: `tests/test_overlay_chat_defaults.py` — purge tests (already drafted)
- Modify: `services/agents/openhands_client.py` — lifecycle spans
- Modify: `services/agents/legacy_bridge.py` — `invoke_agent Native`
- Modify: `routes/model_routes.py` — `overlay.bind` span
- Modify: `app.py` — call `configure_tracer("odysseus")`
- Modify: `requirements.txt` — OTel + OpenLLMetry packages
- Modify: `scripts/openhands_probe.py` — `COMPOSE_FILES` includes observability yml
- Modify: `tests/integration/openhands/test_stack_contract.py` — three overlay files, still no HHPE
- Modify: `docs/operations/linux-vm-openhands-overlay.md`

---

### Task 1: Land leftover cloud ModelEndpoint purge

**Files:**
- Modify: `routes/model_routes.py` (`purge_leftover_cloud_model_endpoints`, `_session_uses_leftover_endpoint_url`)
- Modify: `core/database.py` (`_purge_leftover_cloud_model_endpoints` from `init_db`)
- Modify: `tests/test_overlay_chat_defaults.py`
- Modify: `docs/plans/2026-09-20-settings-clarity-slice-c-d.md`

**Interfaces:**
- Consumes: `_is_public_cloud_inference_url(base_url: str) -> bool`, `overlay_ninerouter_chat_url() -> str`, `overlay_chat_route(model: str | None) -> str`
- Produces: `purge_leftover_cloud_model_endpoints(db) -> dict` with keys `deleted: int`, `sessions: int`

- [ ] **Step 1: Confirm purge tests exist and fail if function is missing**

If `purge_leftover_cloud_model_endpoints` is already in `routes/model_routes.py`, skip to Step 4. Otherwise write the tests already in `tests/test_overlay_chat_defaults.py` (`test_purge_deletes_cloud_rows_and_keeps_local`, `test_purge_noop_when_only_local_leftover`).

- [ ] **Step 2: Run tests**

```bash
python3 -m pytest tests/test_overlay_chat_defaults.py -q --tb=short
```

Expected: PASS (including purge cases). If AttributeError, implement Task 1 Step 3 from the spec: delete public-cloud rows, keep `http://ollama:11434/v1` and `http://192.168.1.10:8080/v1`, rebind matching sessions to `http://9router:20128/v1` + `automatic`, clear `default_endpoint_id`.

- [ ] **Step 3: `init_db` calls purge after encrypt keys**

`core/database.py` `init_db` must call `_purge_leftover_cloud_model_endpoints()` which imports `purge_leftover_cloud_model_endpoints` inside the function and uses `SessionLocal()`.

- [ ] **Step 4: Commit**

```bash
git add routes/model_routes.py core/database.py tests/test_overlay_chat_defaults.py docs/plans/2026-09-20-settings-clarity-slice-c-d.md
git commit -m "$(cat <<'EOF'
feat: purge leftover cloud ModelEndpoint rows

Overlay chat uses 9router; pre-slice-D cloud keys must leave Odysseus SQLite.
EOF
)"
```

---

### Task 2: Secret deny-list (Collector contract)

**Files:**
- Create: `services/observability/redact.py`
- Create: `tests/test_otel_redact.py`

**Interfaces:**
- Consumes: none
- Produces:
  - `DENY_SUBSTRINGS: tuple[str, ...] = ("authorization", "api_key", "cookie", "x-9r-cli-token", "virtual_key")`
  - `redact_span_attributes(attrs: dict[str, object]) -> dict[str, object]`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_otel_redact.py
from services.observability.redact import redact_span_attributes

def test_redact_drops_authorization_and_keeps_model():
    out = redact_span_attributes({
        "http.request.header.authorization": "Bearer sk-live",
        "gen_ai.request.model": "openai/cx/gpt-5.5",
        "api_key": "sk-live",
        "gen_ai.input.messages": [{"role": "user", "content": "Hello"}],
    })
    assert "authorization" not in str(out).lower()
    assert "sk-live" not in str(out)
    assert out["gen_ai.request.model"] == "openai/cx/gpt-5.5"
    assert "gen_ai.input.messages" not in out


def test_redact_drops_enc_prefix_values():
    out = redact_span_attributes({"note": "enc:abc", "http.status_code": 401})
    assert "enc:" not in str(out.values())
    assert out["http.status_code"] == 401
```

- [ ] **Step 2: Run test, expect fail**

```bash
python3 -m pytest tests/test_otel_redact.py -q --tb=short
```

Expected: `ModuleNotFoundError` or import error.

- [ ] **Step 3: Implement**

```python
# services/observability/redact.py
"""Collector contract: drop secrets and prompt bodies from span attributes.

Agents: keep this list in sync with deploy/observability/otel-collector.yaml.
"""
DENY_SUBSTRINGS = (
    "authorization",
    "api_key",
    "cookie",
    "x-9r-cli-token",
    "virtual_key",
)
DROP_KEYS = frozenset({"gen_ai.input.messages", "gen_ai.output.messages"})


def redact_span_attributes(attrs: dict[str, object]) -> dict[str, object]:
    kept: dict[str, object] = {}
    for key, value in attrs.items():
        low = str(key).lower()
        if key in DROP_KEYS or any(s in low for s in DENY_SUBSTRINGS):
            continue
        if isinstance(value, str) and value.startswith("enc:"):
            continue
        kept[key] = value
    return kept
```

- [ ] **Step 4: Tests pass**

```bash
python3 -m pytest tests/test_otel_redact.py -q --tb=short
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/observability/redact.py tests/test_otel_redact.py
git commit -m "feat: redact OTel span secrets and prompt bodies"
```

---

### Task 3: Unpublished observability Compose

**Files:**
- Create: `deploy/observability/versions.env`
- Create: `deploy/observability/otel-collector.yaml` (OTLP 4317/4318, attributes processor using Task 2 deny-list keys, exporters: otlp/tempo, otlphttp/langfuse, prometheus)
- Create: `deploy/observability/tempo.yaml` (local filesystem, OTLP receiver)
- Create: `deploy/observability/prometheus.yml` (scrape `otel-collector:8889` and `odysseus:7000/metrics`)
- Create: `deploy/observability/grafana/datasources.yaml` (Tempo + Prometheus, default org)
- Create: `docker-compose.observability.yml`
- Modify: `tests/integration/openhands/test_stack_contract.py`

**Interfaces:**
- Consumes: Task 2 deny-list key names
- Produces: Compose service DNS names `otel-collector`, `tempo`, `prometheus`, `grafana`, `langfuse-web`, `langfuse-worker`, `langfuse-postgres`, `langfuse-redis`, `langfuse-minio`, `langfuse-clickhouse`

Pin in `deploy/observability/versions.env` (update digests at implement time if tags move; never `:latest`):

```
OTEL_COLLECTOR_IMAGE=otel/opentelemetry-collector-contrib:0.122.1
TEMPO_IMAGE=grafana/tempo:2.7.2
PROMETHEUS_IMAGE=prom/prometheus:v3.2.1
GRAFANA_IMAGE=grafana/grafana:11.6.0
LANGFUSE_IMAGE=langfuse/langfuse:3.29.0
```

Langfuse v3 follows upstream docker-compose (web, worker, postgres, redis, minio, clickhouse). All `ports` are `127.0.0.1:<port>:<port>` on the guest only. Grafana `127.0.0.1:3001:3000`; Langfuse web `127.0.0.1:3002:3000` (guest Forgejo already owns `:3000`); Tempo `127.0.0.1:3200:3200`; Prometheus `127.0.0.1:9090:9090`; Collector **no host ports** (overlay DNS only). Collector still posts to `http://langfuse-web:3000/api/public/otel`.

Collector must **not** export to Jaeger. Langfuse exporter headers from env `LANGFUSE_OTLP_AUTH` (Basic) sourced from overlay `.env` / `deploy/observability/langfuse.env` — not Odysseus SQLite.

- [ ] **Step 1: Failing contract test**

Change `test_probe_compose_files_exclude_hhpe_relay` to expect three files and that `docker-compose.observability.yml` exists, contains `otel-collector`, `tempo`, `prometheus`, `grafana`, `langfuse`, contains `127.0.0.1`, does not contain `jaeger`, `openlit`, `docker-compose.relay.yml`.

```python
assert probe.COMPOSE_FILES == (
    "docker-compose.yml",
    "docker-compose.openhands.yml",
    "docker-compose.observability.yml",
)
obs = (ROOT / "docker-compose.observability.yml").read_text(encoding="utf-8")
assert "jaeger" not in obs.lower()
assert "openlit" not in obs.lower()
assert "127.0.0.1" in obs
```

- [ ] **Step 2: Run, expect fail** (file missing / COMPOSE_FILES still two)

```bash
python3 -m pytest tests/integration/openhands/test_stack_contract.py::test_probe_compose_files_exclude_hhpe_relay -q --tb=short
```

- [ ] **Step 3: Add compose + configs.** Collector yaml `attributes`/`transform` deletes keys matching deny-list and `gen_ai.input.messages`. Export traces to `tempo:4317` and `http://langfuse-web:3000/api/public/otel`. Prometheus exporter `0.0.0.0:8889`.

- [ ] **Step 4: Update `scripts/openhands_probe.py` `COMPOSE_FILES` tuple to the three files. Tests pass.**

```bash
python3 -m pytest tests/integration/openhands/test_stack_contract.py -q --tb=short
```

- [ ] **Step 5: Commit**

```bash
git add deploy/observability docker-compose.observability.yml scripts/openhands_probe.py tests/integration/openhands/test_stack_contract.py
git commit -m "feat: add unpublished overlay Collector Tempo Grafana Prometheus Langfuse"
```

---

### Task 4: Odysseus OTel SDK (export only to Collector)

**Files:**
- Create: `services/observability/otel.py`
- Create: `tests/test_otel_configure.py`
- Modify: `requirements.txt`
- Modify: `app.py` (startup)
- Modify: `docker-compose.openhands.yml` odysseus + model-jobs env `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318`, `OTEL_SERVICE_NAME=odysseus`

**Interfaces:**
- Consumes: `OTEL_EXPORTER_OTLP_ENDPOINT`
- Produces: `configure_tracer(service_name: str) -> None`; `get_tracer(name: str)`

Packages (pin in requirements as the repo does for others, or unpinned like fastapi if that is local style):

```
opentelemetry-api
opentelemetry-sdk
opentelemetry-exporter-otlp-proto-http
opentelemetry-instrumentation-fastapi
opentelemetry-instrumentation-httpx
```

- [ ] **Step 1: Test no-op without endpoint**

```python
def test_configure_tracer_noop_without_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    from services.observability.otel import configure_tracer
    configure_tracer("odysseus")  # must not raise
```

```python
def test_configure_tracer_rejects_tempo_url(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://tempo:4318")
    from services.observability.otel import configure_tracer
    with pytest.raises(ValueError, match="otel-collector"):
        configure_tracer("odysseus")
```

Endpoint must contain `otel-collector` when set (fail closed vs leaking to Tempo).

- [ ] **Step 2: pytest fail, then implement `configure_tracer` using `OTLPSpanExporter(endpoint=f"{base}/v1/traces")` and `BatchSpanProcessor`. FastAPI instrumentation from `app.py` after FastAPI() exists: `FastAPIInstrumentor.instrument_app(app)`.**

- [ ] **Step 3: pytest pass + commit**

```bash
git commit -m "feat: export Odysseus traces only to overlay Collector"
```

---

### Task 5: Session lifecycle spans

**Files:**
- Modify: `routes/model_routes.py` — `overlay_session_bind`
- Modify: `services/agents/openhands_client.py`
- Modify: `services/agents/legacy_bridge.py`
- Modify: `tests/test_overlay_chat_defaults.py`
- Modify: `tests/agents/test_openhands_client.py`
- Create: `tests/test_otel_lifecycle_spans.py` using `opentelemetry.sdk.trace.export.in_memory_span_exporter.InMemorySpanExporter`

**Interfaces:**
- Consumes: `get_tracer("odysseus")` from Task 4
- Produces: spans named `overlay.bind`, `openhands.settings`, `openhands.create`, `openhands.idle`, `invoke_agent Native` with attributes from spec §6. Set `odysseus.synthetic` when env `ODYSSEUS_SYNTHETIC=1`. Record ERROR on non-2xx. Event `odysseus.assistant.empty` when finished and text empty. Never set `gen_ai.input.messages`.

- [ ] **Step 1: In-memory exporter test asserts span names and that `api_key` is absent from attributes after a fake OpenHands create.**

- [ ] **Step 2: Fail, implement spans, pass, commit**

```bash
git commit -m "feat: trace Native session lifecycle without secrets"
```

---

### Task 6: OpenLLMetry libraries only

**Files:**
- Modify: `services/observability/otel.py` — after SDK init, instrument httpx; set `TRACELOOP_TRACE_CONTENT=false` and do not call Traceloop.init cloud
- Modify: `requirements.txt` — `opentelemetry-instrumentation-openai` or `traceloop-sdk` **only if** it can run with `TRACELOOP_BASE_URL` unset and exporter still OTLP Collector. Prefer `opentelemetry-instrumentation-httpx` already in Task 4. If OpenLLMetry openai instrumentation pulls Traceloop telemetry, skip the SDK and keep httpx instrumentation only — still satisfies “OpenLLMetry libraries” via `opentelemetry-instrumentation-openai-v2` if that package exports OTLP without SaaS.

**Decision locked here:** use `opentelemetry-instrumentation-httpx` (already) plus `opentelemetry-instrumentation-openai` if importable without Traceloop export. Do not add `traceloop-sdk`. Tests: `test_otel_configure.py` asserts `TRACELOOP` env is `TRACELOOP_TRACE_CONTENT=false` after configure, and `OTEL_EXPORTER_OTLP_ENDPOINT` still collector.

- [ ] **Step 1: Test TRACELOOP_TRACE_CONTENT false and no TRACELOOP_API_KEY required**
- [ ] **Step 2: Implement, commit**

```bash
git commit -m "feat: instrument outbound HTTP for 9router without Traceloop cloud"
```

---

### Task 7: Prometheus gauges + `/metrics`

**Files:**
- Create: `services/observability/metrics.py`
- Modify: `app.py` — mount Prometheus `/metrics` (prometheus_client)
- Modify: `requirements.txt` — `prometheus_client`
- Modify: `purge` path to set `odysseus_model_endpoints_cloud_rows`
- Create: `tests/test_overlay_metrics.py`

**Interfaces:**
- Produces: `set_cloud_endpoint_rows(n: int) -> None`, `set_native_probe_success(ok: bool) -> None`, metrics names `odysseus_model_endpoints_cloud_rows`, `odysseus_overlay_native_probe_success`

- [ ] **Step 1: Test gauge set/get via prometheus_client REGISTRY**
- [ ] **Step 2: After purge, count remaining public-cloud rows (should be 0) and set gauge**
- [ ] **Step 3: Commit**

```bash
git commit -m "feat: expose overlay cloud-row and probe Prometheus gauges"
```

---

### Task 8: Stability probe (black-box + trace_id)

**Files:**
- Create: `scripts/overlay_stability_probe.py`
- Create: `scripts/run_overlay_stability_probe.sh` (chmod +x; SSH `orchestration-vm`; compose **three** files; exec odysseus)
- Create: `tests/test_overlay_stability_probe.py`
- Modify: `scripts/overlay_native_chat_probe.py` — import/reuse pick + completions helpers from stability or call as a function `run_native_pipe() -> dict` used as one step
- Modify: `docs/operations/linux-vm-openhands-overlay.md`

**Interfaces:**
- Produces: `main() -> int`; stdout JSON `{ok: bool, trace_id: str, session_id: str | None, steps: list}`; exit 0 iff `ok`
- Steps in order: compose files, health, settings model, sidecar bool, catalog pick, `cloud_rows`, native pipe (Hello/Hi), set probe gauge

- [ ] **Step 1: Static tests** — script exists; compose command includes `docker-compose.observability.yml`; `openai/auto` not the pinned model; `gpt-6-astra` skipped; wrapper SSHs `orchestration-vm`; never mentions Jaeger.

- [ ] **Step 2: Implement script using current span context `trace_id` from `otel.trace.get_current_span()` around the native pipe (start a root span `overlay.stability` with `odysseus.synthetic=true`).**

- [ ] **Step 3: Commit**

```bash
git commit -m "feat: add overlay stability probe with trace_id"
```

---

### Task 9: Guest live proof (exit 0 + Grafana)

**Files:** none new. Guest `/home/agent/work/odysseus`.

- [ ] **Step 1: Rsync tree excluding `.env`, `data/`, `logs/`. Do not overwrite guest `.env`.**

- [ ] **Step 2: Rebuild odysseus. Up with three compose files `--wait --pull never`.**

```bash
ssh orchestration-vm
export PATH="$HOME/.local/bin:$PATH"
cd /home/agent/work/odysseus
docker compose -f docker-compose.yml -f docker-compose.openhands.yml -f docker-compose.observability.yml up -d --wait --pull never
docker compose -f docker-compose.yml -f docker-compose.openhands.yml -f docker-compose.observability.yml ls
```

Expected: CONFIG FILES list exactly those three. No relay.

- [ ] **Step 3: `./scripts/run_overlay_stability_probe.sh` from Mac or the same inner exec on guest.**

Expected: `"ok": true`, non-empty `trace_id`, OpenHands `finished` with text.

- [ ] **Step 4: On guest, curl Grafana Tempo datasource search for that `trace_id` (Grafana API or Tempo `/api/traces/{id}` on `127.0.0.1:3200`). Confirm a span `overlay.stability` or `openhands.create`. Confirm Langfuse has a generation for the same synthetic without prompt body (UI or API on `127.0.0.1:3002`).**

If Tempo 404, Collector export is wrong — fix yaml, do not ask for a phone screenshot.

- [ ] **Step 5: Commit any pin/digest fixes from live bring-up.**

```bash
git commit -m "fix: pin overlay observability images that booted on the guest"
```

---

## Spec coverage

| Spec § | Task |
|---|---|
| Collector fan-out Tempo/Langfuse/Prometheus | 3 |
| Deny-list / no prompt bodies | 2, 3 |
| Apps → collector only | 4 |
| Session spans | 5 |
| OpenLLMetry libraries not UI | 6 |
| Prometheus series | 7 |
| Synthetics + Mac shim | 8 |
| Purge + cloud_rows | 1, 7 |
| Unpublished / three compose files / no HHPE | 3, 9 |
| Guest proof + Grafana trace_id | 9 |
| No Jaeger/OpenLIT/product Grafana Tailscale | 3 |

## Placeholder scan

No TBD. Langfuse v3 deps are named. Image tags are starting pins; Task 9 may replace tags with digests after guest pull.
