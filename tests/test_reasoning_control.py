"""Unit tests for per-model reasoning control (src/reasoning_control.py).

Scope: Category #1 — the "/think" soft-switch directive. Covers that it injects
only for /think-dialect models when reasoning is "on", never for off/auto, and
never for models that use a different mechanism (so no directive is leaked to a
model that wouldn't understand it).
"""
from src.reasoning_control import reasoning_directive, inject_directive, reasoning_mode_for, ON, OFF, AUTO


class TestReasoningDirective:
    def test_nemotron_vl_on_injects_think(self):
        assert reasoning_directive("nemotron-nano-12b-vl", ON) == "/think"

    def test_nemotron_vl_variant_matches(self):
        assert reasoning_directive("nvidia/nemotron-nano-vl-8b", ON) == "/think"

    def test_off_and_auto_inject_nothing(self):
        assert reasoning_directive("nemotron-nano-12b-vl", OFF) is None
        assert reasoning_directive("nemotron-nano-12b-vl", AUTO) is None

    def test_non_think_models_unchanged(self):
        # Models that use a different mechanism (or none) must NOT get /think.
        assert reasoning_directive("qwen3-vl-30b", ON) is None
        assert reasoning_directive("gpt-oss-120b", ON) is None
        assert reasoning_directive("llama-3.3-70b-instruct", ON) is None


class TestInjectDirective:
    def test_string_content(self):
        msgs = [{"role": "user", "content": "hello"}]
        inject_directive(msgs, "/think")
        assert msgs[0]["content"] == "/think hello"

    def test_multimodal_list_content(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        inject_directive(msgs, "/think")
        assert msgs[0]["content"][0] == {"type": "text", "text": "/think"}

    def test_idempotent(self):
        msgs = [{"role": "user", "content": "/think hello"}]
        inject_directive(msgs, "/think")
        assert msgs[0]["content"] == "/think hello"

    def test_targets_latest_user_turn(self):
        msgs = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "x"},
            {"role": "user", "content": "second"},
        ]
        inject_directive(msgs, "/think")
        assert msgs[0]["content"] == "first"
        assert msgs[2]["content"] == "/think second"


class TestReasoningModeFor:
    def test_unknown_url_degrades_to_auto(self):
        assert reasoning_mode_for("some-model", "http://nonexistent.invalid:9/v1") == AUTO


class TestEndpointResolution:
    """#6 — when several endpoint rows share a base URL, the preference must be
    read from the row that actually serves the model; and a model no row serves
    must not pick up a stray preference (no-leak)."""

    def test_same_base_url_disambiguates_by_model(self):
        import json
        from core.database import SessionLocal, ModelEndpoint, Base, engine
        # CI runs bare pytest against an in-memory SQLite (see conftest); the
        # model_endpoints table may not be present on the active connection by the
        # time this runs, so ensure it exists before seeding (no-op if present).
        Base.metadata.create_all(bind=engine)
        url = "http://shared-rc-test.invalid:9911/v1"
        ids = ["rc-test-a", "rc-test-b"]
        db = SessionLocal()
        try:
            db.query(ModelEndpoint).filter(ModelEndpoint.id.in_(ids)).delete(synchronize_session=False)
            db.add_all([
                ModelEndpoint(id="rc-test-a", name="A", base_url=url, is_enabled=True,
                              cached_models=json.dumps(["model-a"]),
                              reasoning_modes=json.dumps({"model-a": "on"})),
                ModelEndpoint(id="rc-test-b", name="B", base_url=url, is_enabled=True,
                              cached_models=json.dumps(["model-b"]),
                              reasoning_modes=json.dumps({"model-b": "off"})),
            ])
            db.commit()
        finally:
            db.close()
        try:
            # each model resolves via the row that actually serves it...
            assert reasoning_mode_for("model-a", url) == ON
            assert reasoning_mode_for("model-b", url) == OFF
            # ...and a model neither row serves gets no stray preference.
            assert reasoning_mode_for("model-c", url) == AUTO
        finally:
            db = SessionLocal()
            try:
                db.query(ModelEndpoint).filter(ModelEndpoint.id.in_(ids)).delete(synchronize_session=False)
                db.commit()
            finally:
                db.close()
