"""Exercise the email selectors/search with real JS and provider LIST metadata."""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _function(source, name):
    markers = (
        f"function {name}(", f"async function {name}(",
        f"export function {name}(", f"export async function {name}(",
    )
    start = min(pos for marker in markers if (pos := source.find(marker)) >= 0)
    # Signatures may destructure options, so find the opening body after the
    # matching parameter parenthesis rather than the first opening brace.
    paren = source.index("(", start)
    depth = 0
    for pos in range(paren, len(source)):
        if source[pos] == "(":
            depth += 1
        elif source[pos] == ")":
            depth -= 1
            if not depth:
                body = source.index("{", pos)
                break
    depth = 0
    quote = None
    escaped = False
    template_depth = 0
    line_comment = False
    block_comment = False
    for pos in range(body, len(source)):
        char = source[pos]
        if line_comment:
            if char == "\n":
                line_comment = False
            continue
        if block_comment:
            if source[pos:pos + 2] == "*/":
                block_comment = False
            continue
        if not quote and source[pos:pos + 2] == "//":
            line_comment = True
            continue
        if not quote and source[pos:pos + 2] == "/*":
            block_comment = True
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote and not template_depth:
                quote = None
            elif quote == "`" and source[pos:pos + 2] == "${":
                template_depth += 1
            elif quote == "`" and char == "}" and template_depth:
                template_depth -= 1
            continue
        if char in ("'", '"', "`"):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if not depth:
                return source[start:pos + 1]
    raise AssertionError(f"Unterminated function: {name}")


