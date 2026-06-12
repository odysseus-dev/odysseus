"""Tests for the media model registry (Slice 2).

Covers normalization, enabled-model listing, default resolution, the shared
degraded-state response shape, the public projection, and integration with the
settings persistence layer. No network calls are exercised (the registry makes
none in S2).
"""

import json

from src import media_registry as mr


# ── Fixtures / helpers ──

def _comfy_image(model_id="qwen-image-comfy", **over):
    base = {
        "id": model_id,
        "label": "Qwen-Image",
        "provider": "comfyui",
        "kind": "image",
        "capabilities": ["text-to-image", "image-edit"],
        "endpointUrl": "http://localhost:8188",
        "enabled": True,
    }
    base.update(over)
    return base


def _settings(models, **extra):
    cfg = {
        "media_models": models,
        "default_image_media_model": "",
        "comfyui_endpoint_url": "",
    }
    cfg.update(extra)
    return cfg


# ── Normalization ──

def test_normalize_valid_entry():
    m = mr.normalize_model(_comfy_image(), settings=_settings([]))
    assert m["id"] == "qwen-image-comfy"
    assert m["provider"] == "comfyui"
    assert m["kind"] == "image"
    assert m["capabilities"] == ["text-to-image", "image-edit"]
    assert m["enabled"] is True
    assert m["isDefault"] is False


def test_normalize_rejects_missing_id():
    assert mr.normalize_model({"label": "no id"}, settings=_settings([])) is None
    assert mr.normalize_model({"id": "   "}, settings=_settings([])) is None
    assert mr.normalize_model("not a dict", settings=_settings([])) is None


def test_normalize_coerces_unknown_provider_and_kind():
    m = mr.normalize_model(
        {"id": "x", "provider": "midjourney", "kind": "hologram"},
        settings=_settings([]),
    )
    assert m["provider"] == "custom"
    assert m["kind"] == "image"


def test_normalize_filters_invalid_capabilities():
    m = mr.normalize_model(
        {"id": "x", "capabilities": ["text-to-image", "telepathy", 42]},
        settings=_settings([]),
    )
    assert m["capabilities"] == ["text-to-image"]


def test_normalize_enabled_defaults_true_when_omitted():
    m = mr.normalize_model({"id": "x"}, settings=_settings([]))
    assert m["enabled"] is True


def test_normalize_comfyui_inherits_global_endpoint():
    cfg = _settings([], comfyui_endpoint_url="http://host:8188")
    m = mr.normalize_model({"id": "x", "provider": "comfyui"}, settings=cfg)
    assert m["endpointUrl"] == "http://host:8188"


def test_normalize_keeps_label_fallback_to_id():
    m = mr.normalize_model({"id": "abc"}, settings=_settings([]))
    assert m["label"] == "abc"


def test_normalize_accepts_checkpoint():
    m = mr.normalize_model(
        {"id": "x", "provider": "comfyui", "checkpoint": "  sdxl.safetensors  "},
        settings=_settings([]),
    )
    assert m["checkpoint"] == "sdxl.safetensors"


def test_normalize_accepts_checkpoint_name_alias():
    m = mr.normalize_model(
        {"id": "x", "provider": "comfyui", "checkpointName": "flux.safetensors"},
        settings=_settings([]),
    )
    assert m["checkpoint"] == "flux.safetensors"


def test_normalize_checkpoint_prefers_checkpoint_over_alias():
    m = mr.normalize_model(
        {"id": "x", "checkpoint": "primary.ckpt", "checkpointName": "alias.ckpt"},
        settings=_settings([]),
    )
    assert m["checkpoint"] == "primary.ckpt"


def test_normalize_omits_checkpoint_when_absent_or_blank():
    assert "checkpoint" not in mr.normalize_model({"id": "x"}, settings=_settings([]))
    assert "checkpoint" not in mr.normalize_model(
        {"id": "x", "checkpoint": "   "}, settings=_settings([])
    )


def test_to_public_dict_omits_checkpoint():
    m = mr.normalize_model(
        {"id": "x", "provider": "comfyui", "checkpoint": "sdxl.safetensors"},
        settings=_settings([]),
    )
    pub = mr.to_public_dict(m)
    assert "checkpoint" not in pub
    assert "checkpointName" not in pub


# ── load_media_models ──

def test_load_dedupes_by_id():
    cfg = _settings([_comfy_image(), _comfy_image(label="dup")])
    models = mr.load_media_models(settings=cfg)
    assert len(models) == 1
    assert models[0]["label"] == "Qwen-Image"  # first wins


def test_load_handles_non_list_media_models():
    assert mr.load_media_models(settings={"media_models": "oops"}) == []
    assert mr.load_media_models(settings={}) == []


# ── list_enabled_models ──

