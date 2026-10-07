"""write_file: an empty body must not truncate a file that holds data (#6414).

The reporter's shape: a model call whose arguments lost their content section
(a #6013-class parser failure) reaches WriteFileTool with an empty body, the
existing file is opened in "w" mode, and the tool answers exit_code=0 with
"Wrote 0 bytes". Each "is refused" test below measures that the bytes at the
path are still there afterwards; each "still works" test guards the write path
this change must not narrow.
"""
import builtins
import json
import os
import re

import pytest

from src import tool_execution as te
from src.agent_tools import ToolBlock
from src.agent_tools.filesystem_tools import EditFileTool, WriteFileTool
from src.tool_schemas import function_call_to_tool_block
from src.tool_parsing import parse_tool_blocks

RECIPE = "# Classic banana cake\n\nMash 3 bananas. Bake 180C for 1 hour.\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("before", [None, "", "original bytes"])
@pytest.mark.parametrize("shape,body", [("bare", ""), ("text", ""), ("text", " \t\n"),
                                      ("json", ""), ("json", " \t\n"),
                                      ("text", "new text"), ("json", "new text")])
async def test_bound_dispatch_preserves_original_write_intent(tmp_path, monkeypatch, before, shape, body):
    from tests.runtime_evidence_helpers import server_authorized_executor
    monkeypatch.setattr(te, "_owner_is_admin", lambda owner: True)
    target = tmp_path / "extensionless"
    if before is not None:
        target.write_text(before)
    content = str(target) if shape == "bare" else (
        json.dumps({"path": str(target), "content": body}) if shape == "json" else f"{target}\n{body}"
    )
    _, result = await server_authorized_executor(te.execute_tool_block)(
        ToolBlock("write_file", content), workspace=str(tmp_path), owner="admin",
        security_context=te.NO_TOOL_SECURITY_CONTEXT,
    )
    denied = shape == "bare" or (shape == "text" and not body.strip() and bool(before))
    assert result["exit_code"] == (1 if denied else 0), result
    if denied:
        assert "content" in result["error"] or "empty body" in result["error"]
        assert target.exists() == (before is not None)
        if before is not None:
            assert target.read_text() == before
    else:
        expected = "" if shape == "text" and not body.strip() and before == "" else body
        assert target.read_text() == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"content": None}, {"content": 1},
                                    {"content": []}, {"content": {}}, "malformed"])
async def test_invalid_json_write_rejected_before_path_access(monkeypatch, payload):
    def forbidden(*args):
        raise AssertionError("invalid content accessed filesystem policy")
    monkeypatch.setattr(te, "_resolve_tool_path", forbidden)
    content = '{"path":' if payload == "malformed" else json.dumps({"path": "/unreachable", **payload})
    result = await WriteFileTool().execute(content, {})
    assert result["exit_code"] == 1


@pytest.mark.asyncio
async def test_fenced_json_normalizing_empty_is_not_an_explicit_clear(tmp_path):
    target = tmp_path / "source.py"
    target.write_text("preserve source")
    result = await WriteFileTool().execute(json.dumps({"path": str(target), "content": "```python\n\n```"}), {})
    assert result["exit_code"] == 1
    assert target.read_text() == "preserve source"


@pytest.mark.asyncio
async def test_model_declared_clear_field_cannot_authorize_implicit_empty(target):
    _seed(target)
    result = await WriteFileTool().execute(json.dumps({"path": target, "declared_clear": True}), {})
    assert result["exit_code"] == 1
    assert _read(target) == RECIPE


def test_implicit_whitespace_noop_effect_digest_describes_actual_empty_bytes(tmp_path):
    import hashlib
    from src.agent_runtime.authority import ExactOperation
    from src.agent_runtime.resource_binding import resolve_filesystem_operation
    from src.agent_runtime.resources import FilesystemRoot
    from src.agent_runtime.effect_adapters import _filesystem_scope
    target = tmp_path / "empty.txt"
    target.touch()
    bound = resolve_filesystem_operation(ExactOperation.normalize("write_file", f"{target}\n \t"),
                                         roots=(FilesystemRoot.seal(tmp_path),), workspace=str(tmp_path))
    _, obligations = _filesystem_scope(bound)
    assert obligations[0].expected == hashlib.sha256(b"").hexdigest()


@pytest.fixture
def target(tmp_path, monkeypatch):
    from src import tool_execution

    monkeypatch.setattr(tool_execution, "get_active_workspace", lambda: str(tmp_path))
    return str(tmp_path / "classic-banana-cake.md")


