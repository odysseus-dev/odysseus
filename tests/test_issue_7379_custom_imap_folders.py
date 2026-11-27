import pathlib
import subprocess
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.email.email_routes import setup_email_routes, require_owner


def _build_client() -> TestClient:
    app = FastAPI()
    app.dependency_overrides[require_owner] = lambda: "owner@example.com"
    with patch("routes.email.email_routes._start_poller"):
        app.include_router(setup_email_routes())
    return TestClient(app)


def test_list_folders_live_cache_and_refresh_include_custom_imap_folders(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_FIXTURE_EMAIL_FILE", raising=False)
    initial_lines = [
        b'(\\HasNoChildren) "/" "INBOX"',
        b'(\\HasNoChildren \\Sent) "/" "Sent"',
        b'(\\HasNoChildren \\Drafts) "/" "Drafts"',
        b'(\\HasNoChildren \\Trash) "/" "Trash"',
        b'(\\HasNoChildren) "/" "Receipts"',
        b'(\\HasNoChildren) "/" "Work/Client A"',
    ]
    updated_lines = initial_lines + [
        b'(\\HasNoChildren) "/" "Taxes 2026"',
    ]
    current_lines = [initial_lines]

    @contextmanager
    def fake_imap(account_id=None, owner=""):
        conn = MagicMock()
        conn.list.return_value = ("OK", current_lines[0])
        yield conn

    client = _build_client()
    with (
        patch("routes.email.email_routes._imap", side_effect=fake_imap),
    ):
        # 1. Cold cached_only=1 returns fallback marker
        r_cold = client.get("/api/email/folders", params={"cached_only": 1, "account_id": "acct-7379"})
        assert r_cold.status_code == 200
        d_cold = r_cold.json()
        assert d_cold["sync"]["source"] == "folder_cached_only_fallback"

        # 2. Live request fetches custom IMAP folders and warms _FOLDER_CACHE
        r_live = client.get("/api/email/folders", params={"account_id": "acct-7379"})
        assert r_live.status_code == 200
        d_live = r_live.json()
        assert d_live["sync"]["source"] == "imap"
        assert "Receipts" in d_live["folders"]
        assert "Work/Client A" in d_live["folders"]

        # 3. Subsequent cached_only=1 serves custom folders from cache
        r_warm = client.get("/api/email/folders", params={"cached_only": 1, "account_id": "acct-7379"})
        assert r_warm.status_code == 200
        d_warm = r_warm.json()
        assert d_warm["sync"]["source"] == "folder_cache"
        assert "Receipts" in d_warm["folders"]
        assert "Work/Client A" in d_warm["folders"]

        # 4. Manual refresh=1 bypasses TTL cache and discovers new custom folders
        current_lines[0] = updated_lines
        r_ref = client.get("/api/email/folders", params={"refresh": 1, "account_id": "acct-7379"})
        assert r_ref.status_code == 200
        d_ref = r_ref.json()
        assert d_ref["sync"]["source"] == "imap"
        assert "Taxes 2026" in d_ref["folders"]


def test_frontend_load_folders_follows_fallback_with_live_fetch_via_node():
    src = pathlib.Path("static/js/emailLibrary/index.js").read_text(encoding="utf-8")
    fn_start = src.index("export async function _loadFolders(")
    fn_end = src.index("function _crossFolderCandidates()")
    load_folders_code = src[fn_start:fn_end].replace("export async function _loadFolders", "async function _loadFolders")

    node_script = f"""
    const assert = require('node:assert');

    function makeEl(tag) {{
      return {{
        tagName: tag.toUpperCase(),
        children: [],
        options: [],
        value: 'INBOX',
        disabled: false,
        selected: false,
        textContent: '',
        innerHTML: '',
        classList: {{ remove() {{}}, add() {{}} }},
        appendChild(child) {{
          this.children.push(child);
          if (child.tagName === 'OPTION') {{
            this.options.push(child);
            if (child.selected) this.value = child.value;
          }}
          return child;
        }},
      }};
    }}

    const sel = makeEl('select');
    const elements = {{
      'email-lib-folder': sel,
      'email-lib-search': makeEl('input'),
      'email-lib-filter': makeEl('select'),
      'email-attachments-btn': makeEl('button'),
    }};
    global.document = {{
      getElementById(id) {{ return elements[id] || null; }},
      createElement(tag) {{ return makeEl(tag); }},
    }};

    let _libFolderSeq = 0;
    const _libListCache = new Map();
    const state = {{
      _libAccountId: 'acct-1',
      _libFolder: 'INBOX',
      _libFolders: [],
      _libFilter: 'all',
      _libSearch: '',
      _libHasAttachments: false,
    }};
    function _resolveEmailFolderAlias(f) {{ return f; }}
    function _syncUnreadWindowGlow() {{}}
    function _syncReminderClearButton() {{}}
    let pickerRenders = 0;
    function _renderFolderPicker() {{ pickerRenders += 1; }}
    function folderDisplayName(f) {{ return f; }}
    function sortedFolders(folders) {{
      const priority = folders.filter(f => ['INBOX', 'Sent', 'Archive'].includes(f));
      const others = folders.filter(f => !priority.includes(f));
      return {{ priority, others }};
    }}
    function emailApiUrl(path, params) {{
      const qs = Object.entries(params || {{}})
        .filter(([, v]) => v !== undefined && v !== null && v !== '')
        .map(([k, v]) => `${{encodeURIComponent(k)}}=${{encodeURIComponent(v)}}`)
        .join('&');
      return qs ? `${{path}}?${{qs}}` : path;
    }}

    const requestedUrls = [];
    global.fetch = async (url) => {{
      requestedUrls.push(url);
      if (url.includes('cached_only=1')) {{
        return {{
          json: async () => ({{
            folders: ['INBOX', 'Sent', 'Archive'],
            sync: {{ source: 'folder_cached_only_fallback' }},
          }}),
        }};
      }}
      return {{
        json: async () => ({{
          folders: ['INBOX', 'Sent', 'Archive', 'Receipts', 'Work/Client A'],
          sync: {{ source: 'imap' }},
        }}),
      }};
    }};

    {load_folders_code}

    (async () => {{
      await _loadFolders();
      assert.strictEqual(requestedUrls.length, 2, 'Expected fast cached_only=1 followed by live IMAP fetch');
      assert.ok(requestedUrls[0].includes('cached_only=1'));
      assert.ok(!requestedUrls[1].includes('cached_only=1'));
      const optionValues = sel.options.filter(o => !o.disabled).map(o => o.value);
      assert.ok(optionValues.includes('Receipts'), 'Custom IMAP folder Receipts must be rendered');
      assert.ok(optionValues.includes('Work/Client A'), 'Nested custom IMAP folder must be rendered');
      assert.ok(pickerRenders >= 2);
    }})().catch(err => {{
      console.error(err);
      process.exit(1);
    }});
    """
    proc = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
