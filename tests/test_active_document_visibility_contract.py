from pathlib import Path
import re
from tests.helpers.document_source import document_source, function_body
from tests.helpers.js_modules import email_library_paths


ROOT = Path(__file__).resolve().parents[1]
DOCUMENT_JS = document_source()
CHAT_JS = (ROOT / "static/js/chat.js").read_text(encoding="utf-8")
APP_JS = (ROOT / "static/app.js").read_text(encoding="utf-8")
# The writing-style panel moved into static/js/settings/writingStyle.js; read
# the whole settings surface so this pins behaviour rather than a filename.
SETTINGS_JS = "\n".join(
    p.read_text(encoding="utf-8")
    for p in [ROOT / "static/js/settings.js", *sorted((ROOT / "static/js/settings").glob("*.js"))]
)
INDEX_HTML = (ROOT / "static/index.html").read_text(encoding="utf-8")
CHAT_ROUTE = (ROOT / "routes/chat_routes.py").read_text(encoding="utf-8")


def test_visible_or_minimized_linked_document_is_sent_as_chat_context():
    function = function_body("getChatDocumentId")
    assert "pane?.isConnected" in function
    assert "document.body.classList.contains('doc-view')" not in function
    assert "style?.display !== 'none'" in function
    assert "style?.visibility !== 'hidden'" in function
    assert "const id = visiblyOpen ? activeDocId : minimizedId" in function
    assert "_minimizedDocId" in function


def test_browser_explicitly_reports_absent_document_context():
    assert "? (documentModule?.isPanelOpen?.() ? 'visible' : 'minimized')" in CHAT_JS
    assert ": 'none'" in CHAT_JS


def test_chat_and_app_share_one_document_module_instance():
    chat_version = re.search(r"from './document\.js\?v=([^']+)'", CHAT_JS).group(1)
    app_version = re.search(r"from './js/document\.js\?v=([^']+)'", APP_JS).group(1)
    assert chat_version == app_version


def test_all_runtime_document_imports_share_one_module_url():
    runtime_files = [
        ROOT / "static/app.js",
        ROOT / "static/index.html",
        ROOT / "static/sw.js",
        ROOT / "static/js/chat.js",
        ROOT / "static/js/chatStream.js",
        ROOT / "static/js/chatRenderer.js",
        ROOT / "static/js/slashCommands.js",
        *email_library_paths(include_wrapper=True),
    ]
    versions = {
        match
        for path in runtime_files
        for match in re.findall(r"document\.js\?v=([A-Za-z0-9_-]+)", path.read_text(encoding="utf-8"))
    }
    assert versions == {"20260916docctx2"}


def test_server_fallback_is_legacy_only_when_ui_state_is_absent():
    assert "legacy_active_doc_fallback = not active_doc_state" in CHAT_ROUTE
    assert CHAT_ROUTE.count("if not active_doc and legacy_active_doc_fallback:") == 3


def test_document_writing_action_uses_document_style_not_email_style():
    assert "const generalStyle = String(generalData.document_writing_style || '').trim()" in DOCUMENT_JS
    assert "emailResponse = await fetch(`/api/email/style${suffix}`" in DOCUMENT_JS
    assert "GENERAL WRITING STYLE:" in DOCUMENT_JS
    assert "EMAIL CONVENTIONS:" in DOCUMENT_JS
    assert 'id="set-document-style"' in INDEX_HTML
    assert 'id="set-document-style-extract"' in INDEX_HTML
    assert "'/api/auth/settings/document-style/extract'" in SETTINGS_JS
    assert "spinner.createWhirlpool(14)" in SETTINGS_JS
    assert 'id="set-document-style-save"' in INDEX_HTML
    assert 'M17 21v-8H7v8' in INDEX_HTML
    assert "document_writing_style: styleEl.value" in SETTINGS_JS