def _seed(path, text=RECIPE):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    return text


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _text_call(path, body=None):
    """The documented text form: first line is the path, the rest is the content."""
    return path if body is None else f"{path}\n{body}"


def _json_call(path, **content):
    """The fenced inline-JSON form, which the handler decodes itself."""
    return json.dumps({"path": path, **content})


# ── The truncation the issue reports ──────────────────────────────────────
@pytest.mark.asyncio
async def test_empty_body_after_the_path_line_is_refused_and_the_file_survives(target):
    _seed(target)
    res = await WriteFileTool().execute(_text_call(target, ""), {})
    assert res["exit_code"] == 1, res
    assert _read(target) == RECIPE


@pytest.mark.asyncio
async def test_path_only_call_with_no_content_section_is_refused(target):
    """`lines[1] if len(lines) > 1 else ""` has two producers; this is the no-newline one."""
    _seed(target)
    res = await WriteFileTool().execute(_text_call(target), {})
    assert res["exit_code"] == 1, res
    assert _read(target) == RECIPE


@pytest.mark.asyncio
async def test_inline_json_without_a_content_key_is_refused(target):
    """A parser that keeps `path` and drops `content` is the reported failure."""
    _seed(target)
    res = await WriteFileTool().execute(json.dumps({"path": target}), {})
    assert res["exit_code"] == 1, res
    assert _read(target) == RECIPE


@pytest.mark.asyncio
async def test_inline_json_null_content_is_refused_and_never_written_as_the_word_none(target):
    """`str(_a.get("content", ""))` on a null turns a lost body into the 4 bytes "None"."""
    _seed(target)
    res = await WriteFileTool().execute(_json_call(target, content=None), {})
    assert res["exit_code"] == 1, res
    assert _read(target) == RECIPE


@pytest.mark.asyncio
async def test_whitespace_only_body_is_refused(target):
    """A body that carries no characters is the same failure with padding left in."""
    _seed(target)
    res = await WriteFileTool().execute(_text_call(target, "   \n  "), {})
    assert res["exit_code"] == 1, res
    assert _read(target) == RECIPE


@pytest.mark.asyncio
async def test_non_utf8_target_is_refused_on_its_size_not_on_the_decoded_read(target):
    """The existing read swallows UnicodeDecodeError and answers "", which would let a
    binary or latin-1 file look empty to the guard while holding real bytes."""
    with open(target, "wb") as handle:
        handle.write(b"\xc3\xa9\xe8\xaf\xad\xff\xfe\x00binary-ish payload")
    before = os.path.getsize(target)
    assert before > 0
    res = await WriteFileTool().execute(_text_call(target, ""), {})
    assert res["exit_code"] == 1, res
    assert os.path.getsize(target) == before


@pytest.mark.asyncio
async def test_refusal_creates_no_extra_files_next_to_the_target(target):
    _seed(target)
    directory = os.path.dirname(target)
    res = await WriteFileTool().execute(_text_call(target, ""), {})
    assert res["exit_code"] == 1, res
    assert os.listdir(directory) == [os.path.basename(target)]


@pytest.mark.asyncio
async def test_refusal_names_the_byte_count_and_the_explicit_form(target):
    _seed(target)
    res = await WriteFileTool().execute(_text_call(target, ""), {})
    error = res.get("error", "")
    assert str(os.path.getsize(target)) in error, error
    # The caller in a loop has to be able to correct itself in one round.
    assert '"content": ""' in error, error
    assert "output" not in res, res


@pytest.mark.asyncio
async def test_refusal_suggestion_is_valid_json_for_paths_with_quotes(target, monkeypatch):
    quoted_path = os.path.join(os.path.dirname(target), 'recipe"draft.md')
    # Use an alias for real file I/O: Windows filenames cannot contain quotes.
    real_open = builtins.open
    real_isfile = os.path.isfile
    real_getsize = os.path.getsize
    real_stat = os.stat

    def disk_path(path):
        return target if path == quoted_path else path

    monkeypatch.setattr(builtins, "open", lambda path, *a, **kw: real_open(disk_path(path), *a, **kw))
    monkeypatch.setattr(os.path, "isfile", lambda path: real_isfile(disk_path(path)))
    monkeypatch.setattr(os.path, "getsize", lambda path: real_getsize(disk_path(path)))
    monkeypatch.setattr(os, "stat", lambda path, *a, **kw: real_stat(disk_path(path), *a, **kw))
    _seed(target)
    refused = await WriteFileTool().execute(_text_call(quoted_path, ""), {})
    match = re.search(
        r"explicit empty content: (\{.*\})$", refused["error"], re.S
    )
    assert match, refused
    suggested_args = json.loads(match.group(1))
    assert suggested_args == {"path": quoted_path, "content": ""}
    cleared = await WriteFileTool().execute(match.group(1), {})
    assert cleared["exit_code"] == 0, cleared
    assert os.path.getsize(quoted_path) == 0


