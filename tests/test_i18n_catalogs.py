"""Integrity and static-coverage tests for the browser pt-BR catalogs."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from collections import defaultdict
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = ROOT / "static" / "js" / "i18n"
INDEX_HTML = ROOT / "static" / "index.html"
INDEX_CATALOG = CATALOG_DIR / "index-html.pt-BR.js"
CATALOGS = tuple(sorted(CATALOG_DIR.glob("*.pt-BR.js")))
HAS_NODE = shutil.which("node") is not None

TRANSLATABLE_ATTRIBUTES = frozenset(
    {"placeholder", "title", "aria-label", "aria-placeholder"}
)
OMITTED_SUBTREES = frozenset(
    {"script", "style", "code", "pre", "textarea", "template"}
)
VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

# Product/provider names are intentionally preserved. This is a semantic category,
# not a catch-all allowlist for untranslated interface copy.
PRODUCT_TERMS = frozenset(
    {
        "Anthropic",
        "Brave",
        "DeepSeek",
        "DuckDuckGo",
        "Fireworks AI",
        "ChatGPT",
        "GitHub Copilot",
        "Gemini",
        "GitHub",
        "Google",
        "Google Gemini",
        "Groq",
        "LM Studio",
        "Mistral",
        "NVIDIA",
        "Odysseus",
        "Ollama",
        "Ollama Cloud",
        "OpenAI",
        "OpenCode Go",
        "OpenCode Zen",
        "OpenRouter",
        "SearXNG",
        "Serper",
        "Serper.dev",
        "Tavily",
        "Together AI",
        "xAI Grok",
        "Z.AI (Zhipu)",
        "Z.AI Coding Plan",
    }
)


def _run(
    args: list[str], *, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )


@lru_cache(maxsize=1)
def _captured_catalogs() -> tuple[dict[str, object], ...]:
    """Execute real catalog modules with a synthetic registerMessages import."""

    files = [str(path) for path in CATALOGS]
    script = textwrap.dedent(
        f"""
        import fs from 'node:fs';
        import path from 'node:path';
        import vm from 'node:vm';

        const files = {json.dumps(files)};
        const root = {json.dumps(str(ROOT))};
        const captures = [];
        const context = vm.createContext({{}});
        const runtime = new vm.SyntheticModule(
          ['registerMessages'],
          function initialize() {{
            this.setExport('registerMessages', (lang, messages) => {{
              captures.push({{
                file: path.relative(root, context.__currentCatalog),
                lang,
                messages,
              }});
            }});
          }},
          {{ context, identifier: 'catalog-runtime' }},
        );
        await runtime.link(() => {{}});
        await runtime.evaluate();

        for (const file of files) {{
          context.__currentCatalog = file;
          const source = fs.readFileSync(file, 'utf8');
          const catalog = new vm.SourceTextModule(
            source,
            {{ context, identifier: file }},
          );
          await catalog.link((specifier) => {{
            if (specifier === '../i18n.js') return runtime;
            throw new Error(`${{file}}: unexpected import ${{specifier}}`);
          }});
          await catalog.evaluate();
        }}
        console.log(JSON.stringify(captures));
        """
    )
    result = _run(
        ["node", "--experimental-vm-modules", "--input-type=module"],
        input_text=script,
    )
    assert result.returncode == 0, result.stderr
    return tuple(json.loads(result.stdout))


class _VisibleIndexStrings(HTMLParser):
    """Collect strings translateDOM can reach, retaining source locations."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[tuple[str, bool]] = []
        self._blocked_depth = 0
        self.found: dict[str, list[str]] = defaultdict(list)

    def _record(self, value: str, kind: str) -> None:
        # Keep internal whitespace byte-for-byte: translateDOM() uses raw.trim()
        # and therefore only removes the edges before looking up the key.
        value = value.strip()
        if value:
            line, column = self.getpos()
            self.found[value].append(f"{INDEX_HTML}:{line}:{column + 1} ({kind})")

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        tag = tag.lower()
        attr_map = dict(attrs)
        blocked = (
            self._blocked_depth > 0
            or tag in OMITTED_SUBTREES
            or "data-i18n-skip" in attr_map
        )
        if not blocked:
            for name, value in attrs:
                if name in TRANSLATABLE_ATTRIBUTES and value:
                    self._record(value, f"atributo {name}")
        if tag not in VOID_ELEMENTS:
            self._stack.append((tag, blocked))
            if blocked:
                self._blocked_depth += 1

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_ELEMENTS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] != tag:
                continue
            removed = self._stack[index:]
            del self._stack[index:]
            self._blocked_depth -= sum(blocked for _, blocked in removed)
            return

    def handle_data(self, data: str) -> None:
        if self._blocked_depth == 0:
            self._record(data, "texto")


