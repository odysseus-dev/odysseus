"""Behaviour tests for src/model_customization.py (seam a).

Each test is named after a Gherkin scenario in
openspec/changes/add-model-customization/specs/model-customization/spec.md.
Expected values come from the spec tables, never from the module itself.
"""
import pytest

from src import model_customization as mc


@pytest.fixture
def customization_file(tmp_path, monkeypatch):
    """Point the store at a temp file and start with an empty read cache."""
    path = tmp_path / "model_customizations.json"
    monkeypatch.setattr(mc, "STORE_PATH", str(path))
    reset = getattr(mc, "_reset_cache", None)
    if reset is not None:
        reset()
    yield path
    if reset is not None:
        reset()


# ── Provider Format ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://api.anthropic.com/v1/messages", "excluded"),
        ("http://localhost:11434/api/chat", "ollama-native"),
        ("http://localhost:11434/v1/chat/completions", "ollama-openai"),
        ("http://192.168.1.20:8080/v1/chat/completions", "local-openai"),
        ("https://api.groq.com/openai/v1/chat/completions", "hosted-openai"),
    ],
)
def test_urls_map_to_one_provider_format(url, expected):
    assert mc.provider_format(url) == expected


def test_a_local_ollama_v1_url_is_not_treated_as_a_generic_local_server():
    url = "http://localhost:11434/v1/chat/completions"
    fmt = mc.provider_format(url)
    assert fmt == "ollama-openai"
    assert {"top_k", "min_p", "repeat_penalty"}.isdisjoint(mc.supported_fields(fmt))


# ── Supported settings ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "fmt,expected",
    [
        ("excluded", set()),
        (
            "ollama-native",
            {"num_ctx", "temperature", "top_p", "top_k", "min_p",
             "presence_penalty", "repeat_penalty", "thinking"},
        ),
        ("ollama-openai", {"temperature", "top_p", "presence_penalty", "thinking"}),
        (
            "local-openai",
            {"temperature", "top_p", "top_k", "min_p", "presence_penalty",
             "repeat_penalty", "thinking"},
        ),
        ("hosted-openai", {"temperature", "top_p", "presence_penalty", "thinking"}),
    ],
)
def test_supported_settings_per_provider_format(fmt, expected):
    assert set(mc.supported_fields(fmt)) == expected


@pytest.mark.parametrize(
    "first,second",
    [("src.model_customization", "src.llm_core"), ("src.llm_core", "src.model_customization")],
)
def test_modules_import_in_either_order_without_a_cycle(first, second):
    """A fresh interpreter is the only clean import state, so run one."""
    import subprocess
    import sys
    from pathlib import Path

    code = (
        f"import {first}, {second}; "
        "import src.model_customization as mc; "
        "assert mc.provider_format('http://localhost:11434/api/chat') == 'ollama-native'"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]


def test_thinking_is_not_offered_for_models_without_the_thinking_toggle():
    url = "https://api.groq.com/openai/v1/chat/completions"
    supported = mc.supported_fields_for(url, "llama-3.3-70b")
    assert "thinking" not in supported
    assert "temperature" in supported


def test_thinking_is_offered_for_models_with_the_thinking_toggle():
    url = "https://api.groq.com/openai/v1/chat/completions"
    assert "thinking" in mc.supported_fields_for(url, "qwen3-32b")


# ── Validation ─────────────────────────────────────────────────────────────

OLLAMA_NATIVE_ALL = frozenset(
    {"num_ctx", "temperature", "top_p", "top_k", "min_p",
     "presence_penalty", "repeat_penalty", "thinking"}
)


@pytest.mark.parametrize(
    "setting,value",
    [
        ("temperature", 0), ("temperature", 2), ("top_p", 1),
        ("top_k", 0), ("top_k", 1000), ("min_p", 1),
        ("presence_penalty", -2), ("presence_penalty", 2),
        ("repeat_penalty", 2), ("num_ctx", 256), ("num_ctx", 1048576),
        ("thinking", "on"), ("thinking", "off"),
    ],
)
def test_boundary_values_are_accepted(setting, value):
    assert mc.validate_settings({setting: value}, OLLAMA_NATIVE_ALL) == {setting: value}


@pytest.mark.parametrize(
    "setting,value",
    [
        ("temperature", 2.5), ("top_p", 1.2), ("top_k", -1), ("top_k", 4.5),
        ("min_p", -0.1), ("presence_penalty", 3), ("repeat_penalty", 0),
        ("num_ctx", 100), ("thinking", "maybe"),
    ],
)
def test_out_of_range_values_are_rejected(setting, value):
    with pytest.raises(ValueError, match=setting):
        mc.validate_settings({setting: value}, OLLAMA_NATIVE_ALL)


