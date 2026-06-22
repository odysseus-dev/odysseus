from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_JS = (ROOT / "static/js/workspace.js").read_text(encoding="utf-8")
WORKSPACE_EDITOR_JS = (ROOT / "static/js/workspaceEditor.js").read_text(encoding="utf-8")
APP_JS = (ROOT / "static/app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "static/index.html").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "static/style.css").read_text(encoding="utf-8")
MOBILE_BACKEND = (ROOT / "android/app/src/main/java/com/odysseus/simplesignal/MobileBackendServer.java").read_text(encoding="utf-8")
MAIN_ACTIVITY = (ROOT / "android/app/src/main/java/com/odysseus/simplesignal/MainActivity.java").read_text(encoding="utf-8")
ANDROID_MANIFEST = (ROOT / "android/app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
ANDROID_BUILD_GRADLE = (ROOT / "android/app/build.gradle").read_text(encoding="utf-8")
ANDROID_PC_TOOLS_SCRIPT = (ROOT / "scripts/android-pc-tools.ps1").read_text(encoding="utf-8")
ANDROID_CONNECT_BAT = (ROOT / "connect-android-pc.bat").read_text(encoding="utf-8")
SHELL_ROUTES = (ROOT / "routes/shell_routes.py").read_text(encoding="utf-8")


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


def test_workspace_file_editor_loads_when_opened_from_picker():
    assert "deferLoad: true" not in WORKSPACE_JS
    assert "await editor.openWorkspaceEditor({ workspace: _curPath });" in WORKSPACE_JS
    assert "_hasLoadedDir" in WORKSPACE_EDITOR_JS
    assert "Press Reload to load files." in WORKSPACE_EDITOR_JS
    assert "No files or folders found." in WORKSPACE_EDITOR_JS


def test_android_chat_gets_workspace_tree_and_text_previews():
    assert "mobileWorkspaceTreeSummary(" in MOBILE_BACKEND
    assert "Small text file previews from the visible workspace" in MOBILE_BACKEND
    assert "mobileWorkspaceTextPreviews(" in MOBILE_BACKEND
    assert "mobileIsWorkspaceStatusRequest" in MOBILE_BACKEND


def test_android_has_adb_pc_tools_mode_for_full_desktop_tools():
    assert "ODYSSEUS_ADB_REVERSE_URL" in ANDROID_BUILD_GRADLE
    assert '"http://127.0.0.1:7000"' in ANDROID_BUILD_GRADLE
    assert "ADB PC Tools" in MAIN_ACTIVITY
    assert "startRemoteModeAt(BuildConfig.ODYSSEUS_ADB_REVERSE_URL)" in MAIN_ACTIVITY
    assert "same Agent tools, shell, files, MCP, Cookbook, and image tools" in MAIN_ACTIVITY
    assert "ADB reverse" in ANDROID_PC_TOOLS_SCRIPT
    assert "SetPcMode" in ANDROID_PC_TOOLS_SCRIPT


def test_android_option_screen_uses_product_cards_not_neon_terminal():
    assert "Color.rgb(34, 255, 34)" not in MAIN_ACTIVITY
    assert "createModeCard(" in MAIN_ACTIVITY
    assert "rounded(COLOR_PANEL" in MAIN_ACTIVITY
    assert "Choose how this phone connects." in MAIN_ACTIVITY


def test_android_connect_sidebar_item_opens_native_connection_screen():
    assert 'id="tool-connect-btn"' in INDEX_HTML
    assert 'id="rail-connect"' in INDEX_HTML
    assert '<span class="grow">Connect</span>' in INDEX_HTML
    assert '<span class="vis-label">Connect</span>' in INDEX_HTML
    assert 'data-ui-key="tool-connect"' in INDEX_HTML
    assert "'tool-connect':        '#tool-connect-btn, #rail-connect'" in APP_JS
    assert "'rail-connect':   'tool-connect-btn'" in APP_JS
    assert "openAndroidConnectionMode" in APP_JS
    assert "bridge.showConnectionMode()" in APP_JS
    assert "return openPcAndroidConnectModal()" in APP_JS
    assert "android-connect-modal" in APP_JS
    assert "/api/android/adb-pc/connect" in APP_JS
    assert "ODYSSEUS_NO_PAUSE" in ANDROID_CONNECT_BAT
    assert '@router.post("/api/android/adb-pc/connect")' in SHELL_ROUTES
    assert "connect-android-pc.bat" in SHELL_ROUTES
    assert "ODYSSEUS_NO_PAUSE" in SHELL_ROUTES
    assert "public void showConnectionMode()" in MAIN_ACTIVITY
    assert "showModeChooser(true)" in MAIN_ACTIVITY
    assert "Cancel returns to the current connection." in MAIN_ACTIVITY


def test_sidebar_and_collapsed_tool_menus_are_alphabetical():
    sidebar_ids = [
        "tool-memory-btn",    # Brain
        "tool-calendar-btn",
        "tool-compare-btn",
        "tool-connect-btn",
        "tool-cookbook-btn",
        "tool-research-btn",  # Deep Research
        "tool-gallery-btn",
        "tool-library-btn",
        "tool-notes-btn",
        "tool-tasks-btn",
        "tool-theme-btn",
    ]
    rail_ids = [
        "rail-memory",        # Brain
        "rail-calendar",
        "rail-compare",
        "rail-connect",
        "rail-cookbook",
        "rail-research",      # Deep Research
        "rail-email",
        "rail-gallery",
        "rail-archive",       # Library
        "rail-notes",
        "rail-tasks",
        "rail-theme",
    ]

    assert [INDEX_HTML.index(f'id="{item_id}"') for item_id in sidebar_ids] == sorted(
        INDEX_HTML.index(f'id="{item_id}"') for item_id in sidebar_ids
    )
    assert [INDEX_HTML.index(f'id="{item_id}"') for item_id in rail_ids] == sorted(
        INDEX_HTML.index(f'id="{item_id}"') for item_id in rail_ids
    )
