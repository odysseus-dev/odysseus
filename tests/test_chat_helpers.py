import asyncio
import os
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routes.chat_helpers as chat_helpers
from routes.chat_helpers import (
    _enforce_chat_privileges,
    _session_is_research_spinoff,
    auto_name_session,
    build_chat_context,
    build_uploaded_file_manifest,
    clean_thinking_for_save,
    needs_auto_name,
    PreprocessedMessage,
    PresetInfo,
    save_assistant_response,
)


class _AuthManager:
    def __init__(self, privileges):
        self._privileges = privileges

    def get_privileges(self, username):
        assert username == "alice"
        return self._privileges


class _Request:
    def __init__(self, privileges):
        self.app = type("App", (), {})()
        self.app.state = type("State", (), {"auth_manager": _AuthManager(privileges)})()


class _Session:
    def __init__(self, model):
        self.model = model


def test_allowed_models_legacy_empty_list_remains_unrestricted(monkeypatch):
    monkeypatch.setattr("routes.chat_helpers.effective_user", lambda request: "alice")

    _enforce_chat_privileges(
        _Request({"allowed_models": [], "max_messages_per_day": 0}),
        _Session("provider/model-a"),
    )


def test_allowed_models_explicit_empty_restricted_list_blocks_all_models(monkeypatch):
    monkeypatch.setattr("routes.chat_helpers.effective_user", lambda request: "alice")

    with pytest.raises(HTTPException) as exc:
        _enforce_chat_privileges(
            _Request({
                "allowed_models": [],
                "allowed_models_restricted": True,
                "max_messages_per_day": 0,
            }),
            _Session("provider/model-a"),
        )

    assert exc.value.status_code == 403
    assert "provider/model-a" in exc.value.detail


def test_allowed_models_nonempty_list_still_restricts_without_new_flag(monkeypatch):
    monkeypatch.setattr("routes.chat_helpers.effective_user", lambda request: "alice")

    _enforce_chat_privileges(
        _Request({"allowed_models": ["provider/model-a"], "max_messages_per_day": 0}),
        _Session("provider/model-a"),
    )
    with pytest.raises(HTTPException):
        _enforce_chat_privileges(
            _Request({"allowed_models": ["provider/model-a"], "max_messages_per_day": 0}),
            _Session("provider/model-b"),
        )


def test_no_restriction_allows_any_model(monkeypatch):
    monkeypatch.setattr("routes.chat_helpers.effective_user", lambda request: "alice")

    privs = {"allowed_models": [], "block_all_models": False, "max_messages_per_day": 0}
    _enforce_chat_privileges(_Request(privs), _Session("provider/model-a"))
    _enforce_chat_privileges(_Request(privs), _Session("provider/model-z"))


def test_specific_allowlist_blocks_models_outside_it(monkeypatch):
    monkeypatch.setattr("routes.chat_helpers.effective_user", lambda request: "alice")

    privs = {
        "allowed_models": ["gpt-4"],
        "block_all_models": False,
        "max_messages_per_day": 0,
    }
    _enforce_chat_privileges(_Request(privs), _Session("gpt-4"))
    with pytest.raises(HTTPException) as exc:
        _enforce_chat_privileges(_Request(privs), _Session("gpt-3.5"))
    assert exc.value.status_code == 403


def test_block_all_models_blocks_regardless_of_allowed_models_contents(monkeypatch):
    monkeypatch.setattr("routes.chat_helpers.effective_user", lambda request: "alice")

    # Even if allowed_models contains entries, block_all_models wins.
    privs = {
        "allowed_models": ["gpt-4", "gpt-3.5"],
        "block_all_models": True,
        "max_messages_per_day": 0,
    }
    with pytest.raises(HTTPException) as exc:
        _enforce_chat_privileges(_Request(privs), _Session("gpt-4"))
    assert exc.value.status_code == 403

    with pytest.raises(HTTPException):
        _enforce_chat_privileges(_Request(privs), _Session("anything-else"))


def test_admin_user_is_never_blocked(monkeypatch):
    from core.auth import ADMIN_PRIVILEGES

    monkeypatch.setattr("routes.chat_helpers.effective_user", lambda request: "admin")

    class _AdminAuthManager:
        def get_privileges(self, username):
            assert username == "admin"
            return dict(ADMIN_PRIVILEGES)

    class _AdminRequest:
        def __init__(self):
            self.app = type("App", (), {})()
            self.app.state = type("State", (), {"auth_manager": _AdminAuthManager()})()

    _enforce_chat_privileges(_AdminRequest(), _Session("provider/model-a"))
    _enforce_chat_privileges(_AdminRequest(), _Session("anything-else"))


