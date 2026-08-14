"""Security and determinism contract for the explicit Bash-script runner."""

from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]


def test_bash_runner_sends_exact_script_and_selected_workspace():
    source = (_ROOT / "static" / "js" / "codeRunner.js").read_text(encoding="utf-8")

    assert "var command = code;" in source
    assert "workspaceModule?.getWorkspace?.()" in source
    assert "legacy_tmux_compat: false" in source
    assert "timeout: 120" in source
    assert "subprocess.run(['bash','-c'" not in source


def test_bash_script_entry_point_reuses_document_editor():
    document_source = (_ROOT / "static" / "js" / "document.js").read_text(
        encoding="utf-8"
    )
    app_source = (_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    html = (_ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert "export async function newBashDocument()" in document_source
    assert "documentModule.newBashDocument()" in app_source
    assert 'id="bash-script-btn"' in html
    assert "not sandboxed" in html


def test_code_run_cannot_fall_through_to_hidden_email_send():
    source = (_ROOT / "static" / "js" / "document.js").read_text(encoding="utf-8")

    assert "document.getElementById('doc-header-preview-btn')?.click();" not in source
    assert "el.getClientRects().length === 0" in source
    assert "if (rect.width <= 0 || rect.height <= 0) return false;" in source
    assert "doc.language === 'email' && liveLanguage === 'email'" in source


def test_bash_gui_module_urls_are_cache_busted_consistently():
    app = (_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    document_source = (_ROOT / "static" / "js" / "document.js").read_text(
        encoding="utf-8"
    )
    html = (_ROOT / "static" / "index.html").read_text(encoding="utf-8")

    version = "v=20260813bashrun2"
    assert f"./js/keyboard-shortcuts.js?{version}" in app
    assert f"./js/document.js?{version}" in app
    assert f"./js/chat.js?{version}" in app
    assert f"./codeRunner.js?{version}" in document_source
    assert f'/static/app.js?{version}' in html
    assert f'/static/js/document.js?{version}' in html
    assert f'/static/js/codeRunner.js?{version}' in html
    service_worker = (_ROOT / "static" / "sw.js").read_text(encoding="utf-8")
    assert "odysseus-v377-bash-runner-input" in service_worker
