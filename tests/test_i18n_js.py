import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")


def _node_json(source: str):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return json.loads(result.stdout.splitlines()[-1])


def test_i18n_runtime_fallback_and_interpolation():
    values = _node_json(
        textwrap.dedent(
            """
            import { createI18nRuntime } from './static/js/i18n.js';
            const runtime = createI18nRuntime({
              storage: new Map(),
              fetchCatalog: async (lang) => ({
                en: { common: { save: 'Save' }, chat: { hello: 'Hello, {{name}}' } },
                'zh-CN': { common: { save: '保存' } }
              }[lang]),
              navigatorLanguages: ['zh-CN'],
            });
            await runtime.ready;
            console.log(JSON.stringify({
              lang: runtime.getLanguage(),
              save: runtime.t('common.save'),
              fallback: runtime.t('chat.hello', { name: 'Odysseus' }),
              missing: runtime.t('missing.key'),
            }));
            """
        )
    )
    assert values == {
        "lang": "zh-CN",
        "save": "保存",
        "fallback": "Hello, Odysseus",
        "missing": "missing.key",
    }


def test_i18n_applies_dom_attributes():
    values = _node_json(
        textwrap.dedent(
            """
            import { createI18nRuntime } from './static/js/i18n.js';
            const nodes = [
              { dataset: { i18n: 'common.save' }, textContent: '' },
              { dataset: { i18nPlaceholder: 'chat.placeholder' }, setAttribute(name, value) { this[name] = value; } },
              { dataset: { i18nTitle: 'common.close' }, setAttribute(name, value) { this[name] = value; } },
            ];
            const documentLike = {
              querySelectorAll(selector) {
                if (selector.includes('data-i18n')) return nodes;
                return [];
              },
              documentElement: { setAttribute(name, value) { this[name] = value; } },
            };
            const runtime = createI18nRuntime({
              storage: new Map(),
              fetchCatalog: async () => ({
                common: { save: 'Save', close: 'Close' },
                chat: { placeholder: 'Message Odysseus...' },
              }),
              documentRef: documentLike,
            });
            await runtime.ready;
            runtime.applyToDocument(documentLike);
            console.log(JSON.stringify({
              text: nodes[0].textContent,
              placeholder: nodes[1].placeholder,
              title: nodes[2].title,
              lang: documentLike.documentElement.lang,
            }));
            """
        )
    )
    assert values == {
        "text": "Save",
        "placeholder": "Message Odysseus...",
        "title": "Close",
        "lang": "en",
    }


def test_zh_cn_catalog_keys_match_english():
    en = json.loads((ROOT / "static/i18n/en.json").read_text(encoding="utf-8"))
    zh = json.loads((ROOT / "static/i18n/zh-CN.json").read_text(encoding="utf-8"))

    def flatten(prefix, obj):
        out = set()
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                out.update(flatten(path, value))
            else:
                out.add(path)
        return out

    assert flatten("", zh) == flatten("", en)
