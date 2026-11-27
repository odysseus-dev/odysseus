import json
import shutil
import subprocess

import pytest
from tests.helpers.js_modules import email_library_source, js_function_source


def _refreshes(state_overrides: dict, hidden: bool = False, reading: bool = False) -> bool:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    fn = js_function_source("_autoRefreshEmailList", email_library_source())
    state = {"_libOpen": True, "_libLoading": False, "_libOffset": 0, "_libSearch": "",
             "_selectMode": False, "_libSearchPills": [], "_libSearchDraft": "", "_libFolder": "INBOX"}
    state.update(state_overrides)
    script = f"""
      const state = {json.dumps(state)};
      const modal = {{ classList: {{ contains: (c) => c === 'email-reading' && {json.dumps(reading)} }} }};
      globalThis.document = {{ hidden: {json.dumps(hidden)}, getElementById: () => modal }};
      let calls = [];
      function _loadEmails(opts) {{ calls.push(opts); }}
      {fn}
      _autoRefreshEmailList();
      console.log(JSON.stringify(calls));
    """
    calls = json.loads(subprocess.run([node, "-e", script], capture_output=True, text=True, check=True).stdout)
    assert calls in ([], [{"force": False, "useCache": False, "skipFastPath": True}])
    return bool(calls)


def test_open_idle_mailbox_refreshes_without_forcing_imap():
    assert _refreshes({})


@pytest.mark.parametrize("overrides", [
    {"_libOpen": False},
    {"_libLoading": True},
    {"_libOffset": 50},
    {"_libSearch": "invoice"},
    {"_selectMode": True},
    {"_libSearchPills": [{"text": "from:bob"}]},
    {"_libSearchDraft": "bo"},
    {"_libFolder": "__scheduled__"},
])
def test_skips_when_a_repaint_would_disturb_the_view(overrides):
    assert not _refreshes(overrides)


def test_skips_while_the_tab_is_hidden():
    assert not _refreshes({}, hidden=True)


def test_skips_while_reading_an_email():
    assert not _refreshes({}, reading=True)


def test_opening_from_the_browser_cache_paints_then_asks_the_server():
    # Opening painted the saved list and returned: new mail stayed hidden until
    # the next refresh, and _libLoading stayed set so the auto-refresh skipped.
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    fn = js_function_source("_loadEmails", email_library_source())
    script = f"""
      const state = {{ _libLoading: false, _libOffset: 0, _libSearch: '', _libFolder: 'INBOX', _libEmails: [], _libFilter: 'all' }};
      let _libLoadSeq = 0, _libRenderedViewKey = '';
      const API_BASE = '', _LIB_INITIAL_PAGE_SIZE = 100;
      const grid = {{ classList: {{ remove() {{}} }} }};
      globalThis.document = {{ getElementById: () => grid }};
      const painted = [];
      const _libCacheKey = () => 'k';
      const _libCacheGet = () => ({{ emails: [{{ uid: '1' }}], total: 1 }});
      const _renderGrid = () => painted.push(state._libEmails.map(e => e.uid).join(','));
      const _setEmailSyncStatus = () => {{}}, _clearStaleEmailRefetch = () => {{}};
      const _scheduleStaleEmailRefetch = () => {{}}, _libCachePut = () => {{}};
      const _syncUnreadWindowGlow = () => {{}}, _refreshUnreadBadge = () => {{}};
      const _refreshAccountUnreadHighlights = async () => {{}};
      const urls = [];
      const fetch = async (url) => {{ urls.push(url); return {{ json: async () => ({{ emails: [{{ uid: '2' }}, {{ uid: '1' }}], total: 2 }}) }}; }};
      {fn}
      _loadEmails({{ useCache: true }}).then(() => console.log(JSON.stringify({{ painted, urls, loading: state._libLoading }})));
    """
    out = json.loads(subprocess.run([node, "-e", script], capture_output=True, text=True, check=True).stdout)
    assert out["painted"] == ["1", "2,1"]  # saved list first, then the server's
    assert len(out["urls"]) == 1 and "refresh=1" not in out["urls"][0]
    assert out["loading"] is False
