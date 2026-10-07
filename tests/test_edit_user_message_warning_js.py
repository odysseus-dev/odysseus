"""Exercise the real edit-and-send handler before it can truncate history."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


CHAT_JS = Path(__file__).resolve().parents[1] / "static" / "js" / "chat.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required")
def test_editing_older_message_requires_confirmation_before_truncation():
    script = r"""
const fs = require('node:fs');
const source = fs.readFileSync(CHAT_PATH, 'utf8');
const start = source.indexOf('  export async function editUserMessage(');
const end = source.indexOf('  export async function resendUserMessage(', start);
if (start < 0 || end < 0) throw new Error('editUserMessage source was not found');
const editSource = source.slice(start, end).replace('export async function', 'async function');

async function scenario(roles, selected, approved) {
  const confirmations = [], requests = [], sends = [];
  let input;
  class Element {
    constructor(tag = 'div') {
      this.tag = tag;
      this.children = [];
      this.listeners = {};
      this.style = {};
      this.dataset = {};
      this.textContent = '';
      this._html = '';
      this.removed = false;
    }
    get innerHTML() { return this._html; }
    set innerHTML(value) { this._html = value; this.children = []; }
    appendChild(child) { this.children.push(child); return child; }
    addEventListener(event, callback) { this.listeners[event] = callback; }
    focus() {}
    remove() { this.removed = true; }
    click() { return this.listeners.click?.({ stopPropagation() {} }); }
    querySelector(selector) { return selector === '.body' ? this.body : null; }
  }
  const messages = roles.map((role, index) => {
    const message = new Element();
    message.body = new Element();
    message.body.textContent = `${role} ${index}`;
    message.body.innerHTML = message.body.textContent;
    message.dataset.raw = message.body.textContent;
    return message;
  });
  input = new Element('input');
  const box = { querySelectorAll: () => messages };
  const document = {
    getElementById: id => id === 'chat-history' ? box : null,
    createElement: tag => new Element(tag),
    querySelector: selector => selector === '.send-btn'
      ? { click: () => sends.push(input.value) } : null,
  };
  const uiModule = {
    el: id => id === 'message' ? input : null,
    styledConfirm: async (message, options) => {
      confirmations.push({ message, options });
      return approved;
    },
    showError: message => { throw new Error(message); },
  };
  const sessionModule = { getCurrentSessionId: () => 'session-1' };
  const fetch = async (url, options) => {
    requests.push({ url, body: JSON.parse(options.body) });
    return { ok: true, status: 200 };
  };
  const edit = new Function('document', 'window', 'sessionModule', 'uiModule',
    'API_BASE', 'fetch', 'console', `${editSource}; return editUserMessage;`)(
    document, { innerWidth: 1200 }, sessionModule, uiModule, '', fetch, console);
  const target = messages[selected];
  await edit(target);
  const editor = target.body.children.find(child => child.tag === 'textarea');
  const row = target.body.children.find(child => child.children.length === 2);
  editor.value = 'edited message';
  await row.children[0].click();
  return {
    confirmations, requests, sends,
    removed: messages.map(message => message.removed),
    editorStillOpen: target.body.children.includes(editor),
  };
}

(async () => {
  const roles = ['user', 'ai', 'user', 'ai'];
  const cancelled = await scenario(roles, 0, false);
  const confirmed = await scenario(roles, 0, true);
  const latest = await scenario(roles, 2, true);
  console.log(JSON.stringify({ cancelled, confirmed, latest }));
})().catch(error => { console.error(error); process.exitCode = 1; });
""".replace("CHAT_PATH", json.dumps(str(CHAT_JS)))
    result = subprocess.run(
        ["node"], input=script, text=True, capture_output=True, timeout=20
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)

    cancelled = observed["cancelled"]
    assert len(cancelled["confirmations"]) == 1
    assert "2 messages" in cancelled["confirmations"][0]["message"]
    assert cancelled["requests"] == []
    assert cancelled["sends"] == []
    assert cancelled["removed"] == [False] * 4
    assert cancelled["editorStillOpen"] is True

    confirmed = observed["confirmed"]
    assert confirmed["requests"][0]["body"] == {"keep_count": 0}
    assert confirmed["sends"] == ["edited message"]
    assert confirmed["removed"] == [True] * 4

    latest = observed["latest"]
    assert latest["confirmations"] == []
    assert latest["requests"][0]["body"] == {"keep_count": 2}
    assert latest["sends"] == ["edited message"]
