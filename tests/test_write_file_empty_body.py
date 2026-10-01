"""write_file: an empty body must not truncate a file that holds data (#6414).

The reporter's shape: a model call whose arguments lost their content section
(a #6013-class parser failure) reaches WriteFileTool with an empty body, the
existing file is opened in "w" mode, and the tool answers exit_code=0 with
"Wrote 0 bytes". Each "is refused" test below measures that the bytes at the
path are still there afterwards; each "still works" test guards the write path
this change must not narrow.
"""
import json
import os
import re
import tempfile

import pytest

from src import tool_execution as te
from src.agent_tools import ToolBlock
from src.agent_tools.filesystem_tools import EditFileTool, WriteFileTool

RECIPE = "# Classic banana cake\n\nMash 3 bananas. Bake 180C for 1 hour.\n"


@pytest.fixture
def target():
    """A fresh directory under an allowed workspace root."""
    import tempfile
    # Use C:\tmp which is in _tool_path_roots
    with tempfile.TemporaryDirectory(prefix="odysseus-6414-", dir="C:\\tmp") as directory:
        yield os.path.join(directory, "classic-banana-cake.md")


def _seed(path, text=RECIPE):
    with open(path, "w", encoding="utf-8") as handle:
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
    # The actual file size on disk (accounts for platform line endings)
    actual_size = os.path.getsize(target)
    assert str(actual_size) in error, error
    # The caller in a loop has to be able to correct itself in one round.
    assert '"content": ""' in error, error
    assert "output" not in res, res


@pytest.mark.asyncio
async def test_the_resend_the_refusal_prints_actually_clears_the_file(target):
    """The guidance is only useful if a caller can paste it back verbatim. This reads
    the JSON object out of the refusal and runs it as the next call."""
    _seed(target)
    refused = await WriteFileTool().execute(_text_call(target, ""), {})
    # The error contains a JSON after "resend with an explicit empty content: "
    match = re.search(r'resend with an explicit empty content: (\{.*?\})', refused["error"])
    assert match, f"No JSON found in error: {refused}"
    resend_json = match.group(1)
    # The JSON in the error has unescaped backslashes in the path (e.g., C:\tmp\...)
    # We need to escape them for valid JSON parsing
    resend_json = resend_json.replace('\\', '\\\\')
    resend_dict = json.loads(resend_json)
    res = await WriteFileTool().execute(json.dumps(resend_dict), {})
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
async def test_empty_body_over_an_already_empty_file_succeeds(target):
    """Nothing is at risk, so the guard has nothing to refuse."""
    _seed(target, "")
    res = await WriteFileTool().execute(_text_call(target, ""), {})
    assert res["exit_code"] == 0, res
    assert os.path.getsize(target) == 0


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


# ── The live dispatch path, not just the handler ──────────────────────────
@pytest.mark.asyncio
async def test_execute_tool_block_refuses_a_lost_body_without_touching_the_file(target, monkeypatch):
    """#6414 reached the reporter through a parsed model call, so the refusal has to
    survive execute_tool_block's wrapping and still report failure upstream."""
    _seed(target)
    monkeypatch.setattr(te, "_owner_is_admin", lambda owner: True)
    _desc, result = await te.execute_tool_block(
        ToolBlock("write_file", _text_call(target, "")),
        owner="admin",
        security_context=te.NO_TOOL_SECURITY_CONTEXT,
    )
    assert result.get("exit_code") == 1, result
    assert _read(target) == RECIPE
