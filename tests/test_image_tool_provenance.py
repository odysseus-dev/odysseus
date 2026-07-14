"""Image tool provenance: stdout parse + tool_event args merge (pipeline step 3b)."""

from mcp_servers.gallery_provenance import (
    build_image_args_from_tool_event,
    enrich_image_tool_result,
    parse_tool_stdout_provenance,
)


_SAMPLE_STDOUT = """Generated image for: test prompt
Direct link: /api/generated-image/abc123.png
gallery_id: gid-1
style: realistic (ThisIsReal SDXL v3.0 (photoreal))
size: 1024x1024 | quality: high | count: 1
seed: 165861460
negative_prompt: worst quality
cfg_scale: 4.5
steps: 50
sampler: dpm++2m
scheduler: karras
clip_skip: 1
style: realistic
quality: high
size: 1024x1024
"""


def test_parse_tool_stdout_provenance():
    prov = parse_tool_stdout_provenance(_SAMPLE_STDOUT)
    assert prov["seed"] == 165861460
    assert prov["cfg_scale"] == 4.5
    assert prov["steps"] == 50
    assert prov["style"] == "realistic"
    assert prov["quality"] == "high"


def test_enrich_image_tool_result_from_stdout():
    result = {"exit_code": 0, "stdout": _SAMPLE_STDOUT}
    enrich_image_tool_result(result)
    assert result["seed"] == 165861460
    assert result["provenance"]["seed"] == 165861460
    assert result["style"] == "realistic"


def test_build_image_args_from_tool_event_merges_seed():
    ev = {
        "tool": "generate_image",
        "command": '{"prompt": "solo, 1woman", "style": "realistic", "quality": "high"}',
        "output": _SAMPLE_STDOUT,
        "gallery_id": "gid-1",
        "image_prompt": "solo, 1woman",
        "exit_code": 0,
    }
    args = build_image_args_from_tool_event(ev)
    assert args["seed"] == 165861460
    assert args["source_image_id"] == "gid-1"
    assert args["prompt"] == "solo, 1woman"
    assert args["cfg_scale"] == 4.5


def test_normalize_model_label_replaces_legacy_realvis():
    from titan.style_labels import normalize_model_label

    assert normalize_model_label("realistic (RealVisXL V5 (photoreal))") == "ThisIsReal SDXL v3.0"
    assert normalize_model_label("style: realistic", "realistic") == "ThisIsReal SDXL v3.0"
