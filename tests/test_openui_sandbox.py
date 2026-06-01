"""Smoke coverage for the OpenUI iframe isolation boundary."""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HAS_NODE = shutil.which("node") is not None


@pytest.fixture(scope="module")
def node_available():
    if not _HAS_NODE:
        pytest.skip("node binary not on PATH")


def _run_node(script: str) -> dict:
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=_REPO,
        capture_output=True,
        timeout=15,
        text=True,
    )
    if res.returncode != 0:
        raise AssertionError(f"node failed:\n{res.stderr}")
    lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    if not lines:
        raise AssertionError("node produced no stdout")
    return json.loads(lines[-1])


def test_openui_iframe_sandbox_is_tight(node_available):
    script = textwrap.dedent("""
        const { OPENUI_IFRAME_SANDBOX } = await import('./static/js/openuiSandbox.js');
        console.log(JSON.stringify({
          sandbox: OPENUI_IFRAME_SANDBOX,
          has_scripts: OPENUI_IFRAME_SANDBOX.split(/\\s+/).includes('allow-scripts'),
          has_same_origin: OPENUI_IFRAME_SANDBOX.includes('allow-same-origin'),
          has_forms: OPENUI_IFRAME_SANDBOX.includes('allow-forms'),
          has_popups: OPENUI_IFRAME_SANDBOX.includes('allow-popups'),
          has_top_nav: OPENUI_IFRAME_SANDBOX.includes('allow-top-navigation'),
        }));
    """)
    out = _run_node(script)
    assert out == {
        "sandbox": "allow-scripts",
        "has_scripts": True,
        "has_same_origin": False,
        "has_forms": False,
        "has_popups": False,
        "has_top_nav": False,
    }


def test_openui_action_allowlist_blocks_urls(node_available):
    script = textwrap.dedent("""
        const { sanitizeSandboxAction } = await import('./static/js/openuiSandbox.js');
        const allowed = sanitizeSandboxAction({
          type: 'continue_conversation',
          humanFriendlyMessage: 'Submit',
          formName: 'lead',
          formState: { lead: { email: { value: 'a@example.com' } } },
          ignored: () => 'not serializable',
        });
        const blockedUrl = sanitizeSandboxAction({
          type: 'open_url',
          params: { url: 'https://example.com' },
        });
        const blockedUnknown = sanitizeSandboxAction({ type: 'delete_everything' });
        console.log(JSON.stringify({ allowed, blockedUrl, blockedUnknown }));
    """)
    out = _run_node(script)
    assert out["allowed"] == {
        "type": "continue_conversation",
        "humanFriendlyMessage": "Submit",
        "formName": "lead",
        "formState": {"lead": {"email": {"value": "a@example.com"}}},
    }
    assert out["blockedUrl"] is None
    assert out["blockedUnknown"] is None


def test_openui_parent_bridge_posts_render_and_forwards_allowed_actions(node_available):
    script = textwrap.dedent("""
        class FakeElement {
          constructor(tag) {
            this.tag = tag;
            this.children = [];
            this.attrs = {};
            this.listeners = {};
            this.style = {};
            this.dataset = {};
            this.innerHTML = '';
            this.className = '';
            this.classList = {
              values: new Set(),
              add: (name) => this.classList.values.add(name),
              remove: (name) => this.classList.values.delete(name),
            };
            this.contentWindow = tag === 'iframe' ? { postMessage: (msg) => posts.push(msg) } : null;
          }
          setAttribute(name, value) { this.attrs[name] = String(value); }
          getAttribute(name) { return this.attrs[name]; }
          addEventListener(type, fn) { this.listeners[type] = fn; }
          appendChild(child) { this.children.push(child); return child; }
        }

        const listeners = {};
        const events = [];
        const posts = [];
        globalThis.window = {
          addEventListener: (type, fn) => { listeners[type] = fn; },
          dispatchEvent: (ev) => events.push({ type: ev.type, detail: ev.detail }),
        };
        globalThis.CustomEvent = class CustomEvent {
          constructor(type, init) { this.type = type; this.detail = init?.detail; }
        };
        globalThis.document = {
          documentElement: {},
          createElement: (tag) => new FakeElement(tag),
        };
        globalThis.getComputedStyle = () => ({
          fontFamily: 'system-ui',
          getPropertyValue: (name) => ({
            '--bg': '#fff',
            '--fg': '#111',
            '--accent': '#0aa',
            '--border': '#ddd',
            '--font-family': 'system-ui',
          })[name] || '',
        });

        const { renderSandboxedOpenUI } = await import('./static/js/openuiSandbox.js');
        const mount = new FakeElement('div');
        renderSandboxedOpenUI(mount, 'root = Card()', {
          isStreaming: true,
          forwardActions: true,
          onState: (state) => { globalThis.lastState = state; },
        });
        const iframe = mount.children[0];
        iframe.listeners.load();
        const renderPost = posts[0];

        listeners.message({
          source: iframe.contentWindow,
          data: {
            source: 'odysseus-openui-sandbox',
            id: renderPost.id,
            type: 'action',
            action: { type: 'continue_conversation', humanFriendlyMessage: 'Submit' },
          },
        });
        listeners.message({
          source: iframe.contentWindow,
          data: {
            source: 'odysseus-openui-sandbox',
            id: renderPost.id,
            type: 'action',
            action: { type: 'open_url', params: { url: 'https://example.com' } },
          },
        });

        console.log(JSON.stringify({
          sandbox: iframe.attrs.sandbox,
          src: iframe.src,
          postType: renderPost.type,
          isStreaming: renderPost.isStreaming,
          response: renderPost.response,
          eventCount: events.length,
          eventDetail: events[0]?.detail,
        }));
    """)
    out = _run_node(script)
    assert out["sandbox"] == "allow-scripts"
    assert out["src"] == "/static/openui-sandbox.html"
    assert out["postType"] == "render"
    assert out["isStreaming"] is True
    assert out["response"] == "root = Card()"
    assert out["eventCount"] == 1
    assert out["eventDetail"] == {
        "type": "continue_conversation",
        "humanFriendlyMessage": "Submit",
        "formState": None,
    }


def test_openui_call_sites_use_sandbox_bridge():
    chat_renderer = (_REPO / "static/js/chatRenderer.js").read_text()
    document_js = (_REPO / "static/js/document.js").read_text()
    sandbox_html = (_REPO / "static/openui-sandbox.html").read_text()

    assert "renderSandboxedOpenUI" in chat_renderer
    assert "renderSandboxedOpenUI" in document_js
    assert "import('/static/vendor/openui-renderer.js')" not in chat_renderer
    assert "import('/static/vendor/openui-renderer.js')" not in document_js
    assert "/static/js/openuiSandboxFrame.js" in sandbox_html
    assert "odysseus-openui-parent" in (_REPO / "static/js/openuiSandboxFrame.js").read_text()
