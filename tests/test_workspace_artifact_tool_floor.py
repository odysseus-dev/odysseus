from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def native_resource_authority(tmp_path, monkeypatch):
    from tests.process_resource_helpers import install_native_authority
    from src.agent_runtime import process_resources
    from src import containment
    workspace = tmp_path / "native-workspace"
    workspace.mkdir()
    control = tmp_path.parent / (tmp_path.name + "-control")
    monkeypatch.setattr(process_resources, "_LAUNCH_DIR", control / "launches")
    monkeypatch.setattr(containment, "_store_path", lambda: control / "grants.json")
    install_native_authority(monkeypatch, workspace)


def test_unoffered_artifact_recovery_is_bounded():
    from src.agent_loop import _artifact_unoffered_recovery_exhausted

    assert not _artifact_unoffered_recovery_exhausted(1)
    assert not _artifact_unoffered_recovery_exhausted(2)
    assert _artifact_unoffered_recovery_exhausted(3)
    assert _artifact_unoffered_recovery_exhausted(20)


def test_artifact_mutation_is_not_counted_as_post_correction_verification():
    from src.agent_loop import _artifact_calls_are_verification_only
    from src.tool_types import ToolBlock

    mutation = ToolBlock("write_file", "/workspace/output.html\n<html></html>")
    verification = ToolBlock(
        "private_browser",
        '{"action":"open","url":"file:///workspace/output.html"}',
    )

    assert not _artifact_calls_are_verification_only([])
    assert not _artifact_calls_are_verification_only([mutation])
    assert not _artifact_calls_are_verification_only([mutation, verification])
    assert _artifact_calls_are_verification_only([verification])


def test_post_convergence_artifact_repair_is_bounded_after_success():
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    assert "_artifact_finish_convergence_sent" in source
    assert "_artifact_finish_post_correction_mutation_seen = True" in source
    assert "its automatic preview is terminal" in source


def test_consumed_post_correction_verification_closes_tool_surface():
    from src.agent_loop import _post_correction_verification_available

    assert _post_correction_verification_available(
        correction_seen=True, tool_used=False, mutation_seen=False
    )
    assert not _post_correction_verification_available(
        correction_seen=True, tool_used=True, mutation_seen=False
    )
    assert not _post_correction_verification_available(
        correction_seen=True, tool_used=False, mutation_seen=True
    )
    assert not _post_correction_verification_available(
        correction_seen=False, tool_used=False, mutation_seen=False
    )


def test_workspace_artifact_floor_is_narrow_and_includes_writers():
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    assert "_artifact_creation_requested" in source
    assert '"python", "write_file", "read_file"' in source
    assert "(workspace or _native_artifact_runtime)" in source
    assert "_artifact_mutation_surface_for_missing" in source
    assert "Do not use `python` until every text/table artifact has been written" in source
    assert '"write_file",' in source
    assert '"python",' in source
    assert '"inspect_media",' in source
    assert 'not path.startswith("/workspace/fixtures/")' in source
    assert "写在|输出|放进" in source
    assert "native terminal sandbox re-enabled workspace tools" in source
    assert "set(tool_policy.disabled_tools) - _native_reenabled_tools" in source


def test_native_declared_artifact_exposes_writer_without_creation_verb():
    """Native benchmark contracts must not depend on prompt wording.

    Video/MME tasks commonly say "list ... in /workspace/answer.txt" rather
    than "write" or "create".  The completion contract is authoritative and
    must still make write_file available to the model.
    """
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    assert "_declared_native_artifacts" in source
    assert "bool(_declared_native_artifacts)" in source
    assert "_native_completion.get(\"required_artifacts\")" in source


def test_artifact_creation_flag_is_initialized_before_route_builder():
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    flag = "_artifact_creation_requested = False"
    route = "    def _route_relevant_tools(candidate_model: str):"
    assert source.index(flag) < source.index(route)


def test_html_artifacts_keep_native_browser_for_render_verification():
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    assert 'Path(path).suffix.casefold() in {".html", ".htm"}' in source
    assert "HTML artifact requires native private_browser verification" in source
    assert 'if "private_browser" not in _hard_blocked_tools:' in source
    assert "or _html_artifact_requested" in source
    assert source.count("or _html_artifact_requested") >= 3


def test_compact_html_artifacts_keep_native_browser_schema():
    from src.agent_loop import _compact_native_route_tools

    selected = _compact_native_route_tools(
        {"private_browser", "python", "write_file", "read_file"},
        "Create /workspace/output.html from /workspace/fixtures/config.json",
        set(),
    )
    assert "private_browser" in selected


def test_completed_html_mutation_preserves_browser_verification_only():
    from src.agent_loop import (
        _artifact_browser_render_required,
        _completed_artifact_acquisition_tools_to_remove,
    )

    rendered = _completed_artifact_acquisition_tools_to_remove(browser_render=True)
    ordinary = _completed_artifact_acquisition_tools_to_remove(browser_render=False)

    assert rendered == {"pdf_extract", "web_fetch", "web_search"}
    assert ordinary == {
        "pdf_extract", "private_browser", "web_fetch", "web_search",
    }
    assert _artifact_browser_render_required("Create a PNG", ["/workspace/output.html"])
    assert not _artifact_browser_render_required("Create a PNG", [])


def test_compact_html_artifact_ignores_false_email_and_cookbook_domains():
    from src.agent_loop import _compact_native_route_tools

    selected = _compact_native_route_tools(
        {
            "python", "write_file", "read_file", "private_browser",
            "list_emails", "read_email", "bulk_email", "serve_model",
            "stop_served_model", "list_served_models",
        },
        "Create /workspace/output.html with a Happy New Year message.",
        {"email", "cookbook"},
    )

    assert {"python", "write_file", "read_file", "private_browser"} <= selected
    assert not (
        {
            "list_emails", "read_email", "bulk_email", "serve_model",
            "stop_served_model", "list_served_models",
        }
        & selected
    )


def test_compact_artifact_keeps_explicit_email_and_cookbook_tools():
    from src.agent_loop import _compact_native_route_tools

    email_selected = _compact_native_route_tools(
        {"write_file", "list_emails", "read_email", "download_attachment"},
        "Read the email attachment and save /workspace/summary.txt.",
        {"email", "files"},
    )
    cookbook_selected = _compact_native_route_tools(
        {"write_file", "download_model", "serve_model", "list_served_models"},
        "Download the model checkpoint and save it under /workspace/model.",
        {"cookbook", "files"},
    )

    assert {"list_emails", "read_email", "download_attachment"} <= email_selected
    assert {"serve_model", "list_served_models"} <= cookbook_selected


def test_compact_native_media_artifact_keeps_writer_when_rag_misses_it():
    from src.agent_loop import _compact_native_route_tools

    selected = _compact_native_route_tools(
        {"inspect_media", "transcribe_media", "apply_patch"},
        "写入 /workspace/output.txt，内容来自 /workspace/fixtures/video.webm",
        set(),
    )
    assert "write_file" in selected


def test_compact_native_transformed_media_keeps_shell_mutation_floor():
    from src.agent_loop import _compact_native_route_tools

    selected = _compact_native_route_tools(
        {"inspect_media", "transcribe_media", "read_file", "ls"},
        (
            "分析 /workspace/fixtures/video.mp4，剪辑并拼接后导出 "
            "/workspace/band_cut.mp4，同时写入 /workspace/timestamps.txt。"
        ),
        {"files"},
    )
    assert {"inspect_media", "write_file", "bash"} <= selected


def test_compact_local_report_artifact_keeps_shell_for_workspace_generator():
    from src.agent_loop import _compact_native_route_tools

    selected = _compact_native_route_tools(
        {"python", "write_file", "read_file", "glob"},
        (
            "Run /workspace/process_report.py to analyze /workspace/fixtures/data.csv "
            "and generate the report and chart at /workspace/report.csv and "
            "/workspace/chart.png."
        ),
        {"web", "documents"},
    )
    assert "bash" in selected


def test_url_report_artifact_still_hides_shell():
    from src.agent_loop import _compact_native_route_tools

    selected = _compact_native_route_tools(
        {"python", "write_file", "read_file", "bash", "web_search", "web_fetch"},
        "Read https://example.com/report.pdf and write /workspace/summary.txt.",
        {"web", "documents"},
    )
    assert "bash" not in selected


def test_terminal_html_artifact_queues_one_native_render_check():
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    assert "_html_artifact_verification_required" in source
    assert '"action": "open"' in source
    assert "queued native HTML render verification" in source


def test_workspace_file_mutation_paths_distinguishes_helper_from_html_target():
    import json

    from src.agent_loop import _workspace_file_mutation_paths
    from src.tool_types import ToolBlock

    helper = ToolBlock("write_file", json.dumps({
        "path": "/workspace/create_page.py",
        "content": "print('helper')",
    }))
    target = ToolBlock("write_file", json.dumps({
        "path": "/workspace/output.html",
        "content": "<html></html>",
    }))

    assert _workspace_file_mutation_paths(helper) == {"/workspace/create_page.py"}
    assert _workspace_file_mutation_paths(target) == {"/workspace/output.html"}


def test_workspace_file_mutation_paths_reads_canonical_native_write_file():
    import json

    from src.agent_loop import _workspace_file_mutation_paths
    from src.tool_schemas import function_call_to_tool_block

    block = function_call_to_tool_block(
        "write_file",
        json.dumps({
            "path": "/workspace/output.html",
            "content": "<!doctype html><title>artifact</title>",
        }),
    )

    assert block is not None
    assert block.content.startswith("/workspace/output.html\n")
    assert _workspace_file_mutation_paths(block) == {"/workspace/output.html"}


def test_evidenced_workspace_mutation_paths_recognizes_python_required_output():
    from src.agent_evidence import CompletionRequirements
    from src.agent_loop import _evidenced_workspace_mutation_paths

    requirements = CompletionRequirements(
        required_artifacts=("/workspace/output.html",),
    )
    events = [{
        "round": 2,
        "tool": "python",
        "command": "open('/workspace/output.html', 'w').write('<h1>ok</h1>')",
        "output": "created",
        "exit_code": 0,
    }]

    assert _evidenced_workspace_mutation_paths(
        events,
        requirements,
        round_num=2,
    ) == {"/workspace/output.html"}
    assert not _evidenced_workspace_mutation_paths(
        events,
        requirements,
        round_num=3,
    )


def test_workspace_file_mutation_paths_reads_apply_patch_targets():
    from src.agent_loop import _workspace_file_mutation_paths
    from src.tool_types import ToolBlock

    block = ToolBlock(
        "apply_patch",
        "*** Begin Patch\n*** Update File: /workspace/output.html\n@@\n*** End Patch",
    )
    assert _workspace_file_mutation_paths(block) == {"/workspace/output.html"}