def test_list_enabled_filters_disabled_and_kind():
    cfg = _settings([
        _comfy_image("a", enabled=True, kind="image"),
        _comfy_image("b", enabled=False, kind="image"),
        _comfy_image("c", enabled=True, kind="video"),
    ])
    image_ids = [m["id"] for m in mr.list_enabled_models("image", settings=cfg)]
    assert image_ids == ["a"]
    video_ids = [m["id"] for m in mr.list_enabled_models("video", settings=cfg)]
    assert video_ids == ["c"]


# ── resolve_default_model ──

def test_resolve_default_via_setting():
    cfg = _settings(
        [_comfy_image("a"), _comfy_image("b")],
        default_image_media_model="b",
    )
    assert mr.resolve_default_model("image", settings=cfg)["id"] == "b"


def test_resolve_default_via_is_default_flag():
    cfg = _settings([_comfy_image("a"), _comfy_image("b", isDefault=True)])
    assert mr.resolve_default_model("image", settings=cfg)["id"] == "b"


def test_resolve_default_single_enabled_fallback():
    cfg = _settings([_comfy_image("a"), _comfy_image("b", enabled=False)])
    assert mr.resolve_default_model("image", settings=cfg)["id"] == "a"


def test_resolve_default_ambiguous_returns_none():
    cfg = _settings([_comfy_image("a"), _comfy_image("b")])
    assert mr.resolve_default_model("image", settings=cfg) is None


def test_resolve_default_setting_ignored_when_target_disabled():
    cfg = _settings(
        [_comfy_image("a"), _comfy_image("b", enabled=False)],
        default_image_media_model="b",
    )
    # 'b' is disabled, so the single remaining enabled model wins.
    assert mr.resolve_default_model("image", settings=cfg)["id"] == "a"


# ── Degraded-state contract ──

def test_degraded_no_models_shape():
    model, degraded = mr.default_image_model_or_degraded(settings=_settings([]))
    assert model is None
    assert degraded["ok"] is False
    assert degraded["available"] is False
    assert degraded["status"] == "no_models"
    assert degraded["kind"] == "image"
    assert "no image model" in degraded["message"].lower()
    providers = [c["provider"] for c in degraded["checked"]]
    assert "comfyui" in providers
    assert len(degraded["next_steps"]) >= 1


def test_degraded_no_default_shape():
    cfg = _settings([_comfy_image("a"), _comfy_image("b")])
    model, degraded = mr.default_image_model_or_degraded(settings=cfg)
    assert model is None
    assert degraded["status"] == "no_default"
    assert {c["status"] for c in degraded["checked"]} == {
        "enabled model 'a'", "enabled model 'b'"
    }


def test_default_resolves_when_available():
    cfg = _settings([_comfy_image("a")])
    model, degraded = mr.default_image_model_or_degraded(settings=cfg)
    assert degraded is None
    assert model["id"] == "a"


def test_checked_marks_comfyui_configured_when_endpoint_set():
    cfg = _settings([], comfyui_endpoint_url="http://localhost:8188")
    _, degraded = mr.default_image_model_or_degraded(settings=cfg)
    comfy = next(c for c in degraded["checked"] if c["provider"] == "comfyui")
    assert "configured" in comfy["status"]


# ── Presentation helpers ──

def test_to_public_dict_omits_internal_paths():
    m = mr.normalize_model(
        _comfy_image(workflowPath="/local/secret/workflow.json"),
        settings=_settings([]),
    )
    pub = mr.to_public_dict(m)
    assert "endpointUrl" not in pub
    assert "workflowPath" not in pub
    assert pub["id"] == "qwen-image-comfy"
    assert pub["capabilities"] == ["text-to-image", "image-edit"]


def test_image_generation_routable_false_when_no_models_or_legacy():
    assert mr.image_generation_routable(
        settings=_settings([]),
        include_legacy=False,
        include_db=False,
    ) is False


def test_image_generation_routable_true_with_default_media_model():
    cfg = _settings([_comfy_image(isDefault=True)])
    assert mr.image_generation_routable(
        settings=cfg,
        include_legacy=False,
        include_db=False,
    ) is True


def test_image_generation_routable_true_with_legacy_image_model():
    cfg = _settings([], image_model="gpt-image-1")
    assert mr.image_generation_routable(
        settings=cfg,
        include_db=False,
    ) is True


def test_image_generation_routable_false_when_legacy_disabled(monkeypatch):
    cfg = _settings([], image_model="gpt-image-1")
    assert mr.image_generation_routable(
        settings=cfg,
        include_legacy=False,
        include_db=False,
    ) is False


