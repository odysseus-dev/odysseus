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