def test_missing_raster_after_svg_write_uses_native_svg_renderer():
    from src.agent_loop import _svg_render_recovery_blocks

    blocks = _svg_render_recovery_blocks(
        ["/workspace/floorplan.png"],
        {"inspect_media"},
        set(),
        [{
            "tool": "write_file",
            "command": "/workspace/floorplan.svg\n<svg></svg>",
            "exit_code": 0,
        }],
    )
    assert blocks is not None
    assert blocks[0].tool_type == "inspect_media"
    assert '"path": "/workspace/floorplan.svg"' in blocks[0].content
    assert '"output_path": "/workspace/floorplan.png"' in blocks[0].content


def test_native_terminal_sft_owner_keeps_workspace_writers():
    from src.agent_loop import _strip_workspace_tools_for_sft

    selected = _strip_workspace_tools_for_sft(
        {"bash", "python", "read_file", "write_file"},
        "sft_clawmm_eval",
        {"surface": "odysseus-native", "terminal_agent": True},
    )
    assert selected == {"bash", "python", "read_file", "write_file"}


def test_native_terminal_runtime_bypasses_chat_only_general_no_tool_clamp():
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    assert "_native_terminal_runtime = bool(" in source
    assert "elif _ody_general_no_tool_mode and not _native_terminal_runtime:" in source


def test_pdf_extract_reuses_exact_user_supplied_source_url():
    import json

    from src.agent_loop import _normalize_pdf_extract_source_url
    from src.tool_types import ToolBlock

    exact = (
        "https://openaccess.example.org/Video-MME_The_First_"
        "Benchmark_in_CVPR_2025_paper.pdf"
    )
    malformed = exact.replace("_in_CVPR_", "_in_CVPR")
    block = ToolBlock(
        "pdf_extract",
        json.dumps({"url": malformed, "query": "main results table"}),
    )

    repaired = _normalize_pdf_extract_source_url(
        block, f"Please download and read this PDF:\n{exact}\nThen extract the table."
    )

    assert json.loads(repaired.content) == {
        "url": exact,
        "query": "main results table",
    }


def test_pdf_extract_normalizes_local_file_url():
    import json

    from src.agent_loop import _normalize_pdf_extract_source_url
    from src.tool_types import ToolBlock

    block = ToolBlock(
        "pdf_extract",
        json.dumps({
            "url": "file:///workspace/fixtures/paper.pdf",
            "query": "Table 3 Spatial scores",
        }),
    )
    repaired = _normalize_pdf_extract_source_url(block, "Read the local PDF")
    assert json.loads(repaired.content)["url"] == "/workspace/fixtures/paper.pdf"


def test_pdf_extract_does_not_replace_with_ambiguous_unrelated_pdf():
    import json

    from src.agent_loop import _normalize_pdf_extract_source_url
    from src.tool_types import ToolBlock

    called = "https://third.example.net/report.pdf"
    block = ToolBlock(
        "pdf_extract", json.dumps({"url": called, "query": "results"})
    )
    user_text = (
        "Compare https://one.example.org/a.pdf with "
        "https://two.example.org/b.pdf"
    )

    assert _normalize_pdf_extract_source_url(block, user_text) == block


def test_pdf_extract_query_inherits_requested_technical_entities():
    import json

    from src.agent_loop import ToolBlock, _normalize_pdf_extract_query_entities

    block = ToolBlock(
        "pdf_extract",
        json.dumps({
            "url": "https://example.test/report.pdf",
            "query": "AIME Pass@1 accuracy",
        }),
    )
    user_text = (
        "Read the PDF and extract scores for DeepSeek-R1, DeepSeek-R1-Zero, "
        "Qwen3-235B-A22B, and OpenAI-o1. Save /workspace/reasoning_math.csv."
    )

    repaired = _normalize_pdf_extract_query_entities(block, user_text)
    args = json.loads(repaired.content)
    assert args["query"].startswith("AIME Pass@1 accuracy")
    assert "DeepSeek-R1" in args["query"]
    assert "DeepSeek-R1-Zero" in args["query"]
    assert "Qwen3-235B-A22B" in args["query"]
    assert "OpenAI-o1" in args["query"]
    assert "reasoning_math.csv" not in args["query"]


def test_unscoped_local_pdf_inspection_uses_user_table_terms():
    import json

    from src.agent_loop import _normalize_local_pdf_inspection_query
    from src.tool_types import ToolBlock

    block = ToolBlock(
        "inspect_media",
        json.dumps({"path": "/workspace/fixtures/report.pdf", "pages": 4}),
    )
    repaired = _normalize_local_pdf_inspection_query(
        block,
        "Extract the Qwen3-VL-A22B and Gemini-2.5-Pro rows from the main table.",
    )

    args = json.loads(repaired.content)
    assert args["query"] == (
        "Extract the Qwen3-VL-A22B and Gemini-2.5-Pro rows from the main table."
    )


def test_scoped_local_pdf_inspection_keeps_explicit_page():
    import json

    from src.agent_loop import _normalize_local_pdf_inspection_query
    from src.tool_types import ToolBlock

    block = ToolBlock(
        "inspect_media",
        json.dumps({"path": "/workspace/report.pdf", "page": 7}),
    )
    assert _normalize_local_pdf_inspection_query(block, "Find another row") == block


def test_binary_artifacts_are_not_text_synthesis_targets():
    from src.agent_loop import _binary_artifact_path

    assert _binary_artifact_path("/workspace/frame.png")
    assert _binary_artifact_path("/workspace/clip.mp4")
    assert not _binary_artifact_path("/workspace/index.html")
    assert not _binary_artifact_path("/workspace/result.json")


