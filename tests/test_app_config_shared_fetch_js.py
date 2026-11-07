"""Pin the shared config cache in static/js/appConfig.js.

Background: /api/auth/settings was fetched independently by six modules and
/api/tools by three (chatRenderer.js is imported under three different ?v=
query strings, so it is three separate module instances) — 4 and 3 requests on
one cold load. Beyond the redundant work, each caller could observe a different
snapshot of the same object. appConfig.js gives them one promise each.

The two properties that matter are opposites, so both are tested here:
concurrent and later callers must NOT refetch, and a caller after a write MUST
see the new value — which only holds if every writer invalidates. The last test
is a source scan that checks exactly that, since a forgotten invalidation
serves a stale settings object for the rest of the session, which is worse than
the duplicate fetches this replaces.

Driven through `node --input-type=module` so the real module runs, same idiom as
test_esc_menu_stack_js.py. The module source is inlined rather than imported by
path because the repo has no `"type": "module"` in package.json; appConfig.js
has no imports of its own, so inlining is exact. `fetch` and `sessionStorage`
are stubbed, so nothing here touches the network or depends on timing.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_MODULE = _REPO / "static" / "js" / "appConfig.js"
_HAS_NODE = shutil.which("node") is not None
_SRC = _MODULE.read_text(encoding="utf-8") if _MODULE.exists() else ""

# Browser stand-ins. Every fetch is recorded and resolved from a queue the test
# controls, so "how many requests went out" is an exact count, not a guess.
_STUBS = r"""
const calls = [];
let responses = [];
globalThis.__queue = (fn) => { responses.push(fn); };
globalThis.fetch = (url, opts) => {
  calls.push([url, opts]);
  const next = responses.shift();
  if (!next) throw new Error('unexpected fetch: ' + url);
  return next();
};
globalThis.__calls = () => calls;
globalThis.__json = (value) => () => Promise.resolve({ json: () => Promise.resolve(value) });
globalThis.__fail = (msg) => () => Promise.reject(new Error(msg));

