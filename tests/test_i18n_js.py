"""Behavioral coverage for the browser i18n runtime.

The tests execute the real ES modules with Node.  Tiny browser fakes provide
only the DOM/storage surface each behavior needs; no production source is
copied into the test harness.
"""

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parent.parent
_INDEX_HTML = _REPO / "static" / "index.html"
_HAS_NODE = shutil.which("node") is not None

pytestmark = pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")


def _run_node(script: str) -> dict:
    result = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines, "node produced no output"
    return json.loads(lines[-1])


def _language_bootstrap() -> str:
    html = _INDEX_HTML.read_text(encoding="utf-8")
    match = re.search(
        r"<!-- Resolve the interface language.*?"
        r"<script nonce=\"\{\{CSP_NONCE\}\}\">\s*(.*?)\s*</script>",
        html,
        flags=re.DOTALL,
    )
    assert match, "language bootstrap script not found"
    return match.group(1)


def test_normalize_lang_allows_only_exact_supported_values():
    result = _run_node(
        textwrap.dedent(
            """
            globalThis.window = {};
            const { normalizeLang } = await import('./static/js/i18n.js?normalize');
            const objectValue = {};
            console.log(JSON.stringify({
              ptBR: normalizeLang('pt-BR'),
              en: normalizeLang('en'),
              lowercase: normalizeLang('pt-br'),
              empty: normalizeLang(''),
              nullValue: normalizeLang(null),
              objectValue: normalizeLang(objectValue),
            }));
            """
        )
    )
    assert result == {
        "ptBR": "pt-BR",
        "en": "en",
        "lowercase": "pt-BR",
        "empty": "pt-BR",
        "nullValue": "pt-BR",
        "objectValue": "pt-BR",
    }


def test_active_language_normalizes_window_and_storage_values():
    result = _run_node(
        textwrap.dedent(
            """
            globalThis.window = { __ODY_LANG: 'fr' };
            globalThis.localStorage = { getItem: () => 'en' };
            const first = await import('./static/js/i18n.js?active-window');
            const fromWindow = first.getLang();

            delete globalThis.window.__ODY_LANG;
            globalThis.localStorage = { getItem: () => 'pt-br' };
            const second = await import('./static/js/i18n.js?active-storage');

            globalThis.window.__ODY_LANG = '';
            globalThis.localStorage = { getItem: () => 'en' };
            const third = await import('./static/js/i18n.js?active-empty-window');
            console.log(JSON.stringify({
              fromWindow,
              fromStorage: second.getLang(),
              fromEmptyWindow: third.getLang(),
            }));
            """
        )
    )
    assert result == {
        "fromWindow": "pt-BR",
        "fromStorage": "pt-BR",
        "fromEmptyWindow": "pt-BR",
    }


def test_set_lang_normalizes_before_persisting_and_sending():
    result = _run_node(
        textwrap.dedent(
            """
            const writes = [];
            const requests = [];
            let reloads = 0;
            globalThis.window = {};
            globalThis.localStorage = {
              getItem: () => null,
              setItem: (key, value) => writes.push([key, value]),
            };
            globalThis.fetch = async (url, options) => {
              requests.push([url, JSON.parse(options.body)]);
              return { ok: true };
            };
            globalThis.location = { reload: () => reloads++ };

            const { getLang, setLang } = await import('./static/js/i18n.js?set-lang');
            await setLang('pt-br');
            console.log(JSON.stringify({
              active: getLang(),
              writes,
              requests,
              reloads,
            }));
            """
        )
    )
    assert result == {
        "active": "pt-BR",
        "writes": [["odysseus-language", "pt-BR"]],
        "requests": [["/api/prefs/language", {"value": "pt-BR"}]],
        "reloads": 1,
    }


def test_translation_fallback_and_placeholder_interpolation():
    result = _run_node(
        textwrap.dedent(
            """
            globalThis.window = { __ODY_LANG: 'pt-BR' };
            const { registerMessages, t } = await import('./static/js/i18n.js?translate');
            registerMessages('pt-BR', {
              'Save': 'Salvar',
              'Hello {name}, {count} item(s)': 'Ola {name}, {count} item(ns)',
            });
            const portuguese = t('Save');
            const interpolated = t(
              'Hello {name}, {count} item(s)',
              { name: 'Ada', count: 2 },
            );
            const fallback = t('Missing {name}', { name: 'Lin' });
            window.__ODY_LANG = 'en';
            const english = t('Save');
            console.log(JSON.stringify({ portuguese, interpolated, fallback, english }));
            """
        )
    )
    assert result == {
        "portuguese": "Salvar",
        "interpolated": "Ola Ada, 2 item(ns)",
        "fallback": "Missing Lin",
        "english": "Save",
    }