class _FakeSession:
    def __init__(self, model="selected-model"):
        self.model = model
        self.history = []

    def add_message(self, message):
        self.history.append(message)


class _ManifestUploadHandler:
    def __init__(self, upload_dir, rows):
        self.upload_dir = str(upload_dir)
        self.rows = rows
        self.calls = []

    def _inside_upload_dir(self, path):
        base = os.path.realpath(self.upload_dir)
        candidate = os.path.realpath(path)
        try:
            return os.path.commonpath([base, candidate]) == base
        except ValueError:
            return False

    def resolve_upload(self, upload_id, owner=None):
        self.calls.append((upload_id, owner))
        row = self.rows.get(upload_id)
        if isinstance(row, dict) and row.get("owner") and row.get("owner") != owner:
            return None
        return row


def _manifest_test_dir(name):
    root = Path(__file__).resolve().parents[1] / "tmp_pytest_probe" / f"{name}-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    return root


def test_build_uploaded_file_manifest_filters_and_nulls_unreadable_paths(monkeypatch):
    root = _manifest_test_dir("manifest")
    try:
        upload_dir = root / "uploads"
        upload_dir.mkdir()
        good = upload_dir / "good.txt"
        good.write_text("hello", encoding="utf-8")
        outside = root / "outside.txt"
        outside.write_text("nope", encoding="utf-8")
        missing = upload_dir / "missing.txt"

        import src.settings as settings

        monkeypatch.setattr(
            settings,
            "get_setting",
            lambda key: [str(upload_dir)] if key == "tool_path_extra_roots" else None,
        )
        handler = _ManifestUploadHandler(upload_dir, {
            "good": {
                "id": "good",
                "name": "good.txt",
                "mime": "text/plain",
                "size": 5,
                "path": str(good),
                "owner": "alice",
            },
            "bob": {
                "id": "bob",
                "name": "bob.txt",
                "path": str(good),
                "owner": "bob",
            },
            "outside": {
                "id": "outside",
                "name": "outside.txt",
                "path": str(outside),
                "owner": "alice",
            },
            "missing": {
                "id": "missing",
                "name": "missing.txt",
                "path": str(missing),
                "owner": "alice",
            },
            "bad": ["not", "a", "dict"],
        })

        manifest = build_uploaded_file_manifest(
            ["good", "bob", "outside", "missing", "bad"],
            handler,
            owner="alice",
        )

        assert [item["id"] for item in manifest] == ["good", "outside", "missing"]
        assert manifest[0]["type"] == "attachment_ref"
        assert manifest[0]["attachment_id"] == "good"
        assert manifest[0]["uri"] == "odysseus://attachment/good"
        assert manifest[0]["read_policy"] == "owner_checked_upload"
        assert os.path.realpath(manifest[0]["path"]) == os.path.realpath(good)
        assert manifest[1]["path"] is None
        assert manifest[2]["path"] is None
        assert handler.calls == [
            ("good", "alice"),
            ("bob", "alice"),
            ("outside", "alice"),
            ("missing", "alice"),
            ("bad", "alice"),
        ]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_build_uploaded_file_manifest_hides_paths_read_file_cannot_open(monkeypatch):
    root = _manifest_test_dir("manifest-unreadable")
    try:
        upload_dir = root / "uploads"
        upload_dir.mkdir()
        upload = upload_dir / "upload.txt"
        upload.write_text("hello", encoding="utf-8")
        handler = _ManifestUploadHandler(upload_dir, {
            "upload": {"id": "upload", "name": "upload.txt", "path": str(upload), "owner": "alice"},
        })

        def reject_path(_path):
            raise ValueError("outside the allowed roots")

        monkeypatch.setattr("src.tool_execution._resolve_tool_path", reject_path)

        manifest = build_uploaded_file_manifest(["upload"], handler, owner="alice")

        assert manifest[0]["path"] is None
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.parametrize("name,expected", [
    # 24h format (the bug this PR fixes)
    ("deepseek-v4-flash 14:05:33", True),
    ("qwq 17:46:02", True),
    ("gemma3 23:59:59", True),
    ("claude-sonnet 4 0:00:00", True),

    # 12h format (was already working)
    ("deepseek-v4-flash 2:05:33 PM", True),
    ("qwq 06:46:02 AM", True),
    ("claude-sonnet-4 8:05:17 am", True),

    # empty / default
    ("", True),
    ("  ", False),
    ("Chat: something", True),

    # custom titles – should NOT trigger auto-naming
    ("custom title", False),
    ("CW Decoder for STM32", False),
    ("my chat about python", False),
    ("Fix the login bug", False),
])
def test_needs_auto_name(name, expected):
    assert needs_auto_name(name) == expected, f"needs_auto_name({name!r}) should be {expected}"