@pytest.mark.asyncio
async def test_the_resend_the_refusal_prints_actually_clears_the_file(target):
    """The guidance is only useful if a caller can paste it back verbatim. This reads
    the JSON object out of the refusal and runs it as the next call."""
    _seed(target)
    refused = await WriteFileTool().execute(_text_call(target, ""), {})
    resend = re.search(r"\{.*\}", refused["error"], re.S)
    assert resend, refused
    res = await WriteFileTool().execute(resend.group(0), {})
    assert res["exit_code"] == 0, res
    assert os.path.getsize(target) == 0


# ── Deliberate writes this change must keep working ───────────────────────
@pytest.mark.asyncio
async def test_explicit_empty_content_in_the_json_form_clears_the_file(target):
    """"A deliberate empty-file creation can be made explicit" (the issue's own words):
    a `content` key that is literally an empty string is a declaration, not a loss."""
    _seed(target)
    res = await WriteFileTool().execute(_json_call(target, content=""), {})
    assert res["exit_code"] == 0, res
    assert os.path.getsize(target) == 0


@pytest.mark.asyncio
async def test_empty_body_on_a_new_path_still_creates_an_empty_file(target):
    res = await WriteFileTool().execute(_text_call(target, ""), {})
    assert res["exit_code"] == 0, res
    assert os.path.isfile(target) and os.path.getsize(target) == 0


@pytest.mark.asyncio
async def test_whitespace_only_body_on_a_new_path_preserves_the_requested_content(
    target,
):
    whitespace = "   \n\t"
    res = await WriteFileTool().execute(_text_call(target, whitespace), {})
    assert res["exit_code"] == 0, res
    assert _read(target) == whitespace


@pytest.mark.asyncio
async def test_explicit_whitespace_only_json_content_preserves_the_requested_content(
    target,
):
    _seed(target)
    whitespace = " \t "
    res = await WriteFileTool().execute(_json_call(target, content=whitespace), {})
    assert res["exit_code"] == 0, res
    assert _read(target) == whitespace


@pytest.mark.asyncio
async def test_empty_body_over_an_already_empty_file_succeeds(target):
    """Nothing is at risk, so the guard has nothing to refuse."""
    _seed(target, "")
    res = await WriteFileTool().execute(_text_call(target, ""), {})
    assert res["exit_code"] == 0, res
    assert os.path.getsize(target) == 0


@pytest.mark.asyncio
async def test_empty_body_does_not_truncate_data_written_after_the_size_check(
    target, monkeypatch
):
    """A write racing the size check must survive the empty-body path."""
    _seed(target, "")
    real_getsize = os.path.getsize

    def write_after_size_check(path):
        size = real_getsize(path)
        if path == target and size == 0:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("concurrent update")
        return size

    monkeypatch.setattr(os.path, "getsize", write_after_size_check)
    res = await WriteFileTool().execute(_text_call(target, ""), {})
    assert res["exit_code"] == 0, res
    assert _read(target) == "concurrent update"


@pytest.mark.asyncio
async def test_empty_body_does_not_truncate_a_file_created_after_the_absence_check(
    target, monkeypatch
):
    """Exclusive creation must not overwrite a file that appeared during the check."""
    real_isfile = os.path.isfile

    def create_after_absence_check(path):
        exists = real_isfile(path)
        if path == target and not exists:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("concurrent update")
            return False
        return exists

    monkeypatch.setattr(os.path, "isfile", create_after_absence_check)
    res = await WriteFileTool().execute(_text_call(target, ""), {})
    assert res["exit_code"] == 1, res
    assert _read(target) == "concurrent update"