def _visible_index_strings() -> dict[str, list[str]]:
    parser = _VisibleIndexStrings()
    parser.feed(INDEX_HTML.read_text(encoding="utf-8"))
    parser.close()
    return parser.found


def _exclusion_category(value: str) -> str | None:
    """Return the documented reason a visible value is not interface prose."""

    if value in PRODUCT_TERMS:
        return "termo de produto/provedor preservado"
    if "{{" in value or "{%" in value or "%}" in value:
        return "conteúdo técnico de template"
    if not any(character.isalpha() for character in value):
        return "número ou símbolo"
    if re.fullmatch(r"#[0-9a-f]{3,8}", value, flags=re.IGNORECASE):
        return "valor hexadecimal"
    if re.fullmatch(r"(?:API|LLM|PDF|RAG|URL|DEBUG|INFO|WARNING|ERROR)", value):
        return "sigla técnica"
    if re.fullmatch(r"(?:A-Z|A\|C|Aa|\d+(?:\.\d+)?x)", value):
        return "controle técnico ou escala numérica"
    if re.fullmatch(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", value):
        return "endereço técnico de exemplo"
    if re.fullmatch(r"[a-z][a-z0-9]*(?:[_-][a-z0-9]+)+", value):
        return "identificador técnico"
    if re.match(r"^(?:https?|wss?)://", value, flags=re.IGNORECASE):
        return "URL técnica"
    if value.startswith(("/api/", "./", "../")):
        return "caminho técnico"
    if re.fullmatch(r"[\w.-]+/[\w./-]+", value):
        return "identificador técnico"
    if re.fullmatch(r"[\w.-]+\.(?:json|js|css|html|md|py|txt)", value):
        return "nome de arquivo técnico"
    return None


def _messages_for(catalog_name: str) -> dict[str, str]:
    registrations = [
        item
        for item in _captured_catalogs()
        if Path(str(item["file"])).name == catalog_name
    ]
    assert len(registrations) == 1, (
        f"{catalog_name}: esperado um registerMessages(), "
        f"encontrados {len(registrations)}"
    )
    assert registrations[0]["lang"] == "pt-BR"
    return dict(registrations[0]["messages"])


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
@pytest.mark.parametrize("catalog", CATALOGS, ids=lambda path: path.name)
def test_catalog_is_valid_javascript(catalog: Path) -> None:
    result = _run(["node", "--check", str(catalog)])
    assert result.returncode == 0, (
        f"{catalog}: falha no node --check\n{result.stdout}{result.stderr}"
    )


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_registered_translations_are_non_empty() -> None:
    empty: list[str] = []
    for registration in _captured_catalogs():
        for key, value in dict(registration["messages"]).items():
            if not isinstance(value, str) or not value.strip():
                empty.append(f"{registration['file']}: {key!r}")
    assert not empty, "Traduções vazias registradas:\n" + "\n".join(empty)


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_duplicate_keys_have_one_canonical_translation() -> None:
    occurrences: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for registration in _captured_catalogs():
        for key, value in dict(registration["messages"]).items():
            occurrences[key].append((str(registration["file"]), str(value)))

    divergent = {
        key: entries
        for key, entries in occurrences.items()
        if len({value for _, value in entries}) > 1
    }
    details = "\n".join(
        f"{key!r}: "
        + "; ".join(f"{file} -> {value!r}" for file, value in entries)
        for key, entries in sorted(divergent.items())
    )
    assert not divergent, "Traduções divergentes para chaves duplicadas:\n" + details


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_index_catalog_covers_all_visible_static_strings() -> None:
    catalog = _messages_for(INDEX_CATALOG.name)
    required = {
        key: locations
        for key, locations in _visible_index_strings().items()
        if _exclusion_category(key) is None
    }
    missing = sorted(set(required) - set(catalog))
    details = "\n".join(
        f"- {key!r} em {', '.join(required[key])}" for key in missing
    )
    assert not missing, (
        f"{INDEX_CATALOG}: {len(missing)} chave(s) estática(s) ausente(s):\n"
        f"{details}"
    )