def test_clean_thinking_for_save_extracts_gemma4_thought_channel():
    content, metadata = clean_thinking_for_save(
        "<|channel>thought\ninternal reasoning<channel|>Final answer.",
        {"model": "google/gemma-4-31B-it"},
    )

    assert content == "Final answer."
    assert metadata["thinking"] == "internal reasoning"
    assert metadata["model"] == "google/gemma-4-31B-it"


def test_clean_thinking_for_save_strips_empty_gemma4_thought_channel():
    content, metadata = clean_thinking_for_save(
        "<|channel>thought\n<channel|>Final answer.",
        {"model": "google/gemma-4-31B-it"},
    )

    assert content == "Final answer."
    assert "thinking" not in metadata


def test_clean_thinking_for_save_unwraps_gemma4_response_channel():
    content, metadata = clean_thinking_for_save(
        "<|channel>thought\ninternal reasoning<channel|><|channel>response\nFinal answer.<channel|>",
        {"model": "google/gemma-4-31B-it"},
    )

    assert content == "Final answer."
    assert metadata["thinking"] == "internal reasoning"


def test_clean_thinking_for_save_extracts_thought_tag():
    content, metadata = clean_thinking_for_save(
        "<thought>internal reasoning</thought>Final answer.",
        {},
    )

    assert content == "Final answer."
    assert metadata["thinking"] == "internal reasoning"


def test_save_assistant_response_preserves_actual_and_requested_model():
    sess = _FakeSession("selected-model")

    save_assistant_response(
        sess,
        session_manager=None,
        session_id="s1",
        full_response="hello",
        last_metrics={"model": "actual-model", "input_tokens": 1, "output_tokens": 2},
        incognito=True,
    )

    assert sess.history[-1].metadata["requested_model"] == "selected-model"
    assert sess.history[-1].metadata["model"] == "actual-model"


class _SpinMsg:
    def __init__(self, role, metadata=None):
        self.role = role
        self.metadata = metadata


def test_spinoff_detected_from_chatmessage_history():
    sess = SimpleNamespace(history=[
        _SpinMsg("system", {"research_spinoff_from": "rp-1"}),
        _SpinMsg("user", None),
    ])
    assert _session_is_research_spinoff(sess) is True


def test_auto_name_session_passes_session_fallback_to_task_resolver(monkeypatch):
    import src.llm_core as llm_core
    import src.task_endpoint as task_endpoint

    resolver_calls = []
    llm_calls = []

    def fake_resolve_task_endpoint(
        fallback_url=None,
        fallback_model=None,
        fallback_headers=None,
        owner=None,
    ):
        resolver_calls.append((fallback_url, fallback_model, fallback_headers, owner))
        return fallback_url, fallback_model, fallback_headers

    async def fake_llm_call(url, model, messages, **kwargs):
        llm_calls.append((url, model, messages, kwargs))
        return "Focused Fix"

    monkeypatch.setattr(task_endpoint, "resolve_task_endpoint", fake_resolve_task_endpoint)
    monkeypatch.setattr(llm_core, "llm_call_async", fake_llm_call)

    session_headers = {"Authorization": "Bearer session"}
    sess = SimpleNamespace(
        id="session-1",
        owner="alice",
        endpoint_url="http://session.example/v1/chat/completions",
        model="session-model",
        headers=session_headers,
        history=[SimpleNamespace(role="user", content="Please fix the endpoint fallback bug.")],
    )
    updates = []
    session_manager = SimpleNamespace(
        update_session_name=lambda session_id, title: updates.append((session_id, title))
    )

    asyncio.run(auto_name_session(session_manager, sess))

    assert resolver_calls == [(
        "http://session.example/v1/chat/completions",
        "session-model",
        session_headers,
        "alice",
    )]
    assert llm_calls[0][0] == "http://session.example/v1/chat/completions"
    assert llm_calls[0][1] == "session-model"
    assert llm_calls[0][3]["headers"] == session_headers
    assert updates == [("session-1", "Focused Fix")]