def test_write_file_rejects_svg_markup_under_raster_extension(tmp_path):
    import asyncio
    import json

    from src.agent_tools.filesystem_tools import WriteFileTool
    from src.tool_execution import _active_workspace

    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(WriteFileTool().execute(json.dumps({
            "path": "/workspace/floorplan.png",
            "content": '<svg xmlns="http://www.w3.org/2000/svg"/>',
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 1
    assert result["artifact_format_error"] is True
    assert "inspect_media" in result["error"]
    assert not (tmp_path / "floorplan.png").exists()


def test_explicit_local_media_paths_are_detected_without_matching_documents():
    from src.agent_loop import (
        _explicit_local_media_files,
        _explicit_local_media_inputs,
        _native_local_media_inputs,
        _runtime_local_media_inputs,
    )

    assert _explicit_local_media_files(
        "Inspect /workspace/fixtures/video.mp4 and save /workspace/clip.mp4."
    ) == ["/workspace/fixtures/video.mp4", "/workspace/clip.mp4"]
    assert _explicit_local_media_files("Read /workspace/report.pdf") == [
        "/workspace/report.pdf"
    ]
    assert _explicit_local_media_files("Read /workspace/report.csv") == []
    assert _explicit_local_media_inputs(
        "Read a paper and save /workspace/chart.png"
    ) == []
    assert _explicit_local_media_inputs(
        "Watch /workspace/fixtures/video.mp4 and save /workspace/clip.mp4"
    ) == ["/workspace/fixtures/video.mp4"]
    runtime = {
        "surface": "odysseus-native",
        "input_files": [
            "/workspace/fixtures/implicit.mp4",
            "/workspace/fixtures/notes.txt",
        ],
    }
    assert _runtime_local_media_inputs(runtime) == [
        "/workspace/fixtures/implicit.mp4"
    ]
    assert _native_local_media_inputs("watch this video", runtime) == [
        "/workspace/fixtures/implicit.mp4"
    ]
    assert _runtime_local_media_inputs({
        "surface": "odysseus-native",
        "input_files": ["path=/workspace/fixtures/forwarded.mp4"],
    }) == ["/workspace/fixtures/forwarded.mp4"]


def test_current_market_question_keeps_web_available_after_media_identification():
    from src.agent_loop import _local_media_needs_web_lookup

    assert _local_media_needs_web_lookup("How much does this car sell for now?")
    assert _local_media_needs_web_lookup("现在这车卖多少钱？")
    assert _local_media_needs_web_lookup(
        "Scan the local PDF reference list, then verify which arXiv preprints "
        "have been officially accepted or published in formal conferences."
    )
    assert _local_media_needs_web_lookup(
        "Check the cited papers' publication status as of March 19, 2026."
    )
    assert _local_media_needs_web_lookup(
        "I identified the paper from this video; has its code been open-sourced?"
    )
    assert _local_media_needs_web_lookup(
        "Is the implementation publicly available in a GitHub repository?"
    )
    assert not _local_media_needs_web_lookup("Summarize this local training video")
    assert not _local_media_needs_web_lookup(
        "I'm currently editing a video and need help extracting Chinese subtitles."
    )
    assert not _local_media_needs_web_lookup(
        "I'm currently analyzing a local recording; save its transcript to a file."
    )
    assert not _local_media_needs_web_lookup(
        "Verify that the values extracted from this local PDF match its table."
    )


def test_compact_local_pdf_research_keeps_web_verification_tools():
    from src.agent_loop import _compact_native_route_tools

    selected = _compact_native_route_tools(
        {
            "pdf_extract", "inspect_media", "read_file", "write_file",
            "python", "web_search", "web_fetch",
        },
        "Scan /workspace/fixtures/paper.pdf and identify all arXiv preprints. "
        "Verify which papers were officially accepted or published in formal "
        "academic conferences, then save /workspace/publications.csv.",
        {"files"},
    )

    assert {"pdf_extract", "web_search", "web_fetch", "write_file"} <= selected


def test_local_media_browser_render_intent_is_narrow():
    from src.agent_loop import _local_media_needs_browser_render

    assert _local_media_needs_browser_render(
        "View /workspace/fixtures/image.png, recreate the webpage in HTML, "
        "then render the generated HTML as an image and save as /workspace/output.png."
    )
    assert not _local_media_needs_browser_render(
        "Inspect /workspace/fixtures/image.png and describe the layout."
    )
    assert not _local_media_needs_browser_render(
        "Inspect /workspace/fixtures/video.mp4 and export a still image."
    )


def test_artifact_recovery_keeps_browser_for_html_screenshot():
    from src.agent_loop import (
        _artifact_mutation_surface_for_missing,
        _artifact_recovery_messages,
        _browser_render_recovery_blocks,
    )

    assert "private_browser" in _artifact_mutation_surface_for_missing(
        ["/workspace/output.png"],
        local_media_derivation=True,
        browser_render=True,
    )
    assert "private_browser" not in _artifact_mutation_surface_for_missing(
        ["/workspace/output.png"],
        local_media_derivation=True,
    )
    recovery = _artifact_recovery_messages(
        [{
            "role": "user",
            "content": (
                "Recreate the webpage in HTML, then render the generated HTML "
                "as an image and save as /workspace/output.png."
            ),
        }],
        [{
            "tool": "write_file",
            "command": "/workspace/task_browser.html\n<html></html>",
            "output": "wrote file",
            "exit_code": 0,
        }],
        ["/workspace/output.png"],
    )
    recovery_text = "\n".join(str(message.get("content") or "") for message in recovery)
    assert "private_browser" in recovery_text
    assert "file:///workspace" in recovery_text
    blocks = _browser_render_recovery_blocks(
        "Recreate the webpage in HTML, then render the generated HTML "
        "as an image and save as /workspace/output.png.",
        ["/workspace/output.png"],
        {"private_browser"},
        set(),
        [{
            "tool": "write_file",
            "command": "/workspace/task_browser.html\n<html></html>",
            "output": "wrote file",
            "exit_code": 0,
        }],
    )
    assert blocks is not None
    assert [block.tool_type for block in blocks] == [
        "private_browser", "private_browser"
    ]
    assert '"action": "screenshot"' in blocks[1].content
    assert "/workspace/output.png" in blocks[1].content


def test_media_observation_is_classified_as_read_only_during_recovery():
    from src.agent_loop import _workspace_inspection_tool_block
    from src.tool_types import ToolBlock

    assert _workspace_inspection_tool_block(ToolBlock(
        "inspect_media",
        '{"path":"/workspace/fixtures/video.mp4","frames":4}',
    ))
    assert _workspace_inspection_tool_block(ToolBlock(
        "transcribe_media",
        '{"path":"/workspace/fixtures/video.mp4"}',
    ))


def test_private_browser_duplicate_guard_only_covers_read_only_observations():
    from src.agent_loop import (
        _read_only_repeat_limit,
        _workspace_inspection_tool_block,
    )
    from src.tool_types import ToolBlock

    opened = ToolBlock("private_browser", '{"action":"open","url":"https://example.com"}')
    snapshot = ToolBlock("private_browser", '{"action":"snapshot"}')
    clicked = ToolBlock("private_browser", '{"action":"click","ref":"e2"}')

    assert _workspace_inspection_tool_block(opened)
    assert _workspace_inspection_tool_block(snapshot)
    assert not _workspace_inspection_tool_block(clicked)
    assert _read_only_repeat_limit(opened) == 2
    assert _read_only_repeat_limit(snapshot) == 3
    assert _read_only_repeat_limit(clicked) == 0


def test_private_browser_duplicate_guard_is_bounded_and_state_sensitive():
    from src.agent_loop import _redundant_read_should_block
    from src.tool_types import ToolBlock

    snapshot = ToolBlock("private_browser", '{"action":"snapshot"}')
    history = {"count": 3, "mutation_epoch": 2, "browser_epoch": 4}
    assert _redundant_read_should_block(history, snapshot, 2, 4)
    assert not _redundant_read_should_block({**history, "count": 2}, snapshot, 2, 4)
    assert not _redundant_read_should_block(history, snapshot, 2, 5)


def test_local_media_floor_preserves_native_vision_and_prunes_web_tools():
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    assert '"inspect_media", "transcribe_media", "bash", "read_file", "ls"' in source
    assert "创建|生成|保存|写入|写在|输出|放进|制作|截取|剪辑|拼接|导出" in source
    assert "_native_local_media_inputs(_last_user, client_runtime_context)" in source
    assert "_relevant_tools.difference_update(_irrelevant_web_tools)" in source


def test_compact_native_route_keeps_local_media_off_the_web_path():
    from src.agent_loop import _compact_native_route_tools

    selected = _compact_native_route_tools(
        {"inspect_media", "bash", "read_file", "ls", "web_search", "web_fetch"},
        "容器里有以下文件：/workspace/fixtures/video.mp4",
        {"web"},
    )
    assert selected == {
        "inspect_media", "bash", "read_file", "ls", "get_workspace",
    }


def test_compact_local_pdf_artifact_route_avoids_shell_pdf_probe():
    from src.agent_loop import _compact_native_route_tools

    selected = _compact_native_route_tools(
        {
            "inspect_media", "pdf_extract", "bash", "python", "write_file",
            "read_file", "ls", "web_search",
        },
        "Read /workspace/fixtures/paper.pdf and generate /workspace/chart.png.",
        {"web", "documents"},
    )
    assert "pdf_extract" in selected
    assert "inspect_media" in selected
    assert "python" in selected
    assert "write_file" in selected
    assert "bash" not in selected


def test_compact_native_route_keeps_browser_for_local_html_render():
    from src.agent_loop import _compact_native_route_tools

    selected = _compact_native_route_tools(
        {
            "inspect_media", "bash", "read_file", "ls", "private_browser",
            "web_search", "web_fetch", "pdf_extract",
        },
        "View /workspace/fixtures/image.png, recreate it in HTML, then render "
        "the generated HTML as an image and save /workspace/output.png.",
        {"web"},
    )
    assert "inspect_media" in selected
    assert "private_browser" in selected
    assert "web_search" not in selected
    assert "web_fetch" not in selected
    assert "pdf_extract" not in selected


def test_compact_native_artifact_bundle_prunes_project_management_tools():
    from src.agent_loop import _compact_native_artifact_tools

    selected = _compact_native_artifact_tools(
        {
            "apply_patch", "bash", "edit_file", "get_workspace", "glob",
            "grep", "inspect_media", "ls", "manage_bg_jobs",
            "private_browser", "python", "read_file", "todowrite",
            "transcribe_media", "write_file",
        },
        text=(
            "View /workspace/fixtures/reference.png and generate "
            "/workspace/output.html. Preview and adjust it."
        ),
        artifacts=["/workspace/output.html"],
        media_inputs=["/workspace/fixtures/reference.png"],
    )

    assert selected == {
        "inspect_media", "ls", "private_browser", "python", "read_file",
        "write_file",
    }


def test_compact_png_artifact_keeps_browser_for_html_render_contract():
    from src.agent_loop import _compact_native_artifact_tools

    selected = _compact_native_artifact_tools(
        {"inspect_media", "private_browser", "python", "read_file", "write_file"},
        text=(
            "Recreate the reference as HTML, render the generated HTML as an "
            "image, and save /workspace/output.png."
        ),
        artifacts=["/workspace/output.png"],
        media_inputs=["/workspace/fixtures/reference.png"],
    )

    assert "private_browser" in selected


def test_compact_native_artifact_bundle_preserves_script_execution():
    from src.agent_loop import _compact_native_artifact_tools

    selected = _compact_native_artifact_tools(
        {"bash", "manage_bg_jobs", "python", "read_file", "write_file"},
        text="Run /workspace/build.py and create /workspace/output.png",
        artifacts=["/workspace/output.png"],
        media_inputs=[],
    )

    assert {"bash", "manage_bg_jobs", "python", "read_file", "write_file"} <= selected


def test_compact_native_artifact_bundle_preserves_explicit_edit_tools():
    from src.agent_loop import _compact_native_artifact_tools

    selected = _compact_native_artifact_tools(
        {"apply_patch", "edit_file", "glob", "grep", "read_file", "write_file"},
        text="Fix /workspace/output.html and update the existing animation",
        artifacts=["/workspace/output.html"],
        media_inputs=[],
    )

    assert {"apply_patch", "edit_file", "glob", "grep"} <= selected


def test_compact_native_route_keeps_writers_for_chinese_web_artifact():
    from src.agent_loop import _compact_native_route_tools

    selected = _compact_native_route_tools(
        {"inspect_media", "python", "write_file", "read_file", "ls", "web_search"},
        "请制作网页 /workspace/index.html 并保存。视频在 /workspace/fixtures/video.mp4",
        {"web"},
    )
    assert {"inspect_media", "python", "write_file", "read_file", "ls"} <= selected
    assert "web_search" not in selected


def test_compact_native_route_keeps_native_pdf_extractor_for_local_pdf():
    from src.agent_loop import _compact_native_route_tools

    selected = _compact_native_route_tools(
        {"inspect_media", "pdf_extract", "web_fetch", "read_file", "ls"},
        "Read the chart in /workspace/paper.pdf",
        {"web", "documents"},
    )
    assert "inspect_media" in selected
    assert "pdf_extract" in selected
    assert "web_fetch" not in selected


def test_pdf_extract_accepts_task_local_pdf_path(tmp_path):
    import asyncio
    from unittest.mock import patch

    from src.agent_tools.web_tools import PdfExtractTool
    from src.tool_execution import _active_workspace

    local_pdf = tmp_path / "fixtures" / "paper.pdf"
    local_pdf.parent.mkdir()
    local_pdf.write_bytes(b"%PDF-local-test")
    token = _active_workspace.set(str(tmp_path))
    try:
        with patch.object(
            PdfExtractTool,
            "_positioned_table_evidence",
            return_value="Table 3 local evidence",
        ):
            result = asyncio.run(PdfExtractTool().execute(
                '{"url":"/workspace/fixtures/paper.pdf","query":"Table 3"}',
                {},
            ))
    finally:
        _active_workspace.reset(token)
    assert result == {
        "output": "Source: /workspace/fixtures/paper.pdf\n\nTable 3 local evidence",
        "exit_code": 0,
    }


def test_pdf_extract_accepts_confined_physical_file_uri(tmp_path):
    import asyncio
    import json
    from unittest.mock import patch

    from src.agent_tools.web_tools import PdfExtractTool
    from src.tool_execution import _active_workspace

    local_pdf = tmp_path / "fixtures" / "report with spaces.pdf"
    local_pdf.parent.mkdir()
    local_pdf.write_bytes(b"%PDF-local-test")
    token = _active_workspace.set(str(tmp_path))
    try:
        with patch.object(
            PdfExtractTool,
            "_positioned_table_evidence",
            return_value="Focused local evidence",
        ):
            result = asyncio.run(PdfExtractTool().execute(json.dumps({
                "url": local_pdf.as_uri(),
                "query": "focused evidence",
            }), {}))
    finally:
        _active_workspace.reset(token)

    assert result == {
        "output": (
            "Source: /workspace/fixtures/report with spaces.pdf\n\n"
            "Focused local evidence"
        ),
        "exit_code": 0,
    }
    assert PdfExtractTool._local_pdf_path("file:///etc/passwd") is None


def test_compact_native_route_preserves_pdf_extractor_for_online_pdf():
    from src.agent_loop import _compact_native_route_tools

    selected = _compact_native_route_tools(
        {"pdf_extract", "web_fetch", "web_search", "write_file", "python"},
        "Read https://arxiv.org/pdf/2406.04264 and save /workspace/result.csv",
        {"web", "files"},
    )
    assert "pdf_extract" in selected


def test_compact_native_route_discovers_named_paper_without_url():
    from src.agent_loop import _compact_native_route_tools

    selected = _compact_native_route_tools(
        {"bash", "web_search", "web_fetch", "pdf_extract", "write_file", "python"},
        'From the paper "How Far Are We to GPT-4V?" extract Table 2 and save '
        "/workspace/result.csv",
        {"files"},
    )
    assert {"web_search", "web_fetch", "pdf_extract", "write_file", "python"} <= selected
    assert "bash" not in selected


def test_per_model_route_reapplies_local_media_after_contextual_web_clamps():
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    route_start = source.index("    def _route_relevant_tools(candidate_model: str):")
    route_end = source.index("\n    (\n        _ody_qwen_finetune_model", route_start)
    route_source = source[route_start:route_end]
    assert route_source.rfind(
        "_native_local_media_inputs(_last_user, client_runtime_context)"
    ) > route_source.rfind(
        "_contextual_public_web_followup"
    )
    assert 'route_tools.add("private_browser")' in route_source
    assert 'route_tools.difference_update(' in route_source


def test_compact_request_reapplies_runner_declared_media_after_compaction():
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    compact_start = source.index('        if tool_surface == "compact":')
    prompt_start = source.index("        prompt_route_tools =", compact_start)
    compact_source = source[compact_start:prompt_start]
    assert "_compact_native_route_tools(" in compact_source
    assert compact_source.rfind("_native_local_media_inputs(") > compact_source.rfind(
        "_compact_native_route_tools("
    )
    assert '"inspect_media", "transcribe_media"' in compact_source


def test_native_runtime_context_can_cap_endpoint_context_budget():
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    assert '(client_runtime_context or {}).get("model_context_window")' in source
    assert "min(candidate_context, runtime_context_window)" in source


def test_native_local_media_prunes_host_bridge_and_cookbook_tools():
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    assert 'client_runtime_context.get("surface") or "") == "odysseus-native"' in source
    assert '_base_relevant_tools.discard("host_shell")' in source
    assert '_DOMAIN_TOOL_MAP.get("cookbook", set())' in source


def test_terminal_artifact_observation_budget_forces_mutation_recovery():
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    assert "_artifact_observation_rounds >= 6" in source
    assert '"reason": "artifact_observation_budget"' in source
    assert "_artifact_mutation_only_mode = True" in source
    assert '"python",' in source
    assert "code must generate a chart/image" in source
    assert source.count("and not _artifact_mutation_only_mode") >= 2


def test_missing_text_and_binary_artifacts_keep_python_for_synthesis():
    from src.agent_loop import _artifact_mutation_surface_for_missing

    assert "python" in _artifact_mutation_surface_for_missing(
        ["/workspace/rows.csv", "/workspace/chart.png"]
    )


def test_direct_source_media_extraction_is_narrow_and_bilingual():
    from src.agent_loop import (
        _direct_source_media_extraction_requested,
        _visible_media_caption_requested,
    )

    assert _direct_source_media_extraction_requested(
        "Save a frame screenshot for each athlete as /workspace/student1.png.",
        ["/workspace/student1.png"],
    )
    assert _direct_source_media_extraction_requested(
        "把每位同学对应的帧截图保存为 /workspace/student1.png。",
        ["/workspace/student1.png"],
    )
    assert not _direct_source_media_extraction_requested(
        "Generate a chart from the video data as /workspace/chart.png.",
        ["/workspace/chart.png"],
    )
    assert not _direct_source_media_extraction_requested(
        "Extract the chase clip, make video and audio 2x speed, and save chase_2x.mp4.",
        ["/workspace/chase_2x.mp4"],
    )
    assert not _direct_source_media_extraction_requested(
        "提取追逐片段，将视频和音频都加速制作成2倍速版本。",
        ["/workspace/chase_2x.mp4"],
    )
    assert not _visible_media_caption_requested(
        "Save a frame screenshot for each athlete."
    )
    assert _visible_media_caption_requested(
        "Add a visible caption to each saved screenshot."
    )
    assert _visible_media_caption_requested("给截图添加文字标签。")


def test_visual_text_extraction_is_distinct_from_speech_transcription():
    from src.agent_loop import _visual_text_extraction_requested

    assert _visual_text_extraction_requested(
        "Extract all flashing English words shown on screen from 0:25 to 0:30."
    )
    assert _visual_text_extraction_requested(
        "Run OCR on that same image again, returning only numbers."
    )
    assert _visual_text_extraction_requested("识别视频画面中的文字。")
    assert not _visual_text_extraction_requested(
        "Transcribe everything the speaker says from 0:25 to 0:30."
    )
    assert not _visual_text_extraction_requested(
        "Extract subtitles from this recording."
    )


def test_local_media_routes_select_dedicated_ocr_for_visual_text():
    import re

    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()

    assert source.count(
        "_ocr_requested = _visual_text_extraction_requested(_last_user)"
    ) == 3
    assert len(re.findall(r'\{"extract_text"\}\s*\n\s*if _ocr_requested', source)) == 3


def test_workspace_paths_split_on_chinese_list_punctuation():
    from src.agent_loop import _explicit_workspace_files

    assert _explicit_workspace_files(
        "保存为 /workspace/student1.png、/workspace/student2.png；然后完成。"
    ) == ["/workspace/student1.png", "/workspace/student2.png"]


def test_direct_source_media_extraction_recovery_only_exports_source_pixels():
    from src.agent_loop import (
        _artifact_mutation_surface_for_missing,
        _artifact_recovery_messages,
    )

    missing = ["/workspace/student1.png", "/workspace/student2.png"]
    surface = _artifact_mutation_surface_for_missing(
        missing,
        local_media_derivation=True,
        source_media_extraction=True,
    )
    assert surface == {"inspect_media"}

    recovered = _artifact_recovery_messages(
        [{
            "role": "user",
            "content": (
                "Watch /workspace/fixtures/video.mp4 and save frame screenshots "
                "as /workspace/student1.png and /workspace/student2.png."
            ),
        }],
        [],
        missing,
    )
    text = "\n".join(str(message.get("content") or "") for message in recovered)
    assert "native source-media export call" in text
    assert "do not synthesize" in text
    assert "Python synthesis call" not in text


def test_source_media_recovery_keeps_writer_for_text_companion_artifact():
    from src.agent_loop import (
        _artifact_mutation_surface_for_missing,
        _source_media_text_companion_recovery_tools,
    )

    surface = _artifact_mutation_surface_for_missing(
        ["/workspace/timestamp.txt", "/workspace/cropped_frame.png"],
        local_media_derivation=True,
        source_media_extraction=True,
    )

    assert surface == {"inspect_media", "write_file"}
    assert _source_media_text_companion_recovery_tools(
        ["/workspace/timestamp.txt", "/workspace/cropped_frame.png"],
        recovery_active=True,
    ) == {"write_file"}
    assert _source_media_text_companion_recovery_tools(
        ["/workspace/cropped_frame.png"],
        recovery_active=True,
    ) == set()
    assert _source_media_text_companion_recovery_tools(
        ["/workspace/timestamp.txt"],
        recovery_active=False,
    ) == set()


def test_source_media_schema_boundary_keeps_text_companion_writer():
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    boundary = source.index(
        "# Final provenance boundary: route construction, fallbacks, and"
    )
    materialization = source.index(
        "all_tool_schemas = _tool_schemas_for_route(_active_route_state)",
        boundary,
    )
    segment = source[boundary:materialization]
    assert "_workspace_artifacts" in segment
    assert "recovery_active=True" in segment
    assert "_source_companion_tools" in segment


def test_artifact_recovery_does_not_shadow_recovery_message_builder():
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    assert "_recovery_messages = _artifact_recovery_messages(" not in source
    assert "messages = _artifact_recovery_messages(" in source
    assert "_artifact_recovery_message_list = _artifact_recovery_messages(" in source


def test_source_media_recovery_retains_bounded_candidate_working_notes():
    from src.agent_loop import _artifact_recovery_messages

    recovered = _artifact_recovery_messages(
        [
            {
                "role": "user",
                "content": (
                    "Watch /workspace/fixtures/video.mp4 and save a frame screenshot "
                    "as /workspace/student1.png."
                ),
            },
            {
                "role": "assistant",
                "content": (
                    "Visual candidate: the blue-shirt athlete's chin appears above "
                    "the bar near 00:00:52.050; verify that timestamp before export."
                ),
            },
        ],
        [],
        ["/workspace/student1.png"],
    )

    text = "\n".join(str(message.get("content") or "") for message in recovered)
    assert "00:00:52.050" in text
    assert "candidate hypotheses" in text
    assert "not independent pixel evidence" in text


def test_direct_source_media_extraction_filter_is_reapplied_per_model_route():
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    route_start = source.index("    def _route_relevant_tools(candidate_model: str):")
    route_end = source.index("\n    (\n        _ody_qwen_finetune_model,", route_start)
    route_source = source[route_start:route_end]
    assert "if _source_media_extraction_requested:" in route_source
    for tool in ("python", "bash", "generate_image", "edit_image"):
        assert f'"{tool}"' in route_source

    schema_boundary = source.index(
        "# Final provenance boundary: route construction, fallbacks, and"
    )
    schema_materialization = source.index(
        "all_tool_schemas = _tool_schemas_for_route(_active_route_state)",
        schema_boundary,
    )
    assert schema_boundary < schema_materialization


def test_mixed_local_pdf_artifacts_keep_native_media_inspection():
    from src.agent_loop import _artifact_mutation_surface_for_missing

    assert "inspect_media" in _artifact_mutation_surface_for_missing(
        ["/workspace/rows.csv", "/workspace/chart.png"],
        local_media_derivation=True,
    )


def test_local_audio_video_artifact_recovery_keeps_ffmpeg_shell_surface():
    from src.agent_loop import _artifact_mutation_surface_for_missing

    surface = _artifact_mutation_surface_for_missing(
        ["/workspace/merged.mp4"],
        local_media_derivation=True,
    )

    assert "bash" in surface
    assert "inspect_media" in surface


def test_non_media_binary_recovery_does_not_gain_shell_surface():
    from src.agent_loop import _artifact_mutation_surface_for_missing

    assert "bash" not in _artifact_mutation_surface_for_missing(
        ["/workspace/chart.png"],
        local_media_derivation=True,
    )


def test_text_only_local_media_recovery_requires_mutation_not_reinspection():
    from src.agent_loop import _artifact_mutation_surface_for_missing

    surface = _artifact_mutation_surface_for_missing(
        ["/workspace/output.txt"],
        local_media_derivation=True,
    )
    assert "write_file" in surface
    assert "inspect_media" not in surface
    assert "transcribe_media" not in surface


def test_artifact_recovery_preserves_media_read_and_browser_capabilities():
    from src.agent_loop import _artifact_recovery_capability_floor

    assert _artifact_recovery_capability_floor(
        local_media_derivation=True,
        browser_render=True,
    ) == {"inspect_media", "read_file", "private_browser"}


def test_artifact_recovery_capability_floor_is_not_enabled_for_plain_text():
    from src.agent_loop import _artifact_recovery_capability_floor

    assert _artifact_recovery_capability_floor() == set()


def test_post_redirect_local_pdf_inspection_is_bounded():
    from src.agent_loop import _bounded_local_pdf_inspection_blocks
    from src.tool_types import ToolBlock

    blocks = [ToolBlock(
        "inspect_media",
        '{"path":"/workspace/paper.pdf","page":7}',
    )]
    allowed, used = _bounded_local_pdf_inspection_blocks(
        blocks,
        local_pdf_turn=True,
        already_used=0,
    )
    assert allowed == blocks
    assert used == 1
    allowed, used = _bounded_local_pdf_inspection_blocks(
        blocks,
        local_pdf_turn=True,
        already_used=2,
    )
    assert allowed == []
    assert used == 0
    allowed, used = _bounded_local_pdf_inspection_blocks(
        [*blocks, ToolBlock("read_file", "/workspace/paper.pdf")],
        local_pdf_turn=True,
        already_used=0,
    )
    assert allowed == []
    assert used == 0


def test_artifact_recovery_treats_native_web_pdf_tools_as_inspection():
    from src.agent_loop import _workspace_inspection_tool_block
    from src.tool_types import ToolBlock

    for tool_name in ("web_search", "web_fetch", "pdf_extract"):
        assert _workspace_inspection_tool_block(ToolBlock(tool_name, "query"))


def test_artifact_recovery_keeps_acquisition_until_source_evidence_exists():
    from src.agent_loop import _artifact_source_evidence_ready

    assert not _artifact_source_evidence_ready(
        [{"tool": "web_search", "exit_code": 0, "output": "generic result"}],
        'Read the "Attention Is All You Need" paper and extract Table 2.',
    )
    assert _artifact_source_evidence_ready(
        [{"tool": "pdf_extract", "exit_code": 0, "output": "Table 2 ..."}],
        'Read the "Attention Is All You Need" paper and extract Table 2.',
    )


def test_local_pdf_is_not_complete_evidence_for_external_publication_check():
    from src.agent_loop import (
        _artifact_acquisition_recovery_messages,
        _artifact_source_evidence_ready,
    )

    request = (
        "Scan /workspace/fixtures/paper.pdf and verify which cited arXiv "
        "preprints were officially accepted or published in conferences."
    )
    events = [{
        "tool": "pdf_extract",
        "exit_code": 0,
        "output": "References: Example Paper, arXiv preprint arXiv:2501.00001",
    }]

    assert not _artifact_source_evidence_ready(events, request)
    recovered = _artifact_acquisition_recovery_messages(
        [{"role": "user", "content": request}],
        events,
        ["/workspace/publications.csv"],
        user_text=request,
    )
    text = "\n".join(str(message.get("content") or "") for message in recovered)
    assert "external verification" in text
    assert "followed by `web_fetch`" in text
    assert "Do not call `pdf_extract` again" in text


def test_pdf_bibliography_evidence_preserves_columns_and_filters_arxiv_entries():
    from src.agent_tools.web_tools import PdfExtractTool

    pages = [
        (1, "Introduction\nA citation-heavy body page.", "Related work"),
        (
            2,
            "Acknowledgments\nThanks.\nReferences\n"
            "[1] Alpha. First preprint. arXiv preprint, abs/2401.00001, 2024.\n"
            "[2] Beta. Published in ICML, 2024.",
            "[3] Gamma. Another work. ArXiv preprint, abs/2402.00002, 2024.",
        ),
        (
            3,
            "[4] Delta. Journal article, 2023.",
            "[5] Epsilon. Late matching work. arXiv:2403.00003, 2024.",
        ),
    ]

    evidence = PdfExtractTool._bibliography_evidence(
        pages, "Find all references listed as arXiv preprints"
    )

    assert "3 arXiv/preprint entries" in evidence
    assert "[1] Alpha" in evidence
    assert "[3] Gamma" in evidence
    assert "[5] Epsilon" in evidence
    assert "[2] Beta" not in evidence
    assert "Acknowledgments" not in evidence


def test_pdf_bibliography_evidence_does_not_override_unrelated_queries():
    from src.agent_tools.web_tools import PdfExtractTool

    pages = [(7, "References\n[1] Alpha. arXiv preprint.", "")]

    assert PdfExtractTool._bibliography_evidence(pages, "Extract Table 3") == ""


def test_pdf_bibliography_evidence_honors_requested_reference_range():
    from src.agent_tools.web_tools import PdfExtractTool

    pages = [(
        9,
        "References\n[29] Alpha. Earlier work.\n[30] Beta. Earlier work.\n"
        "[31] Gamma. Target work.\n[32] Delta. Target work.",
        "[33] Epsilon. Target work.\n[34] Zeta. Later work.",
    )]

    evidence = PdfExtractTool._bibliography_evidence(
        pages, "Extract references [31] through [33]"
    )

    assert "reference entries 31-33" in evidence
    assert "[31] Gamma" in evidence
    assert "[32] Delta" in evidence
    assert "[33] Epsilon" in evidence
    assert "[30] Beta" not in evidence
    assert "[34] Zeta" not in evidence


def test_artifact_recovery_hands_off_existing_plot_script_to_python():
    from src.agent_loop import _artifact_recovery_messages

    recovered = _artifact_recovery_messages(
        [{
            "role": "user",
            "content": "Create /workspace/chart.png from the extracted table.",
        }],
        [{
            "tool": "write_file",
            "command": (
                "/workspace/chart.py\n"
                "import matplotlib.pyplot as plt\n"
                "plt.savefig('/workspace/chart.png')"
            ),
            "output": "wrote file",
            "exit_code": 0,
        }],
        ["/workspace/chart.png"],
    )
    text = "\n".join(str(message.get("content") or "") for message in recovered)
    assert "Execute it now with the native `python` tool" in text
    assert "/workspace/chart.py" in text
    assert "Do not rewrite the script" in text


def test_artifact_body_handoff_executes_existing_generator_first():
    from src.agent_loop import _artifact_generator_execution_block

    block = _artifact_generator_execution_block(
        [{
            "tool": "write_file",
            "command": (
                "/workspace/build_report.py\n"
                "import pandas as pd\n"
                "pd.DataFrame({'value': [1]}).to_csv('/workspace/report.csv')"
            ),
            "output": "wrote file",
            "exit_code": 0,
        }],
        ["/workspace/report.csv"],
        {"python", "write_file"},
    )

    assert block is not None
    assert block.tool_type == "python"
    assert "runpy.run_path" in block.content
    assert "/workspace/build_report.py" in block.content


def test_artifact_body_handoff_does_not_run_unrelated_or_failed_script():
    from src.agent_loop import _artifact_generator_execution_block

    unrelated = [{
        "tool": "write_file",
        "command": "/workspace/helper.py\nprint('unrelated')",
        "output": "wrote file",
        "exit_code": 0,
    }]
    failed = [{
        "tool": "write_file",
        "command": (
            "/workspace/build_report.py\n"
            "open('/workspace/report.csv', 'w').write('value\\n1')"
        ),
        "output": "failed",
        "exit_code": 1,
    }]

    assert _artifact_generator_execution_block(
        unrelated, ["/workspace/report.csv"], {"python"}
    ) is None
    assert _artifact_generator_execution_block(
        failed, ["/workspace/report.csv"], {"python"}
    ) is None
    assert _artifact_generator_execution_block(
        unrelated, ["/workspace/report.csv"], {"write_file"}
    ) is None


def test_artifact_body_handoff_does_not_retry_generator_until_repaired():
    import json

    from src.agent_loop import _artifact_generator_execution_block

    write = {
        "tool": "write_file",
        "command": (
            "/workspace/build_report.py\n"
            "open('/workspace/report.csv', 'w').write('value\\n1')"
        ),
        "output": "wrote file",
        "exit_code": 0,
    }
    failed_run = {
        "tool": "python",
        "command": (
            "import runpy\n"
            "runpy.run_path(\"/workspace/build_report.py\", run_name='__main__')"
        ),
        "output": "SyntaxError: invalid syntax",
        "exit_code": 1,
    }
    assert _artifact_generator_execution_block(
        [write, failed_run], ["/workspace/report.csv"], {"python"}
    ) is None

    repaired = {
        "tool": "edit_file",
        "command": json.dumps({
            "path": "/workspace/build_report.py",
            "old_string": "broken",
            "new_string": "fixed",
        }),
        "output": "edited file",
        "exit_code": 0,
    }
    assert _artifact_generator_execution_block(
        [write, failed_run, repaired], ["/workspace/report.csv"], {"python"}
    ) is not None


def test_failed_generator_allows_one_repair_read_but_not_a_read_loop():
    from src.agent_loop import _failed_artifact_generator_repair_reads
    from src.tool_types import ToolBlock

    failed_run = {
        "tool": "python",
        "command": "runpy.run_path('/workspace/build_report.py')",
        "output": "SyntaxError: invalid syntax",
        "exit_code": 1,
    }
    requested = [ToolBlock("read_file", "/workspace/build_report.py")]

    assert _failed_artifact_generator_repair_reads(requested, [failed_run]) == requested

    prior_read = {
        "tool": "read_file",
        "command": "/workspace/build_report.py",
        "output": "print('broken')",
        "exit_code": 0,
    }
    assert _failed_artifact_generator_repair_reads(
        requested, [failed_run, prior_read]
    ) == []


def test_artifact_recovery_directs_python_for_missing_chart_without_script():
    from src.agent_loop import _artifact_recovery_messages

    recovered = _artifact_recovery_messages(
        [{"role": "user", "content": "Generate /workspace/chart.png."}],
        [],
        ["/workspace/chart.png"],
    )
    text = "\n".join(str(message.get("content") or "") for message in recovered)
    assert "Python synthesis call" in text
    assert "Do not rewrite completed CSV/text files" in text


def test_transformed_media_recovery_directs_shell_mutation_not_source_export():
    from src.agent_loop import _artifact_recovery_messages

    recovered = _artifact_recovery_messages(
        [{
            "role": "user",
            "content": (
                "Read /workspace/fixtures/video.mp4, extract the chase clip, "
                "make video and audio 2x speed, and save /workspace/chase_2x.mp4."
            ),
        }],
        [],
        ["/workspace/chase_2x.mp4"],
    )
    text = "\n".join(str(message.get("content") or "") for message in recovered)
    assert "Bash" in text
    assert "ffmpeg" in text
    assert "audio/video transformation" in text
    assert "Python synthesis call" not in text
    assert "native source-media export call" not in text


def test_ffmpeg_media_transform_is_classified_as_workspace_mutation():
    from src.agent_evidence import command_has_mutation_effect

    assert command_has_mutation_effect(
        "ffmpeg -i /workspace/fixtures/video.mp4 -filter:v setpts=0.5*PTS "
        "-filter:a atempo=2 /workspace/chase_2x.mp4"
    )
    assert command_has_mutation_effect(
        "sox /workspace/fixtures/audio.wav /workspace/audio_2x.wav tempo 2"
    )


def test_abstract_landing_page_is_not_detail_source_evidence():
    from src.agent_loop import _artifact_source_evidence_ready

    assert not _artifact_source_evidence_ready(
        [{
            "tool": "web_fetch",
            "output": (
                "Attention Is All You Need. We propose a new simple network "
                "architecture based solely on attention mechanisms, dispensing "
                "with recurrence and convolutions. The paper discusses training "
                "costs and English-to-German translation. "
            ) * 20,
            "exit_code": 0,
        }],
        "Extract Table 2 training FLOPs and costs from the Attention Is All You Need paper",
    )


def test_fetched_detail_page_is_source_evidence():
    from src.agent_loop import _artifact_source_evidence_ready

    assert _artifact_source_evidence_ready(
        [{
            "tool": "web_fetch",
            "output": (
                "Attention Is All You Need. Table 2 reports training cost "
                "and FLOPs for the EN-DE translation models. "
            ) * 20,
            "exit_code": 0,
        }],
        "Extract Table 2 training FLOPs and costs from the Attention Is All You Need paper",
    )


def test_artifact_recovery_redirects_wrong_http_and_partial_mutations():
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    assert '"artifact_wrong_tool_http"' in source
    assert '"artifact_native_acquisition_required"' in source
    assert '"reason": "partial_artifact_mutation"' in source
    assert "ad-hoc HTTP" in source
    wrong_http_start = source.index('"artifact_wrong_tool_http"')
    wrong_http_detector_start = source.rfind(
        'and "ad-hoc HTTP" in str(',
        0,
        wrong_http_start,
    )
    wrong_http_detector = source[wrong_http_detector_start:wrong_http_start]
    assert 'get("exit_code") not in (None, 0)' not in wrong_http_detector
    assert "partial artifact mutation left required artifacts missing" in source
    assert "For `.png` chart artifacts" in source


def test_answer_only_local_media_has_bounded_inspection_budget():
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    assert "_completed_media_inspections >= 8" in source
    assert "round_num >= _round_limit - 1" in source
    assert "_media_inspection_budget_exhausted" in source
    assert "local-media inspection budget exhausted" in source
    assert "answer the user's question now from the evidence" in source
    assert "_media_artifacts_complete" in source
    assert "do not refine it again" in source


def test_force_answer_discards_native_tool_plan_before_grace_synthesis():
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    force_answer_start = source.index("        if _force_answer:")
    force_answer_end = source.index("        # A single giant SVG", force_answer_start)
    force_answer_block = source[force_answer_start:force_answer_end]
    assert "if native_tool_calls:" in force_answer_block
    assert "discarding unfinished tool-plan text before synthesis" in force_answer_block
    assert 'round_response = ""' in force_answer_block
    assert "native_tool_calls = []" in force_answer_block


def test_local_media_is_exempt_from_pure_web_schema_and_round_clamps():
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    pure_web_start = source.index("    _local_media_turn = bool(")
    pure_web_end = source.index("\n    if (\n        _pure_web_turn", pure_web_start)
    assert "and not _local_media_turn" in source[pure_web_start:pure_web_end]
    assert source.count("if _pure_web_turn:") == 2
    assert 'if _pure_web_turn and tool_surface != "full":' in source


def test_empty_local_media_round_nudges_export_instead_of_ending():
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    assert "or _local_media_turn" in source
    assert 'requested output_path, and timestamp_path when requested' in source


def test_local_media_answer_requires_real_source_evidence():
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    assert '"type": "source_evidence_required"' in source
    assert '"reason": "local_media_not_observed"' in source
    assert "blocked local-media answer without source evidence" in source
    assert "Do not infer source contents from" in source


def test_local_media_gate_allows_only_bounded_workspace_discovery():
    from src.agent_loop import _local_media_discovery_call_allowed

    assert _local_media_discovery_call_allowed("ls", "/workspace/fixtures")
    assert _local_media_discovery_call_allowed("glob", "*.mp4")
    assert _local_media_discovery_call_allowed("get_workspace", "")
    assert _local_media_discovery_call_allowed(
        "bash", "#!bg\nls -la /workspace/fixtures/video.webm"
    )
    assert _local_media_discovery_call_allowed("bash", "stat /workspace/fixtures/video.webm")
    assert not _local_media_discovery_call_allowed(
        "bash", "ffmpeg -i /workspace/fixtures/video.webm"
    )
    assert not _local_media_discovery_call_allowed("bash", "ls /workspace | cat")
    assert not _local_media_discovery_call_allowed("python", "os.listdir('/workspace')")


def test_local_media_gate_has_one_automatic_evidence_acquisition_path():
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    assert "_auto_local_media_evidence" in source
    assert "_local_media_evidence_block_count == 0" in source
    assert 'ToolBlock(\n                    "inspect_media"' in source
    assert "_local_media_files[0]" in source
    assert "and not _auto_local_media_evidence" in source
    assert "_local_media_evidence_required_block = False" in source
    assert "_local_media_evidence_required_block = True" in source
    assert "elif _allow_local_media_discovery:" in source


def test_combined_web_workspace_route_keeps_native_writers():
    source = (Path(__file__).parents[1] / "routes" / "chat_routes.py").read_text()
    assert "_web_workspace_output" in source
    assert "if not _web_workspace_output" in source
    assert "创建|生成|保存|写入|制作|截取|剪辑|拼接|导出" in source


def test_compact_tool_surface_does_not_expand_to_every_admin_schema():
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    assert 'and tool_surface != "compact"' in source


def test_web_fetch_focused_passages_keeps_matching_context():
    from src.agent_tools.web_tools import WebFetchTool

    text = "\n".join([*(f"noise {i}" for i in range(30)), "DocVQA 96.4 ChartQA 89.5", *(f"tail {i}" for i in range(30))])
    focused = WebFetchTool._focused_passages(text, "DocVQA, ChartQA", 2000)
    assert "DocVQA 96.4 ChartQA 89.5" in focused
    assert "noise 0" not in focused


def test_web_fetch_focused_passages_tokenizes_natural_query():
    from src.agent_tools.web_tools import WebFetchTool

    text = "intro\nDocVQA 96.4 ChartQA 89.5 TextVQA 83.5\nfooter"
    focused = WebFetchTool._focused_passages(
        text,
        "DocVQA ChartQA TextVQA benchmark table evaluation scores Qwen2.5-VL-72B",
        2000,
    )
    assert "DocVQA 96.4" in focused


def test_web_fetch_one_large_match_cannot_hide_later_exact_table():
    from src.agent_tools.web_tools import WebFetchTool

    broad = "Qwen " + ("noise 12.3 " * 2000)
    exact = "Qwen DocVQA 96.4 ChartQA 89.5 TextVQA 83.5"
    text = "\n".join([broad, *(f"gap {i}" for i in range(40)), exact])
    focused = WebFetchTool._focused_passages(
        text, "Qwen DocVQA ChartQA TextVQA", 12000
    )
    assert exact in focused


def test_web_fetch_windows_one_line_arxiv_html_around_exact_table_row():
    from src.agent_tools.web_tools import WebFetchTool

    text = (
        "abstract GPT-4o " + ("background prose " * 1200)
        + " LLaVA-Onevision | 83.5 | 56.4 | 3.75 | 46.7 | 58.4 | 58.0 | 5.09 "
        + " GPT-4o | 83.7 | 68.8 | 4.94 | 42.9 | 47.8 | 57.1 | 6.80"
    )
    focused = WebFetchTool._focused_passages(
        text, "GPT-4o LLaVA-Onevision TR AR NQA ER PQA AO AC", 12000
    )
    assert "LLaVA-Onevision | 83.5" in focused
    assert "GPT-4o | 83.7" in focused


def test_read_file_url_is_routed_to_native_web_fetch():
    from src.tool_schemas import function_call_to_tool_block

    block = function_call_to_tool_block(
        "read_file", '{"path":"https://arxiv.org/pdf/2502.13923"}'
    )
    assert block.tool_type == "web_fetch"
    assert "https://arxiv.org/pdf/2502.13923" in block.content


def test_local_html_web_fetch_is_routed_to_native_browser():
    import json

    from src.tool_schemas import function_call_to_tool_block

    block = function_call_to_tool_block(
        "web_fetch", '{"url":"file:///workspace/output.html"}'
    )

    assert block.tool_type == "private_browser"
    assert json.loads(block.content) == {
        "action": "open",
        "url": "file:///workspace/output.html",
    }


def test_workspace_alias_resolves_inside_active_workspace(tmp_path):
    from src.tool_execution import _resolve_tool_path_in_workspace

    assert _resolve_tool_path_in_workspace(str(tmp_path), "/workspace/result.csv") == str(tmp_path / "result.csv")


def test_combined_web_artifact_floor_hides_generic_shell():
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    assert '_relevant_tools.discard("bash")' in source
    assert "combined web artifact task prefers structured tools" in source
    assert "_named_online_document" in source
    assert '{"web_search", "web_fetch", "pdf_extract"}' in source


def test_source_acquisition_recovery_retains_artifact_mutation_floor():
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    assert '"python", "write_file", "read_file"' in source
    assert "set(_native_acquisition_tools)" in source
    assert "| _artifact_mutation_tools" in source


def test_failed_artifact_verifier_skips_readonly_followthrough():
    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    assert "_failed_artifact_verifier = any(" in source
    assert '"private_browser", "builtin_browser"' in source
    assert "2 if _failed_artifact_verifier else 0" in source
    assert "Source acquisition and artifact mutation" in source
    assert "_acquisition_mutation_floor" in source
    assert "_selected_acquisition_surface" in source


def test_pdf_extract_requires_focus_and_reuses_native_fetch():
    import asyncio
    from unittest.mock import AsyncMock, patch

    from src.agent_tools.web_tools import PdfExtractTool, WebFetchTool

    expected = {"output": "Source: test\nDocVQA 96.4", "exit_code": 0}
    with patch.object(WebFetchTool, "execute", new=AsyncMock(return_value=expected)) as fetch:
        result = asyncio.run(PdfExtractTool().execute(
            '{"url":"https://example.test/paper.pdf","query":"Qwen DocVQA"}',
            {},
        ))
    assert result["exit_code"] == 0
    assert "Align row values to the full column header" in result["output"]
    forwarded = fetch.await_args.args[0]
    assert '"full": true' in forwarded
    assert '"query": "Qwen DocVQA"' in forwarded


def test_pdf_extract_uses_official_arxiv_html_companion():
    import asyncio
    from unittest.mock import AsyncMock, patch

    from src.agent_tools.web_tools import PdfExtractTool, WebFetchTool

    assert PdfExtractTool._arxiv_html_url(
        "https://arxiv.org/pdf/2406.04264v3#page=13"
    ) == "https://arxiv.org/html/2406.04264v3"

    html = {
        "output": "LLaVA-Onevision | 83.5 | 56.4 | 3.75",
        "exit_code": 0,
    }
    with (
        patch.object(
            PdfExtractTool,
            "_positioned_table_evidence",
            return_value="GPT-4o | 83.7 | 68.8 | 4.94",
        ),
        patch.object(WebFetchTool, "execute", new=AsyncMock(return_value=html)) as fetch,
    ):
        result = asyncio.run(PdfExtractTool().execute(
            '{"url":"https://arxiv.org/pdf/2406.04264",'
            '"query":"GPT-4o LLaVA-Onevision TR AR NQA"}',
            {},
        ))

    assert result["exit_code"] == 0
    assert "LLaVA-Onevision | 83.5" in result["output"]
    assert "GPT-4o | 83.7" in result["output"]
    assert "skip those columns" in result["output"]
    assert fetch.await_count == 2
    focused_queries = {
        __import__("json").loads(call.args[0])["query"]
        for call in fetch.await_args_list
    }
    assert focused_queries == {"GPT-4o", "LLaVA-Onevision"}
    forwarded = fetch.await_args.args[0]
    assert "https://arxiv.org/html/2406.04264" in forwarded


def test_pdf_extract_keeps_positioned_table_before_long_html():
    import asyncio
    from unittest.mock import AsyncMock, patch

    from src.agent_tools.web_tools import PdfExtractTool, WebFetchTool

    positioned = (
        "[Positioned PDF table evidence, page 3.]\n"
        "Model | Metric-A | Metric-B\nTarget-7B | 81.2 | 94.6"
    )
    html = {"output": "prose " * 20_000, "exit_code": 0}
    with (
        patch.object(PdfExtractTool, "_positioned_table_evidence", return_value=positioned),
        patch.object(WebFetchTool, "execute", new=AsyncMock(return_value=html)),
    ):
        result = asyncio.run(PdfExtractTool().execute(
            '{"url":"https://arxiv.org/pdf/2406.04264",'
            '"query":"Target-7B Metric-A Metric-B"}',
            {},
        ))

    output = result["output"]
    assert "Target-7B | 81.2 | 94.6" in output
    assert output.index("Positioned PDF table evidence") < output.index("Focused HTML evidence")


def test_pdf_table_region_prefers_exact_requested_rows_over_named_variants():
    from src.agent_tools.web_tools import PdfExtractTool

    rows = [
        (10.0, [{"text": "Table"}, {"text": "4:"}, {"text": "Main"}]),
        (20.0, [{"text": "Model"}, {"text": "AIME"}, {"text": "MATH-500"}]),
        (30.0, [{"text": "DeepSeek-R1"}, {"text": "79.8"}, {"text": "97.3"}]),
        (40.0, [{"text": "OpenAI-o1"}, {"text": "74.3"}, {"text": "96.4"}]),
        (100.0, [{"text": "Table"}, {"text": "15:"}, {"text": "Distilled"}]),
        (110.0, [{"text": "Model"}, {"text": "AIME"}, {"text": "MATH-500"}]),
        (120.0, [{"text": "DeepSeek-R1-Distill-Qwen-7B"}, {"text": "55.5"}, {"text": "92.8"}]),
        (130.0, [{"text": "DeepSeek-R1-Distill-Qwen-32B"}, {"text": "72.6"}, {"text": "94.3"}]),
    ]

    selected = PdfExtractTool._select_positioned_table_region(
        rows,
        metric_terms=["AIME", "MATH-500"],
        model_terms=["DeepSeek-R1", "OpenAI-o1"],
        requested_table_number=None,
    )

    text = " ".join(word["text"] for _top, words in selected for word in words)
    assert "DeepSeek-R1 79.8 97.3" in text
    assert "Distill" not in text


def test_pdf_extract_positioned_target_prefers_model_present_in_rows():
    from src.agent_tools.web_tools import PdfExtractTool

    broad_query_models = [
        "GLM-4.6V",
        "Qwen3-VL-A22B-Instruct",
        "Seed-1.5-VL-Thinking",
    ]
    glm_rows = [
        (78.0, [
            {"text": "Task", "x0": 111},
            {"text": "Benchmark", "x0": 171},
            {"text": "GLM-4.6V", "x0": 302},
            {"text": "Qwen2.5-VL", "x0": 382},
        ]),
        (480.0, [
            {"text": "RefCOCO-avg", "x0": 171},
            {"text": "88.6", "x0": 302},
            {"text": "90.3", "x0": 382},
        ]),
    ]
    seed_rows = [
        (138.0, [
            {"text": "Capability", "x0": 76},
            {"text": "Benchmark", "x0": 136},
            {"text": "Seed", "x0": 219},
            {"text": "Qwen", "x0": 504},
        ]),
        (147.0, [
            {"text": "1.5-VL", "x0": 219},
            {"text": "2.5-VL", "x0": 504},
        ]),
        (159.0, [
            {"text": "thinking", "x0": 219},
            {"text": "non-thinking", "x0": 504},
        ]),
        (510.0, [
            {"text": "RefCOCO-avg", "x0": 136},
            {"text": "91.3", "x0": 219},
            {"text": "91.6", "x0": 504},
        ]),
    ]

    assert (
        PdfExtractTool._select_positioned_target_model(broad_query_models, glm_rows)
        == "GLM-4.6V"
    )
    assert (
        PdfExtractTool._select_positioned_target_model(broad_query_models, seed_rows)
        == "Seed-1.5-VL-Thinking"
    )


def test_pdf_extract_is_native_schema_and_rag_tool():
    from src.agent_tools import TOOL_HANDLERS
    from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

    names = {item["function"]["name"] for item in FUNCTION_TOOL_SCHEMAS}
    assert "pdf_extract" in names
    assert "pdf_extract" in TOOL_HANDLERS
    assert "pdf_extract" in BUILTIN_TOOL_DESCRIPTIONS


def test_positioned_pdf_keeps_continuous_rows_between_table_anchors():
    source = (Path(__file__).parents[1] / "src" / "agent_tools" / "web_tools.py").read_text()
    assert "table_top = max(0.0, min(anchors) - 100)" in source
    assert "table_bottom = max(anchors) + 100" in source
    assert "table_top <= row[0] <= table_bottom" in source


def test_pdf_table_model_match_survives_split_hyphenated_name():
    from src.agent_tools.web_tools import PdfExtractTool

    assert PdfExtractTool._pdf_row_contains_term(
        "LLaVA- Onevision [24] 2024-08 32 frm 83.5 56.4",
        "LLaVA-Onevision",
    )
    assert not PdfExtractTool._pdf_row_contains_term(
        "GPT-4o 83.7 68.8", "LLaVA-Onevision"
    )


def test_pdf_table_exact_model_match_rejects_named_variants():
    from src.agent_tools.web_tools import PdfExtractTool

    assert PdfExtractTool._pdf_row_exact_term_match(
        "DeepSeek-VL2 | 0.4B | 93.3 | 86.0 | 84.2",
        "DeepSeek-VL2",
    )
    assert PdfExtractTool._pdf_row_exact_term_match(
        "LLaVA- Onevision | 83.5 | 56.4",
        "LLaVA-Onevision",
    )
    assert not PdfExtractTool._pdf_row_exact_term_match(
        "DeepSeek-VL2-Tiny | 0.4B | 88.9 | 81.0 | 80.7",
        "DeepSeek-VL2",
    )
    assert not PdfExtractTool._pdf_row_exact_term_match(
        "DeepSeek-VL2-Small | 0.4B | 92.3 | 84.5 | 83.4",
        "DeepSeek-VL2",
    )


def test_pdf_model_alias_matches_abbreviated_header_not_named_variant():
    from src.agent_tools.web_tools import PdfExtractTool

    assert "R1-Zero" in PdfExtractTool._pdf_model_suffix_aliases(
        "DeepSeek-R1-Zero"
    )
    assert PdfExtractTool._pdf_row_contains_model_alias(
        "Benchmark | R1-Zero | R1-Dev1 | R1",
        "DeepSeek-R1-Zero",
    )
    assert not PdfExtractTool._pdf_row_contains_model_alias(
        "DeepSeek-R1-Distill-Qwen-32B | 72.6 | 94.3",
        "DeepSeek-R1",
    )


def test_pdf_metric_terms_include_short_table_acronyms_without_generic_noise():
    from src.agent_tools.web_tools import PdfExtractTool

    for term in ("TR", "AR", "NQA", "ER", "PQA", "AO", "AC"):
        assert PdfExtractTool._looks_like_pdf_metric_term(term)
    for term in ("PDF", "URL", "HTML", "GPT", "LLM", "VLM"):
        assert not PdfExtractTool._looks_like_pdf_metric_term(term)
    tokens = PdfExtractTool._pdf_query_tokens(
        "Table scores for TR AR NQA ER PQA AO AC"
    )
    assert {"TR", "AR", "NQA", "ER", "PQA", "AO", "AC"} <= set(tokens)


def test_pdf_model_terms_reject_hyphenated_query_prose():
    from src.agent_tools.web_tools import PdfExtractTool

    for term in ("GPT-4o", "LLaVA-Onevision", "DeepSeek-VL2", "Qwen3-235B-A22B"):
        assert PdfExtractTool._looks_like_pdf_model_term(term)
    for term in ("multiple-choice", "state-of-the-art", "cross-document"):
        assert not PdfExtractTool._looks_like_pdf_model_term(term)
    for term in ("1", "2024", "2501.12948"):
        assert not PdfExtractTool._looks_like_pdf_model_term(term)


def test_pdf_short_metric_matching_uses_cells_not_substrings():
    from src.agent_tools.web_tools import PdfExtractTool

    table_words = [{"text": "TR"}, {"text": "AR"}, {"text": "VS∗"}]
    prose_words = [{"text": "arXiv"}, {"text": "training"}, {"text": "report"}]
    assert PdfExtractTool._pdf_words_contain_metric(table_words, "TR")
    assert PdfExtractTool._pdf_words_contain_metric(table_words, "AR")
    assert not PdfExtractTool._pdf_words_contain_metric(prose_words, "TR")
    assert not PdfExtractTool._pdf_words_contain_metric(prose_words, "AR")


def test_pdf_table_label_matching_is_exact():
    from src.agent_tools.web_tools import PdfExtractTool

    rows = [
        (90.0, [{"text": "TR"}, {"text": "AR"}]),
        (459.0, [{"text": "Table"}, {"text": "2."}, {"text": "Results"}]),
    ]
    assert PdfExtractTool._pdf_rows_contain_table_label(rows, 2)
    assert not PdfExtractTool._pdf_rows_contain_table_label(rows, 3)


def test_plain_pdf_web_fetch_redirects_to_pdf_extract():
    import asyncio
    from src.agent_tools.web_tools import WebFetchTool

    result = asyncio.run(WebFetchTool().execute(
        '{"url":"https://arxiv.org/pdf/2502.13923"}', {}
    ))
    assert result["exit_code"] == 1
    assert "Use pdf_extract" in result["error"]


def test_pdf_web_fetch_auto_focuses_from_active_request():
    import asyncio
    from unittest.mock import patch
    from src.agent_tools.web_tools import WebFetchTool

    result_doc = {
        "content": "noise\nDocVQA 96.4 ChartQA 89.5 TextVQA 83.5\nnoise",
        "title": "paper",
    }
    ctx = {"client_runtime_context": {"request_text": (
        "Read the PDF and extract Qwen2.5-VL-72B DocVQA ChartQA TextVQA"
    )}}
    with patch("src.search.content.fetch_webpage_content", return_value=result_doc):
        result = asyncio.run(WebFetchTool().execute(
            '{"url":"https://arxiv.org/pdf/2502.13923"}', ctx
        ))
    assert result["exit_code"] == 0
    assert "DocVQA 96.4" in result["output"]


def test_python_http_download_is_rejected_before_subprocess():
    import asyncio
    from src.agent_tools.subprocess_tools import PythonTool

    result = asyncio.run(PythonTool().execute(
        'import requests; requests.get("https://example.test/file.pdf")', {}
    ))
    assert result["exit_code"] == 1
    assert "Use pdf_extract" in result["error"]
    subprocess_download = asyncio.run(PythonTool().execute(
        "import subprocess; subprocess.run(['curl', 'https://example.test/a.pdf'])", {}
    ))
    assert subprocess_download["exit_code"] == 1
    assert "Use pdf_extract" in subprocess_download["error"]
    urlretrieve = asyncio.run(PythonTool().execute(
        'import urllib.request; urllib.request.urlretrieve("https://example.test/a.pdf", "/tmp/a.pdf")',
        {},
    ))
    assert urlretrieve["exit_code"] == 1


def test_python_surfaces_failed_child_viewer_even_when_script_exits_zero():
    import asyncio
    from src.agent_tools.subprocess_tools import PythonTool

    result = asyncio.run(PythonTool().execute(
        "import sys; print(\"xdg-open: no method available for opening '/tmp/a.png'\", file=sys.stderr)",
        {},
    ))

    assert result["exit_code"] == 1
    assert "child operation failed" in result["error"]


def test_python_keeps_warning_only_stderr_successful():
    import asyncio
    from src.agent_tools.subprocess_tools import PythonTool

    result = asyncio.run(PythonTool().execute(
        "import sys; print('ordinary warning', file=sys.stderr)",
        {},
    ))

    assert result["exit_code"] == 0


def test_python_emits_one_final_bare_expression_without_duplicating_print():
    import asyncio
    from src.agent_tools.subprocess_tools import PythonTool

    bare = asyncio.run(PythonTool().execute(
        "from collections import Counter\nCounter([1, 1, 2])", {},
    ))
    explicit = asyncio.run(PythonTool().execute("print('once')", {}))

    assert bare["exit_code"] == 0
    assert bare["output"] == "Counter({1: 2, 2: 1})"
    assert explicit["output"] == "once"


def test_python_loaded_code_sees_virtual_workspace_alias(monkeypatch, tmp_path):
    """Absolute /workspace paths must work inside generated Python scripts."""
    import asyncio
    import shutil
    import sys
    import venv
    from types import SimpleNamespace

    from src import containment
    if not containment._bwrap_available():
        pytest.skip("functional bubblewrap namespaces unavailable")

    from pathlib import Path

    from src.agent_tools import subprocess_tools
    from src import tool_execution
    workspace = tmp_path
    environment = tmp_path / "confined-venv"
    venv.EnvBuilder(with_pip=False).create(environment)
    monkeypatch.setattr(subprocess_tools, "sys", SimpleNamespace(
        prefix=str(environment),
        base_prefix=sys.base_prefix,
        executable=str(environment / "bin" / "python"),
        version_info=sys.version_info,
    ))
    script = workspace / ".python-workspace-alias-test.py"
    output = workspace / ".python-workspace-alias-test.txt"
    outside = workspace.parent / "host-sibling.txt"
    outside.write_text("must stay hidden from private /tmp")
    script.write_text(
        "from pathlib import Path; "
        "assert not list(Path('/tmp').rglob('host-sibling.txt')); "
        "Path('/workspace/.python-workspace-alias-test.txt').write_text('ok')"
    )
    monkeypatch.setattr(tool_execution, "agent_cwd", lambda: str(workspace))
    result = asyncio.run(subprocess_tools.PythonTool().execute(
        "import runpy; runpy.run_path('/workspace/.python-workspace-alias-test.py', run_name='__main__')",
        {},
    ))
    assert result["exit_code"] == 0, result
    assert output.read_text() == "ok"


def test_workspace_namespace_mounts_only_a_nested_python_environment(monkeypatch, tmp_path):
    import shlex

    from src.agent_tools import subprocess_tools

    monkeypatch.setattr(subprocess_tools.shutil, "which", lambda name: "/usr/bin/bwrap")
    monkeypatch.setattr(subprocess_tools.containment, "_bwrap_available", lambda: True)
    environment = tmp_path / "nested" / "venv"
    environment.mkdir(parents=True)
    (environment / "pyvenv.cfg").write_text("home = /usr/bin\n")
    command = subprocess_tools._wrap_workspace_namespace(
        "echo ok", str(tmp_path), interpreter_prefix=str(environment),
    )
    args = shlex.split(command)
    assert ["--ro-bind", str(environment), str(environment)] in [
        args[index:index + 3] for index in range(len(args) - 2)
    ]
    assert ["--tmpfs", "/tmp"] in [
        args[index:index + 2] for index in range(len(args) - 1)
    ]


def test_workspace_namespace_rejects_broad_or_symlinked_python_prefixes(monkeypatch, tmp_path):
    import shlex

    from src.agent_tools import subprocess_tools

    monkeypatch.setattr(subprocess_tools.shutil, "which", lambda name: "/usr/bin/bwrap")
    monkeypatch.setattr(subprocess_tools.containment, "_bwrap_available", lambda: True)
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to("/", target_is_directory=True)
    # Compared against the argv with no interpreter prefix at all: an unsafe
    # prefix must add *nothing*. Asserting the absence of a literal
    # `--ro-bind <prefix> <prefix>` instead would also fire on a base mount the
    # argv makes for its own reasons -- /home and /mnt are read-only binds
    # there -- which says nothing about whether the prefix was rejected.
    baseline = shlex.split(
        subprocess_tools._wrap_workspace_namespace("echo ok", str(tmp_path))
    )
    for unsafe_prefix in ("/", "/tmp", "/var", "/home", str(linked_root)):
        command = subprocess_tools._wrap_workspace_namespace(
            "echo ok", str(tmp_path), interpreter_prefix=unsafe_prefix,
        )
        args = shlex.split(command)
        assert args == baseline, f"prefix {unsafe_prefix} changed the namespace argv"
        assert ["--tmpfs", "/tmp"] in [
            args[index:index + 2] for index in range(len(args) - 1)
        ]
        assert ["--bind", str(tmp_path), "/workspace"] in [
            args[index:index + 3] for index in range(len(args) - 2)
        ]


def test_workspace_namespace_preserves_the_64_bit_dynamic_loader(monkeypatch):
    """The namespace's /lib64 must mirror usrmerge hosts, not /usr/lib."""
    import shlex

    from src.agent_tools import subprocess_tools

    monkeypatch.setattr(subprocess_tools.shutil, "which", lambda name: "/usr/bin/bwrap")
    monkeypatch.setattr(subprocess_tools.containment, "_bwrap_available", lambda: True)
    command = subprocess_tools._wrap_workspace_namespace("echo ok", "/tmp/workspace")
    assert command is not None
    args = shlex.split(command)
    assert ["--symlink", "usr/lib64", "/lib64"] == args[args.index("/lib") + 1 : args.index("/lib") + 4]


def test_native_web_tool_shell_wrapper_is_repaired_without_claw_mapping():
    from src.agent_loop import _normalize_native_tool_shell_wrapper
    from src.tool_types import ToolBlock

    block = _normalize_native_tool_shell_wrapper(
        ToolBlock("bash", "web_fetch https://arxiv.org/pdf/2502.13923"),
        "Compare Qwen2.5-VL-72B DocVQA ChartQA TextVQA",
    )
    assert block.tool_type == "web_fetch"
    assert '"query": "Qwen2.5-VL-72B, DocVQA, ChartQA, TextVQA"' in block.content
    untouched = _normalize_native_tool_shell_wrapper(
        ToolBlock("bash", "echo web_fetch https://example.test"), ""
    )
    assert untouched.tool_type == "bash"


def test_bash_rejects_http_download_and_sudo_before_subprocess():
    import asyncio
    from src.agent_tools.subprocess_tools import BashTool

    download = asyncio.run(BashTool().execute(
        'curl -L https://example.test/paper.pdf -o /tmp/paper.pdf', {}
    ))
    assert download["exit_code"] == 1
    assert "Use pdf_extract" in download["error"]
    privileged = asyncio.run(BashTool().execute("sudo apt-get install poppler-utils", {}))
    assert privileged["exit_code"] == 1
    assert "privilege escalation" in privileged["error"]


def test_forced_finish_keeps_tools_only_for_missing_terminal_artifacts():
    from src.agent_loop import _force_answer_keeps_artifact_tools

    assert _force_answer_keeps_artifact_tools(
        force_answer=True,
        artifact_recovery_enabled=True,
        artifact_creation_requested=True,
        missing_artifacts=("/workspace/output.html",),
    )
    assert not _force_answer_keeps_artifact_tools(
        force_answer=True,
        artifact_recovery_enabled=True,
        artifact_creation_requested=True,
        missing_artifacts=(),
    )
    assert not _force_answer_keeps_artifact_tools(
        force_answer=True,
        artifact_recovery_enabled=False,
        artifact_creation_requested=True,
        missing_artifacts=("/workspace/output.html",),
    )
    assert _force_answer_keeps_artifact_tools(
        force_answer=True,
        artifact_recovery_enabled=True,
        artifact_creation_requested=True,
        missing_artifacts=(),
        correction_available=True,
        convergence_sent=False,
    )
    assert not _force_answer_keeps_artifact_tools(
        force_answer=True,
        artifact_recovery_enabled=True,
        artifact_creation_requested=True,
        missing_artifacts=(),
        correction_available=True,
        convergence_sent=True,
    )
