import pytest

import src.model_capabilities as mc
import src.model_capability_readers as readers
from src import llm_core, model_capability_cache
from src.chatgpt_subscription import validate_reasoning_effort
from src.model_capability_readers import llamaswap
from src.model_capability_readers.base import VENDOR_GENERIC_OPENAI, VENDOR_LLAMASWAP

BASE = "https://llama-swap.example/v1"
URL = "https://llama-swap.example/v1/chat/completions"

PAYLOAD = {
    "data": [
        {
            "id": "gpt-oss-20b",
            "owned_by": "llama-swap",
            "meta": {"llamaswap": {"type": "model", "reasoning_efforts": ["low", "medium", "High", "low"]}},
        },
        {"id": "qwen3.8-27b", "meta": {"llamaswap": {"reasoning_efforts": ["xhigh", "medium", "low"]}}},
        {"id": "plain-model", "meta": {"llamaswap": {"type": "model"}}},
        {"id": "bad-meta", "meta": {"llamaswap": {"reasoning_efforts": "high"}}},
        {"id": "empty", "meta": {"llamaswap": {"reasoning_efforts": []}}},
    ]
}


@pytest.fixture(autouse=True)
def _clean_cache():
    model_capability_cache.clear()
    yield
    model_capability_cache.clear()


def _records_by_id(payload, **kwargs):
    return {record.model_id: record for record in readers.records_from_payload(payload, base_url=BASE, **kwargs)}


def test_native_shape_selects_llamaswap_reader_and_claims_effort_levels_in_order():
    records = _records_by_id(PAYLOAD)

    assert {record.vendor for record in records.values()} == {VENDOR_LLAMASWAP}
    (control,) = records["gpt-oss-20b"].deterministic_controls
    assert control.control == mc.REASONING_CONTROL_EFFORT
    assert control.status == mc.ASSERTION_CLAIMED
    assert control.source == mc.SOURCE_PROVIDER_READER
    assert control.confidence == mc.CONFIDENCE_PROVIDER_REPORTED
    assert dict(control.evidence)["field"] == llamaswap.REASONING_EFFORTS_FIELD
    assert mc.reasoning_effort_levels(records["gpt-oss-20b"].deterministic_controls) == ("low", "medium", "high")
    assert mc.reasoning_effort_levels(records["qwen3.8-27b"].deterministic_controls) == ("xhigh", "medium", "low")
    for model_id in ("plain-model", "bad-meta", "empty"):
        assert records[model_id].deterministic_controls == ()


def test_llamaswap_records_stay_inventory_only_apart_from_the_effort_claim():
    record = _records_by_id(PAYLOAD)["gpt-oss-20b"]

    assert record.capability.family == mc.FAMILY_UNKNOWN
    assert record.capability.capabilities == ()
    assert record.capability_assertions == ()


def test_payload_without_native_shape_stays_generic_and_conservative():
    payload = {"data": [{"id": "gpt-oss-20b", "meta": {"reasoning_efforts": ["low"]}, "reasoning_efforts": ["low"]}]}
    (record,) = readers.records_from_payload(payload, base_url=BASE)

    assert record.vendor == VENDOR_GENERIC_OPENAI
    assert record.deterministic_controls == ()


def test_native_shape_does_not_override_a_recognized_vendor_reader():
    payload = {"data": [{"id": "m", "meta": {"llamaswap": {"reasoning_efforts": ["low"]}}}]}
    (record,) = readers.records_from_payload(payload, base_url="https://openrouter.ai/api/v1")

    assert record.vendor != VENDOR_LLAMASWAP
    assert mc.reasoning_effort_levels(record.deterministic_controls) == ()


def test_supported_parameter_lists_never_promote_reasoning_effort_to_a_control():
    controls = mc.deterministic_controls_from_values(["reasoning_effort", "effort", "temperature"])

    assert [control.control for control in controls] == [mc.CONTROL_TEMPERATURE]


