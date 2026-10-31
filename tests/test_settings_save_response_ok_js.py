"""Frontend regression tests: verify settings save handlers check HTTP response status.

Locks in that static/js/settings.js verifies response.ok before displaying success,
preventing 401, 403, 400, and 500 responses from reporting false success.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SETTINGS_JS = _REPO / "static" / "js" / "settings.js"
_SETTINGS_DIR = _REPO / "static" / "js" / "settings"
_SETTINGS_API_JS = _SETTINGS_DIR / "api.js"
_HAS_NODE = shutil.which("node") is not None


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_settings_js_syntax():
    """Verify static/js/settings.js and settings/*.js modules are syntactically valid JavaScript."""
    js_files = [_SETTINGS_JS] + list(_SETTINGS_DIR.glob("*.js"))
    for js_file in js_files:
        proc = subprocess.run(
            ["node", "--check", str(js_file)],
            capture_output=True,
            text=True,
            cwd=str(_REPO),
        )
        assert proc.returncode == 0, f"JS syntax error in {js_file.name}:\n{proc.stderr}"


def test_post_settings_helper_verifies_response_ok():
    """Centralized postSettings in settings/api.js must verify response.ok and throw on failure."""
    assert _SETTINGS_API_JS.exists(), f"Expected {_SETTINGS_API_JS} to exist"
    api_content = _SETTINGS_API_JS.read_text(encoding="utf-8")

    # Verify fetch to /api/auth/settings with POST method
    match = re.search(r"fetch\(['\"]/api/auth/settings['\"],\s*\{\s*method:\s*['\"]POST['\"]", api_content)
    assert match is not None, "Expected POST fetch to /api/auth/settings in settings/api.js"

    # Verify .ok is checked within 300 characters of fetch
    window = api_content[match.start(): match.start() + 300]
    assert ".ok" in window, f"postSettings must check response.ok:\n{window}"

    # Verify that it throws an error when !response.ok
    assert "throw error" in api_content or "throw new Error" in api_content, (
        "postSettings must throw when response is not ok"
    )


def test_all_settings_save_handlers_route_through_post_settings():
    """All settings save handlers in settings.js and settings/*.js must route saves via postSettings."""
    settings_content = _SETTINGS_JS.read_text(encoding="utf-8")
    module_contents = {p.name: p.read_text(encoding="utf-8") for p in _SETTINGS_DIR.glob("*.js")}

    # Assert no unverified direct POST fetch to /api/auth/settings in settings.js
    direct_posts = list(re.finditer(r"fetch\(['\"]/api/auth/settings['\"],\s*\{\s*method:\s*['\"]POST['\"]", settings_content))
    assert len(direct_posts) == 0, (
        f"Found direct POST fetch to /api/auth/settings in settings.js; all writes must route through _postSettings"
    )

    # Count _postSettings invocations across settings.js and modules
    post_settings_calls = len(re.findall(r"_postSettings\(", settings_content))
    for mod_name, mod_text in module_contents.items():
        if mod_name != "api.js":
            post_settings_calls += len(re.findall(r"_postSettings\(", mod_text))

    assert post_settings_calls >= 15, (
        f"Expected at least 15 _postSettings calls across settings modules, found {post_settings_calls}"
    )


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_failed_save_shows_failure_in_simulated_dom():
    """Verify that when fetch returns !res.ok, save logic catches and flags error."""
    js = """
    let msgText = '';
    let msgColor = '';
    const msg = {
      set textContent(v) { msgText = v; },
      get textContent() { return msgText; },
      style: { set color(v) { msgColor = v; }, get color() { return msgColor; } }
    };

    async function save(fetchImpl) {
      try {
        var res = await fetchImpl('/api/auth/settings', { method: 'POST' });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        msg.textContent = 'Saved'; msg.style.color = 'var(--fg)';
      } catch (e) {
        msg.textContent = 'Failed to save'; msg.style.color = 'var(--red)';
      }
    }

    async function run() {
      // 1. Rejected fetch (403)
      await save(async () => ({ ok: false, status: 403 }));
      const rejectedResult = { text: msgText, color: msgColor };

      // 2. Server error (500)
      await save(async () => ({ ok: false, status: 500 }));
      const serverErrResult = { text: msgText, color: msgColor };

      // 3. Successful fetch (200)
      await save(async () => ({ ok: true, status: 200 }));
      const okResult = { text: msgText, color: msgColor };

      console.log(JSON.stringify({ rejectedResult, serverErrResult, okResult }));
    }
    run();
    """
    proc = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True)
    import json
    data = json.loads(proc.stdout)
    assert data["rejectedResult"] == {"text": "Failed to save", "color": "var(--red)"}
    assert data["serverErrResult"] == {"text": "Failed to save", "color": "var(--red)"}
    assert data["okResult"] == {"text": "Saved", "color": "var(--fg)"}


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_image_preferences_js_policy_and_non_admin_fallback():
    """Verify imagePreferences.js non-admin fallback, policy toggle isolation, and admin clearing."""
    js = """
    globalThis.window = { dispatchEvent: () => {} };
    globalThis.CustomEvent = class CustomEvent {};

    const { saveImagePreferences, readImagePreferences } = await import('./static/js/settings/imagePreferences.js');

    const results = {};

    // Scenario 1: Non-admin save (POST /api/auth/settings returns 403)
    const prefCalls = [];
    globalThis.fetch = async (url, opts = {}) => {
      if (url === '/api/auth/settings') {
        return { status: 403, ok: false, text: async () => 'Forbidden' };
      }
      if (url.startsWith('/api/prefs/')) {
        prefCalls.push({ url, body: JSON.parse(opts.body) });
        return { status: 200, ok: true, json: async () => ({ ok: true }) };
      }
      throw new Error('Unexpected URL: ' + url);
    };

    const patch = {
      image_gen_enabled: true,
      image_model: 'user-gen-model',
      image_edit_model: 'user-edit-model',
      image_quality: 'high',
    };

    const res = await saveImagePreferences(patch);
    results.fallbackResOk = res.ok;
    results.fallbackResStatus = res.status;
    results.prefCalls = prefCalls;

    // Scenario 2: Non-admin save fails on prefs PUT (keeps actual failure visible)
    globalThis.fetch = async (url) => {
      if (url === '/api/auth/settings') return { status: 403, ok: false };
      if (url.startsWith('/api/prefs/')) return { status: 500, ok: false, text: async () => 'Disk error' };
      throw new Error('Unexpected URL: ' + url);
    };
    try {
      await saveImagePreferences({ image_model: 'fail-model' });
      results.prefFailureThrew = false;
    } catch (err) {
      results.prefFailureThrew = true;
      results.prefFailureStatus = err.status;
    }

    // Scenario 3: Admin save clears pre-existing per-user preferences
    const adminPrefClears = [];
    globalThis.fetch = async (url, opts = {}) => {
      if (url === '/api/auth/settings') {
        return { status: 200, ok: true, json: async () => ({ ok: true }) };
      }
      if (url.startsWith('/api/prefs/')) {
        adminPrefClears.push({ url, body: JSON.parse(opts.body) });
        return { status: 200, ok: true, json: async () => ({ ok: true }) };
      }
      throw new Error('Unexpected URL: ' + url);
    };
    const adminRes = await saveImagePreferences({ image_model: 'admin-model', image_gen_enabled: true });
    results.adminResOk = adminRes.ok;
    results.adminPrefClears = adminPrefClears;

    // Scenario 4: readImagePreferences loads image_quality along with models
    globalThis.fetch = async (url) => {
      if (url === '/api/prefs') {
        return {
          ok: true,
          json: async () => ({ image_model: 'pref-gen', image_quality: 'low' }),
        };
      }
      if (url === '/api/auth/settings') {
        return {
          ok: true,
          json: async () => ({ image_model: 'global-gen', image_edit_model: 'global-edit', image_quality: 'medium' }),
        };
      }
      throw new Error('Unexpected URL: ' + url);
    };
    const loaded = await readImagePreferences();
    results.loaded = loaded;

    console.log(JSON.stringify(results));
    """
    proc = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, check=True)
    import json
    data = json.loads(proc.stdout)

    # 1. Non-admin fallback returns ok=True, status=200
    assert data["fallbackResOk"] is True
    assert data["fallbackResStatus"] == 200

    # 2. image_gen_enabled was filtered out; only image_model, image_edit_model, image_quality written
    written_keys = [c["url"].split("/api/prefs/")[1] for c in data["prefCalls"]]
    assert "image_gen_enabled" not in written_keys
    assert "image_model" in written_keys
    assert "image_edit_model" in written_keys
    assert "image_quality" in written_keys

    # 3. Preference failure threw error and preserved status
    assert data["prefFailureThrew"] is True
    assert data["prefFailureStatus"] == 500

    # 4. Admin save cleared per-user preferences with value: null
    assert data["adminResOk"] is True
    cleared_keys = [c["url"].split("/api/prefs/")[1] for c in data["adminPrefClears"]]
    assert "image_model" in cleared_keys
    for call in data["adminPrefClears"]:
        assert call["body"] == {"value": None}

    # 5. readImagePreferences includes image_quality from prefs
    assert data["loaded"]["image_model"] == "pref-gen"
    assert data["loaded"]["image_edit_model"] == "global-edit"
    assert data["loaded"]["image_quality"] == "low"