def test_spinoff_detected_from_dict_history():
    sess = SimpleNamespace(history=[
        {"role": "system", "metadata": {"research_spinoff_from": "rp-2"}},
        {"role": "user", "content": "hi"},
    ])
    assert _session_is_research_spinoff(sess) is True


def test_non_spinoff_plain_session_is_false():
    sess = SimpleNamespace(history=[
        _SpinMsg("system", {"compacted": True}),
        _SpinMsg("user", None),
    ])
    assert _session_is_research_spinoff(sess) is False


def test_metadata_on_non_system_message_ignored():
    sess = SimpleNamespace(history=[_SpinMsg("user", {"research_spinoff_from": "rp-3"})])
    assert _session_is_research_spinoff(sess) is False


def test_empty_or_missing_history():
    assert _session_is_research_spinoff(SimpleNamespace(history=[])) is False
    assert _session_is_research_spinoff(SimpleNamespace()) is False


async def _build_context_owner_probe(monkeypatch, request_state):
    captured = {
        "prefs_owner": None,
        "preface_owner": None,
        "compact_owner": None,
    }

    async def fake_preprocess(chat_handler, message, att_ids, sess, **kwargs):
        return PreprocessedMessage(
            enhanced_message=message,
            user_content=message,
            text_for_context=message,
            youtube_transcripts=[],
            attachment_meta=[],
        )

    def fake_extract_preset(chat_handler, preset_id):
        return PresetInfo(
            temperature=0.7,
            max_tokens=1024,
            system_prompt=None,
            character_name=None,
        )

    def fake_add_user_message(sess, chat_handler, preprocessed, incognito=False):
        sess.messages.append({"role": "user", "content": preprocessed.user_content})

    def fake_load_prefs(owner):
        captured["prefs_owner"] = owner
        return {"memory_enabled": True, "skills_enabled": True}

    def fake_build_context_preface(**kwargs):
        captured["preface_owner"] = kwargs["owner"]
        return [], [], []

    async def fake_maybe_compact(sess, endpoint_url, model, messages, headers, owner=None):
        captured["compact_owner"] = owner
        return messages, 8192, False

    monkeypatch.setattr(chat_helpers, "preprocess", fake_preprocess)
    monkeypatch.setattr(chat_helpers, "extract_preset", fake_extract_preset)
    monkeypatch.setattr(chat_helpers, "add_user_message", fake_add_user_message)
    monkeypatch.setattr(chat_helpers, "load_prefs_for_user", fake_load_prefs)
    monkeypatch.setattr(chat_helpers, "_normalize_model_id_from_cache", lambda sess: None)
    monkeypatch.setattr(chat_helpers, "normalize_model_id", lambda endpoint_url, model, **kwargs: None)
    monkeypatch.setattr(chat_helpers, "maybe_compact", fake_maybe_compact)
    monkeypatch.setattr(chat_helpers, "trim_for_context", lambda messages, context_length: messages)

    import src.user_time as user_time

    monkeypatch.setattr(
        user_time,
        "current_datetime_context_message",
        lambda now_utc=None: {"role": "user", "content": "[Context - current date/time]"},
        raising=False,
    )

    sess = SimpleNamespace(
        endpoint_url="http://model.local/v1/chat/completions",
        model="test-model",
        headers={},
        history=[],
        messages=[],
    )
    sess.get_context_messages = lambda: list(sess.messages)

    request = SimpleNamespace(state=SimpleNamespace(**request_state))
    ctx = await build_chat_context(
        sess=sess,
        request=request,
        chat_handler=SimpleNamespace(),
        chat_processor=SimpleNamespace(build_context_preface=fake_build_context_preface),
        message="hello",
        session_id="session-1",
        incognito=True,
    )

    return ctx, captured


@pytest.mark.asyncio
async def test_build_chat_context_uses_api_token_owner_for_compaction_scope(monkeypatch):
    ctx, captured = await _build_context_owner_probe(
        monkeypatch,
        {
            "api_token": True,
            "api_token_owner": "alice",
            "current_user": "api",
        },
    )

    assert ctx.user == "alice"
    assert captured == {
        "prefs_owner": "alice",
        "preface_owner": "alice",
        "compact_owner": "alice",
    }


@pytest.mark.asyncio
async def test_build_chat_context_keeps_cookie_user_owner_scope(monkeypatch):
    ctx, captured = await _build_context_owner_probe(
        monkeypatch,
        {
            "api_token": False,
            "current_user": "bob",
        },
    )

    assert ctx.user == "bob"
    assert captured == {
        "prefs_owner": "bob",
        "preface_owner": "bob",
        "compact_owner": "bob",
    }
