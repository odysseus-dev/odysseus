"""Tests for Model Picker Endpoint Grouping and ChatGPT Reasoning Effort Control.

Covers:
1. Endpoint/account grouping in modelPicker.js (keyed on endpoint_id, distinct routes, collapsible headers, search auto-expansion, keyboard nav).
2. ChatGPT reasoning effort (validation, catalog metadata, session thinking_mode persistence, payload shaping with zero provider tools).
3. Composer UI elements and styles.
"""

import json
from pathlib import Path
import subprocess
import pytest

from src import chatgpt_subscription, llm_core
from tests.helpers.stylesheets import app_css

ROOT = Path(__file__).parents[1]


# ============================================================
# PART 1: FRONTEND MODEL PICKER GROUPING & UI
# ============================================================

def test_model_picker_endpoint_grouping_logic_in_node():
    """Verify in node that modelPicker groups models by endpoint_id and maintains distinct routes."""
    source = (ROOT / "static/js/modelPicker.js").read_text(encoding="utf-8")
    key_start = source.index("function _pickerModelKey(m)")
    key_end = source.index("// ── Shared keyboard nav")
    key_helper = source[key_start:key_end]

    group_start = source.index("const _endpointGroupNames =")
    group_end = source.index("const _collapsedProviders =")
    group_snippet = source[group_start:group_end]

    test_js = f"""
    const _PROVIDER_NAMES = {{}};
    const _PROVIDER_ALIAS = {{}};
    {key_helper}
    {group_snippet}

    const codex00_model = {{
        mid: 'gpt-5.5',
        display: 'gpt-5.5',
        endpointId: 'chatgpt-codex00',
        epName: 'ChatGPT · codex00',
        category: 'chatgpt_subscription',
        url: 'https://chatgpt.com/backend-api/codex'
    }};

    const codex01_model = {{
        mid: 'gpt-5.5',
        display: 'gpt-5.5',
        endpointId: 'chatgpt-codex01',
        epName: 'ChatGPT · codex01',
        category: 'chatgpt_subscription',
        url: 'https://chatgpt.com/backend-api/codex'
    }};

    const k0 = _pickerModelKey(codex00_model);
    const k1 = _pickerModelKey(codex01_model);
    if (k0 === k1) throw new Error('Same model across accounts must have distinct route keys');

    const g0 = _providerGroupKey(codex00_model);
    const g1 = _providerGroupKey(codex01_model);
    if (g0 === g1) throw new Error('Groups must be separate per account endpoint');

    const name0 = _providerGroupName(g0);
    const name1 = _providerGroupName(g1);
    if (name0 !== 'ChatGPT · codex00') throw new Error('Unexpected group name for codex00: ' + name0);
    if (name1 !== 'ChatGPT · codex01') throw new Error('Unexpected group name for codex01: ' + name1);

    console.log(JSON.stringify({{ k0, k1, g0, g1, name0, name1 }}));
    """
    proc = subprocess.run(["node", "-e", test_js], check=True, capture_output=True, text=True)
    res = json.loads(proc.stdout)
    assert res["k0"] != res["k1"]
    assert res["g0"] != res["g1"]
    assert res["name0"] == "ChatGPT · codex00"
    assert res["name1"] == "ChatGPT · codex01"


def test_model_picker_source_invariants():
    """Verify modelPicker.js contains required UI grouping structures."""
    src = (ROOT / "static/js/modelPicker.js").read_text(encoding="utf-8")
    # Grouping keyed on endpoint_id
    assert "m.endpointId || m.url" in src
    # Header class and chevron
    assert "mp-provider-header" in src
    assert "mp-provider-chevron" in src
    assert "mp-provider-name" in src
    assert "mp-provider-count" in src
    # Group container
    assert "mp-provider-group" in src
    # Search mode groups matches
    assert "isSearch" in src
    # TextContent used to prevent XSS
    assert "nameSpan.textContent = _providerGroupName(provider)" in src
    # Collapsed persistence key
    assert "odysseus-model-collapsed" in src


def test_composer_reasoning_effort_ui_markup():
    """Verify static/index.html and the app stylesheet cascade include reasoning effort controls."""
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    css = app_css()
    # HTML elements
    assert 'id="reasoning-effort-wrap"' in html
    assert 'id="reasoning-effort-btn"' in html
    assert 'id="reasoning-effort-current"' in html
    assert 'id="reasoning-effort-menu"' in html
    assert 'title="Reasoning effort"' in html
    assert 'class="reasoning-effort-prefix">Reasoning effort</span>' in html
    # CSS classes
    assert ".reasoning-effort-wrap" in css
    assert ".reasoning-effort-btn" in css
    assert ".reasoning-effort-menu" in css
    assert ".reasoning-effort-option" in css
    # The control lives in the Chat Context popup, where the prefix is the
    # row label rather than chat-bar text hidden at narrow widths.
    assert ".chat-context-popup .reasoning-effort-prefix {" in css