const store = new Map();
globalThis.sessionStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => { store.set(k, String(v)); },
  removeItem: (k) => { store.delete(k); },
};
globalThis.__seedPrefetch = (value) => {
  store.set('ody-prefetch-settings', JSON.stringify(value));
};
globalThis.__prefetchLeft = () => store.has('ody-prefetch-settings');
"""


def _run(body: str) -> str:
    js = _STUBS + "\n" + _SRC + "\n" + body
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, encoding="utf-8",
        cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_concurrent_callers_share_one_request():
    # The startup case: several modules ask before the first response lands.
    body = """
    __queue(__json({ tts_enabled: true }));
    const [a, b, c] = await Promise.all([getSettings(), getSettings(), getSettings()]);
    console.log(JSON.stringify({
      requests: __calls().length,
      url: __calls()[0][0],
      credentials: __calls()[0][1].credentials,
      sameObject: a === b && b === c,
      value: a.tts_enabled,
    }));
    """
    assert json.loads(_run(body)) == {
        "requests": 1,
        "url": "/api/auth/settings",
        "credentials": "same-origin",
        "sameObject": True,
        "value": True,
    }


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_later_caller_reuses_the_resolved_snapshot():
    # A panel opened long after boot must not re-request.
    body = """
    __queue(__json({ search_provider: 'brave' }));
    const first = await getSettings();
    const second = await getSettings();
    console.log(JSON.stringify({ requests: __calls().length, sameObject: first === second }));
    """
    assert json.loads(_run(body)) == {"requests": 1, "sameObject": True}


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_invalidate_forces_the_next_read_to_refetch():
    # The write path: save a setting, then read it back and see the new value.
    body = """
    __queue(__json({ tts_enabled: true }));
    __queue(__json({ tts_enabled: false }));
    const before = await getSettings();
    invalidateSettings();
    const after = await getSettings();
    console.log(JSON.stringify({
      requests: __calls().length,
      before: before.tts_enabled,
      after: after.tts_enabled,
    }));
    """
    assert json.loads(_run(body)) == {"requests": 2, "before": True, "after": False}


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_a_failed_fetch_does_not_poison_the_cache():
    # Plain `??=` memoisation would keep the rejected promise, so one blip at
    # boot would leave keybinds/TTS/search on defaults for the whole session.
    body = """
    __queue(__fail('offline'));
    __queue(__json({ tts_enabled: true }));
    let rejected = false;
    try { await getSettings(); } catch (e) { rejected = e.message === 'offline'; }
    const retry = await getSettings();
    console.log(JSON.stringify({
      rejected,
      requests: __calls().length,
      recovered: retry.tts_enabled,
    }));
    """
    assert json.loads(_run(body)) == {"rejected": True, "requests": 2, "recovered": True}


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_settings_and_tools_are_independent_slots():
    body = """
    __queue(__json({ tts_enabled: true }));
    __queue(__json({ tools: [{ id: 'web_search' }] }));
    await getSettings();
    const tools = await getTools();
    const toolsUrl = __calls()[1][0];
    invalidateSettings();          // must not drop the tools snapshot
    const toolsAgain = await getTools();
    console.log(JSON.stringify({
      requests: __calls().length,
      toolsUrl,
      sameObject: tools === toolsAgain,
    }));
    """
    assert json.loads(_run(body)) == {
        "requests": 2,
        "toolsUrl": "/api/tools",
        "sameObject": True,
    }


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_login_prefetch_is_used_once_and_then_consumed():
    # login.html stashes a settings snapshot in sessionStorage just before it
    # redirects, so the first load after a login should issue no request at all.
    body = """
    __seedPrefetch({ tts_enabled: false, from: 'prefetch' });
    __queue(__json({ tts_enabled: true, from: 'network' }));
    const first = await getSettings();
    const consumed = !__prefetchLeft();
    invalidateSettings();
    const second = await getSettings();
    console.log(JSON.stringify({
      requests: __calls().length,
      first: first.from,
      consumed,
      second: second.from,
    }));
    """
    assert json.loads(_run(body)) == {
        "requests": 1,
        "first": "prefetch",
        "consumed": True,
        "second": "network",
    }


# ── Writers must invalidate ─────────────────────────────────────────────────

_WRITE_ENDPOINTS = {
    "/api/auth/settings": "invalidateSettings",
    "/api/tools": "invalidateTools",
}
# login.html is the pre-app login page: it has no module graph and its only call
# is the prefetch GET, so it is not a writer and cannot import appConfig.js.
_SCANNED = [_REPO / "static" / "app.js"] + sorted((_REPO / "static" / "js").rglob("*.js"))


def _post_sites(source: str, endpoint: str):
    """Yield the 1-based line of every fetch() to `endpoint` that is a POST."""
    for m in re.finditer(re.escape(f"'{endpoint}'"), source):
        window = source[m.start():m.start() + 240]
        if re.search(r"method:\s*'POST'", window):
            yield source[:m.start()].count("\n") + 1


def test_every_settings_writer_invalidates_the_shared_cache():
    """A POST that skips the invalidation serves a stale object for the session.

    Checked by source scan rather than at runtime: the failure mode is a call
    site that was never wired up, which no unit test of the cache itself can
    see. `appConfig.js` itself is skipped — it is the cache, not a writer.
    """
    missing = []
    for path in _SCANNED:
        if path.name == "appConfig.js":
            continue
        source = path.read_text(encoding="utf-8")
        for endpoint, invalidator in _WRITE_ENDPOINTS.items():
            for line in _post_sites(source, endpoint):
                # The invalidation belongs in the same function as the POST;
                # accept it anywhere in the surrounding 20 lines either way.
                lines = source.splitlines()
                near = "\n".join(lines[max(0, line - 20):line + 20])
                if invalidator + "(" not in near:
                    missing.append(f"{path.relative_to(_REPO)}:{line} POST {endpoint}")
    assert not missing, (
        "POST sites with no nearby cache invalidation — the UI will serve a "
        "stale snapshot after these writes:\n  " + "\n  ".join(missing)
    )


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_out_of_band_tool_change_is_not_undone_by_an_unrelated_panel_save():
    """Admin > Tools must render authoritative state, not the startup snapshot.

    The save posts the whole disabled-tool list rebuilt from the checkboxes, so
    a stale render turns any unrelated toggle into a lost update: a tool
    disabled out of band (manage_settings, another tab) comes back enabled.
    refreshAll() calls loadBuiltinTools() on every panel open, which is why the
    editor drops the shared entry before reading it.
    """
    body = """
    // Boot: chatRenderer.js reads the tool list for the exec-fence regex.
    __queue(__json({ tools: [{ id: 'web_search', enabled: true }, { id: 'shell', enabled: true }] }));
    const boot = await getTools();

    // Out of band, this page hearing nothing about it: web_search is disabled.
    __queue(__json({ tools: [{ id: 'web_search', enabled: false }, { id: 'shell', enabled: true }] }));

    // Admin > Tools opens. loadBuiltinTools() invalidates, then reads.
    invalidateTools();
    const panel = await getTools();

    // The user toggles one unrelated tool off. The save posts every unchecked
    // box, so the list is only right if the render was authoritative.
    const post = (snapshot) => {
      const boxes = snapshot.tools.map(t => ({ id: t.id, checked: t.enabled }));
      boxes.find(b => b.id === 'shell').checked = false;
      return boxes.filter(b => !b.checked).map(b => b.id);
    };

    console.log(JSON.stringify({
      requests: __calls().length,
      posted: post(panel),
      postedFromStaleSnapshot: post(boot),
    }));
    """
    assert json.loads(_run(body)) == {
        "requests": 2,
        # web_search stays disabled, which is the point.
        "posted": ["web_search", "shell"],
        # What the page-lifetime snapshot would have posted: web_search silently
        # re-enabled by a toggle that had nothing to do with it.
        "postedFromStaleSnapshot": ["shell"],
    }


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_out_of_band_tool_change_after_panel_open_is_preserved_on_save():
    """Saving must merge the user's edit onto a fresh authoritative snapshot."""
    body = """
    // Panel opens while both tools are enabled.
    __queue(__json({ tools: [
      { id: 'web_search', enabled: true },
      { id: 'shell', enabled: true },
    ] }));
    invalidateTools();
    const panel = await getTools();

    // Another tab disables web_search after this panel has already rendered.
    __queue(__json({ tools: [
      { id: 'web_search', enabled: false },
      { id: 'shell', enabled: true },
    ] }));

    // The user only disables shell. Saving refreshes the authoritative state
    // first and applies that one intended change on top of it.
    invalidateTools();
    const latest = await getTools();
    const state = new Map(latest.tools.map(t => [t.id, !!t.enabled]));
    state.set('shell', false);

    const disabled = Array.from(state.entries())
      .filter(([, enabled]) => !enabled)
      .map(([id]) => id);

    console.log(JSON.stringify({
      panelWebSearchEnabled: panel.tools.find(t => t.id === 'web_search').enabled,
      requests: __calls().length,
      disabled,
    }));
    """
    assert json.loads(_run(body)) == {
        "panelWebSearchEnabled": True,
        "requests": 2,
        "disabled": ["web_search", "shell"],
    }


