import json

import pytest

from routes.hwfit_routes import _inspect_model_path
from services.hwfit.hf_discovery import _infer_context


@pytest.mark.parametrize("repo_id,pipeline_tag", [
    ("Qwen/Qwen3.8-27B", "image-text-to-text"),
    ("Qwen/Qwen3.8-27B", "image-to-text"),
    ("Qwen/Qwen3.8-27B", "video-text-to-text"),
    ("Qwen/Qwen3.8-27B", "audio-text-to-text"),
    ("acme/Omni-Audio-Video-Chat", "any-to-any"),
])
def test_multimodal_llms_do_not_get_media_fallback(repo_id, pipeline_tag):
    # Vision/omni LLMs carry "image"/"audio"/"video" in their pipeline tag (or
    # name) but are long-context chat models; 4096 clamped the Context field.
    assert _infer_context(repo_id, pipeline_tag) == 32768


@pytest.mark.parametrize("repo_id,pipeline_tag", [
    ("openai/whisper-large-v3", "automatic-speech-recognition"),
    ("Qwen/Qwen-Image-2.1", "text-to-image"),
    ("some-org/video-gen", "text-to-video"),
    ("some-org/tts-model", "text-to-speech"),
])
def test_media_models_keep_short_fallback(repo_id, pipeline_tag):
    assert _infer_context(repo_id, pipeline_tag) == 4096


def test_multimodal_llm_still_gets_family_override():
    assert _infer_context("zai-org/GLM-5.2-V", "image-text-to-text") == 1_000_000


def _model_dir(tmp_path, config):
    (tmp_path / "config.json").write_text(json.dumps(config, indent=2))
    (tmp_path / "model.safetensors").write_bytes(b"\0" * 1024)
    return str(tmp_path)


def test_inspect_model_path_reads_text_config(tmp_path):
    path = _model_dir(tmp_path, {
        "architectures": ["Qwen3_5ForConditionalGeneration"],
        "text_config": {"max_position_embeddings": 262144},
        "vision_config": {"hidden_size": 1152},
    })
    assert _inspect_model_path(path)["model_ctx_max"] == 262144


def test_inspect_model_path_prefers_top_level(tmp_path):
    path = _model_dir(tmp_path, {
        "max_position_embeddings": 131072,
        "text_config": {"max_position_embeddings": 8192},
    })
    assert _inspect_model_path(path)["model_ctx_max"] == 131072
