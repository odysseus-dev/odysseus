import json
import os

import pytest

from src.agent_loop import TOOL_SECTIONS, _DOMAIN_TOOL_MAP
from src.agent_tools import TOOL_HANDLERS, TOOL_TAGS, ToolBlock
from src.agent_tools.attachment_tools import ReadAttachmentTool, parse_attachment_id
from src.constants import MAX_READ_CHARS
from src.tool_execution import execute_tool_block
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS, function_call_to_tool_block
from src.tool_security import NON_ADMIN_BLOCKED_TOOLS, PLAN_MODE_READONLY_TOOLS
from src.upload_handler import UploadHandler


UPLOAD_ID = "a" * 32 + ".txt"


def _seed_upload(tmp_path, *, upload_id=UPLOAD_ID, owner="alice", content="hello"):
    upload_dir = tmp_path / "uploads"
    handler = UploadHandler(str(tmp_path), str(upload_dir))
    stored_dir = upload_dir / "2026" / "07" / "11"
    stored_dir.mkdir(parents=True)
    path = stored_dir / upload_id
    path.write_text(content, encoding="utf-8")
    row = {
        "id": upload_id,
        "path": str(path),
        "mime": "text/plain",
        "size": path.stat().st_size,
        "name": "C:\\server-secret\\notes.txt",
        "original_name": "C:\\server-secret\\notes.txt",
        "owner": owner,
        "hash": "hash-" + (owner or "single-user"),
    }
    handler._atomic_write_json(
        str(upload_dir / "uploads.json"),
        {f"{owner}:hash": row},
    )
    return handler, path


@pytest.mark.parametrize(
    "reference",
    [
        UPLOAD_ID,
        f"odysseus://attachment/{UPLOAD_ID}",
        json.dumps({"attachment": UPLOAD_ID}),
        json.dumps({"attachment": f"odysseus://attachment/{UPLOAD_ID}"}),
    ],
)
def test_parse_attachment_id_accepts_only_exact_internal_references(reference):
    assert parse_attachment_id(reference) == UPLOAD_ID


@pytest.mark.parametrize(
    "reference",
    [
        f"/api/upload/{UPLOAD_ID}",
        f"C:\\uploads\\{UPLOAD_ID}",
        f"../uploads/{UPLOAD_ID}",
        f"odysseus://attachment/{UPLOAD_ID}?download=1",
        f"odysseus://attachment/{UPLOAD_ID}#fragment",
        f"https://example.test/{UPLOAD_ID}",
        json.dumps({"attachment": f"{UPLOAD_ID}?x=1"}),
    ],
)
def test_parse_attachment_id_rejects_paths_queries_fragments_and_external_urls(reference):
    assert parse_attachment_id(reference) is None


@pytest.mark.asyncio
async def test_read_attachment_is_owner_checked_and_never_returns_host_path(tmp_path):
    handler, path = _seed_upload(tmp_path)
    tool = ReadAttachmentTool()

    allowed = await tool.execute(
        json.dumps({"attachment": f"odysseus://attachment/{UPLOAD_ID}"}),
        {"owner": "alice", "upload_handler": handler},
    )
    denied = await tool.execute(
        UPLOAD_ID,
        {"owner": "bob", "upload_handler": handler},
    )

    assert allowed["exit_code"] == 0
    assert allowed["output"] == "hello"
    assert allowed["attachment"] == {
        "id": UPLOAD_ID,
        "uri": f"odysseus://attachment/{UPLOAD_ID}",
        "name": "notes.txt",
        "mime": "text/plain",
        "size": 5,
    }
    serialized = json.dumps(allowed)
    assert str(path) not in serialized
    assert str(tmp_path) not in serialized
    assert "server-secret" not in serialized
    assert denied["exit_code"] == 1
    assert denied["error"] == "read_attachment: attachment not found or not authorized."


@pytest.mark.asyncio
async def test_read_attachment_passes_owner_and_explicitly_disables_admin_override(tmp_path):
    handler, path = _seed_upload(tmp_path)
    calls = []

    class RecordingHandler:
        upload_dir = handler.upload_dir

        @staticmethod
        def _inside_upload_dir(candidate):
            return handler._inside_upload_dir(candidate)

        @staticmethod
        def resolve_upload(upload_id, owner=None, allow_admin=True):
            calls.append((upload_id, owner, allow_admin))
            return {
                "id": upload_id,
                "path": str(path),
                "name": "notes.txt",
                "mime": "text/plain",
                "size": 5,
            }

    result = await ReadAttachmentTool().execute(
        UPLOAD_ID,
        {"owner": "site-admin", "upload_handler": RecordingHandler()},
    )

    assert result["exit_code"] == 0
    assert calls == [(UPLOAD_ID, "site-admin", False)]


