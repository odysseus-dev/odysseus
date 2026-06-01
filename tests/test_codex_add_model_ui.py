from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_codex_add_models_card_has_add_button():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="adm-codexAddModelBtn"' in html
    assert 'id="adm-codexModelDropdownBtn"' in html
    assert '>select models</span>' in html
    assert 'class="admin-model-form-row adm-codex-add-row"' in html
    assert 'id="adm-codexModelPanel" class="mcp-tools-panel adm-codex-model-panel hidden"' in html
    assert 'id="adm-codexProviderModelList" class="mcp-tools-list adm-codex-model-list"' in html
    assert 'id="adm-codexSelectAll"' in html
    assert 'id="adm-codexSelectNone"' in html


def test_codex_add_button_calls_backend_and_selects_model():
    js = (ROOT / "static" / "js" / "admin.js").read_text(encoding="utf-8")
    assert "const codexModelDropdownBtn = el('adm-codexModelDropdownBtn')" in js
    assert "codexModelDropdownLabel.textContent" not in js
    assert "_syncCodexDropdownLabel" not in js
    assert "codexModelPanel.classList.toggle('hidden')" in js
    assert "querySelectorAll('input[type=checkbox]:checked')" in js
    assert "body: JSON.stringify({ models: selectedModels })" in js
    assert "'/api/codex-model-provider/add-model'" in js
    assert "_selectAddedModelInChat(data.endpoint" in js


def test_codex_add_model_backend_route_exists():
    py = (ROOT / "routes" / "codex_model_provider_routes.py").read_text(encoding="utf-8")
    assert '@router.post("/add-model")' in py
    assert 'default_endpoint_id' in py
    assert 'ModelEndpoint' in py
    assert 'cached_models' in py
    assert 'hidden_models' in py
    assert 'CODEX_PROVIDER_ENDPOINT_ID' in py
