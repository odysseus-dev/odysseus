"""Static and helper tests for provider-specific TTS voice pickers."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")

OPENAI_VOICES = [
    "alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx",
    "sage", "shimmer", "verse", "marin", "cedar",
]
XAI_VOICES = [
    "carina", "zagan", "helix", "orion", "luna", "iris", "altair", "zenith",
    "perseus", "helios", "lux", "kepler", "rigel", "cosmo", "celeste",
    "ursa", "sirius", "lumen", "castor", "naksh", "atlas", "aurora",
    "liora", "ara", "eve", "leo", "rex", "sal",
]
def _node_eval(source: str):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_provider_voice_catalogs_match_documented_ids():
    values = _node_eval(
        """
        import {
          TTS_VOICE_SETS, inferTtsProvider, voiceOptionsForProvider,
          defaultVoiceForProvider
        } from './static/js/settings/ttsVoices.js';
        console.log(JSON.stringify({
          openai: TTS_VOICE_SETS.openai,
          xai: TTS_VOICE_SETS.xai,
          kokoro: TTS_VOICE_SETS.kokoro ?? null,
          genericVoices: voiceOptionsForProvider('generic'),
          browserVoices: voiceOptionsForProvider('browser'),
          openaiDefault: defaultVoiceForProvider('openai'),
          xaiDefault: defaultVoiceForProvider('xai'),
          kokoroDefault: defaultVoiceForProvider('kokoro') || null,
          inferred: {
            local: inferTtsProvider({ provider: 'local' }),
            openaiUrl: inferTtsProvider({
              provider: 'custom',
              endpointUrl: 'https://api.openai.com/v1',
              model: 'tts-1'
            }),
            xaiUrl: inferTtsProvider({
              provider: 'endpoint:1',
              endpoint: { name: 'Grok API', base_url: 'https://api.x.ai/v1' }
            }),
            xaiModel: inferTtsProvider({
              provider: 'endpoint:2',
              endpoint: { name: 'API', base_url: 'https://example.com', models: ['grok-4'] }
            }),
            kokoroModel: inferTtsProvider({
              provider: 'custom',
              endpointUrl: 'http://localhost:8880/v1',
              model: 'unlisted-voice-model'
            }),
            openaiProviderField: inferTtsProvider({
              provider: 'endpoint:3',
              endpoint: { name: 'API', provider: 'openai', base_url: 'https://proxy.example.com' }
            }),
            generic: inferTtsProvider({
              provider: 'endpoint:4',
              endpoint: { name: 'My TTS', base_url: 'https://tts.example.com', models: ['custom-voice-model'] },
              model: 'custom-voice-model'
            })
          }
        }));
        """
    )

    assert values["openai"] == OPENAI_VOICES
    assert values["xai"] == XAI_VOICES
    assert values["kokoro"] is None
    assert values["genericVoices"] is None
    assert values["browserVoices"] is None
    assert values["openaiDefault"] == "alloy"
    assert values["xaiDefault"] == "eve"
    assert values["kokoroDefault"] is None
    assert values["inferred"] == {
        "local": "generic",
        "openaiUrl": "openai",
        "xaiUrl": "xai",
        "xaiModel": "xai",
        "kokoroModel": "generic",
        "openaiProviderField": "openai",
        "generic": "generic",
    }


def test_tts_panel_repopulates_voice_picker_on_provider_endpoint_and_model_changes():
    speech = (ROOT / "static" / "js" / "settings" / "speech.js").read_text(encoding="utf-8")
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert "inferTtsProvider(" in speech
    assert "voiceOptionsForProvider(" in speech
    assert "updateVoicePicker(" in speech
    assert "useVoiceSelect ? voiceSelect.value : voiceInput.value" in speech
    assert "populateVoiceSelect(voices, selected)" in speech
    assert "voiceSelect.style.display = ''" in speech
    assert "voiceInput.style.display = provider === 'disabled' ? 'none' : ''" in speech
    assert "defaultVoiceForProvider(getInferredProvider())" in speech
    assert "savedProvider === 'local'" in speech
    assert "migration.tts_provider = 'disabled'" in speech

    provider_change = speech[speech.index("provSel.addEventListener('change'"):]
    provider_change = provider_change[:provider_change.index("modelSelect.addEventListener")]
    assert "updateVisibility(" in provider_change
    assert "updateVoicePicker" in speech[
        speech.index("modelSelect.addEventListener"):speech.index("voiceSelect.addEventListener")
    ]
    assert "updateVoicePicker();" in speech[
        speech.index("modelInput.addEventListener"):speech.index("voiceSelect.addEventListener")
    ]
    endpoint_change = speech[speech.index("endpointUrlInput.addEventListener"):]
    endpoint_change = endpoint_change[:endpoint_change.index("apiKeyInput.addEventListener")]
    assert "updateVoicePicker();" in endpoint_change

    assert 'id="set-ttsVoiceSelect"' in index
    assert 'id="set-ttsVoiceInput"' in index


def test_tts_settings_assets_use_ttsclean_cache_bust():
    refs = {
        "static/index.html": "20261105followupleft",
        "static/app.js": "20261105hinttooltip1",
        "static/sw.js": "20261105followupleft",
        "static/js/chat.js": "20261104ttspause1",
        "static/js/emailLibrary/index.js": "20261104ttspause1",
        "static/js/chatRenderer.js": "20261104ttspause1",
        "static/js/modelPicker.js": "20261104ttspause1",
        "static/js/slashCommands.js": "20261104ttspause1",
        "static/js/gallery.js": "20261104ttspause1",
        "static/js/chatStream.js": "20261104ttspause1",
        "static/js/settings.js": "20261104ttspause1",
    }
    for relative, version in refs.items():
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert version in source
        assert "20261103voices1" not in source
        assert "20261103voices2" not in source
        assert "20261103nokokoro1" not in source

    speech = (ROOT / "static/js/settings/speech.js").read_text(encoding="utf-8")
    assert "ttsVoices.js?v=20261103ttsclean1" in speech


def test_kokoro_is_absent_from_tts_provider_and_voice_options():
    files = [
        "static/index.html",
        "static/js/settings/speech.js",
        "static/js/settings/ttsVoices.js",
        "services/tts/tts_service.py",
        "requirements-optional.txt",
        "specs/speech.md",
        "specs/testing-devops.md",
        "website/setup.md",
    ]
    for relative in files:
        source = (ROOT / relative).read_text(encoding="utf-8").lower()
        source = source.replace("20261103nokokoro1", "").replace("20261103ttsclean1", "")
        assert "kokoro" not in source, relative
        assert "af_heart" not in source, relative
