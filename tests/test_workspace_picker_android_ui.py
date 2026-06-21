from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_JS = (ROOT / "static/js/workspace.js").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "static/style.css").read_text(encoding="utf-8")
MOBILE_BACKEND = (ROOT / "android/app/src/main/java/com/odysseus/simplesignal/MobileBackendServer.java").read_text(encoding="utf-8")
ANDROID_MANIFEST = (ROOT / "android/app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")


def test_workspace_picker_renders_browse_failures_inside_modal():
    assert "function _renderError" in WORKSPACE_JS
    assert "_renderError(message, { retryOpen: true })" in WORKSPACE_JS
    assert "_renderError(message, { retryPath: targetPath })" in WORKSPACE_JS
    assert "_updateActionButtons(false, message)" in WORKSPACE_JS
    assert "Workspace browsing is not available on this backend" in WORKSPACE_JS
    assert "Old private workspace cleared" in WORKSPACE_JS
    assert "Number(err && err.status ? err.status : 0) === 410" in WORKSPACE_JS


def test_workspace_picker_keeps_older_browse_backend_compatible():
    assert "Older PC backends did not have shortcut roots" in WORKSPACE_JS
    assert "_roots = { default_path: '', roots: [] }" in WORKSPACE_JS
    assert "e.status === 401 || e.status === 403 || e.timeout || !e.status" in WORKSPACE_JS


def test_workspace_picker_has_android_friendly_retry_and_submit_affordances():
    assert "addEventListener('change'" in WORKSPACE_JS
    assert ".workspace-retry-btn" in STYLE_CSS
    assert "flex-wrap: wrap;" in STYLE_CSS
    assert 'path.equals("/api/workspace") || path.startsWith("/api/workspace/")' in MOBILE_BACKEND


def test_android_standalone_has_local_workspace_routes():
    assert "routeWorkspace(request, out" in MOBILE_BACKEND
    assert "mobileWorkspaceRoots()" in MOBILE_BACKEND
    assert "mobilePublicWorkspaceDir(Environment.DIRECTORY_DOCUMENTS)" in MOBILE_BACKEND
    assert 'mobileWorkspaceRootEntry("workspace", "App Workspace"' in MOBILE_BACKEND
    assert 'mobileWorkspaceChildDir("Documents")' not in MOBILE_BACKEND
    assert 'mobileWorkspaceChildDir("Downloads")' not in MOBILE_BACKEND
    assert "mobileIsDeprecatedPrivateWorkspaceFolder" in MOBILE_BACKEND
    assert "MOBILE_PUBLIC_WORKSPACE_ACCESS_DETAIL" in MOBILE_BACKEND
    assert "android.permission.MANAGE_EXTERNAL_STORAGE" in ANDROID_MANIFEST
    assert "mobileWorkspaceBrowse(" in MOBILE_BACKEND
    assert "mobileWorkspaceListFiles(" in MOBILE_BACKEND
    assert "mobileWorkspaceWriteFile(" in MOBILE_BACKEND
    assert "Workspace selection needs the PC backend on Android" not in MOBILE_BACKEND
    assert "local to this Android device" in WORKSPACE_JS


def test_android_chat_distinguishes_workspace_from_saved_folders():
    assert "tryHandleMobileWorkspaceRequest(userText, activeWorkspace, workspaceRejected)" in MOBILE_BACKEND
    assert "workspaceRejectedEvent(workspaceRejected)" in MOBILE_BACKEND
    assert "Saved Folders/RAG entries are separate from Workspace" in MOBILE_BACKEND
    assert "They may include PC paths such as D:/" in MOBILE_BACKEND
    assert "not the active Android workspace" in MOBILE_BACKEND
    assert "Do not say Android standalone needs Connect to PC for the active Android workspace" in MOBILE_BACKEND
