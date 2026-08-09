"""Node-driven tests for browser model-control capability gating."""

import json
import re
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


def test_app_reuses_the_canonical_sessions_module_instance():
    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    match = re.search(r"import sessionModule from '([^']+)'", source)

    assert match is not None
    assert match.group(1) == "./js/sessions.js"


def test_endpoint_detection_uses_parsed_host_path_and_local_ollama_surfaces():
    values = _node_eval(
        """
        const controls = await import('./static/js/modelControls.js');
        console.log(JSON.stringify({
          subscription: controls.isChatGptSubscriptionEndpoint('https://chatgpt.com/backend-api/codex/responses'),
          subscriptionSubdomain: controls.isChatGptSubscriptionEndpoint('https://api.chatgpt.com/backend-api/codex'),
          subscriptionSpoof: controls.isChatGptSubscriptionEndpoint('https://chatgpt.com.evil.test/backend-api/codex'),
          subscriptionInQuery: controls.isChatGptSubscriptionEndpoint('https://evil.test/?next=https://chatgpt.com/backend-api/codex'),
          ollamaNativeCustomPort: controls.isOllamaEndpoint('http://localhost:12345/api/chat'),
          ollamaCompatCustomPort: controls.isOllamaEndpoint('http://127.0.0.1:12345/v1/chat/completions'),
          ollamaContainerPort: controls.isOllamaEndpoint('http://ollama:11434/v1/chat/completions'),
          ollamaSpoof: controls.isOllamaEndpoint('https://ollama.evil.test/v1/chat/completions'),
          ollamaInQuery: controls.isOllamaEndpoint('https://api.example.test/v1/chat/completions?provider=ollama'),
        }));
        """
    )

    assert values == {
        "subscription": True,
        "subscriptionSubdomain": False,
        "subscriptionSpoof": False,
        "subscriptionInQuery": False,
        "ollamaNativeCustomPort": True,
        "ollamaCompatCustomPort": True,
        "ollamaContainerPort": True,
        "ollamaSpoof": False,
        "ollamaInQuery": False,
    }


def test_openai_reasoning_capabilities_match_model_contracts():
    values = _node_eval(
        """
        const { modelControlCapabilities } = await import('./static/js/modelControls.js');
        const endpointUrl = 'https://chatgpt.com/backend-api/codex';
        const allowed = model => modelControlCapabilities('reasoning_effort', { model, endpointUrl }).allowed;
        console.log(JSON.stringify({
          gpt5: allowed('gpt-5'),
          gpt51: allowed('gpt-5.1'),
          gpt51Codex: allowed('gpt-5.1-codex'),
          gpt51CodexMax: allowed('gpt-5.1-codex-max'),
          gpt52: allowed('gpt-5.2'),
          gpt52Codex: allowed('gpt-5.2-codex'),
          gpt52pro: allowed('gpt-5.2-pro'),
          gpt54pro: allowed('gpt-5.4-pro'),
          gpt56: allowed('gpt-5.6-sol'),
          gpt5pro: allowed('gpt-5-pro'),
          o3: allowed('o3-mini'),
          nearMatch: allowed('gpt-50'),
        }));
        """
    )

    assert values == {
        "gpt5": ["auto", "minimal", "low", "medium", "high"],
        "gpt51": ["auto", "off", "low", "medium", "high"],
        "gpt51Codex": ["auto", "low", "medium", "high"],
        "gpt51CodexMax": ["auto", "low", "medium", "high", "xhigh"],
        "gpt52": ["auto", "off", "low", "medium", "high", "xhigh"],
        "gpt52Codex": ["auto", "low", "medium", "high", "xhigh"],
        "gpt52pro": ["auto", "medium", "high", "xhigh"],
        "gpt54pro": ["auto", "medium", "high", "xhigh"],
        "gpt56": ["auto", "off", "low", "medium", "high", "xhigh", "max"],
        "gpt5pro": ["auto", "high"],
        "o3": ["auto", "low", "medium", "high"],
        "nearMatch": ["auto"],
    }


def test_ollama_reasoning_capabilities_distinguish_boolean_and_level_models():
    values = _node_eval(
        """
        const { modelControlCapabilities } = await import('./static/js/modelControls.js');
        const endpointUrl = 'http://localhost:12345/v1/chat/completions';
        const allowed = model => modelControlCapabilities('reasoning_effort', { model, endpointUrl }).allowed;
        console.log(JSON.stringify({
          qwen: allowed('qwen3:14b'),
          deepseek: allowed('deepseek-v3.1:671b'),
          gptOss: allowed('gpt-oss:20b'),
          llama: allowed('llama3.2:3b'),
        }));
        """
    )

    assert values == {
        "qwen": ["auto", "off", "on"],
        "deepseek": ["auto", "off", "on"],
        "gptOss": ["auto", "low", "medium", "high"],
        "llama": ["auto"],
    }


def test_verbosity_stays_subscription_and_gpt5_specific():
    values = _node_eval(
        """
        const { modelControlCapabilities } = await import('./static/js/modelControls.js');
        const allowed = (model, endpointUrl) => modelControlCapabilities('verbosity', { model, endpointUrl }).allowed;
        console.log(JSON.stringify({
          supported: allowed('gpt-5.6-sol', 'https://chatgpt.com/backend-api/codex'),
          generic: allowed('gpt-5.6-sol', 'https://api.example.test/v1/chat/completions'),
          older: allowed('gpt-4o', 'https://chatgpt.com/backend-api/codex'),
        }));
        """
    )

    assert values == {
        "supported": ["auto", "low", "medium", "high"],
        "generic": ["auto"],
        "older": ["auto"],
    }