@pytest.mark.asyncio
async def test_whitespace_body_does_not_clobber_a_concurrent_creation(
    target, monkeypatch
):
    """Whitespace on a missing path must not overwrite a competing writer."""
    real_open = builtins.open
    real_link = os.link

    def write_concurrent_content(path):
        with real_open(path, "w", encoding="utf-8") as concurrent:
            concurrent.write("concurrent update")

    def interleaved_open(path, mode="r", *args, **kwargs):
        handle = real_open(path, mode, *args, **kwargs)
        if path == target and mode == "x":
            write_concurrent_content(path)
        return handle

    def interleaved_link(source, destination, *args, **kwargs):
        if destination == target:
            write_concurrent_content(destination)
        return real_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", interleaved_open)
    monkeypatch.setattr(os, "link", interleaved_link)
    res = await WriteFileTool().execute(_text_call(target, "  \n\t"), {})
    assert res["exit_code"] == 1, res
    assert _read(target) == "concurrent update"


@pytest.mark.asyncio
async def test_a_real_body_still_writes_and_reports_a_diff(target):
    _seed(target)
    replacement = "# Classic banana cake\n\nMash 4 bananas.\n"
    res = await WriteFileTool().execute(_text_call(target, replacement), {})
    assert res["exit_code"] == 0, res
    assert _read(target) == replacement
    assert res["diff"]["added"] == 1 and res["diff"]["removed"] == 1


@pytest.mark.asyncio
async def test_edit_file_remains_an_explicit_way_to_clear_a_file(target):
    """The route this change leaves open for a caller that cannot reach the fenced
    inline-JSON form: replace the whole content with nothing."""
    _seed(target)
    res = await EditFileTool().execute(
        json.dumps({"path": target, "old_string": RECIPE, "new_string": ""}), {}
    )
    assert res["exit_code"] == 0, res
    assert os.path.getsize(target) == 0


@pytest.mark.asyncio
async def test_native_function_call_can_explicitly_clear_a_file(target):
    """The native schema conversion must preserve the explicit empty-content intent."""
    _seed(target)
    block = function_call_to_tool_block(
        "write_file", json.dumps({"path": target, "content": ""})
    )
    assert block is not None
    res = await WriteFileTool().execute(block.content, {})
    assert res["exit_code"] == 0, res
    assert os.path.getsize(target) == 0


@pytest.mark.asyncio
async def test_raw_openai_function_call_can_explicitly_clear_a_file(target):
    """The raw OpenAI JSON parser must retain explicit empty-content intent too."""
    _seed(target)
    arguments = json.dumps({"path": target, "content": ""})
    raw_call = json.dumps(
        {
            "type": "function",
            "function": {"name": "write_file", "arguments": arguments},
        }
    )
    blocks = parse_tool_blocks(raw_call)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "write_file"
    res = await WriteFileTool().execute(blocks[0].content, {})
    assert res["exit_code"] == 0, res
    assert os.path.getsize(target) == 0


# ── The live dispatch path, not just the handler ──────────────────────────
@pytest.mark.asyncio
async def test_execute_tool_block_refuses_a_lost_body_without_touching_the_file(target, monkeypatch):
    """#6414 reached the reporter through a parsed model call, so the refusal has to
    survive execute_tool_block's wrapping and still report failure upstream."""
    _seed(target)
    monkeypatch.setattr(te, "_owner_is_admin", lambda owner: True)
    from tests.runtime_evidence_helpers import server_authorized_executor
    _desc, result = await server_authorized_executor(te.execute_tool_block)(
        ToolBlock("write_file", _text_call(target, "")),
        owner="admin",
        workspace=os.path.dirname(target),
        security_context=te.NO_TOOL_SECURITY_CONTEXT,
    )
    assert result.get("exit_code") == 1, result
    assert "holds" in result["error"] and "unchanged" in result["error"]
    assert _read(target) == RECIPE


# ── Task 3.5 regression: transport repair cannot mint clear intent ────────
@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [{}, {"content": None}], ids=["missing-content", "null-content"])
async def test_legacy_command_whitespace_does_not_mint_explicit_clear(tmp_path, monkeypatch, extra):
    """Normalization or alias repair must never mint clear authority from legacy command text."""
    from tests.runtime_evidence_helpers import server_authorized_executor
    monkeypatch.setattr(te, "_owner_is_admin", lambda owner: True)
    initial = b"ORIGINAL NONEMPTY CONTENT\n"
    direct = tmp_path / "direct.txt"
    direct.write_bytes(initial)
    execute = server_authorized_executor(te.execute_tool_block)
    _, control = await execute(
        ToolBlock("write_file", str(direct) + "\n \t"),
        workspace=str(tmp_path),
        owner="admin",
        security_context=te.NO_TOOL_SECURITY_CONTEXT,
    )
    assert control["exit_code"] == 1 and direct.read_bytes() == initial

    target = tmp_path / "native.txt"
    target.write_bytes(initial)
    block = function_call_to_tool_block("write_file", json.dumps({"command": str(target) + "\n \t", **extra}))
    # Legacy command whitespace is refused repair, rejecting the call before dispatch.
    assert block is None
    if block is not None:
        _, result = await execute(
            block,
            workspace=str(tmp_path),
            owner="admin",
            security_context=te.NO_TOOL_SECURITY_CONTEXT,
        )
    assert target.read_bytes() == initial, f"Native transport repair authorized destructive whitespace: {block!r}"


