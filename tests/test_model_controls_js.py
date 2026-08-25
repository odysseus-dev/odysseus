"""Node-driven tests for canonical browser model-control gating."""

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


def test_provider_evidence_supplies_exact_reasoning_and_verbosity_values():
    values = _node_eval(
        """
        const { modelControlCapabilities, normalizeModelControlValue } = await import('./static/js/modelControls.js');
        const modelCapability = {
          model_id: 'opaque-slug',
          deterministic_controls: [
            { control: 'reasoning_effort', status: 'claimed', evidence: { allowed_values: ['none', 'low', 'high', 'xhigh', 'ultra'] } },
            { control: 'verbosity', status: 'verified', evidence: { allowed_values: ['low', 'medium', 'high'] } },
          ],
        };
        console.log(JSON.stringify({
          reasoning: modelControlCapabilities('reasoning_effort', { model: 'opaque-slug', modelCapability }),
          verbosity: modelControlCapabilities('verbosity', { model: 'opaque-slug', modelCapability }),
          future: normalizeModelControlValue('Ultra'),
        }));
        """
    )

    assert values["reasoning"] == {
        "supported": True,
        "allowed": ["auto", "off", "low", "high", "xhigh", "ultra"],
        "reason": "",
    }
    assert values["verbosity"] == {
        "supported": True,
        "allowed": ["auto", "low", "medium", "high"],
        "reason": "",
    }
    assert values["future"] == "ultra"


def test_names_and_urls_never_infer_controls_without_canonical_evidence():
    values = _node_eval(
        """
        const { modelControlCapabilities } = await import('./static/js/modelControls.js');
        const allowed = (model, endpointUrl) => modelControlCapabilities(
          'reasoning_effort', { model, endpointUrl }
        ).allowed;
        console.log(JSON.stringify({
          chatgpt: allowed('gpt-5.6-sol', 'https://chatgpt.com/backend-api/codex'),
          ollama: allowed('qwen3:14b', 'http://127.0.0.1:11434/v1/chat/completions'),
          generic: allowed('reasoning-super-model', 'https://example.test/v1/chat/completions'),
        }));
        """
    )

    assert values == {"chatgpt": ["auto"], "ollama": ["auto"], "generic": ["auto"]}


def test_unknown_or_unsupported_evidence_remains_conservative():
    values = _node_eval(
        """
        const { modelControlCapabilities } = await import('./static/js/modelControls.js');
        const cap = status => ({ model_id: 'm', deterministic_controls: [
          { control: 'reasoning_effort', status, evidence: { allowed_values: ['high'] } },
        ] });
        console.log(JSON.stringify({
          unknown: modelControlCapabilities('reasoning_effort', { model: 'm', modelCapability: cap('unknown') }).allowed,
          unsupported: modelControlCapabilities('reasoning_effort', { model: 'm', modelCapability: cap('unsupported') }).allowed,
          missingValues: modelControlCapabilities('reasoning_effort', {
            model: 'm', modelCapability: { model_id: 'm', deterministic_controls: [
              { control: 'reasoning_effort', status: 'claimed', evidence: {} },
            ] },
          }).allowed,
        }));
        """
    )

    assert values == {"unknown": ["auto"], "unsupported": ["auto"], "missingValues": ["auto"]}


def test_catalog_lookup_uses_exact_endpoint_and_model_identity():
    values = _node_eval(
        """
        const { modelCapabilityForContext } = await import('./static/js/modelControls.js');
        const first = { model_id: 'same-model', deterministic_controls: [{ control: 'verbosity', status: 'claimed', evidence: { allowed_values: ['low'] } }] };
        const second = { model_id: 'same-model', deterministic_controls: [{ control: 'verbosity', status: 'claimed', evidence: { allowed_values: ['high'] } }] };
        const items = [
          { endpoint_id: 'ep-a', url: 'https://same.test/chat', model_capabilities: [first] },
          { endpoint_id: 'ep-b', url: 'https://same.test/chat', model_capabilities: [second] },
        ];
        console.log(JSON.stringify({
          exact: modelCapabilityForContext(items, { model: 'same-model', endpointId: 'ep-b' }),
          ambiguousUrl: modelCapabilityForContext(items, { model: 'same-model', endpointUrl: 'https://same.test/chat' }),
          nearMatch: modelCapabilityForContext(items, { model: 'same-model-v2', endpointId: 'ep-b' }),
        }));
        """
    )

    assert values["exact"]["deterministic_controls"][0]["evidence"]["allowed_values"] == ["high"]
    assert values["ambiguousUrl"] is None
    assert values["nearMatch"] is None
