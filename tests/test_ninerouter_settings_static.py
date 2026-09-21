"""Static regressions for Settings 9router connections card.

Agents: this card is the product connect surface. It must talk to
``/api/ninerouter/connections``, never iframe 9router, and never POST
``/api/model-endpoints`` for cloud connect.
"""

from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent
_INDEX = (_REPO / "static" / "index.html").read_text(encoding="utf-8")
_ADMIN = (_REPO / "static" / "js" / "admin.js").read_text(encoding="utf-8")


_SETTINGS = (_REPO / "static" / "js" / "settings.js").read_text(encoding="utf-8")
_REGISTRY = (_REPO / "static" / "js" / "settings" / "registry.js").read_text(
    encoding="utf-8"
)
_SLASH = (_REPO / "static" / "js" / "slashCommands.js").read_text(encoding="utf-8")


def test_settings_card_is_ninerouter_connections():
    assert "9router connections" in _INDEX
    assert "Add API Models" not in _INDEX
    assert 'id="adm-ninerouter-connections"' in _INDEX
    assert 'id="adm-nrApiKey"' in _INDEX


def test_admin_loads_ninerouter_catalog_not_model_endpoint_keys():
    assert "/api/ninerouter/connections" in _ADMIN
    assert "adm-ninerouter-connections" in _ADMIN


def test_slice_a_copy_names_leftovers_vs_overlay_9router():
    """User-facing copy must not imply leftover pages own overlay chat."""
    assert "Local leftover" in _INDEX
    assert "ChatGPT / Copilot belong here, not on Integrations" in _INDEX
    assert "Fine control for overlay chat" in _INDEX
    assert "plugins that install Odysseus" in _INDEX
    assert "Slice C" not in _INDEX
    assert "Add a local model server (Ollama, llama.cpp, vLLM)." not in _INDEX
    assert "All external service connections in one place." not in _INDEX


def test_slice_a_integrations_are_inbound_cli_plugins():
    assert "Claude Code plugin (calls Odysseus)" in _SETTINGS
    assert "Codex CLI plugin (calls Odysseus)" in _SETTINGS
    assert "Does not add Anthropic as an inference provider." in _SETTINGS
    assert "Does not add OpenAI as an inference provider." in _SETTINGS
    assert "['claude', 'Claude Agent']" not in _SETTINGS
    assert "['codex', 'Codex Agent']" not in _SETTINGS


def test_slice_a_registry_and_walkthrough_find_9router():
    assert "'9router'" in _REGISTRY
    assert "inbound" in _REGISTRY
    assert "overlay 9router connections" in _SLASH
    assert "inbound CLI plugins that call Odysseus" in _SLASH


def _services_panel() -> str:
    start = _INDEX.index('data-settings-panel="services"')
    rest = _INDEX[start + 1 :]
    nxt = rest.find("data-settings-panel=")
    return _INDEX[start : start + 1 + nxt] if nxt >= 0 else _INDEX[start:]


def test_slice_b_nav_is_one_inference_area():
    """Phone Settings must not present Add Models + Added Models as peers."""
    assert 'data-settings-tab="services"' in _INDEX
    assert ">Inference</span>" in _INDEX
    assert 'data-settings-tab="added-models"' not in _INDEX
    assert 'data-settings-panel="added-models"' not in _INDEX
    assert 'id="added-models"' not in _INDEX
    assert _REGISTRY.count("id: 'added-models'") == 0
    assert "label: 'Inference'" in _REGISTRY


def test_slice_b_inference_hierarchy_is_connections_then_routes():
    block = _services_panel()
    order = [
        "9router connections",
        'id="adm-ninerouter-routes"',
        'id="adm-openhands-runtime"',
        'id="adm-chat-agent-privilege"',
        "Add Local Models",
        'id="adm-epList-local"',
    ]
    positions = [block.index(token) for token in order]
    assert positions == sorted(positions)
    for route in ("automatic", "fast", "balanced", "best"):
        assert f'data-nr-route="{route}"' in block
    assert "Native" in block and "OpenCode" in block
    assert "Agent" in block and "Chat" in block
    assert 'id="adm-epList-api"' not in _INDEX


def test_slice_b_integrations_group_inbound_plugins():
    assert "CLI plugins (call Odysseus)" in _SETTINGS
    assert "intg-group-inbound" in _SETTINGS


def test_slice_c_default_chat_is_overlay_route_select():
    """AI Defaults Default Chat Model writes 9router routes, not endpoints."""
    assert 'id="set-defaultRouteSelect"' in _INDEX
    assert 'id="set-defaultEpSelect"' not in _INDEX
    assert 'id="set-defaultModelSelect"' not in _INDEX
    assert "set-defaultRouteSelect" in _SETTINGS
    assert "default_model: routeSel.value" in _SETTINGS
    assert "Leftover Odysseus endpoint + model pick" not in _INDEX
    assert "fine control for overlay chat" in _INDEX.lower() or "9router route" in _INDEX
    assert "Prefer a local leftover endpoint" in _INDEX or "local leftover" in _INDEX.lower()


def test_slice_c_composer_default_uses_saved_route():
    picker = (_REPO / "static" / "js" / "modelPicker.js").read_text(encoding="utf-8")
    app = (_REPO / "static" / "app.js").read_text(encoding="utf-8")
    assert "/api/default-chat" in picker
    assert "d.endpoint_url && d.model" not in app
    assert "d && d.model" in app


def test_slice_d_setup_cloud_keys_go_to_ninerouter_not_model_endpoints():
    fn = _SLASH.split("async function connectDetectedSetupEndpoint", 1)[1].split(
        "\nasync function ", 1
    )[0]
    assert "/api/ninerouter/connections" in fn
    assert "fd.append('provider'" in fn
    assert "fd.append('api_key'" in fn
    assert fn.index("/api/ninerouter/connections") < fn.index("/api/model-endpoints")

