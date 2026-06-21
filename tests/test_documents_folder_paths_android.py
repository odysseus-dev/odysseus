from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC_LIBRARY = (ROOT / "static" / "js" / "documentLibrary.js").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
PERSONAL_ROUTES = (ROOT / "routes" / "personal_routes.py").read_text(encoding="utf-8")
ANDROID_SERVER = (
    ROOT
    / "android"
    / "app"
    / "src"
    / "main"
    / "java"
    / "com"
    / "odysseus"
    / "simplesignal"
    / "MobileBackendServer.java"
).read_text(encoding="utf-8")


def test_documents_library_exposes_folder_paths_drawer():
    assert 'id="doclib-folders-btn"' in DOC_LIBRARY
    assert 'id="doclib-folder-panel"' in DOC_LIBRARY
    assert "libraryFetchFolders" in DOC_LIBRARY
    assert "/api/personal/add_directory" in DOC_LIBRARY
    assert "/api/personal/remove_directory?directory=" in DOC_LIBRARY
    assert "data.base_directory || data.allowed_directory_root" in DOC_LIBRARY


def test_documents_library_mobile_tabs_hide_inactive_panels():
    assert "p.classList.toggle('active', active);" in DOC_LIBRARY
    assert "p.hidden = !active;" in DOC_LIBRARY
    assert "#doclib-modal [data-doclib-panel]:not(.active)" in STYLE_CSS
    assert "display: none !important;" in STYLE_CSS
    assert "#doclib-modal [data-doclib-panel].active" in STYLE_CSS


def test_personal_list_returns_folder_root_metadata():
    assert '"base_directory": base_directory' in PERSONAL_ROUTES
    assert '"allowed_directory_root": PERSONAL_DIR' in PERSONAL_ROUTES
    assert '"rag_available": bool(_rag())' in PERSONAL_ROUTES


def test_android_documents_library_route_precedes_session_route():
    exact = ANDROID_SERVER.index('"/api/documents/library".equals(path)')
    generic = ANDROID_SERVER.index('path.startsWith("/api/documents/")')
    assert exact < generic
    assert "private JSONObject documentsLibrary(Request request)" in ANDROID_SERVER
    assert '.put("documents", out)' in ANDROID_SERVER
    assert '.put("languages", languages)' in ANDROID_SERVER


def test_android_personal_and_mcp_routes_are_not_empty_stubs():
    assert "private void routePersonal(Request request, OutputStream out, String tail)" in ANDROID_SERVER
    assert "PREF_PERSONAL_DIRECTORIES" in ANDROID_SERVER
    assert '"/api/mcp/servers/android_rag/tools".equals(path)' in ANDROID_SERVER
    assert "private JSONArray mobileMcpServers()" in ANDROID_SERVER
    assert "private JSONArray mobileMcpTools()" in ANDROID_SERVER
    assert "mobilePersonalContextForPrompt(userText)" in ANDROID_SERVER
    assert "Saved Folders/RAG entries:" in ANDROID_SERVER
    assert 'new JSONObject().put("servers", new JSONArray())' not in ANDROID_SERVER
