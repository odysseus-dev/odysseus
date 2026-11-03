"""Tests for Part A — Endpoint Card UI Polish and Model Tools Selector Layout."""

import json
import shutil
import subprocess
from pathlib import Path
import pytest
from tests.helpers.stylesheets import app_css

_REPO = Path(__file__).resolve().parent.parent
_ADMIN_JS = _REPO / "static" / "js" / "admin.js"
_ADMIN = _ADMIN_JS.read_text(encoding="utf-8")
_STYLE = app_css()
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")


def _run_node(script: str):
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


class TestEndpointCardUrlPresentation:
    def test_should_display_endpoint_base_url_policy(self):
        js = f"""
          import fs from 'node:fs';
          import {{ isChatgptSubscriptionEndpoint }} from '{(_REPO / 'static' / 'js' / 'chatgptSubscriptionUsage.js').as_posix()}';
          const source = fs.readFileSync('{_ADMIN_JS.as_posix()}', 'utf8');
          const fnStart = source.indexOf('function shouldDisplayEndpointBaseUrl');
          const fnEnd = source.indexOf('// ChatGPT per-endpoint usage panel', fnStart);
          const fnCode = source.slice(fnStart, fnEnd);
          const fns = new Function('isChatgptSubscriptionEndpoint', 'esc',
            fnCode + '; return {{ shouldDisplayEndpointBaseUrl, isFeatherlessEndpoint, endpointDetailHtml }};'
          )(isChatgptSubscriptionEndpoint, x => String(x));

          const results = {{
            chatgpt: fns.shouldDisplayEndpointBaseUrl({{
              base_url: 'https://chatgpt.com/backend-api/codex',
              provider: 'chatgpt-subscription',
              provider_auth_id: 'auth-123'
            }}),
            featherless: fns.shouldDisplayEndpointBaseUrl({{
              base_url: 'https://api.featherless.ai/v1',
              name: 'Featherless.ai'
            }}),
            featherless_subdomain: fns.shouldDisplayEndpointBaseUrl({{
              base_url: 'https://eu.featherless.ai/v1'
            }}),
            generic: fns.shouldDisplayEndpointBaseUrl({{
              base_url: 'https://api.example.com/v1',
              name: 'Custom AI'
            }}),
            local: fns.shouldDisplayEndpointBaseUrl({{
              base_url: 'http://127.0.0.1:11434',
              name: 'Ollama'
            }}),
            null_ep: fns.shouldDisplayEndpointBaseUrl(null)
          }};
          console.log(JSON.stringify(results));
        """
        out = _run_node(js)
        assert out["chatgpt"] is False, "ChatGPT raw transport URL must be hidden"
        assert out["featherless"] is False, "Featherless raw transport URL must be hidden"
        assert out["featherless_subdomain"] is False, "Featherless domain variants must be hidden"
        assert out["generic"] is True, "Generic custom endpoint URL must remain visible"
        assert out["local"] is True, "Local endpoint URL must remain visible"
        assert out["null_ep"] is False

    def test_endpoint_detail_html_suppresses_first_class_urls_and_keeps_others(self):
        js = f"""
          import fs from 'node:fs';
          import {{ isChatgptSubscriptionEndpoint }} from '{(_REPO / 'static' / 'js' / 'chatgptSubscriptionUsage.js').as_posix()}';
          const source = fs.readFileSync('{_ADMIN_JS.as_posix()}', 'utf8');
          const fnStart = source.indexOf('function shouldDisplayEndpointBaseUrl');
          const fnEnd = source.indexOf('// ChatGPT per-endpoint usage panel', fnStart);
          const fnCode = source.slice(fnStart, fnEnd);
          const fns = new Function('isChatgptSubscriptionEndpoint', 'esc',
            fnCode + '; return {{ shouldDisplayEndpointBaseUrl, isFeatherlessEndpoint, endpointDetailHtml }};'
          )(isChatgptSubscriptionEndpoint, x => String(x));

          const chatgptEp = {{
            base_url: 'https://chatgpt.com/backend-api/codex',
            provider: 'chatgpt-subscription',
            provider_auth_id: 'auth-1',
            has_key: false
          }};
          const featherlessEp = {{
            base_url: 'https://api.featherless.ai/v1',
            name: 'Featherless.ai',
            has_key: true,
            api_key_fingerprint: 'abcd1234'
          }};
          const genericEp = {{
            base_url: 'https://api.custom.com/v1',
            name: 'Custom',
            has_key: true,
            api_key_fingerprint: 'ef5678'
          }};
          const localEp = {{
            base_url: 'http://127.0.0.1:11434',
            name: 'Local',
            has_key: false
          }};

          const results = {{
            chatgptHtml: fns.endpointDetailHtml(chatgptEp, 'api'),
            featherlessHtml: fns.endpointDetailHtml(featherlessEp, 'api'),
            genericHtml: fns.endpointDetailHtml(genericEp, 'api'),
            localHtml: fns.endpointDetailHtml(localEp, 'local')
          }};
          console.log(JSON.stringify(results));
        """
        out = _run_node(js)
        # ChatGPT card suppresses raw base URL and has no key, so no detail line
        assert "chatgpt.com" not in out["chatgptHtml"]
        assert out["chatgptHtml"] == ""

        # Featherless suppresses raw base URL but can display key fingerprint if present
        assert "api.featherless.ai" not in out["featherlessHtml"]
        assert "abcd1234" in out["featherlessHtml"]

        # Generic custom endpoint retains URL and key
        assert "https://api.custom.com/v1" in out["genericHtml"]
        assert "ef5678" in out["genericHtml"]

        # Local endpoint retains URL and copy affordance
        assert "http://127.0.0.1:11434" in out["localHtml"]
        assert 'data-adm-copy-url="http://127.0.0.1:11434"' in out["localHtml"]
        assert "admin-ep-copy-btn" in out["localHtml"]