def test_effort_control_round_trips_through_dict():
    control = mc.reasoning_effort_control(["low", "high"], field="x")

    assert mc.DeterministicControl.from_dict(control.to_dict()) == control
    assert mc.reasoning_effort_levels([control]) == ("low", "high")


def test_unsupported_or_unknown_effort_controls_yield_no_levels():
    unsupported = mc.reasoning_effort_control(["low"], field="x", status=mc.ASSERTION_UNSUPPORTED)

    assert mc.reasoning_effort_levels([unsupported]) == ()
    assert mc.reasoning_effort_control([], field="x") is None
    assert mc.reasoning_effort_control("low", field="x") is None


def test_cache_scopes_by_endpoint_and_reprobe_replaces_records():
    model_capability_cache.record_models_payload(BASE, PAYLOAD)

    assert model_capability_cache.reasoning_effort_levels("gpt-oss-20b", BASE) == ("low", "medium", "high")
    assert model_capability_cache.reasoning_effort_levels("gpt-oss-20b", "https://other.example/v1") == ()
    assert model_capability_cache.reasoning_effort_levels("gpt-oss-20b") == ("low", "medium", "high")

    model_capability_cache.record_models_payload(BASE, {"data": [{"id": "gpt-oss-20b"}]})
    assert model_capability_cache.reasoning_effort_levels("gpt-oss-20b", BASE) == ()


def test_cache_scopes_by_route_prefix_on_same_host():
    swap_base = "https://gateway.example/swap/v1"
    plain_base = "https://gateway.example/plain/v1"
    model_capability_cache.record_models_payload(swap_base, PAYLOAD)
    model_capability_cache.record_models_payload(plain_base, {"data": [{"id": "gpt-oss-20b"}]})

    assert model_capability_cache.reasoning_effort_levels("gpt-oss-20b", swap_base) == ("low", "medium", "high")
    assert model_capability_cache.reasoning_effort_levels("gpt-oss-20b", plain_base) == ()
    assert model_capability_cache.reasoning_effort_levels("gpt-oss-20b", f"{swap_base}/chat/completions") == ("low", "medium", "high")


def test_reader_failure_leaves_the_endpoint_unknown(monkeypatch):
    model_capability_cache.record_models_payload(BASE, PAYLOAD)

    def _boom(*args, **kwargs):
        raise RuntimeError("reader bug")

    monkeypatch.setattr(model_capability_cache, "records_from_payload", _boom)
    model_capability_cache.record_models_payload(BASE, PAYLOAD)
    assert model_capability_cache.reasoning_effort_levels("gpt-oss-20b", BASE) == ()


def test_validate_keeps_claimed_effort_and_drops_others():
    model_capability_cache.record_models_payload(BASE, PAYLOAD)

    assert validate_reasoning_effort("qwen3.8-27b", "XHigh") == "xhigh"
    assert validate_reasoning_effort("gpt-oss-20b", "xhigh") is None
    assert validate_reasoning_effort("plain-model", "high") is None
    assert validate_reasoning_effort("gpt-oss-20b", "default") is None


def test_payload_gets_top_level_reasoning_effort():
    model_capability_cache.record_models_payload(BASE, PAYLOAD)
    payload = {"chat_template_kwargs": {"enable_thinking": True}}

    llm_core._apply_capability_reasoning_effort(payload, URL, "gpt-oss-20b", "high")

    assert payload == {
        "chat_template_kwargs": {"enable_thinking": True},
        "reasoning_effort": "high",
    }


def test_payload_untouched_without_matching_evidence():
    model_capability_cache.record_models_payload(BASE, PAYLOAD)
    for model, url, effort in [
        ("plain-model", URL, "high"),
        ("gpt-oss-20b", URL, "xhigh"),
        ("gpt-oss-20b", "https://other.example/v1/chat/completions", "high"),
        ("gpt-oss-20b", URL, None),
    ]:
        payload = {}
        llm_core._apply_capability_reasoning_effort(payload, url, model, effort)
        assert payload == {}