def test_booleans_are_not_accepted_as_numbers():
    with pytest.raises(ValueError, match="top_k"):
        mc.validate_settings({"top_k": True}, OLLAMA_NATIVE_ALL)


def test_unknown_setting_names_are_rejected():
    with pytest.raises(ValueError, match="mirostat"):
        mc.validate_settings({"mirostat": 2}, OLLAMA_NATIVE_ALL)


def test_a_setting_the_endpoint_cannot_accept_is_rejected():
    hosted = mc.supported_fields("hosted-openai")
    with pytest.raises(ValueError, match="top_k"):
        mc.validate_settings({"top_k": 40}, hosted)


def test_a_blank_control_is_not_stored():
    assert mc.validate_settings(
        {"temperature": 0.6, "top_p": None, "top_k": ""}, OLLAMA_NATIVE_ALL
    ) == {"temperature": 0.6}


# ── Store ──────────────────────────────────────────────────────────────────

import json  # noqa: E402
from datetime import datetime  # noqa: E402

LAB_BASE = "http://localhost:11434"


def _entries_on_disk(path):
    return json.loads(path.read_text(encoding="utf-8"))["entries"]


def test_reading_before_any_save_creates_nothing(customization_file):
    assert mc.load_entries() == []
    assert not customization_file.exists()


def test_first_save_creates_the_file_with_one_entry(customization_file):
    mc.save_entry("lab-ollama", LAB_BASE, "qwen3:8b", {"temperature": 0.6})
    assert customization_file.exists()
    entries = _entries_on_disk(customization_file)
    assert len(entries) == 1
    entry = entries[0]
    assert set(entry) == {"model", "endpoint_id", "base_url", "settings", "edited_at"}
    assert entry["model"] == "qwen3:8b"
    assert entry["endpoint_id"] == "lab-ollama"
    assert entry["base_url"] == LAB_BASE
    assert entry["settings"] == {"temperature": 0.6}
    assert datetime.fromisoformat(entry["edited_at"]).tzinfo is not None


def test_saving_the_same_model_and_endpoint_again_replaces_the_entry(customization_file):
    mc.save_entry("lab-ollama", LAB_BASE, "qwen3:8b", {"temperature": 0.6})
    first = _entries_on_disk(customization_file)[0]["edited_at"]
    mc.save_entry("lab-ollama", LAB_BASE, "qwen3:8b", {"temperature": 0.3})
    entries = [e for e in _entries_on_disk(customization_file)
               if e["endpoint_id"] == "lab-ollama" and e["model"] == "qwen3:8b"]
    assert len(entries) == 1
    assert entries[0]["settings"] == {"temperature": 0.3}
    assert datetime.fromisoformat(entries[0]["edited_at"]) > datetime.fromisoformat(first)


def test_the_same_model_on_two_endpoints_keeps_two_entries(customization_file):
    mc.save_entry("lab-ollama", LAB_BASE, "qwen3:8b", {"temperature": 0.6})
    mc.save_entry("office", "http://office:11434", "qwen3:8b", {"temperature": 0.9})
    by_endpoint = {e["endpoint_id"]: e["settings"] for e in _entries_on_disk(customization_file)}
    assert by_endpoint == {"lab-ollama": {"temperature": 0.6}, "office": {"temperature": 0.9}}


def test_clearing_one_setting_removes_only_that_setting(customization_file):
    mc.save_entry("lab-ollama", LAB_BASE, "qwen3:8b", {"temperature": 0.6, "top_p": 0.9})
    cleaned = mc.validate_settings({"temperature": "", "top_p": 0.9}, OLLAMA_NATIVE_ALL)
    mc.save_entry("lab-ollama", LAB_BASE, "qwen3:8b", cleaned)
    assert _entries_on_disk(customization_file)[0]["settings"] == {"top_p": 0.9}


def test_clearing_every_setting_removes_the_entry(customization_file):
    mc.save_entry("lab-ollama", LAB_BASE, "qwen3:8b", {"temperature": 0.6})
    assert mc.save_entry("lab-ollama", LAB_BASE, "qwen3:8b", {}) is None
    assert _entries_on_disk(customization_file) == []
    assert mc.load_entries() == []


def test_an_interrupted_save_leaves_the_previous_file_intact(customization_file, monkeypatch):
    import core.atomic_io as atomic_io

    mc.save_entry("lab-ollama", LAB_BASE, "qwen3:8b", {"temperature": 0.6})
    before = customization_file.read_bytes()

    def broken_dump(data, fh, **kwargs):
        fh.write('{"version": 1, "entr')
        raise OSError("disk full")

    monkeypatch.setattr(atomic_io.json, "dump", broken_dump)
    with pytest.raises(OSError):
        mc.save_entry("lab-ollama", LAB_BASE, "qwen3:8b", {"temperature": 0.3})
    assert customization_file.read_bytes() == before


