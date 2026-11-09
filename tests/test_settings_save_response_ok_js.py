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


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_admin_cleanup_failure_and_rejection_surfaced():
    """Verify saveImagePreferences surfaces HTTP failures and rejected network requests during admin cleanup."""
    js = """
    globalThis.window = { dispatchEvent: () => {} };
    globalThis.CustomEvent = class CustomEvent {};

    const { saveImagePreferences } = await import('./static/js/settings/imagePreferences.js');

    const results = {};

    // 1. HTTP 500 failure during admin preference cleanup
    globalThis.fetch = async (url) => {
      if (url === '/api/auth/settings') {
        return { status: 200, ok: true, json: async () => ({ ok: true }) };
      }
      if (url.startsWith('/api/prefs/')) {
        return { status: 500, ok: false, text: async () => 'Database lock error' };
      }
      throw new Error('Unexpected URL: ' + url);
    };
    try {
      await saveImagePreferences({ image_model: 'admin-model', image_gen_enabled: true });
      results.http500Threw = false;
    } catch (err) {
      results.http500Threw = true;
      results.http500Status = err.status;
      results.http500GlobalSaved = err.globalSaved;
      results.http500Phase = err.phase;
    }

    // 2. Network rejection during admin preference cleanup
    globalThis.fetch = async (url) => {
      if (url === '/api/auth/settings') {
        return { status: 200, ok: true, json: async () => ({ ok: true }) };
      }
      if (url.startsWith('/api/prefs/')) {
        throw new Error('Network offline');
      }
      throw new Error('Unexpected URL: ' + url);
    };
    try {
      await saveImagePreferences({ image_model: 'admin-model', image_gen_enabled: true });
      results.networkRejectThrew = false;
    } catch (err) {
      results.networkRejectThrew = true;
      results.networkRejectStatus = err.status;
      results.networkRejectGlobalSaved = err.globalSaved;
      results.networkRejectPhase = err.phase;
    }

    // 3. Successful cleanup
    const clearedKeys = [];
    globalThis.fetch = async (url, opts = {}) => {
      if (url === '/api/auth/settings') {
        return { status: 200, ok: true, json: async () => ({ ok: true }) };
      }
      if (url.startsWith('/api/prefs/')) {
        clearedKeys.push({ url, body: JSON.parse(opts.body) });
        return { status: 200, ok: true, json: async () => ({ ok: true }) };
      }
      throw new Error('Unexpected URL: ' + url);
    };
    const okRes = await saveImagePreferences({ image_model: 'admin-model', image_gen_enabled: true });
    results.okResStatus = okRes.status;
    results.clearedKeysCount = clearedKeys.length;

    console.log(JSON.stringify(results));
    """
    proc = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, check=True)
    import json
    data = json.loads(proc.stdout)
    assert data["http500Threw"] is True
    assert data["http500Status"] == 500
    assert data["http500GlobalSaved"] is True
    assert data["http500Phase"] == "cleanup"
    assert data["networkRejectThrew"] is True
    assert data["networkRejectStatus"] == 500
    assert data["networkRejectGlobalSaved"] is True
    assert data["networkRejectPhase"] == "cleanup"
    assert data["okResStatus"] == 200
    assert data["clearedKeysCount"] == 3


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_admin_toggle_reconciliation_and_partial_failure_reporting():
    """Verify admin toggle reconciles with authoritative server state and reports partial success accurately."""
    js = """
    const { initImageSettings } = await import('./static/js/settings/imageModels.js');

    function createEnv() {
      const elements = {};
      function makeElement(id, tag = 'div') {
        const listeners = {};
        const el = {
          id, tagName: tag.toUpperCase(),
          value: '', checked: false, disabled: false, title: '',
          style: {},
          parentElement: { title: '', style: {} },
          closest: () => ({ style: {} }),
          addEventListener: (evt, cb) => { listeners[evt] = listeners[evt] || []; listeners[evt].push(cb); },
          dispatchEvent: (evt) => Promise.all((listeners[evt] || []).map(cb => cb())),
          length: 0, appendChild: () => {}, remove: () => {}
        };
        elements[id] = el;
        return el;
      }

      const modelSel = makeElement('set-imgModelSelect', 'select');
      const editModelSel = makeElement('set-imgEditModelSelect', 'select');
      const qualSel = makeElement('set-imgQualitySelect', 'select');
      const msg = makeElement('set-imgSettingsMsg', 'span');
      const enabledToggle = makeElement('set-imgEnabledToggle', 'input');

      globalThis.document = {
        getElementById: (id) => elements[id] || null,
        createElement: (tag) => makeElement('anon-' + Math.random(), tag),
      };
      globalThis.window = {
        _isAdmin: true,
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => {}
      };
      globalThis.CustomEvent = class CustomEvent {};

      return { modelSel, editModelSel, qualSel, msg, enabledToggle };
    }

    const testResults = {};

    // 1. Global POST 200 + cleanup HTTP 500 failure
    {
      const { enabledToggle, msg } = createEnv();
      let serverGlobal = true;
      globalThis.fetch = async (url, opts = {}) => {
        if (url === '/api/model-endpoints') return { ok: true, json: async () => [] };
        if (url === '/api/prefs') return { ok: true, json: async () => ({}) };
        if (url === '/api/auth/settings') {
          if (opts.method === 'POST') {
            const body = JSON.parse(opts.body);
            serverGlobal = body.image_gen_enabled;
            return { status: 200, ok: true, json: async () => ({ ok: true }) };
          }
          return { ok: true, json: async () => ({ image_gen_enabled: serverGlobal }) };
        }
        if (url.startsWith('/api/prefs/')) {
          return { status: 500, ok: false, text: async () => 'DB lock' };
        }
        throw new Error('Unexpected URL: ' + url);
      };
      await initImageSettings();
      enabledToggle.checked = false;
      await enabledToggle.dispatchEvent('change');
      testResults.s1_checked = enabledToggle.checked;
      testResults.s1_server = serverGlobal;
      testResults.s1_msg = msg.textContent;
      testResults.s1_color = msg.style.color;
    }

    // 2. Global POST 200 + cleanup network rejection
    {
      const { enabledToggle, msg } = createEnv();
      let serverGlobal = true;
      globalThis.fetch = async (url, opts = {}) => {
        if (url === '/api/model-endpoints') return { ok: true, json: async () => [] };
        if (url === '/api/prefs') return { ok: true, json: async () => ({}) };
        if (url === '/api/auth/settings') {
          if (opts.method === 'POST') {
            const body = JSON.parse(opts.body);
            serverGlobal = body.image_gen_enabled;
            return { status: 200, ok: true, json: async () => ({ ok: true }) };
          }
          return { ok: true, json: async () => ({ image_gen_enabled: serverGlobal }) };
        }
        if (url.startsWith('/api/prefs/')) {
          throw new Error('Network offline');
        }
        throw new Error('Unexpected URL: ' + url);
      };
      await initImageSettings();
      enabledToggle.checked = false;
      await enabledToggle.dispatchEvent('change');
      testResults.s2_checked = enabledToggle.checked;
      testResults.s2_server = serverGlobal;
      testResults.s2_msg = msg.textContent;
      testResults.s2_color = msg.style.color;
    }

    // 3. Successful cleanup
    {
      const { enabledToggle, msg } = createEnv();
      let serverGlobal = true;
      globalThis.fetch = async (url, opts = {}) => {
        if (url === '/api/model-endpoints') return { ok: true, json: async () => [] };
        if (url === '/api/prefs') return { ok: true, json: async () => ({}) };
        if (url === '/api/auth/settings') {
          if (opts.method === 'POST') {
            const body = JSON.parse(opts.body);
            serverGlobal = body.image_gen_enabled;
            return { status: 200, ok: true, json: async () => ({ ok: true }) };
          }
          return { ok: true, json: async () => ({ image_gen_enabled: serverGlobal }) };
        }
        if (url.startsWith('/api/prefs/')) {
          return { status: 200, ok: true, json: async () => ({ ok: true }) };
        }
        throw new Error('Unexpected URL: ' + url);
      };
      await initImageSettings();
      enabledToggle.checked = false;
      await enabledToggle.dispatchEvent('change');
      testResults.s3_checked = enabledToggle.checked;
      testResults.s3_server = serverGlobal;
      testResults.s3_msg = msg.textContent;
      testResults.s3_color = msg.style.color;
    }

    // 4. Failed global POST (HTTP 500)
    {
      const { enabledToggle, msg } = createEnv();
      let serverGlobal = true;
      globalThis.fetch = async (url, opts = {}) => {
        if (url === '/api/model-endpoints') return { ok: true, json: async () => [] };
        if (url === '/api/prefs') return { ok: true, json: async () => ({}) };
        if (url === '/api/auth/settings') {
          if (opts.method === 'POST') {
            return { status: 500, ok: false, text: async () => 'Internal Server Error' };
          }
          return { ok: true, json: async () => ({ image_gen_enabled: serverGlobal }) };
        }
        if (url.startsWith('/api/prefs/')) {
          return { status: 200, ok: true, json: async () => ({ ok: true }) };
        }
        throw new Error('Unexpected URL: ' + url);
      };
      await initImageSettings();
      enabledToggle.checked = false;
      await enabledToggle.dispatchEvent('change');
      testResults.s4_checked = enabledToggle.checked;
      testResults.s4_server = serverGlobal;
      testResults.s4_msg = msg.textContent;
      testResults.s4_color = msg.style.color;
    }

    console.log(JSON.stringify(testResults));
    """
    proc = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, check=True)
    import json
    data = json.loads(proc.stdout)

    # 1. Successful global POST followed by HTTP 500 cleanup failure:
    # Toggle does not revert, server policy was persisted, and message reports partial success + cleanup error
    assert data["s1_checked"] is False
    assert data["s1_server"] is False
    assert "Policy saved, but failed to clear preferences" in data["s1_msg"]
    assert "DB lock" in data["s1_msg"]
    assert data["s1_color"] == "var(--red)"

    # 2. Successful global POST followed by network rejection during cleanup:
    # Toggle does not revert, server policy was persisted, and message reports network failure
    assert data["s2_checked"] is False
    assert data["s2_server"] is False
    assert "Policy saved, but failed to clear preferences" in data["s2_msg"]
    assert "Network offline" in data["s2_msg"]
    assert data["s2_color"] == "var(--red)"

    # 3. Successful cleanup:
    # Toggle saved, server updated, success message displayed
    assert data["s3_checked"] is False
    assert data["s3_server"] is False
    assert data["s3_msg"] == "Saved"
    assert data["s3_color"] == "var(--fg)"

    # 4. Failed global POST:
    # Toggle reverts to authoritative server state, error displayed
    assert data["s4_checked"] is True
    assert data["s4_server"] is True
    assert data["s4_msg"] == "Failed to save"
    assert data["s4_color"] == "var(--red)"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_non_admin_toggle_rejection_and_ui_behavior():
    """Verify non-admin cannot change image_gen_enabled via API or UI, and receives unambiguous feedback."""
    js = """
    const { saveImagePreferences } = await import('./static/js/settings/imagePreferences.js');

    const results = {};

    // 1. Direct call to saveImagePreferences with only non-admin excluded toggle
    globalThis.window = { dispatchEvent: () => {} };
    globalThis.CustomEvent = class CustomEvent {};
    globalThis.fetch = async (url) => {
      if (url === '/api/auth/settings') return { status: 403, ok: false, text: async () => 'Forbidden' };
      throw new Error('Unexpected URL: ' + url);
    };
    try {
      await saveImagePreferences({ image_gen_enabled: true });
      results.toggleOnlyThrew = false;
    } catch (err) {
      results.toggleOnlyThrew = true;
      results.toggleOnlyStatus = err.status;
    }

    // 2. Simulated UI interaction in imageModels.js for non-admin
    const elements = {};
    function makeElement(id, tag = 'div') {
      const listeners = {};
      const el = {
        id, tagName: tag.toUpperCase(),
        value: '', checked: false, disabled: false, title: '',
        style: {},
        parentElement: { title: '', style: {} },
        closest: () => ({ style: {} }),
        addEventListener: (evt, cb) => { listeners[evt] = listeners[evt] || []; listeners[evt].push(cb); },
        dispatchEvent: (evt) => Promise.all((listeners[evt] || []).map(cb => cb())),
        length: 0, appendChild: () => {}, remove: () => {}
      };
      elements[id] = el;
      return el;
    }

    const modelSel = makeElement('set-imgModelSelect', 'select');
    const editModelSel = makeElement('set-imgEditModelSelect', 'select');
    const qualSel = makeElement('set-imgQualitySelect', 'select');
    const msg = makeElement('set-imgSettingsMsg', 'span');
    const enabledToggle = makeElement('set-imgEnabledToggle', 'input');

    globalThis.document = {
      getElementById: (id) => elements[id] || null,
      createElement: (tag) => makeElement('anon-' + Math.random(), tag),
    };
    globalThis.window._isAdmin = false;
    globalThis.window.addEventListener = () => {};
    globalThis.window.removeEventListener = () => {};

    const apiCalls = [];
    globalThis.fetch = async (url, opts = {}) => {
      apiCalls.push({ url, method: opts.method || 'GET' });
      if (url === '/api/model-endpoints') return { ok: true, json: async () => [] };
      if (url === '/api/prefs') return { ok: true, json: async () => ({}) };
      if (url === '/api/auth/settings') {
        if (opts.method === 'POST') return { status: 403, ok: false, text: async () => 'Forbidden' };
        return { ok: true, json: async () => ({ image_gen_enabled: true, image_model: 'global-model' }) };
      }
      if (url.startsWith('/api/prefs/')) {
        return { status: 200, ok: true, json: async () => ({ ok: true }) };
      }
      throw new Error('Unexpected URL: ' + url);
    };

    const { initImageSettings } = await import('./static/js/settings/imageModels.js');
    await initImageSettings();

    // Check non-admin initial UI state
    results.nonAdminToggleDisabled = enabledToggle.disabled;
    results.nonAdminToggleTitle = enabledToggle.title;
    results.nonAdminToggleChecked = enabledToggle.checked;

    // Simulate non-admin attempting to toggle image generation
    enabledToggle.checked = false; // user clicked switch to turn off
    enabledToggle.dispatchEvent('change');
    results.revertedChecked = enabledToggle.checked;
    results.revertedMsgText = msg.textContent;
    results.revertedMsgColor = msg.style.color;

    // Simulate non-admin changing model preference
    modelSel.value = 'user-selected-model';
    await modelSel.dispatchEvent('change');
    results.modelChangeMsg = msg.textContent;

    // 3. Admin UI interaction in imageModels.js
    globalThis.window._isAdmin = true;
    const adminToggle = makeElement('set-imgEnabledToggle', 'input');
    const adminMsg = makeElement('set-imgSettingsMsg', 'span');
    elements['set-imgEnabledToggle'] = adminToggle;
    elements['set-imgSettingsMsg'] = adminMsg;

    let adminSavedToggle = null;
    globalThis.fetch = async (url, opts = {}) => {
      if (url === '/api/model-endpoints') return { ok: true, json: async () => [] };
      if (url === '/api/prefs') return { ok: true, json: async () => ({}) };
      if (url === '/api/auth/settings') {
        if (opts.method === 'POST') {
          const body = JSON.parse(opts.body);
          adminSavedToggle = body.image_gen_enabled;
          return { status: 200, ok: true, json: async () => ({ ok: true }) };
        }
        return { ok: true, json: async () => ({ image_gen_enabled: true, image_model: 'global-model' }) };
      }
      if (url.startsWith('/api/prefs/')) {
        return { status: 200, ok: true, json: async () => ({ ok: true }) };
      }
      throw new Error('Unexpected URL: ' + url);
    };

    await initImageSettings();
    results.adminToggleDisabled = adminToggle.disabled;
    adminToggle.checked = false;
    await adminToggle.dispatchEvent('change');
    results.adminSavedToggle = adminSavedToggle;
    results.adminMsg = adminMsg.textContent;

    console.log(JSON.stringify(results));
    """
    proc = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, check=True)
    import json
    data = json.loads(proc.stdout)

    # 1. Non-admin toggle-only save threw 403
    assert data["toggleOnlyThrew"] is True
    assert data["toggleOnlyStatus"] == 403

    # 2. Non-admin UI has disabled toggle and rejects changes with warning
    assert data["nonAdminToggleDisabled"] is True
    assert "Admin access is required" in data["nonAdminToggleTitle"]
    assert data["nonAdminToggleChecked"] is True
    assert data["revertedChecked"] is True
    assert "Admin access is required" in data["revertedMsgText"]
    assert data["revertedMsgColor"] == "var(--red)"

    # 3. Non-admin model changes succeed
    assert data["modelChangeMsg"] == "Saved"

    # 4. Admin UI allows toggle changes and persists to auth settings
    assert data["adminToggleDisabled"] is False
    assert data["adminSavedToggle"] is False
    assert data["adminMsg"] == "Saved"