@pytest.mark.asyncio
async def test_read_attachment_requires_owner_unless_auth_is_explicitly_disabled(
    tmp_path,
    monkeypatch,
):
    handler, _path = _seed_upload(tmp_path, owner=None)
    tool = ReadAttachmentTool()

    monkeypatch.setenv("AUTH_ENABLED", "true")
    denied = await tool.execute(
        UPLOAD_ID,
        {"owner": None, "upload_handler": handler},
    )
    assert denied == {
        "error": "read_attachment: attachment not found or not authorized.",
        "exit_code": 1,
    }

    monkeypatch.setenv("AUTH_ENABLED", "false")
    allowed = await tool.execute(
        UPLOAD_ID,
        {"owner": None, "upload_handler": handler},
    )
    assert allowed["exit_code"] == 0
    assert allowed["output"] == "hello"


@pytest.mark.asyncio
async def test_read_attachment_rejects_resolved_paths_outside_upload_storage(tmp_path):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    outside = tmp_path / UPLOAD_ID
    outside.write_text("must not leak", encoding="utf-8")

    class OutsideHandler:
        def __init__(self, root):
            self.upload_dir = str(root)

        def _inside_upload_dir(self, candidate):
            base = os.path.realpath(self.upload_dir)
            resolved = os.path.realpath(candidate)
            return os.path.commonpath([base, resolved]) == base

        @staticmethod
        def resolve_upload(upload_id, owner=None, allow_admin=True):
            return {
                "id": upload_id,
                "path": str(outside),
                "name": "notes.txt",
                "mime": "text/plain",
                "owner": owner,
            }

    result = await ReadAttachmentTool().execute(
        UPLOAD_ID,
        {"owner": "alice", "upload_handler": OutsideHandler(upload_dir)},
    )

    assert result == {
        "error": "read_attachment: attachment not found or not authorized.",
        "exit_code": 1,
    }
    assert "must not leak" not in json.dumps(result)


@pytest.mark.asyncio
async def test_read_attachment_caps_returned_content(tmp_path):
    handler, _path = _seed_upload(tmp_path, content="x" * (MAX_READ_CHARS + 500))

    result = await ReadAttachmentTool().execute(
        UPLOAD_ID,
        {"owner": "alice", "upload_handler": handler},
    )

    assert result["exit_code"] == 0
    assert len(result["output"]) == MAX_READ_CHARS
    assert result["output"].endswith("[attachment content truncated]")


@pytest.mark.asyncio
async def test_dispatch_threads_owner_and_upload_handler_to_read_attachment(tmp_path):
    handler, _path = _seed_upload(tmp_path)

    desc, result = await execute_tool_block(
        ToolBlock("read_attachment", UPLOAD_ID),
        owner="alice",
        upload_handler=handler,
    )

    assert desc.startswith("registry: read_attachment")
    assert result["exit_code"] == 0
    assert result["output"] == "hello"


def test_read_attachment_registry_schema_and_security_classification():
    schema_names = {
        item["function"]["name"]
        for item in FUNCTION_TOOL_SCHEMAS
        if item.get("type") == "function"
    }

    assert "read_attachment" in TOOL_HANDLERS
    assert "read_attachment" in TOOL_TAGS
    assert "read_attachment" in schema_names
    assert "read_attachment" in TOOL_SECTIONS
    assert "read_attachment" in _DOMAIN_TOOL_MAP["files"]
    assert "read_attachment" in PLAN_MODE_READONLY_TOOLS
    assert "read_attachment" not in NON_ADMIN_BLOCKED_TOOLS
    assert "read_file" in NON_ADMIN_BLOCKED_TOOLS

    block = function_call_to_tool_block(
        "read_attachment",
        json.dumps({"attachment": f"odysseus://attachment/{UPLOAD_ID}"}),
    )
    assert block is not None
    assert block.tool_type == "read_attachment"
    assert json.loads(block.content)["attachment"].endswith(UPLOAD_ID)