class TestChatGPTCardLayoutAndControls:
    def test_chatgpt_controls_secondary_action_layout(self):
        # Verify right-alignment in style.css
        assert ".adm-chatgpt-controls {" in _STYLE
        controls_block = _STYLE.split(".adm-chatgpt-controls {")[1].split("}")[0]
        assert "justify-content: flex-end" in controls_block
        assert "display: flex" in controls_block

    def test_chatgpt_usage_attributes_and_lazy_fetch_retained(self):
        assert 'aria-controls="adm-chatgpt-usage-${esc(ep.id)}"' in _ADMIN
        assert 'aria-expanded="${isUsageExpanded ? \'true\' : \'false\'}"' in _ADMIN
        assert "data-adm-chatgpt-usage-toggle" in _ADMIN
        assert "data-adm-chatgpt-reconnect" in _ADMIN
        assert "adm-chatgpt-usage-host" in _ADMIN
        # Lazy loading on toggle click
        assert "_loadChatgptUsage(host, authId, epId)" in _ADMIN
        assert "_isChatgptUsageExpanded(epId, authId)" in _ADMIN


class TestModelRowToolsSelectLayout:
    def test_css_classes_prevent_clipping_and_guarantee_layout(self):
        # Audit classes in style.css
        assert ".adm-model-row {" in _STYLE
        assert ".adm-model-label {" in _STYLE
        assert ".adm-model-name {" in _STYLE
        assert ".adm-model-tools-col {" in _STYLE
        assert ".adm-model-tool-mode," in _STYLE or ".adm-model-tool-mode {" in _STYLE
        assert ".admin-tools-select" in _STYLE

        # Verify tool-mode select styling
        tool_mode_css = _STYLE.split(".adm-model-tool-mode,")[1].split("}")[0]
        assert "height: 24px" in tool_mode_css
        assert "line-height: 22px" in tool_mode_css
        assert "padding: 0 18px 0 6px" in tool_mode_css
        assert "box-sizing: border-box" in tool_mode_css
        assert "width: 124px" in tool_mode_css
        assert "vertical-align: middle" in tool_mode_css

        # Verify right column is fixed / shrink-safe
        col_css = _STYLE.split(".adm-model-tools-col {")[1].split("}")[0]
        assert "flex: 0 0 auto" in col_css
        assert "margin-left: auto" in col_css

        # Verify model name is flexible with ellipsis
        name_css = _STYLE.split(".adm-model-name {")[1].split("}")[0]
        assert "overflow: hidden" in name_css
        assert "text-overflow: ellipsis" in name_css
        assert "white-space: nowrap" in name_css
        assert "flex: 1" in name_css

    def test_admin_js_uses_classes_instead_of_inline_clipping_styles(self):
        assert 'class="adm-model-row"' in _ADMIN
        assert 'class="adm-model-label"' in _ADMIN
        assert 'class="adm-model-name"' in _ADMIN
        assert 'class="adm-model-tools-col"' in _ADMIN
        assert 'class="adm-model-tool-mode admin-tools-select"' in _ADMIN