def _run(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("node on PATH is required for JavaScript behavior checks")
    inbox = (ROOT / "static/js/emailInbox.js").read_text()
    library = (ROOT / "static/js/emailLibrary/index.js").read_text()
    functions = "\n".join([
        *(_function(inbox, name) for name in (
            "folderRole", "folderDisplayName", "sortedFolders", "_populateFolderSelect",
        )),
        *(_function(library, name) for name in (
            "_resetEmailFoldersForAccount", "_emailFolderRole", "_loadFolders",
            "_sentFolderName", "_crossFolderCandidates", "_resolveEmailFolderAlias", "_emailRowsWithFolder", "_loadEmails", "_deriveSearchScope", "_doSearch",
        )),
    ])
    script = """
      import assert from 'node:assert/strict';
      const state = {
        _libAccountId: 'gmail', _libFoldersAccountId: 'gmail',
        _libFolder: 'INBOX', _libFolders: [], _libFolderRoles: {},
        _libFolderDisplayNames: {}, _libSearch: '', _libFilter: 'all',
      };
      class Select {
        constructor() { this.options = []; this.disabled = false; this._value = ''; }
        set innerHTML(value) { this.options = []; this._value = ''; }
        appendChild(option) { this.options.push(option); }
        set value(value) {
          this._value = this.options.some(o => o.value === value) ? value : '';
          this.options.forEach(o => o.selected = o.value === this._value);
        }
        get value() { return this._value; }
      }
      const select = new Select();
      const document = {
        getElementById(id) { return id === 'email-lib-folder' ? select : null; },
        createElement() { return {value: '', textContent: '', disabled: false}; },
      };
      let _libFolderSeq = 0;
      let _libSearchSeq = 0;
      let _currentFolder = 'INBOX';
      let reloads = 0;
      let renders = 0;
      const toasts = [];
      const _libListCache = new Map();
      function _loadEmailsFresh() { reloads += 1; }
      function _renderGrid() { renders += 1; }
      function _renderFolderPicker() {}
      function _syncUnreadWindowGlow() {}
      function _syncReminderClearButton() {}
      function _exitEmailReaderModeForList() {}
      function _resetBulkSelectionForContextChange() {}
      function showToast(message) { toasts.push(message); }
      function emailApiUrl(path, params = {}) {
        const url = new URL(path, 'http://localhost');
        for (const [key, value] of Object.entries(params)) {
          if (value !== undefined) url.searchParams.set(key, value);
        }
        return url.toString();
      }
      let fetch = async () => { throw new Error('Unexpected fetch'); };
    """ + functions + "\n" + scenario
    result = subprocess.run(
        [node, "--input-type=module"], input=script, text=True,
        capture_output=True, cwd=ROOT, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_cold_folder_load_requests_live_discovery_and_preserves_gmail_wire_names():
    _run("""
      const urls = [];
      fetch = async url => {
        urls.push(new URL(url));
        return {json: async () => ({
          folders: ['INBOX', '[Gmail]/Sent Mail', '[Gmail]/All Mail'],
          roles: {'INBOX': 'inbox', '[Gmail]/Sent Mail': 'sent', '[Gmail]/All Mail': 'all'},
          display_names: {},
        })};
      };
      await _loadFolders();
      assert.equal(urls.length, 1);
      assert.equal(urls[0].searchParams.get('account_id'), 'gmail');
      assert.equal(urls[0].searchParams.has('cached_only'), false);
      const selectable = select.options.filter(o => !o.disabled).map(o => o.value);
      assert.deepEqual(selectable, ['INBOX', '[Gmail]/Sent Mail', '[Gmail]/All Mail', '__scheduled__']);
      assert.equal(_sentFolderName(), '[Gmail]/Sent Mail');
      assert.equal(_deriveSearchScope('sent meeting').folder, '[Gmail]/Sent Mail');
      assert.equal(select.value, 'INBOX');
    """)


def test_localized_special_use_roles_sort_label_and_resolve_sent():
    _run("""
      state._libFolders = ['Projects/Sent invoices', '[Gmail]/Gesendet', 'INBOX', '&ZeVnLIqe-', '[Gmail]/Important'];
      state._libFolderRoles = {'[Gmail]/Gesendet': 'sent', 'INBOX': 'inbox', '[Gmail]/Important': 'important'};
      state._libFolderDisplayNames = {'&ZeVnLIqe-': '日本語'};
      assert.deepEqual(sortedFolders(state._libFolders, state._libFolderRoles), {
        priority: ['INBOX', '[Gmail]/Gesendet'],
        others: ['Projects/Sent invoices', '&ZeVnLIqe-', '[Gmail]/Important'],
      });
      assert.equal(_emailFolderRole('[Gmail]/Gesendet'), 'sent');
      assert.equal(folderDisplayName('[Gmail]/Gesendet', state._libFolderRoles), 'Sent');
      assert.equal(folderDisplayName('&ZeVnLIqe-', {}, state._libFolderDisplayNames), '日本語');
      assert.equal(folderDisplayName('Projects/Sent invoices'), 'Projects/Sent invoices');
      assert.equal(_emailFolderRole('Projects/Sent invoices'), '');
      assert.equal(_sentFolderName(), '[Gmail]/Gesendet');
      assert.deepEqual(_crossFolderCandidates(), ['[Gmail]/Gesendet', 'INBOX']);
    """)


def test_old_servers_without_special_use_flags_use_only_existing_mailboxes():
    _run("""
      state._libFolders = ['INBOX', 'INBOX.Sent', 'Archive'];
      assert.equal(_sentFolderName(), 'INBOX.Sent');
      assert.deepEqual(_crossFolderCandidates(), ['INBOX', 'INBOX.Sent', 'Archive']);
      state._libFolders = ['Custom'];
      assert.equal(_sentFolderName(), '');
      assert.deepEqual(_crossFolderCandidates(), []);
    """)


def test_account_switch_clears_metadata_and_ignores_late_previous_response():
    _run("""
      state._libFolders = ['INBOX', '[Gmail]/Sent Mail'];
      state._libFolderRoles = {'[Gmail]/Sent Mail': 'sent'};
      state._libFolderDisplayNames = {'private label': 'old label'};
      state._libFolder = '[Gmail]/Sent Mail';
      let finishOld;
      fetch = () => new Promise(resolve => finishOld = resolve);
      const oldLoad = _loadFolders();
      state._libAccountId = 'other';
      _resetEmailFoldersForAccount();
      assert.deepEqual(state._libFolders, []);
      assert.deepEqual(state._libFolderRoles, {});
      assert.deepEqual(state._libFolderDisplayNames, {});
      assert.equal(state._libFolder, 'INBOX');
      assert.equal(select.disabled, true);
      fetch = async () => ({json: async () => ({
        folders: ['INBOX', 'Envoyés'], roles: {'Envoyés': 'sent'}, display_names: {},
      })});
      await _loadFolders({resetMissing: true});
      finishOld({json: async () => ({
        folders: ['INBOX', '[Gmail]/Sent Mail'], roles: {'[Gmail]/Sent Mail': 'sent'},
      })});
      await oldLoad;
      assert.deepEqual(state._libFolders, ['INBOX', 'Envoyés']);
      assert.equal(_sentFolderName(), 'Envoyés');
      assert.equal(select.options.some(o => o.value === '[Gmail]/Sent Mail'), false);
      assert.equal(select.disabled, false);
    """)


def test_missing_current_folder_resets_and_reloads_matching_messages():
    _run("""
      state._libFolder = 'Old Sent';
      state._libSearch = 'old query';
      state._libFilter = 'unread';
      fetch = async () => ({json: async () => ({
        folders: ['Sent Items', 'INBOX'], roles: {'Sent Items': 'sent', 'INBOX': 'inbox'},
      })});
      await _loadFolders({resetMissing: true});
      assert.equal(state._libFolder, 'INBOX');
      assert.equal(select.value, 'INBOX');
      assert.equal(state._libSearch, '');
      assert.equal(state._libFilter, 'all');
      assert.equal(reloads, 1);
    """)


def test_folder_failure_never_creates_static_sent_or_archive_options():
    _run("""
      fetch = async () => ({json: async () => ({
        folders: [], roles: {}, display_names: {}, error: 'Folder list timed out',
      })});
      await _loadFolders();
      assert.deepEqual(state._libFolders, []);
      assert.equal(_sentFolderName(), '');
      assert.deepEqual(select.options.filter(o => !o.disabled).map(o => o.value), ['__scheduled__']);
      assert.equal(select.options.some(o => o.textContent === 'Folder list timed out'), true);
      state._libSearch = 'sent meeting';
      await _doSearch();
      assert.equal(toasts.length, 1);
      assert.match(toasts[0], /Sent folder is unavailable/);
    """)


def test_transient_failure_keeps_actual_folders_for_same_account():
    _run("""
      state._libFolders = ['INBOX', '[Gmail]/Sent Mail'];
      state._libFolderRoles = {'[Gmail]/Sent Mail': 'sent'};
      fetch = async () => ({json: async () => ({folders: [], error: 'Connection failed'})});
      await _loadFolders();
      assert.deepEqual(state._libFolders, ['INBOX', '[Gmail]/Sent Mail']);
      assert.equal(_sentFolderName(), '[Gmail]/Sent Mail');
    """)


def test_inbox_selector_uses_display_name_but_retains_encoded_wire_name():
    _run("""
      _populateFolderSelect(select, ['&ZeVnLIqe-', 'Boîte envoyée', 'INBOX'], {
        'Boîte envoyée': 'sent',
      }, {'&ZeVnLIqe-': '日本語'});
      const options = select.options.filter(o => !o.disabled);
      assert.deepEqual(options.map(o => o.value), ['INBOX', 'Boîte envoyée', '&ZeVnLIqe-']);
      assert.deepEqual(options.map(o => o.textContent), ['INBOX', 'Sent', '日本語']);
    """)


def test_delayed_role_discovery_redraws_localized_sent_messages():
    _run("""
      state._libFolder = '[Gmail]/Gesendet';
      state._libEmails = [{uid: '1', to: 'recipient@example.com'}];
      fetch = async () => ({json: async () => ({
        folders: ['INBOX', '[Gmail]/Gesendet'], roles: {'[Gmail]/Gesendet': 'sent'},
      })});
      await _loadFolders();
      assert.equal(select.value, '[Gmail]/Gesendet');
      assert.equal(renders, 1);
      assert.equal(reloads, 0);
    """)


def test_account_folder_reset_reloads_new_inbox_instead_of_old_sent():
    _run("""
      state._libFolder = '[Gmail]/Sent Mail';
      state._libFolders = ['INBOX', '[Gmail]/Sent Mail'];
      state._libFolderRoles = {'[Gmail]/Sent Mail': 'sent'};
      state._libAccountId = 'other';
      fetch = async () => ({json: async () => ({
        folders: ['INBOX', 'Envoyés'], roles: {INBOX: 'inbox', 'Envoyés': 'sent'},
      })});
      await _loadFolders({resetMissing: true});
      assert.equal(state._libFolder, 'INBOX');
      assert.equal(reloads, 1);
    """)


def test_explicit_sent_open_survives_account_reset_and_resolves_actual_folder():
    _run("""
      state._libFolder = '[Gmail]/Sent Mail';
      state._libAccountId = 'other';
      _resetEmailFoldersForAccount();
      state._libFolder = 'Sent';
      fetch = async () => ({json: async () => ({
        folders: ['INBOX', 'Envoyés'], roles: {INBOX: 'inbox', 'Envoyés': 'sent'},
      })});
      await _loadFolders();
      assert.equal(state._libFolder, 'Envoyés');
      assert.equal(select.value, 'Envoyés');
      assert.equal(reloads, 1);
    """)


def test_list_rows_and_cached_rows_keep_resolved_folder_identity():
    _run("""
      const response = {folder: 'Envoyés', emails: [{uid: '17'}, {uid: '18', folder: 'Custom'}]};
      const rows = _emailRowsWithFolder(response, 'Sent');
      assert.deepEqual(rows, [{uid: '17', folder: 'Envoyés'}, {uid: '18', folder: 'Custom'}]);
      assert.deepEqual(_emailRowsWithFolder({emails: rows}, 'Sent'), rows);
      assert.equal(response.emails[0].folder, undefined);
    """)


def test_list_request_resets_account_before_fetch_and_retains_wire_identity_in_cache():
    _run("""
      let _libLoadSeq = 0;
      let _libRenderedViewKey = '';
      const _LIB_INITIAL_PAGE_SIZE = 50;
      const API_BASE = 'http://localhost';
      const grid = {classList: {remove() {}}};
      const originalGet = document.getElementById;
      document.getElementById = id => id === 'email-lib-grid' ? grid : originalGet(id);
      function _libCacheKey() { return String(state._libAccountId) + ':' + state._libFolder; }
      function _libCacheGet(key) { return _libListCache.get(key); }
      function _libCachePut(key, value) { _libListCache.set(key, value); }
      function _renderEmailLoading() { return {destroy() {}}; }
      function _setEmailSyncStatus() {}
      function _refreshUnreadBadge() {}
      function _refreshAccountUnreadHighlights() { return Promise.resolve(); }
      state._libOffset = 0;
      state._libEmails = [];
      state._libFolder = '[Gmail]/Sent Mail';
      state._libFolders = ['INBOX', '[Gmail]/Sent Mail'];
      state._libFolderRoles = {'[Gmail]/Sent Mail': 'sent'};
      state._libAccountId = 'other';
      const urls = [];
      fetch = async url => {
        urls.push(new URL(url));
        return {json: async () => ({emails: [{uid: '17'}], total: 1, folder: 'INBOX'})};
      };
      await _loadEmails({force: true, useCache: false});
      assert.equal(urls[0].searchParams.get('account_id'), 'other');
      assert.equal(urls[0].searchParams.get('folder'), 'INBOX');
      assert.equal(state._libEmails[0].folder, 'INBOX');
      state._libFolder = 'Sent';
      fetch = async url => {
        urls.push(new URL(url));
        return {json: async () => ({emails: [{uid: '18'}], total: 1, folder: 'Envoyés'})};
      };
      await _loadEmails({force: true, useCache: false});
      assert.equal(urls[1].searchParams.get('folder'), 'Sent');
      assert.equal(state._libEmails[0].folder, 'Envoyés');
      const calls = urls.length;
      await _loadEmails({force: false, useCache: true});
      assert.equal(urls.length, calls);
      assert.equal(state._libEmails[0].folder, 'Envoyés');
    """)
