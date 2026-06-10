import pytest
from fastapi import FastAPI, Request, Form
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, AsyncMock
from pathlib import Path
import json
import textwrap

from services.memory.skills import SkillsManager
from routes.skills_routes import setup_skills_routes
from routes.chat_routes import setup_chat_routes
from src.request_models import ChatRequest

# Helper to write mock skills to disk
def _write_skill_md(skills_root: Path, category: str, name: str,
                    owner: str, status: str = "draft", description: str = "test description") -> Path:
    from services.memory.skill_format import slugify
    skill_dir = skills_root / slugify(category or "general", fallback="general") / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    md = textwrap.dedent(f"""\
        ---
        name: {name}
        description: {description}
        version: 1.0.0
        category: {category}
        tags: []
        status: {status}
        confidence: 0.8
        source: learned
        owner: {owner}
        created: 2026-01-01T00:00:00Z
        ---

        # When to use
        test

        # Procedure
        - step 1
        """)
    path = skill_dir / "SKILL.md"
    path.write_text(md, encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_slash_catalog_and_invoke_allow_drafts(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr("routes.skills_routes.get_current_user", lambda _req: "alice")

    sm = SkillsManager(str(tmp_path))
    # Write one published skill and one draft skill for alice
    _write_skill_md(tmp_path / "skills", "general", "published-skill", "alice", status="published")
    _write_skill_md(tmp_path / "skills", "general", "draft-skill", "alice", status="draft")

    app = FastAPI()
    app.include_router(setup_skills_routes(sm))
    client = TestClient(app)

    # 1. Test slash-catalog contains both published and draft skills
    response = client.get("/api/skills/slash-catalog")
    assert response.status_code == 200
    res_json = response.json()
    names = [s["name"] for s in res_json["skills"]]
    assert "published-skill" in names
    assert "draft-skill" in names

    # 2. Test invoke works for the draft skill
    response = client.post(
        "/api/skills/draft-skill/invoke",
        json={"request": "test request for draft"}
    )
    assert response.status_code == 200
    res_json = response.json()
    assert res_json["ok"] is True
    assert res_json["name"] == "draft-skill"
    assert "--- BEGIN SKILL ---" in res_json["message"]
    assert "test request for draft" in res_json["message"]


@pytest.mark.asyncio
async def test_chat_routes_apply_attached_skill_formatting(tmp_path, monkeypatch):
    # Setup skills manager with a draft skill
    sm = SkillsManager(str(tmp_path))
    _write_skill_md(tmp_path / "skills", "general", "my-super-skill", "alice", status="draft")

    # Mocks for chat dependencies
    session_manager = MagicMock()
    chat_handler = MagicMock()
    chat_handler.handle_memory_command = AsyncMock(return_value=None)
    chat_processor = MagicMock()
    memory_manager = MagicMock()
    research_handler = MagicMock()
    upload_handler = MagicMock()

    mock_session = MagicMock()
    mock_session.model = "gpt-4o"
    mock_session.endpoint_url = "https://api.openai.com/v1"
    mock_session.headers = {"Authorization": "Bearer key"}
    mock_session.history = []
    session_manager.get_session.return_value = mock_session

    # Monkeypatch gates/auth
    monkeypatch.setattr("routes.chat_routes.get_current_user", lambda req: "alice")
    monkeypatch.setattr("routes.chat_routes._verify_session_owner", lambda req, sess: None)
    monkeypatch.setattr("routes.chat_routes._set_user_time_from_request", lambda req: None)
    monkeypatch.setattr("routes.chat_routes._clear_orphaned_session_endpoint", lambda sess, owner: False)
    monkeypatch.setattr("routes.chat_routes._recover_empty_session_model", lambda sess, session, owner: None)
    monkeypatch.setattr("routes.chat_routes._enforce_chat_privileges", lambda req, sess: None)
    monkeypatch.setattr("routes.chat_routes.resolve_session_auth", lambda sess, session, owner: None)
    monkeypatch.setattr("routes.chat_routes.set_session_mode", lambda session, mode: None)
    
    mock_policy = MagicMock()
    mock_policy.block_all_tool_calls = False
    mock_policy.blocks.return_value = False
    mock_policy.all_disabled_names.return_value = set()
    monkeypatch.setattr("routes.chat_routes.build_effective_tool_policy", lambda **kwargs: mock_policy)

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None
    monkeypatch.setattr("routes.chat_routes.SessionLocal", lambda: mock_db)

    monkeypatch.setattr("routes.chat_routes.llm_call_async", AsyncMock(return_value="mocked llm reply"))
    monkeypatch.setattr("routes.chat_routes.run_post_response_tasks", lambda *args, **kwargs: None)

    # Capture the message parameter and attached_skill_name passed to build_chat_context and exit early
    captured_messages = []
    captured_skills = []
    async def mock_build_chat_context(sess, request, handler, processor, **kwargs):
        captured_messages.append(kwargs.get("message"))
        captured_skills.append(kwargs.get("attached_skill_name"))
        raise ValueError("Exit test early")

    monkeypatch.setattr("routes.chat_routes.build_chat_context", mock_build_chat_context)

    # Setup the FastAPI app and TestClient
    app = FastAPI()
    app.include_router(setup_chat_routes(
        session_manager=session_manager,
        chat_handler=chat_handler,
        chat_processor=chat_processor,
        memory_manager=memory_manager,
        research_handler=research_handler,
        upload_handler=upload_handler,
        skills_manager=sm
    ))
    client = TestClient(app)

    # 1. Test POST /api/chat (non-streaming) with attached_skill_name
    with pytest.raises(ValueError, match="Exit test early"):
        client.post(
            "/api/chat",
            json={
                "message": "please write some code",
                "session": "session-1",
                "attached_skill_name": "my-super-skill"
            }
        )
    assert len(captured_messages) == 1
    assert captured_messages[0] == "please write some code"
    assert captured_skills[0] == "my-super-skill"

    # Clear captured data for the next test
    captured_messages.clear()
    captured_skills.clear()

    # 2. Test POST /api/chat_stream with attached_skill_name
    # Form data is used for chat_stream route
    with pytest.raises(ValueError, match="Exit test early"):
        client.post(
            "/api/chat_stream",
            data={
                "message": "please write some code streaming",
                "session": "session-1",
                "attached_skill_name": "my-super-skill"
            }
        )
    assert len(captured_messages) == 1
    assert captured_messages[0] == "please write some code streaming"
    assert captured_skills[0] == "my-super-skill"


@pytest.mark.asyncio
async def test_build_chat_context_attached_skill(tmp_path, monkeypatch):
    from routes import chat_helpers
    from core.models import ChatMessage

    # Setup skills manager with a skill
    sm = SkillsManager(str(tmp_path))
    _write_skill_md(tmp_path / "skills", "general", "target-skill", "alice", status="draft")

    # Mocks
    chat_handler = MagicMock()
    chat_processor = MagicMock()
    chat_processor.build_context_preface = MagicMock(return_value=([], [], []))
    
    # Mock preprocess to return the message as is
    async def mock_preprocess(chat_handler, message, attachment_ids, sess, **kwargs):
        return chat_helpers.PreprocessedMessage(
            enhanced_message=message,
            user_content=message,
            text_for_context=message,
            youtube_transcripts=[],
            attachment_meta=[],
        )
    monkeypatch.setattr(chat_helpers, "preprocess", mock_preprocess)
    monkeypatch.setattr(chat_helpers, "extract_preset", lambda *a: MagicMock(temperature=0.3, max_tokens=100))
    
    # Simple Session class with a messages attribute and history list
    class FakeSession:
        def __init__(self):
            self.messages = []
            self.history = []
            self.endpoint_url = "http://localhost:8000/v1"
            self.model = "test-model"
            self.headers = {}
            self.owner = "alice"
        def get_context_messages(self):
            return list(self.messages)
        def add_message(self, msg):
            self.messages.append(msg)
            self.history.append(msg)

    sess = FakeSession()
    
    # Mock dependencies
    monkeypatch.setattr(chat_helpers, "get_current_user", lambda r: "alice")
    monkeypatch.setattr(chat_helpers, "load_prefs_for_user", lambda u: {"skills_enabled": True})
    monkeypatch.setattr(chat_helpers, "maybe_compact", AsyncMock(side_effect=lambda s, e, m, msgs, h, owner=None: (msgs, 0, False)))
    monkeypatch.setattr(chat_helpers, "trim_for_context", lambda msgs, context_length: msgs)
    
    request = MagicMock()
    
    # Build chat context with attached_skill_name
    ctx = await chat_helpers.build_chat_context(
        sess=sess,
        request=request,
        chat_handler=chat_handler,
        chat_processor=chat_processor,
        message="Do something with target-skill",
        session_id="session-123",
        attached_skill_name="target-skill",
        skills_manager=sm
    )
    
    # Verify:
    # 1. build_context_preface was called with use_skills=False (to suppress general catalog/matching)
    # 2. History remains clean (no mock assistant tool calls are appended beforehand)
    chat_processor.build_context_preface.assert_called_once()
    kwargs_called = chat_processor.build_context_preface.call_args[1]
    assert kwargs_called.get("use_skills") is False
    
    # History must only contain the original user message
    assert len(sess.messages) == 1
    assert sess.messages[0].role == "user"
    assert sess.messages[0].content == "Do something with target-skill"


@pytest.mark.asyncio
async def test_agent_loop_exclusivity(tmp_path, monkeypatch):
    from src.agent_loop import _build_system_prompt
    
    # Setup skills manager
    sm = SkillsManager(str(tmp_path))
    # Write a published skill so it would normally be matched via Jaccard
    _write_skill_md(tmp_path / "skills", "general", "some-skill", "alice", status="published", description="do target work")
    
    # Set data dir mock
    monkeypatch.setattr("src.constants.DATA_DIR", str(tmp_path))
    
    # Mocks
    mcp_mgr = MagicMock()
    mcp_mgr.get_all_openai_schemas.return_value = []
    mcp_mgr.get_tool_descriptions_for_prompt.return_value = ""
    
    messages = [{"role": "user", "content": "do target work"}]
    
    # 1. Normal run (no attached skill): Jaccard relevant skills are queried and injected
    res_normal, _ = _build_system_prompt(
        messages=messages,
        model="gpt-4o",
        active_document=None,
        mcp_mgr=mcp_mgr,
        owner="alice",
    )
    # The relevant skills block should be injected as an untrusted system / user context message
    skills_msgs = [m for m in res_normal if m.get("role") == "user" and m.get("metadata", {}).get("source") == "skills"]
    assert len(skills_msgs) > 0
    assert "some-skill" in skills_msgs[0]["content"]
    assert "Available skills" in skills_msgs[0]["content"]
    assert "ATTACHED SKILL DIRECTIVE" not in res_normal[0]["content"]

    # 2. Attached skill run: suppress skills index and Jaccard-matched skills, inject directive
    res_attached, _ = _build_system_prompt(
        messages=messages,
        model="gpt-4o",
        active_document=None,
        mcp_mgr=mcp_mgr,
        owner="alice",
        attached_skill_name="target-skill",
    )
    skills_msgs_att = [m for m in res_attached if m.get("role") == "user" and m.get("metadata", {}).get("source") == "skills"]
    assert len(skills_msgs_att) == 0
    
    # System message must now contain the attached skill directive
    system_msg = res_attached[0]["content"]
    assert "🛠️ ATTACHED SKILL DIRECTIVE" in system_msg
    assert "target-skill" in system_msg

    # 3. Detached skill notification: verify the DETACHED SKILL DIRECTIVE is injected
    messages_detached = [{"role": "user", "content": "I have detached the skill \"target-skill\"."}]
    res_detached, _ = _build_system_prompt(
        messages=messages_detached,
        model="gpt-4o",
        active_document=None,
        mcp_mgr=mcp_mgr,
        owner="alice",
    )
    system_msg_detached = res_detached[0]["content"]
    assert "🛠️ DETACHED SKILL DIRECTIVE" in system_msg_detached
    assert "confirm you are no longer using the detached skill" in system_msg_detached


@pytest.mark.asyncio
async def test_do_manage_skills_exclusivity(tmp_path, monkeypatch):
    from src.tool_implementations import do_manage_skills
    from services.memory.skills import SkillsManager

    # Setup skills manager with a path mock
    monkeypatch.setattr("src.constants.DATA_DIR", str(tmp_path))

    # Write target attached skill and other skill
    _write_skill_md(tmp_path / "skills", "general", "attached-skill", "alice", status="published", description="attached description")
    _write_skill_md(tmp_path / "skills", "general", "other-skill", "alice", status="published", description="other description")

    # 1. No attached skill name: list returns both
    res_list = await do_manage_skills(json.dumps({"action": "list"}), owner="alice")
    assert "attached-skill" in res_list["results"]
    assert "other-skill" in res_list["results"]

    # 2. Attached skill name = "attached-skill": list ONLY returns attached-skill
    res_list_filtered = await do_manage_skills(
        json.dumps({"action": "list"}),
        owner="alice",
        attached_skill_name="attached-skill",
    )
    assert "attached-skill" in res_list_filtered["results"]
    assert "other-skill" not in res_list_filtered["results"]

    # 3. Attached skill name = "attached-skill": view other-skill returns not found
    res_view_other = await do_manage_skills(
        json.dumps({"action": "view", "name": "other-skill"}),
        owner="alice",
        attached_skill_name="attached-skill",
    )
    assert "error" in res_view_other
    assert "not found" in res_view_other["error"]

    # 4. Attached skill name = "attached-skill": view attached-skill succeeds
    res_view_attached = await do_manage_skills(
        json.dumps({"action": "view", "name": "attached-skill"}),
        owner="alice",
        attached_skill_name="attached-skill",
    )
    assert "results" in res_view_attached
    assert "attached-skill" in res_view_attached["results"]

    # 5. Attached skill name = "attached-skill": search only matches attached-skill
    res_search = await do_manage_skills(
        json.dumps({"action": "search", "query": "description"}),
        owner="alice",
        attached_skill_name="attached-skill",
    )
    assert "attached-skill" in res_search["results"]
    assert "other-skill" not in res_search["results"]



