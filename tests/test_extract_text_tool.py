import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from src.agent_tools import ocr_engine
from src.agent_tools.media_tools import ExtractTextTool, InspectMediaTool
from src.agent_tools import TOOL_HANDLERS
from src.tool_execution import _active_workspace
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS


def test_extract_text_schema_is_bounded_and_requires_a_path():
    schema = next(item["function"] for item in FUNCTION_TOOL_SCHEMAS if item["function"]["name"] == "extract_text")
    assert schema["parameters"]["additionalProperties"] is False
    assert schema["parameters"]["required"] == ["path"]
    assert schema["parameters"]["properties"]["max_results"]["maximum"] == 512
    assert TOOL_HANDLERS["extract_text"].__self__.__class__ is ExtractTextTool


def test_ocr_can_read_only_the_callers_uploaded_image_without_a_workspace(monkeypatch, tmp_path):
    from src import tool_utils
    source = tmp_path / 'owned.png'
    Image.new('RGB', (20, 20), 'white').save(source)
    def resolve(upload_id, *, owner, allow_admin):
        assert allow_admin is False
        return {'path': str(source)} if upload_id == 'fixture-upload.png' and owner == 'alice' else None
    monkeypatch.setattr(tool_utils, 'get_upload_handler', lambda: SimpleNamespace(resolve_upload=resolve))
    monkeypatch.setattr(ocr_engine, 'extract_image_text', lambda path, **kwargs: {'lines': [{'t': '42'}]})
    token = _active_workspace.set(None)
    try:
        args = json.dumps({'path': 'odysseus://attachment/fixture-upload.png'})
        owned = asyncio.run(ExtractTextTool().execute(args, {'owner': 'alice'}))
        other = asyncio.run(ExtractTextTool().execute(args, {'owner': 'bob'}))
        anonymous = asyncio.run(ExtractTextTool().execute(args, {}))
        host = asyncio.run(ExtractTextTool().execute(json.dumps({'path': str(source)}), {'owner': 'alice'}))
        traversal = asyncio.run(ExtractTextTool().execute(json.dumps({'path': 'odysseus://attachment/../fixture-upload.png'}), {'owner': 'alice'}))
    finally:
        _active_workspace.reset(token)
    assert owned['exit_code'] == 0 and owned['ocr']['lines'][0]['t'] == '42'
    assert other['exit_code'] == anonymous['exit_code'] == host['exit_code'] == traversal['exit_code'] == 1


def test_ocr_engine_returns_compact_centers_and_can_filter_numbers(monkeypatch, tmp_path: Path):
    output = SimpleNamespace(
        boxes=[[[0, 0], [10, 0], [10, 10], [0, 10]], [[20, 20], [30, 20], [30, 30], [20, 30]]],
        txts=["Label", "42"], scores=[0.99, 0.98],
    )
    monkeypatch.setattr(ocr_engine, "_engine", lambda: lambda _path: output)
    result = ocr_engine.extract_image_text(tmp_path / "unused.png", numeric_only=True)
    assert result["lines"] == [{"t": "42", "p": 0.98, "xy": [25.0, 25.0]}]


def test_extract_text_is_workspace_confined(monkeypatch, tmp_path: Path):
    Image.new("RGB", (20, 20), "white").save(tmp_path / "source.png")
    monkeypatch.setattr(ocr_engine, "extract_image_text", lambda _path, **_kwargs: {
        "legend": {"t": "text", "p": "confidence", "xy": "pixel center"}, "count": 1,
        "returned": 1, "truncated": False, "lines": [{"t": "FRESH", "p": 1.0, "xy": [10.0, 10.0]}],
    })
    token = _active_workspace.set(str(tmp_path))
    try:
        success = asyncio.run(ExtractTextTool().execute(json.dumps({"path": "/workspace/source.png"}), {}))
        escaped = asyncio.run(ExtractTextTool().execute(json.dumps({"path": "/etc/passwd"}), {}))
    finally:
        _active_workspace.reset(token)
    assert success["ocr"]["lines"][0]["t"] == "FRESH"
    assert escaped["exit_code"] == 1 and "workspace" in escaped["error"]


def test_extract_text_accepts_workspace_uri_alias_without_weakening_confinement(monkeypatch, tmp_path: Path):
    Image.new("RGB", (20, 20), "white").save(tmp_path / "source.png")
    monkeypatch.setattr(ocr_engine, "extract_image_text", lambda _path, **_kwargs: {
        "lines": [{"t": "VISIBLE", "p": 1.0, "xy": [10.0, 10.0]}],
    })
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(ExtractTextTool().execute(json.dumps({
            "path": "odysseus://workspace/source.png",
        }), {}))
        escaped = asyncio.run(ExtractTextTool().execute(json.dumps({
            "path": "odysseus://workspace/../outside.png",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert result["ocr"]["lines"][0]["t"] == "VISIBLE"
    assert escaped["exit_code"] == 1


def test_extract_text_renders_and_ocr_scans_pdf_pages(monkeypatch, tmp_path: Path):
    source = tmp_path / "scan.pdf"
    pages = [Image.new("RGB", (40, 40), "white") for _ in range(2)]
    pages[0].save(source, "PDF", save_all=True, append_images=pages[1:])
    calls = []

    def fake_ocr(path, **_kwargs):
        calls.append(Path(path).name)
        return {
            "count": 1,
            "returned": 1,
            "truncated": False,
            "lines": [{"t": "VISIBLE", "p": 1.0, "xy": [10.0, 10.0]}],
        }

    monkeypatch.setattr(ocr_engine, "extract_image_text", fake_ocr)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(ExtractTextTool().execute(
            json.dumps({"path": "/workspace/scan.pdf"}),
            {},
        ))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert result["ocr"]["page_count"] == 2
    assert result["ocr"]["pages_processed"] == 2
    assert [line["page"] for line in result["ocr"]["lines"]] == [1, 2]
    assert len(calls) == 2


def test_inspect_media_auto_augments_exact_text_queries_only(monkeypatch, tmp_path: Path):
    Image.new("RGB", (20, 20), "white").save(tmp_path / "source.png")
    monkeypatch.setattr(ocr_engine, "extract_image_text", lambda _path, **_kwargs: {
        "legend": {"t": "text", "p": "confidence", "xy": "pixel center"}, "count": 1,
        "returned": 1, "truncated": False, "lines": [{"t": "42", "p": .99, "xy": [10.0, 10.0]}],
    })
    token = _active_workspace.set(str(tmp_path))
    try:
        exact = asyncio.run(InspectMediaTool().execute(json.dumps({"path": "/workspace/source.png", "query": "numbered labels"}), {}))
        general = asyncio.run(InspectMediaTool().execute(json.dumps({"path": "/workspace/source.png", "query": "overall composition"}), {}))
    finally:
        _active_workspace.reset(token)
    assert exact["ocr_augmented"] is True and '"t":"42"' in exact["output"]
    assert "ocr_augmented" not in general