def test_admin_tool_save_refreshes_before_full_state_post():
    """Pin the lost-update guard in the Admin Tools full-list writer."""
    source = (_REPO / "static" / "js" / "admin.js").read_text(encoding="utf-8")
    match = re.search(
        r"async function _saveToolState\(changes\) \{(.*?)\n    \}\n"
        r"    function _updateCatCounter",
        source,
        re.S,
    )
    assert match, "_saveToolState(changes) not found in static/js/admin.js"

    body = match.group(1)
    invalidate = body.find("invalidateTools()")
    refresh = body.find("getTools()")
    post = body.find("fetch('/api/tools'")

    assert -1 not in (invalidate, refresh, post)
    assert invalidate < refresh < post, (
        "Admin Tools must invalidate and refresh authoritative tool state before "
        "posting the endpoint's full disabled-tools replacement list"
    )
    assert "for (const change of changes)" in body


def test_the_admin_tools_editor_does_not_read_a_cached_snapshot():
    """Pin the invalidate-before-read in loadBuiltinTools().

    A source scan because the failure is an ordering in a call site, not
    behaviour of the cache: getTools() is doing exactly its job either way.
    """
    source = (_REPO / "static" / "js" / "admin.js").read_text(encoding="utf-8")
    match = re.search(r"\nasync function loadBuiltinTools\(\) \{\n(.*?)\n\}\n", source, re.S)
    assert match, "loadBuiltinTools() not found in static/js/admin.js"
    body = match.group(1)
    read = body.find("getTools(")
    assert read != -1, "loadBuiltinTools() no longer reads the shared tool cache"
    assert "invalidateTools(" in body[:read], (
        "loadBuiltinTools() reads the shared /api/tools snapshot without dropping "
        "it first, so a reopened panel can render tool state that changed out of "
        "band and re-post it on the next unrelated toggle"
    )


def test_appconfig_is_precached_by_the_service_worker():
    """PRECACHE is hand-maintained; a module missing from it breaks offline."""
    sw = (_REPO / "static" / "sw.js").read_text(encoding="utf-8")
    assert "'/static/js/appConfig.js'" in sw