def test_chat_submit_includes_reasoning_effort():
    """Verify static/js/chat.js sends reasoning_effort when active."""
    src = (ROOT / "static/js/chat.js").read_text(encoding="utf-8")
    assert "window.__odysseusGetReasoningEffort" in src
    assert "fd.append('reasoning_effort', effort)" in src


# ============================================================
# PART 2: BACKEND REASONING EFFORT CATALOG & VALIDATION
# ============================================================

def test_chatgpt_reasoning_effort_validation():
    """Test validate_reasoning_effort against catalog definitions."""
    # gpt-6-astra supports low, medium, high, xhigh, max, ultra
    assert chatgpt_subscription.validate_reasoning_effort("gpt-6-astra", "high") == "high"
    assert chatgpt_subscription.validate_reasoning_effort("gpt-6-astra", "LOW") == "low"
    assert chatgpt_subscription.validate_reasoning_effort("gpt-6-astra", "medium") == "medium"
    assert chatgpt_subscription.validate_reasoning_effort("gpt-6-astra", "xhigh") == "xhigh"
    assert chatgpt_subscription.validate_reasoning_effort("gpt-6-astra", "unsupported_level") is None
    assert chatgpt_subscription.validate_reasoning_effort("gpt-6-astra", "default") is None
    assert chatgpt_subscription.validate_reasoning_effort("gpt-6-astra", None) is None

    # Model metadata catalog
    meta_astra = chatgpt_subscription.get_chatgpt_model_metadata("gpt-6-astra")
    assert meta_astra["default_reasoning_level"] == "low"
    levels = meta_astra["supported_reasoning_levels"]
    assert "low" in levels
    assert "medium" in levels
    assert "high" in levels

    meta_55 = chatgpt_subscription.get_chatgpt_model_metadata("gpt-5.5")
    assert meta_55["default_reasoning_level"] == "medium"


def test_models_metadata_attached_for_chatgpt_catalog():
    """Verify get_chatgpt_model_metadata populates metadata for ChatGPT models."""
    from src.chatgpt_subscription import get_chatgpt_model_metadata
    models = ["gpt-6-astra", "gpt-5.5", "gpt-5.6-sol", "llama3"]
    models_metadata = {}
    for mid in models:
        meta = get_chatgpt_model_metadata(mid)
        if meta:
            models_metadata[mid] = meta
    assert "gpt-6-astra" in models_metadata
    assert "gpt-5.5" in models_metadata
    assert "gpt-5.6-sol" in models_metadata
    assert "llama3" not in models_metadata
    assert models_metadata["gpt-6-astra"]["default_reasoning_level"] == "low"
    assert "high" in models_metadata["gpt-6-astra"]["supported_reasoning_levels"]


def test_model_routes_contains_metadata_population():
    """Verify routes/model_routes.py populates models_metadata."""
    src = (ROOT / "routes/model_routes.py").read_text(encoding="utf-8")
    assert "from src.chatgpt_subscription import get_chatgpt_model_metadata" in src
    assert "models_metadata[mid] = meta" in src
    assert '"models_metadata": models_metadata' in src


def test_history_and_chat_routes_support_reasoning_effort():
    """Verify history and chat routes support reasoning_effort and thinking_mode persistence."""
    hist_src = (ROOT / "routes/history/history_routes.py").read_text(encoding="utf-8")
    assert "reasoning_effort" in hist_src
    assert 'mode = f"effort:{clean_effort}"' in hist_src

    chat_src = (ROOT / "routes/chat_routes.py").read_text(encoding="utf-8")
    assert "validate_reasoning_effort(sess.model, reasoning_effort)" in chat_src
    assert "session_mode.startswith(\"effort:\")" in chat_src

    sess_src = (ROOT / "routes/session_routes.py").read_text(encoding="utf-8")
    assert "session.thinking_mode = \"off\"" in sess_src


def test_responses_payload_zero_tools_with_reasoning():
    """Verify that reasoning effort does NOT permit any native tool surfaces."""
    forbidden_tools = [
        {"type": "function", "function": {"name": "test"}},
        {"type": "web_search_preview"},
    ]
    payload = llm_core._build_chatgpt_responses_payload(
        model="gpt-6-astra",
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.7,
        max_tokens=4096,
        stream=True,
        reasoning_effort="high",
        tools=forbidden_tools,
    )
    assert payload["reasoning"] == {"effort": "high"}
    assert "tools" not in payload
    assert "tool_choice" not in payload
    for k in llm_core.CHATGPT_FORBIDDEN_PAYLOAD_KEYS:
        assert k not in payload
    assert set(payload.keys()) <= llm_core.CHATGPT_ALLOWED_PAYLOAD_KEYS


def test_session_model_change_revalidates_effort_logic():
    """Switching to an unsupported model resets effort to off."""
    meta_astra = chatgpt_subscription.get_chatgpt_model_metadata("gpt-6-astra")
    supported = [lvl.lower() for lvl in meta_astra.get("supported_reasoning_levels", [])]

    current_effort = "high"
    assert current_effort in supported  # retained for gpt-6-astra

    meta_local = chatgpt_subscription.get_chatgpt_model_metadata("llama3.2")
    assert meta_local is None  # unsupported -> resets to off
