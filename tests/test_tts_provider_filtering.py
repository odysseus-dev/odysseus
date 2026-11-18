"""TTS provider/model filtering.

The Settings -> Text to Speech card must only offer speech-capable endpoints and
models. OpenRouter GPT Audio is advertised even when OpenRouter's model catalog
omits it, while music models (google/lyria-*) and text-only endpoints such as
DeepSeek stay out.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")


def _node_eval(source: str):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_tts_endpoint_and_model_filtering():
    values = _node_eval(
        """
        import {
          TTS_VOICE_SETS, inferTtsProvider, isTtsCapableEndpoint,
          isTtsModelForProvider, ttsModelsForEndpoint, voiceOptionsForProvider,
        } from './static/js/settings/ttsVoices.js';

        const deepseek = {
          id: 'ds', name: 'DeepSeek', is_enabled: true,
          base_url: 'https://api.deepseek.com/v1',
          models: ['deepseek-chat', 'deepseek-reasoner'],
        };
        const openrouter = {
          id: 'or', name: 'OpenRouter', is_enabled: true,
          base_url: 'https://openrouter.ai/api/v1',
          models: ['google/lyria-002', 'deepseek/deepseek-chat'],
        };
        const openai = {
          id: 'oa', name: 'OpenAI', is_enabled: true,
          base_url: 'https://api.openai.com/v1',
          models: ['gpt-4o', 'gpt-4o-mini'],
        };
        const xai = {
          id: 'xa', name: 'xAI', is_enabled: true,
          base_url: 'https://api.x.ai/v1',
          models: ['grok-4'],
        };
        const localTts = {
          id: 'lt', name: 'Local speech', is_enabled: true,
          base_url: 'http://127.0.0.1:8880/v1',
          models: ['local-tts'],
        };
        const localLlm = {
          id: 'll', name: 'Local LLM', is_enabled: true,
          base_url: 'http://127.0.0.1:1234/v1',
          models: ['qwen35-9b-base-a40g0'],
        };

        console.log(JSON.stringify({
          capable: {
            deepseek: isTtsCapableEndpoint(deepseek),
            openrouter: isTtsCapableEndpoint(openrouter),
            openai: isTtsCapableEndpoint(openai),
            xai: isTtsCapableEndpoint(xai),
            localTts: isTtsCapableEndpoint(localTts),
            localLlm: isTtsCapableEndpoint(localLlm),
          },
          models: {
            openrouter: ttsModelsForEndpoint(openrouter),
            openai: ttsModelsForEndpoint(openai),
            xai: ttsModelsForEndpoint(xai),
            localTts: ttsModelsForEndpoint(localTts),
            deepseek: ttsModelsForEndpoint(deepseek),
          },
          valid: {
            gptAudio: isTtsModelForProvider('openrouter', 'openai/gpt-audio'),
            lyria: isTtsModelForProvider('openrouter', 'google/lyria-002'),
            deepseek: isTtsModelForProvider('openai', 'deepseek-chat'),
          },
          voices: {
            openrouter: voiceOptionsForProvider('openrouter'),
            openai: TTS_VOICE_SETS.openai,
          },
          inferredOpenRouter: inferTtsProvider({
            provider: 'endpoint:or', endpoint: openrouter, model: 'openai/gpt-audio'
          }),
        }));
        """
    )

    assert values["capable"] == {
        "deepseek": False,
        "openrouter": True,
        "openai": True,
        "xai": True,
        "localTts": True,
        "localLlm": False,
    }
    assert values["models"]["openrouter"] == [
        "openai/gpt-audio",
        "openai/gpt-audio-mini",
    ]
    assert "google/lyria-002" not in values["models"]["openrouter"]
    assert values["models"]["openai"] == ["tts-1", "tts-1-hd", "gpt-4o-mini-tts"]
    assert values["models"]["deepseek"] == []
    assert values["models"]["localTts"] == ["local-tts"]
    assert values["valid"] == {"gptAudio": True, "lyria": False, "deepseek": False}
    assert values["voices"]["openrouter"] == values["voices"]["openai"]
    assert values["inferredOpenRouter"] == "openai"


def test_speech_card_uses_filter_and_shows_hint_and_empty_state():
    speech = (ROOT / "static/js/settings/speech.js").read_text(encoding="utf-8")
    index = (ROOT / "static/index.html").read_text(encoding="utf-8")

    assert "isTtsCapableEndpoint" in speech
    assert "if (!isTtsCapableEndpoint(ep)) return;" in speech
    assert "ttsModelsForEndpoint" in speech
    assert "isTtsModelForProvider" in speech
    assert "migration.tts_provider" in speech
    assert "migration.tts_model" in speech
    assert "DeepSeek" not in speech

    assert "Only TTS-capable providers/models are listed." in index
    assert 'id="set-ttsEmptyState"' in index
    assert "No TTS-capable endpoints found." in index
    assert '<option value="custom">' in index


def test_tts_assets_use_ttsclean_cache_bust():
    refs = {
        "static/index.html": "tts-ai.js?v=20261105ttsscroll1",
        "static/app.js": "tts-ai.js?v=20261105ttsscroll1",
        "static/sw.js": "tts-ai.js?v=20261105ttsscroll1",
        "static/js/chat.js": "tts-ai.js?v=20261105ttsscroll1",
        "static/js/chatRenderer.js": "tts-ai.js?v=20261105ttsscroll1",
    }
    for relative, needle in refs.items():
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert needle in source, relative

    settings = (ROOT / "static/js/settings.js").read_text(encoding="utf-8")
    speech = (ROOT / "static/js/settings/speech.js").read_text(encoding="utf-8")
    sw = (ROOT / "static/sw.js").read_text(encoding="utf-8")
    index = (ROOT / "static/index.html").read_text(encoding="utf-8")

    assert "settings/speech.js?v=20261103ttsclean1" in settings
    assert "ttsVoices.js?v=20261103ttsclean1" in speech
    assert "settings.js?v=20261105hinttooltip1" in index
    assert "/static/js/settings/speech.js?v=20261103ttsclean1" in sw
    assert "/static/js/settings/ttsVoices.js?v=20261103ttsclean1" in sw
    assert "odysseus-v1202-ttsscroll1" in sw