def _post_generation_settings(monkeypatch, body):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import routes.history_routes as history_routes

    session = SimpleNamespace(id="s1", model="gpt-oss-20b", thinking_mode="off",
                              temperature_override=None, max_tokens_override=None)
    row = SimpleNamespace(thinking_mode="off")

    class _Db:
        def query(self, model):
            return SimpleNamespace(filter=lambda *a: SimpleNamespace(first=lambda: row))

        def commit(self):
            pass

        def close(self):
            pass

    manager = SimpleNamespace(get_session=lambda sid: session)
    monkeypatch.setattr(history_routes, "_verify_session_owner", lambda *a, **k: None)
    monkeypatch.setattr(history_routes, "SessionLocal", _Db)
    app = FastAPI()
    app.include_router(history_routes.setup_history_routes(manager))
    response = TestClient(app).post("/api/session/s1/generation-settings", json=body)
    assert response.status_code == 200
    return response.json(), row.thinking_mode


def test_picked_effort_persists_on_the_session(monkeypatch):
    # The picker posts here; a non-ChatGPT model used to be reset to "off",
    # so the next chat request carried no effort.
    model_capability_cache.record_models_payload(BASE, PAYLOAD)
    data, stored = _post_generation_settings(
        monkeypatch, {"thinking_mode": "effort:low", "reasoning_effort": "low"})
    assert (data["reasoning_effort"], stored) == ("low", "effort:low")
    data, stored = _post_generation_settings(monkeypatch, {"thinking_mode": "effort:xhigh"})
    assert (data["reasoning_effort"], stored) == ("default", "off")


# --- first-load warm-up: the capability cache is in-memory ---


class _Col:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return ("eq", self.name, value)


class _FakeEndpointModel:
    id = _Col("id")
    is_enabled = _Col("is_enabled")


class _FakeDb:
    def __init__(self, rows):
        self.rows = rows

    def query(self, _model):
        return self

    def filter(self, *conditions):
        rows = self.rows
        for tag, field, value in conditions:
            rows = [r for r in rows if getattr(r, field) == value]
        self.rows = rows
        return self

    def all(self):
        return list(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None

    def commit(self):
        pass

    def close(self):
        pass


def _fresh_cached_endpoint():
    from datetime import datetime
    from types import SimpleNamespace

    return SimpleNamespace(
        id="ls", base_url=BASE, api_key=None, provider_auth_id=None, is_enabled=True,
        cached_models='["gpt-oss-20b"]', pinned_models="[]", model_refresh_mode=None,
        model_refresh_interval=None, updated_at=datetime.now(), created_at=datetime.now(),
    )


def test_endpoint_with_fresh_cached_list_is_probed_once_per_process(monkeypatch):
    import time
    import routes.model_routes as model_routes

    ep = _fresh_cached_endpoint()
    router = model_routes.setup_model_routes(model_discovery=None)
    # Fresh cached list, no probe yet this process: the capability cache is
    # empty, so the endpoint must still be probed.
    assert router._should_refresh_endpoint(ep, time.time())[0] is True

    probed = []
    monkeypatch.setattr(model_routes, "SessionLocal", lambda: _FakeDb([ep]))
    monkeypatch.setattr(model_routes, "ModelEndpoint", _FakeEndpointModel)
    monkeypatch.setattr(model_routes, "_disable_stale_cookbook_local_endpoints", lambda db: False)
    monkeypatch.setattr(model_routes, "_resolve_probe_key", lambda e: None)
    monkeypatch.setattr(
        model_routes, "_probe_endpoint",
        lambda base, key=None, timeout=2: probed.append(base) or ["gpt-oss-20b"],
    )
    router.warm_model_caches().wait(timeout=10)

    assert probed == [BASE]
    # Probed successfully: the normal interval applies again.
    assert router._should_refresh_endpoint(ep, time.time())[0] is False