def test_translate_dom_skip_protects_descendant_attributes():
    result = _run_node(
        textwrap.dedent(
            """
            function element(name, attrs = {}, parentNode = null) {
              return {
                nodeType: 1,
                nodeName: name,
                parentNode,
                attrs: { ...attrs },
                hasAttribute(key) { return Object.hasOwn(this.attrs, key); },
                getAttribute(key) { return this.attrs[key] ?? null; },
                setAttribute(key, value) { this.attrs[key] = value; },
                querySelectorAll() { return []; },
              };
            }

            const root = element('DIV');
            const skipped = element('SECTION', { 'data-i18n-skip': '' }, root);
            const protectedChild = element('INPUT', { title: 'Save' }, skipped);
            const translatedChild = element('INPUT', { title: 'Save' }, root);
            root.querySelectorAll = () => [skipped, protectedChild, translatedChild];

            globalThis.window = { __ODY_LANG: 'pt-BR' };
            globalThis.NodeFilter = {
              SHOW_TEXT: 4,
              FILTER_REJECT: 2,
              FILTER_ACCEPT: 1,
            };
            globalThis.document = {
              createTreeWalker: () => ({ nextNode: () => null }),
            };

            const { registerMessages, translateDOM } =
              await import('./static/js/i18n.js?dom-skip');
            registerMessages('pt-BR', { 'Save': 'Salvar' });
            translateDOM(root);
            console.log(JSON.stringify({
              protectedTitle: protectedChild.getAttribute('title'),
              translatedTitle: translatedChild.getAttribute('title'),
            }));
            """
        )
    )
    assert result == {
        "protectedTitle": "Save",
        "translatedTitle": "Salvar",
    }


@pytest.mark.parametrize(
    ("stored", "expected_lang", "expected_html_lang"),
    [
        ("pt-BR", "pt-BR", "pt-br"),
        ("en", "en", "en"),
        ("pt-br", "pt-BR", "pt-br"),
        ("fr", "pt-BR", "pt-br"),
        (None, "pt-BR", "pt-br"),
    ],
)
def test_inline_bootstrap_normalizes_and_syncs_html_lang(
    stored, expected_lang, expected_html_lang
):
    bootstrap = json.dumps(_language_bootstrap())
    stored_js = json.dumps(stored)
    result = _run_node(
        textwrap.dedent(
            f"""
            globalThis.window = {{}};
            globalThis.document = {{ documentElement: {{ lang: 'stale' }} }};
            globalThis.localStorage = {{ getItem: () => {stored_js} }};
            eval({bootstrap});
            console.log(JSON.stringify({{
              active: window.__ODY_LANG,
              htmlLang: document.documentElement.lang,
            }}));
            """
        )
    )
    assert result == {"active": expected_lang, "htmlLang": expected_html_lang}


def test_inline_bootstrap_syncs_html_lang_when_storage_throws():
    bootstrap = json.dumps(_language_bootstrap())
    result = _run_node(
        textwrap.dedent(
            f"""
            globalThis.window = {{}};
            globalThis.document = {{ documentElement: {{ lang: 'stale' }} }};
            globalThis.localStorage = {{
              getItem: () => {{ throw new Error('storage unavailable'); }},
            }};
            eval({bootstrap});
            console.log(JSON.stringify({{
              active: window.__ODY_LANG,
              htmlLang: document.documentElement.lang,
            }}));
            """
        )
    )
    assert result == {"active": "pt-BR", "htmlLang": "pt-br"}


@pytest.mark.parametrize(
    ("active", "remote", "expected"),
    [
        (
            "pt-BR",
            "en",
            {
                "selected": "en",
                "stored": "en",
                "writes": 1,
                "gets": 1,
                "puts": 1,
                "reloads": 1,
            },
        ),
        (
            "en",
            "pt-br",
            {
                "selected": "en",
                "stored": None,
                "writes": 0,
                "gets": 1,
                "puts": 0,
                "reloads": 0,
            },
        ),
        (
            "en",
            "",
            {
                "selected": "en",
                "stored": None,
                "writes": 0,
                "gets": 1,
                "puts": 0,
                "reloads": 0,
            },
        ),
        (
            "en",
            None,
            {
                "selected": "en",
                "stored": None,
                "writes": 0,
                "gets": 1,
                "puts": 0,
                "reloads": 0,
            },
        ),
        (
            "en",
            {"code": "pt-BR"},
            {
                "selected": "en",
                "stored": None,
                "writes": 0,
                "gets": 1,
                "puts": 0,
                "reloads": 0,
            },
        ),
        (
            "pt-BR",
            "pt-BR",
            {
                "selected": "pt-BR",
                "stored": "pt-BR",
                "writes": 1,
                "gets": 1,
                "puts": 0,
                "reloads": 0,
            },
        ),
    ],
)
def test_remote_language_reconciliation(active, remote, expected):
    result = _run_node(
        textwrap.dedent(
            f"""
            const active = {json.dumps(active)};
            const remote = {json.dumps(remote)};
            const writes = [];
            const requests = [];
            let reloads = 0;
            const select = {{
              value: '',
              dataset: {{}},
              addEventListener() {{}},
            }};
            globalThis.window = {{ __ODY_LANG: active }};
            globalThis.document = {{
              getElementById: (id) => id === 'set-language' ? select : null,
            }};
            globalThis.localStorage = {{
              getItem: () => active,
              setItem: (key, value) => writes.push([key, value]),
            }};
            globalThis.location = {{ reload: () => reloads++ }};
            globalThis.fetch = async (url, options = {{}}) => {{
              requests.push([url, options.method || 'GET']);
              if (!options.method) {{
                return {{ ok: true, json: async () => ({{ value: remote }}) }};
              }}
              return {{ ok: true }};
            }};

            const {{ initLanguagePref }} =
              await import('./static/js/languagePref.js?reconcile');
            initLanguagePref();
            await new Promise((resolve) => setTimeout(resolve, 0));
            await new Promise((resolve) => setTimeout(resolve, 0));

            console.log(JSON.stringify({{
              selected: select.value,
              stored: writes.length ? writes.at(-1)[1] : null,
              writes: writes.length,
              gets: requests.filter(([, method]) => method === 'GET').length,
              puts: requests.filter(([, method]) => method === 'PUT').length,
              reloads,
            }}));
            """
        )
    )
    assert result == expected