def test_a_corrupt_file_does_not_break_reads(customization_file, caplog):
    customization_file.write_text("{not json", encoding="utf-8")
    with caplog.at_level("WARNING", logger="src.model_customization"):
        assert mc.load_entries() == []
    assert any("model customizations" in r.getMessage() for r in caplog.records)


def test_saving_repairs_a_corrupt_file(customization_file):
    customization_file.write_text("{not json", encoding="utf-8")
    mc.save_entry("lab-ollama", LAB_BASE, "qwen3:8b", {"temperature": 0.6})
    entries = _entries_on_disk(customization_file)
    assert [(e["model"], e["settings"]) for e in entries] == [("qwen3:8b", {"temperature": 0.6})]


def test_unchanged_file_is_parsed_once_and_rewrites_are_seen(customization_file, monkeypatch):
    import os

    mc.save_entry("lab-ollama", LAB_BASE, "qwen3:8b", {"temperature": 0.6})
    calls = []
    real_load = json.load

    def counting_load(fh, *a, **kw):
        calls.append(1)
        return real_load(fh, *a, **kw)

    monkeypatch.setattr(mc.json, "load", counting_load)
    mc.load_entries()
    mc.load_entries()
    assert len(calls) == 1

    # An outside rewrite (another worker) with a new mtime is picked up.
    customization_file.write_text(
        json.dumps({"version": 1, "entries": []}), encoding="utf-8"
    )
    stat = customization_file.stat()
    os.utime(customization_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert mc.load_entries() == []


def test_concurrent_saves_of_different_models_both_survive(customization_file, monkeypatch):
    import threading
    import time

    real_write = mc.atomic_write_json

    def slow_write(*args, **kwargs):
        time.sleep(0.2)  # widen the read-modify-write window
        return real_write(*args, **kwargs)

    monkeypatch.setattr(mc, "atomic_write_json", slow_write)
    threads = [
        threading.Thread(target=mc.save_entry, args=("lab-ollama", LAB_BASE, model, {"temperature": 0.5}))
        for model in ("qwen3:8b", "gemma3:12b")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(e["model"] for e in _entries_on_disk(customization_file)) == ["gemma3:12b", "qwen3:8b"]


# ── Matching calls to entries (group 5) ─────────────────────────────────────

import json as _json

from core import database
from tests.helpers.sqlite_db import make_temp_sqlite

LAB_URL = "http://127.0.0.1:11434"
DESK_URL = "http://127.0.0.2:8080/v1"


@pytest.fixture
def endpoint_db(monkeypatch, customization_file):
    """Temp sqlite holding ModelEndpoint rows, bound to the module's session factory."""
    factory, engine, tmpfile = make_temp_sqlite(database.Base.metadata)
    monkeypatch.setattr(mc, "_session_factory", lambda: factory)
    mc._reset_endpoint_cache()

    def add(endpoint_id, base_url, models, **extra):
        with factory() as db:
            db.add(database.ModelEndpoint(
                id=endpoint_id, name=endpoint_id, base_url=base_url,
                cached_models=_json.dumps(models), **extra,
            ))
            db.commit()
        mc._reset_endpoint_cache()

    def delete(endpoint_id):
        with factory() as db:
            db.query(database.ModelEndpoint).filter_by(id=endpoint_id).delete()
            db.commit()

    add.delete = delete
    yield add
    engine.dispose()
    tmpfile.close()


def test_a_registered_endpoint_entry_matches_its_chat_url(endpoint_db):
    endpoint_db("lab-ollama", LAB_URL, ["qwen3:8b"])
    mc.save_entry("lab-ollama", LAB_URL, "qwen3:8b", {"temperature": 0.6})
    assert mc.lookup(LAB_URL + "/api/chat", "qwen3:8b") == {"temperature": 0.6}


def test_an_unregistered_url_is_never_customized(endpoint_db):
    endpoint_db("lab-ollama", LAB_URL, ["qwen3:8b"])
    mc.save_entry("lab-ollama", LAB_URL, "qwen3:8b", {"temperature": 0.6})
    assert mc.lookup("http://127.0.0.9:11434/api/chat", "qwen3:8b") is None


def test_another_model_on_the_same_endpoint_is_not_matched(endpoint_db):
    endpoint_db("lab-ollama", LAB_URL, ["qwen3:8b", "llama3:8b"])
    mc.save_entry("lab-ollama", LAB_URL, "qwen3:8b", {"temperature": 0.6})
    assert mc.lookup(LAB_URL + "/api/chat", "llama3:8b") is None


def test_two_endpoints_sharing_one_server_make_the_setting_ambiguous(endpoint_db, caplog):
    endpoint_db("team-a", DESK_URL, ["qwen3:8b"], api_key="key-a")
    endpoint_db("team-b", DESK_URL, ["qwen3:8b"], api_key="key-b")
    mc.save_entry("team-a", DESK_URL, "qwen3:8b", {"temperature": 0.6})
    mc.save_entry("team-b", DESK_URL, "qwen3:8b", {"temperature": 0.9})
    with caplog.at_level("DEBUG", logger=mc.logger.name):
        assert mc.lookup(DESK_URL + "/chat/completions", "qwen3:8b") is None
    assert "ambiguous" in caplog.text.lower()
    assert mc.is_ambiguous("team-a", "qwen3:8b") is True


def test_one_entry_on_a_shared_server_is_not_ambiguous(endpoint_db):
    endpoint_db("team-a", DESK_URL, ["qwen3:8b"], api_key="key-a")
    endpoint_db("team-b", DESK_URL, ["qwen3:8b"], api_key="key-b")
    mc.save_entry("team-a", DESK_URL, "qwen3:8b", {"temperature": 0.6})
    assert mc.lookup(DESK_URL + "/chat/completions", "qwen3:8b") == {"temperature": 0.6}
    assert mc.is_ambiguous("team-a", "qwen3:8b") is False


def test_a_deleted_endpoints_entry_is_ignored(endpoint_db):
    endpoint_db("old-box", LAB_URL, ["qwen3:8b"])
    mc.save_entry("old-box", LAB_URL, "qwen3:8b", {"temperature": 0.6})
    endpoint_db.delete("old-box")
    mc._reset_endpoint_cache()
    assert mc.lookup(LAB_URL + "/api/chat", "qwen3:8b") is None


def test_a_model_no_longer_enabled_is_not_matched_but_kept(endpoint_db, customization_file):
    endpoint_db("lab-ollama", LAB_URL, ["qwen3:8b"], hidden_models=_json.dumps(["qwen3:8b"]))
    mc.save_entry("lab-ollama", LAB_URL, "qwen3:8b", {"temperature": 0.6})
    assert mc.lookup(LAB_URL + "/api/chat", "qwen3:8b") is None
    assert [e["model"] for e in _entries_on_disk(customization_file)] == ["qwen3:8b"]


def test_a_deleted_endpoint_stops_matching_once_the_cache_expires(endpoint_db, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(mc, "_monotonic", lambda: clock[0])
    endpoint_db("old-box", LAB_URL, ["qwen3:8b"])
    mc.save_entry("old-box", LAB_URL, "qwen3:8b", {"temperature": 0.6})
    assert mc.lookup(LAB_URL + "/api/chat", "qwen3:8b") == {"temperature": 0.6}
    endpoint_db.delete("old-box")
    assert mc.lookup(LAB_URL + "/api/chat", "qwen3:8b") == {"temperature": 0.6}
    clock[0] += mc.ENDPOINT_CACHE_TTL + 0.1
    assert mc.lookup(LAB_URL + "/api/chat", "qwen3:8b") is None


def test_an_entry_on_an_excluded_url_never_returns_settings(endpoint_db):
    endpoint_db("claude", "https://api.anthropic.com", ["claude-sonnet-4"])
    mc.save_entry("claude", "https://api.anthropic.com", "claude-sonnet-4", {"temperature": 0.6})
    assert mc.lookup("https://api.anthropic.com/v1/messages", "claude-sonnet-4") is None


def test_lookup_without_entries_never_touches_the_database(customization_file, monkeypatch):
    def boom():
        raise AssertionError("database queried")
    monkeypatch.setattr(mc, "_session_factory", boom)
    mc._reset_endpoint_cache()
    assert mc.lookup(LAB_URL + "/api/chat", "qwen3:8b") is None


# ── Pure call-time helpers (group 6) ────────────────────────────────────────

ALL_SAMPLING = {
    "temperature": 0.6, "top_p": 0.9, "top_k": 40, "min_p": 0.05,
    "presence_penalty": 0.5, "repeat_penalty": 1.1, "num_ctx": 8192, "thinking": "on",
}


@pytest.mark.parametrize("explicit, settings, fmt, expected", [
    (0.2, {"temperature": 0.6}, "ollama-native", 0.2),
    (None, {"temperature": 0.6}, "ollama-native", 0.6),
    (None, {"temperature": 0.6}, "hosted-openai", 0.6),
    (None, {}, "local-openai", 1.0),
    (None, None, "ollama-openai", 1.0),
    (None, {"temperature": 0.6}, "excluded", 1.0),
    (0.0, {"temperature": 0.6}, "local-openai", 0.0),
])
def test_pick_temperature_precedence(explicit, settings, fmt, expected):
    assert mc.pick_temperature(explicit, settings, fmt) == expected


def test_inject_ollama_native_writes_inside_options_only():
    payload = {"model": "qwen3:8b", "options": {"temperature": 1.0, "num_predict": 512}}
    assert mc.inject(payload, "ollama-native", ALL_SAMPLING) == {
        "model": "qwen3:8b",
        "options": {
            "temperature": 1.0, "num_predict": 512,
            "top_p": 0.9, "top_k": 40, "min_p": 0.05,
            "presence_penalty": 0.5, "repeat_penalty": 1.1,
        },
    }


def test_inject_ollama_native_creates_missing_options():
    assert mc.inject({"model": "m"}, "ollama-native", {"top_k": 20}) == {
        "model": "m", "options": {"top_k": 20},
    }


@pytest.mark.parametrize("fmt", ["ollama-openai", "hosted-openai"])
def test_inject_openai_formats_write_top_p_and_presence_only(fmt):
    payload = {"model": "m", "temperature": 1.0}
    assert mc.inject(payload, fmt, ALL_SAMPLING) == {
        "model": "m", "temperature": 1.0, "top_p": 0.9, "presence_penalty": 0.5,
    }


def test_inject_local_openai_writes_all_sampling_and_both_repeat_keys():
    payload = {"model": "m", "temperature": 1.0}
    assert mc.inject(payload, "local-openai", ALL_SAMPLING) == {
        "model": "m", "temperature": 1.0,
        "top_p": 0.9, "top_k": 40, "min_p": 0.05, "presence_penalty": 0.5,
        "repeat_penalty": 1.1, "repetition_penalty": 1.1,
    }


def test_inject_is_idempotent():
    once = mc.inject({"model": "m", "options": {}}, "ollama-native", ALL_SAMPLING)
    twice = mc.inject(_json.loads(_json.dumps(once)), "ollama-native", ALL_SAMPLING)
    assert twice == once


def test_inject_leaves_excluded_payloads_untouched():
    payload = {"model": "claude", "max_tokens": 10}
    assert mc.inject(payload, "excluded", ALL_SAMPLING) == {"model": "claude", "max_tokens": 10}


def test_inject_without_settings_changes_nothing():
    assert mc.inject({"model": "m"}, "local-openai", None) == {"model": "m"}


# ── I/O wrappers (6.6) ──────────────────────────────────────────────────────

@pytest.fixture
def lab_entry(endpoint_db):
    endpoint_db("lab-ollama", LAB_URL, ["qwen3:8b"])
    mc.save_entry("lab-ollama", LAB_URL, "qwen3:8b", {
        "temperature": 0.6, "top_k": 40, "num_ctx": 8192, "thinking": "on",
    })
    return LAB_URL + "/api/chat"


def test_resolve_temperature_uses_the_saved_value(lab_entry):
    assert mc.resolve_temperature(lab_entry, "qwen3:8b", None) == 0.6
    assert mc.resolve_temperature(lab_entry, "qwen3:8b", 0.1) == 0.1
    assert mc.resolve_temperature(lab_entry, "other", None) == 1.0


def test_apply_sampling_injects_the_saved_values(lab_entry):
    payload = {"model": "qwen3:8b", "options": {"temperature": 0.6}}
    assert mc.apply_sampling(payload, lab_entry, "qwen3:8b") == {
        "model": "qwen3:8b", "options": {"temperature": 0.6, "top_k": 40},
    }


def test_cache_key_settings_is_sorted_and_empty_when_nothing_applies(lab_entry):
    assert list(mc.cache_key_settings(lab_entry, "qwen3:8b")) == ["num_ctx", "temperature", "thinking", "top_k"]
    assert mc.cache_key_settings(lab_entry, "other") == {}


def test_context_window_override_is_ollama_native_only(lab_entry):
    assert mc.context_window_override(lab_entry, "qwen3:8b") == 8192
    assert mc.context_window_override(lab_entry, "other") is None


def test_seed_thinking_mode_returns_the_saved_choice(lab_entry):
    assert mc.seed_thinking_mode(lab_entry, "qwen3:8b") == "on"
    assert mc.seed_thinking_mode(lab_entry, "other") is None