@pytest.mark.asyncio
async def test_explicit_json_whitespace_content_authorizes_replacement(tmp_path, monkeypatch):
    """Original explicit JSON string content intentionally authorizes whitespace replacement."""
    from tests.runtime_evidence_helpers import server_authorized_executor
    monkeypatch.setattr(te, "_owner_is_admin", lambda owner: True)
    initial = b"ORIGINAL NONEMPTY CONTENT\n"
    target = tmp_path / "whitespace_replace.txt"
    target.write_bytes(initial)
    execute = server_authorized_executor(te.execute_tool_block)
    block = function_call_to_tool_block("write_file", json.dumps({"path": str(target), "content": " \t"}))
    assert block is not None
    _, result = await execute(
        block,
        workspace=str(tmp_path),
        owner="admin",
        security_context=te.NO_TOOL_SECURITY_CONTEXT,
    )
    assert result["exit_code"] == 0, result
    assert target.read_bytes() == b" \t"


@pytest.mark.asyncio
async def test_legacy_command_exact_empty_is_rejected(tmp_path):
    """Legacy exact-empty command shape is rejected before dispatch."""
    target = tmp_path / "exact_empty.txt"
    block = function_call_to_tool_block("write_file", json.dumps({"command": str(target) + "\n"}))
    assert block is None


@pytest.mark.asyncio
async def test_legacy_command_nonempty_write_still_works(tmp_path, monkeypatch):
    """Ordinary nonempty legacy command write shape functions correctly."""
    from tests.runtime_evidence_helpers import server_authorized_executor
    monkeypatch.setattr(te, "_owner_is_admin", lambda owner: True)
    target = tmp_path / "nonempty_command.txt"
    target.write_text("before\n")
    execute = server_authorized_executor(te.execute_tool_block)
    block = function_call_to_tool_block("write_file", json.dumps({"command": str(target) + "\nhello world"}))
    assert block is not None
    _, result = await execute(
        block,
        workspace=str(tmp_path),
        owner="admin",
        security_context=te.NO_TOOL_SECURITY_CONTEXT,
    )
    assert result["exit_code"] == 0, result
    assert target.read_text() == "hello world"


@pytest.mark.asyncio
async def test_legacy_command_indented_code_preserves_indentation(tmp_path, monkeypatch):
    """Legacy command repair must preserve raw indentation and not strip code content."""
    from tests.runtime_evidence_helpers import server_authorized_executor
    monkeypatch.setattr(te, "_owner_is_admin", lambda owner: True)
    target = tmp_path / "indented_code.py"
    execute = server_authorized_executor(te.execute_tool_block)
    code = "    def compute():\n        return 42\n"
    block = function_call_to_tool_block("write_file", json.dumps({"command": str(target) + "\n" + code}))
    assert block is not None
    _, result = await execute(
        block,
        workspace=str(tmp_path),
        owner="admin",
        security_context=te.NO_TOOL_SECURITY_CONTEXT,
    )
    assert result["exit_code"] == 0, result
    assert target.read_text() == code


@pytest.mark.asyncio
async def test_raw_openai_call_legacy_command_whitespace_is_rejected(tmp_path):
    """Raw OpenAI function call parser rejects ambiguous whitespace-only legacy commands."""
    initial = b"PRESERVED VIA OPENAI CALL\n"
    target = tmp_path / "raw_call.txt"
    target.write_bytes(initial)
    raw_call = json.dumps({
        "type": "function",
        "function": {
            "name": "write_file",
            "arguments": json.dumps({"command": str(target) + "\n \t"}),
        },
    })
    blocks = parse_tool_blocks(raw_call)
    assert len(blocks) == 0
    assert target.read_bytes() == initial