def test_image_generation_routable_true_with_enabled_db_endpoint(monkeypatch):
    class _FakeQuery:
        def __init__(self, result):
            self._result = result

        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return self._result

    class _FakeDB:
        def __init__(self, result):
            self._result = result

        def query(self, *args):
            return _FakeQuery(self._result)

        def close(self):
            pass

    import src.database as db_mod

    monkeypatch.setattr(db_mod, "SessionLocal", lambda: _FakeDB(object()))
    assert mr.image_generation_routable(
        settings=_settings([]),
        include_legacy=False,
        include_db=True,
    ) is True


def test_image_generation_routable_false_when_db_fallback_disabled(monkeypatch):
    class _FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return object()

    class _FakeDB:
        def query(self, *args):
            return _FakeQuery()

        def close(self):
            pass

    import src.database as db_mod

    monkeypatch.setattr(db_mod, "SessionLocal", lambda: _FakeDB())
    assert mr.image_generation_routable(
        settings=_settings([]),
        include_legacy=False,
        include_db=False,
    ) is False


def test_format_degraded_message_renders_block():
    _, degraded = mr.default_image_model_or_degraded(settings=_settings([]))
    text = mr.format_degraded_message(degraded)
    assert "Checked:" in text
    assert "Next steps:" in text
    assert "- comfyui:" in text
    assert "1." in text
    assert "Configure a local ComfyUI endpoint in settings." in text
    assert "8188" not in text
    assert "://" not in text


# ── Settings integration ──

def test_registry_reads_from_settings_file(tmp_path, monkeypatch):
    from src import settings as s

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({"media_models": [_comfy_image("from-file", isDefault=True)]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(s, "SETTINGS_FILE", str(settings_file))
    s._invalidate_caches()
    try:
        enabled = mr.list_enabled_models("image")
        assert [m["id"] for m in enabled] == ["from-file"]
        assert mr.resolve_default_model("image")["id"] == "from-file"
    finally:
        s._invalidate_caches()


def test_normalize_model_parses_generation_timeout():
    m = mr.normalize_model(
        {"id": "x", "generationTimeoutSeconds": 420},
        settings=_settings([]),
    )
    assert m["generationTimeoutSeconds"] == 420.0


def test_resolve_generation_timeout_prefers_model_over_global():
    model = mr.normalize_model(
        {"id": "x", "generationTimeoutSeconds": 450},
        settings=_settings([]),
    )
    cfg = _settings([], comfyui_generation_timeout_seconds=300)
    assert mr.resolve_generation_timeout(model, settings=cfg) == 450.0


def test_resolve_generation_timeout_uses_global_setting():
    cfg = _settings([], comfyui_generation_timeout_seconds=360)
    assert mr.resolve_generation_timeout(settings=cfg) == 360.0


def test_resolve_generation_timeout_clamps_invalid_values():
    model = mr.normalize_model(
        {"id": "x", "generationTimeoutSeconds": 5},
        settings=_settings([]),
    )
    assert mr.resolve_generation_timeout(model, settings=_settings([])) == 30.0
    cfg = _settings([], comfyui_generation_timeout_seconds=9999)
    assert mr.resolve_generation_timeout(settings=cfg) == 900.0


def test_resolve_generation_timeout_default_when_unset():
    assert mr.resolve_generation_timeout(settings=_settings([])) == 300.0


def test_normalize_model_parses_default_size():
    m = mr.normalize_model(
        {"id": "x", "defaultSize": "768x768"},
        settings=_settings([]),
    )
    assert m["defaultSize"] == "768x768"


def test_resolve_image_size_order():
    model = mr.normalize_model(
        {"id": "x", "defaultSize": "768x768"},
        settings=_settings([]),
    )
    cfg = _settings([], comfyui_default_image_size="1024x1024")
    assert mr.resolve_image_size(explicit_size="512x512", media_model=model, settings=cfg) == "512x512"
    assert mr.resolve_image_size(media_model=model, settings=cfg) == "768x768"
    assert mr.resolve_image_size(settings=cfg) == "1024x1024"
    assert mr.resolve_image_size(settings=_settings([])) == "512x512"


def test_normalize_image_size_rejects_invalid():
    assert mr.normalize_image_size("999x999") is None
    assert mr.normalize_image_size("512x512") == "512x512"


def test_default_settings_registers_media_keys():
    """Required so /api/auth/settings can persist the registry config."""
    from src.settings import DEFAULT_SETTINGS

    assert "media_models" in DEFAULT_SETTINGS
    assert "default_image_media_model" in DEFAULT_SETTINGS
    assert "comfyui_endpoint_url" in DEFAULT_SETTINGS
    assert "comfyui_generation_timeout_seconds" in DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["comfyui_generation_timeout_seconds"] == 300
    assert "comfyui_default_image_size" in DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["comfyui_default_image_size"] == "512x512"
    assert DEFAULT_SETTINGS["media_models"] == []
