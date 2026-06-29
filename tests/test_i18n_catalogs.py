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
APP_JS = ROOT / "static" / "app.js"
APP_CATALOG = CATALOG_DIR / "app.pt-BR.js"
UI_JS = ROOT / "static" / "js" / "ui.js"
MODAL_MANAGER_JS = ROOT / "static" / "js" / "modalManager.js"
SPINNER_JS = ROOT / "static" / "js" / "spinner.js"
WORKSPACE_JS = ROOT / "static" / "js" / "workspace.js"
MODAL_SNAP_JS = ROOT / "static" / "js" / "modalSnap.js"
SHARED_UI_CATALOG = CATALOG_DIR / "shared-ui.pt-BR.js"
WORKSPACE_CATALOG = CATALOG_DIR / "workspace-misc.pt-BR.js"
CATALOGS = tuple(sorted(CATALOG_DIR.glob("*.pt-BR.js")))
HAS_NODE = shutil.which("node") is not None
APP_IMPORT_RE = re.compile(
    r"""^import\s+(?:[^'"]+\s+from\s+)?['"]([^'"]+)['"];\s*$""",
    re.MULTILINE,
)

TRANSLATABLE_ATTRIBUTES = frozenset(
    {"placeholder", "title", "aria-label", "aria-placeholder"}
)
OMITTED_SUBTREES = frozenset(
    {"script", "style", "code", "pre", "textarea", "template", "noscript"}
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
VOICE_NAMES = frozenset(
    {"Alloy", "Ash", "Coral", "Echo", "Fable", "Nova", "Onyx", "Sage", "Shimmer"}
)
PRESERVED_TECHNICAL_TERMS = frozenset(
    {
        "(Endpoint)",
        "(Endpoints)",
        "Endpoint",
        "Persona",
        "Personas",
        "Prompt",
        "Proxy",
        "Webhook",
        "ntfy",
        "proxy",
    }
)
LANGUAGE_SELF_NAMES = frozenset({"Português (Brasil)"})


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


class _TranslatedTextByClass(HTMLParser):
    """Compose rendered text for elements after applying catalog translations."""

    def __init__(self, class_name: str, catalog: dict[str, str]) -> None:
        super().__init__(convert_charrefs=True)
        self.class_name = class_name
        self.catalog = catalog
        self._target_depth = 0
        self._parts: list[str] = []
        self.texts: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        classes = set(dict(attrs).get("class", "").split())
        if self._target_depth:
            self._target_depth += 1
        elif self.class_name in classes:
            self._target_depth = 1
            self._parts = []

    def handle_endtag(self, tag: str) -> None:
        if not self._target_depth:
            return
        self._target_depth -= 1
        if self._target_depth == 0:
            self.texts.append(re.sub(r"\s+", " ", "".join(self._parts)).strip())

    def handle_data(self, data: str) -> None:
        if not self._target_depth:
            return
        key = data.strip()
        translated = self.catalog.get(key)
        self._parts.append(data.replace(key, translated) if key and translated else data)


def _visible_index_strings() -> dict[str, list[str]]:
    parser = _VisibleIndexStrings()
    parser.feed(INDEX_HTML.read_text(encoding="utf-8"))
    parser.close()
    return parser.found


def _exclusion_category(value: str) -> str | None:
    """Return the documented reason a visible value is not interface prose."""

    if value in PRODUCT_TERMS:
        return "termo de produto/provedor preservado"
    if value in VOICE_NAMES:
        return "nome de voz preservado"
    if value in PRESERVED_TECHNICAL_TERMS:
        return "termo técnico preservado"
    if value in LANGUAGE_SELF_NAMES:
        return "nome autóctone de idioma preservado"
    if "{{" in value or "{%" in value or "%}" in value:
        return "conteúdo técnico de template"
    if not any(character.isalpha() for character in value):
        return "número ou símbolo"
    if re.fullmatch(r"#[0-9a-f]{3,8}", value, flags=re.IGNORECASE):
        return "valor hexadecimal"
    if re.fullmatch(r"(?:API|LLM|PDF|RAG|URL|DEBUG|INFO|WARNING|ERROR)", value):
        return "sigla técnica"
    if re.fullmatch(r"(?:A-Z|A\|C|Aa|\d+(?:\.\d+)?x(?: \(normal\))?)", value):
        return "controle técnico ou escala numérica"
    if re.fullmatch(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", value):
        return "endereço técnico de exemplo"
    if re.fullmatch(r"[a-z][a-z0-9]*(?:[_-][a-z0-9]+)+", value):
        return "identificador técnico"
    if re.fullmatch(r"(?:https?|wss?)://\S+", value, flags=re.IGNORECASE):
        return "URL técnica"
    if value.startswith(("/api/", "./", "../")):
        return "caminho técnico"
    if re.fullmatch(r"[\w.-]+/[\w./-]+", value):
        return "identificador técnico"
    if re.fullmatch(r"[\w.-]+\.(?:json|js|css|html|md|py|sh|txt)", value):
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


def _translated_index_texts_for_class(class_name: str) -> list[str]:
    parser = _TranslatedTextByClass(
        class_name, _messages_for(INDEX_CATALOG.name)
    )
    parser.feed(INDEX_HTML.read_text(encoding="utf-8"))
    parser.close()
    return parser.texts


def _translated_app_examples(lang: str) -> dict[str, str]:
    """Run the real i18n runtime and app catalog for representative templates."""

    script = textwrap.dedent(
        f"""
        globalThis.window = {{ __ODY_LANG: {json.dumps(lang)} }};
        globalThis.localStorage = {{ getItem: () => null }};
        const {{ t }} = await import({json.dumps((ROOT / "static" / "js" / "i18n.js").as_uri())});
        await import({json.dumps(APP_CATALOG.as_uri())});
        console.log(JSON.stringify({{
          messages: t('· {{count}} messages', {{ count: 4 }}),
          tidy: t(
            'Sorted {{count}} chats into {{folders}} folders',
            {{ count: 3, folders: 2 }},
          ),
          rename: t('AI renamed to {{name}}', {{ name: 'Atena' }}),
          restore: t('Restore {{title}}', {{ title: 'Agenda' }}),
          files: t('Added {{count}} files to chat', {{ count: 3 }}),
          deletePrompt: t('Delete "{{name}}"?', {{ name: 'Atena' }}),
          deleteAction: t('Delete'),
          endpoint: t(
            'Add an AI endpoint from Settings in the sidebar, or paste an endpoint/API key into the chat.',
          ),
        }}));
        """
    )
    result = _run(["node", "--input-type=module"], input_text=script)
    assert result.returncode == 0, result.stderr
    return dict(json.loads(result.stdout))


def _translated_shared_workspace_examples(lang: str) -> dict[str, str]:
    """Run shared/workspace catalogs with the reused canonical dictionaries."""

    runtime = ROOT / "static" / "js" / "i18n.js"
    catalogs = (
        INDEX_CATALOG,
        APP_CATALOG,
        SHARED_UI_CATALOG,
        WORKSPACE_CATALOG,
    )
    imports = "\n".join(
        f"await import({json.dumps(catalog.as_uri())});" for catalog in catalogs
    )
    script = textwrap.dedent(
        f"""
        globalThis.window = {{ __ODY_LANG: {json.dumps(lang)} }};
        globalThis.localStorage = {{ getItem: () => null }};
        const {{ t }} = await import({json.dumps(runtime.as_uri())});
        {imports}
        console.log(JSON.stringify({{
          confirm: t('Confirm'),
          cancel: t('Cancel'),
          cookbook: t('Cookbook'),
          prompt: t('Prompt'),
          restore: t('Restore {{label}}', {{ label: 'Calendário' }}),
          processing: t('AI is processing'),
          loading: t('Loading…'),
          workspaceTitle: t(
            'Workspace: {{path}}\\nFile tools are confined here; shell commands start here but are not sandboxed and can reach outside it.\\nClick to clear.',
            {{ path: 'D:/Projetos/Atena' }},
          ),
          workspaceSet: t(
            'Workspace set: {{name}}',
            {{ name: 'Atena' }},
          ),
        }}));
        """
    )
    result = _run(["node", "--input-type=module"], input_text=script)
    assert result.returncode == 0, result.stderr
    return dict(json.loads(result.stdout))


def _app_import_specifiers() -> list[str]:
    return APP_IMPORT_RE.findall(APP_JS.read_text(encoding="utf-8"))


def _browser_dom_prelude(lang: str, *, dock_capable: bool = False) -> str:
    if not dock_capable:
        return textwrap.dedent(
            f"""
            globalThis.window = {{
              __ODY_LANG: {json.dumps(lang)},
              innerWidth: 1200,
              innerHeight: 800,
              addEventListener() {{}},
              getComputedStyle() {{ return {{ zIndex: '250' }}; }},
            }};
            globalThis.localStorage = {{
              getItem: () => null,
              setItem() {{}},
              removeItem() {{}},
            }};
            const classList = {{
              add() {{}}, remove() {{}}, contains() {{ return false; }},
            }};
            const appended = [];
            const makeElement = () => ({{
              style: {{}},
              classList,
              addEventListener() {{}},
              setPointerCapture() {{}},
              releasePointerCapture() {{}},
              isConnected: true,
            }});
            globalThis.document = {{
              readyState: 'complete',
              body: {{
                style: {{}},
                classList,
                appendChild(el) {{ appended.push(el); }},
              }},
              documentElement: {{
                style: {{
                  getPropertyValue() {{ return ''; }},
                  setProperty() {{}},
                  removeProperty() {{}},
                }},
              }},
              createElement() {{ return makeElement(); }},
              addEventListener() {{}},
              removeEventListener() {{}},
              getElementById() {{ return null; }},
              querySelector() {{ return null; }},
              querySelectorAll() {{ return []; }},
            }};
            globalThis.MutationObserver = class {{ observe() {{}} disconnect() {{}} }};
            globalThis.requestAnimationFrame = () => 0;
            globalThis.getComputedStyle = () => ({{
              zIndex: '250',
              getPropertyValue() {{ return ''; }},
            }});
            """
        )

    return textwrap.dedent(
        f"""
        class FakeClassList {{
          constructor(owner) {{
            this.owner = owner;
            this.values = new Set();
          }}
          set(value) {{
            this.values = new Set(String(value || '').split(/\\s+/).filter(Boolean));
          }}
          add(...values) {{ values.forEach((value) => this.values.add(value)); }}
          remove(...values) {{ values.forEach((value) => this.values.delete(value)); }}
          contains(value) {{ return this.values.has(value); }}
          toggle(value, force) {{
            const enabled = force === undefined ? !this.contains(value) : Boolean(force);
            if (enabled) this.add(value); else this.remove(value);
            return enabled;
          }}
          toString() {{ return [...this.values].join(' '); }}
        }}
        class FakeStyle {{
          constructor() {{ this.cssText = ''; }}
          setProperty(name, value) {{ this[name] = String(value); }}
          removeProperty(name) {{ delete this[name]; }}
          getPropertyValue(name) {{ return this[name] || ''; }}
        }}
        const elementsById = new Map();
        class FakeElement {{
          constructor(tagName) {{
            this.tagName = String(tagName).toUpperCase();
            this.children = [];
            this.parentNode = null;
            this.style = new FakeStyle();
            this.classList = new FakeClassList(this);
            this.dataset = {{}};
            this._attributes = new Map();
            this._id = '';
            this._innerHTML = '';
            this.textContent = '';
            this.isConnected = true;
          }}
          set id(value) {{
            if (this._id) elementsById.delete(this._id);
            this._id = String(value || '');
            if (this._id) elementsById.set(this._id, this);
          }}
          get id() {{ return this._id; }}
          set className(value) {{ this.classList.set(value); }}
          get className() {{ return this.classList.toString(); }}
          set innerHTML(value) {{
            this._innerHTML = String(value);
            this.children = [];
            const spanPattern = /<span class="([^"]+)"(?: title="([^"]*)")?>([^<]*)<\\/span>/g;
            for (const match of this._innerHTML.matchAll(spanPattern)) {{
              const span = new FakeElement('span');
              span.className = match[1];
              span.title = match[2] || '';
              span.textContent = match[3];
              this.appendChild(span);
            }}
          }}
          get innerHTML() {{ return this._innerHTML; }}
          get attributes() {{
            return [...this._attributes].map(([name, value]) => ({{ name, value }}));
          }}
          appendChild(child) {{
            child.parentNode = this;
            this.children.push(child);
            return child;
          }}
          remove() {{
            if (!this.parentNode) return;
            this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
            this.parentNode = null;
          }}
          setAttribute(name, value) {{
            const stringValue = String(value);
            this._attributes.set(name, stringValue);
            if (name.startsWith('data-')) {{
              const key = name.slice(5).replace(/-([a-z])/g, (_, char) => char.toUpperCase());
              this.dataset[key] = stringValue;
            }}
          }}
          addEventListener() {{}}
          removeEventListener() {{}}
          setPointerCapture() {{}}
          releasePointerCapture() {{}}
          contains(candidate) {{
            return candidate === this || this.children.some((child) => child.contains(candidate));
          }}
          querySelector(selector) {{ return this.querySelectorAll(selector)[0] || null; }}
          querySelectorAll(selector) {{
            const className = selector.match(/\\.([a-zA-Z0-9_-]+)/)?.[1];
            const matches = [];
            const visit = (node) => {{
              for (const child of node.children) {{
                if (className && child.classList.contains(className)) matches.push(child);
                visit(child);
              }}
            }};
            visit(this);
            return matches;
          }}
          closest() {{ return null; }}
          getBoundingClientRect() {{
            return {{ left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 }};
          }}
        }}
        const body = new FakeElement('body');
        const documentElement = new FakeElement('html');
        globalThis.window = {{
          __ODY_LANG: {json.dumps(lang)},
          innerWidth: 1200,
          innerHeight: 800,
          addEventListener() {{}},
          dispatchEvent() {{}},
        }};
        globalThis.localStorage = {{
          getItem: () => null,
          setItem() {{}},
          removeItem() {{}},
        }};
        globalThis.document = {{
          readyState: 'complete',
          body,
          documentElement,
          createElement(tagName) {{ return new FakeElement(tagName); }},
          addEventListener() {{}},
          removeEventListener() {{}},
          getElementById(id) {{ return elementsById.get(id) || null; }},
          querySelector(selector) {{ return body.querySelector(selector); }},
          querySelectorAll(selector) {{ return body.querySelectorAll(selector); }},
        }};
        globalThis.MutationObserver = class {{ observe() {{}} disconnect() {{}} }};
        globalThis.CustomEvent = class {{
          constructor(type, options = {{}}) {{
            this.type = type;
            this.detail = options.detail;
          }}
        }};
        globalThis.setInterval = () => 0;
        globalThis.requestAnimationFrame = () => 0;
        globalThis.getComputedStyle = (element) => ({{
          display: element?.style?.display || 'block',
          zIndex: element?.style?.zIndex || '250',
          getPropertyValue(name) {{ return element?.style?.getPropertyValue(name) || ''; }},
        }});
        """
    )


def _app_ordered_imports(target_specifier: str, target_path: Path) -> str:
    statements: list[str] = []
    for specifier in _app_import_specifiers():
        if specifier == target_specifier:
            statements.append(f"const targetModule = await import({json.dumps(target_path.as_uri())});")
        elif specifier in {"./js/i18n.js", "./js/languagePref.js"} or (
            specifier.startswith("./js/i18n/") and specifier.endswith(".pt-BR.js")
        ):
            statements.append(
                f"await import({json.dumps((ROOT / 'static' / specifier[2:]).as_uri())});"
            )
    return "\n".join(statements)


def _rendered_modal_snap_titles(lang: str) -> list[str]:
    """Evaluate modalSnap and central catalogs in their real app import order."""

    script = textwrap.dedent(
        f"""
        {_browser_dom_prelude(lang)}
        {_app_ordered_imports("./js/modalManager.js", MODAL_SNAP_JS)}
        console.log(JSON.stringify(
          [...new Set(appended.map((el) => el.title).filter(Boolean))],
        ));
        """
    )
    result = _run(["node", "--input-type=module"], input_text=script)
    assert result.returncode == 0, result.stderr
    return list(json.loads(result.stdout))


def _rendered_registered_modal_label(lang: str) -> dict[str, str]:
    script = textwrap.dedent(
        f"""
        {_browser_dom_prelude(lang, dock_capable=True)}
        {_app_ordered_imports("./js/modalManager.js", MODAL_MANAGER_JS)}
        targetModule.register('email-lib-modal', {{
          label: 'Email',
          icon: 'M2 4h20v16H2z',
        }});
        targetModule.minimize('email-lib-modal');
        targetModule.register('Email', {{ icon: 'M2 4h20v16H2z' }});
        targetModule.minimize('Email');
        const chips = document
          .getElementById('minimized-dock')
          .querySelectorAll('.minimized-dock-chip');
        const chip = chips.find((candidate) => candidate.dataset.modalId === 'email-lib-modal');
        const internalChip = chips.find((candidate) => candidate.dataset.modalId === 'Email');
        console.log(JSON.stringify({{
          label: chip.querySelector('.minimized-dock-label').textContent,
          title: chip.title,
          internalLabel: internalChip.querySelector('.minimized-dock-label').textContent,
        }}));
        """
    )
    result = _run(["node", "--input-type=module"], input_text=script)
    assert result.returncode == 0, result.stderr
    return dict(json.loads(result.stdout))


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
def test_registered_translations_are_not_source_identity() -> None:
    identities: list[str] = []
    for registration in _captured_catalogs():
        for key, value in dict(registration["messages"]).items():
            if key == value:
                identities.append(f"{registration['file']}: {key!r}")
    assert not identities, (
        "Entradas idênticas não traduzem e devem ser excluídas semanticamente "
        "ou receber pt-BR real:\n" + "\n".join(identities)
    )


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_shared_most_used_label_is_context_neutral() -> None:
    catalog = _messages_for(INDEX_CATALOG.name)
    assert catalog["Most used"] == "Mais frequentes"


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_url_exclusion_requires_the_entire_visible_value_to_be_a_url() -> None:
    catalog = _messages_for(INDEX_CATALOG.name)

    assert _exclusion_category("https://example.com/path") == "URL técnica"
    assert _exclusion_category("http://localhost:8080 (optional)") is None
    assert (
        catalog["http://localhost:8080 (optional)"]
        == "http://localhost:8080 (opcional)"
    )


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_memory_import_description_composes_natural_portuguese() -> None:
    descriptions = _translated_index_texts_for_class("memory-import-description")

    assert descriptions == [
        "Importe um arquivo (.txt, .md, .pdf, .csv, .log, .json, .py, .js "
        "ou .html) — a IA lê o arquivo e sugere memórias que você pode aprovar."
    ]


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_custom_font_description_composes_natural_portuguese() -> None:
    descriptions = _translated_index_texts_for_class(
        "theme-custom-font-description"
    )

    assert descriptions == [
        "Solte arquivos de fonte (.woff2, .ttf ou .otf) em "
        "static/fonts/custom/ e recarregue — eles aparecerão na lista de fontes acima."
    ]


def test_visible_parser_omits_noscript_like_the_runtime() -> None:
    parser = _VisibleIndexStrings()
    parser.feed(
        '<noscript title="Hidden title">Hidden fallback</noscript>'
        '<div title="Visible title">Visible text</div>'
    )
    parser.close()

    assert set(parser.found) == {"Visible title", "Visible text"}


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


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_app_catalog_has_contextual_portuguese_for_dynamic_interface_copy() -> None:
    catalog = _messages_for(APP_CATALOG.name)
    expected = {
        "· {count} message": "· {count} mensagem",
        "· {count} messages": "· {count} mensagens",
        "User": "Usuário",
        "Assistant": "Assistente",
        "[Tool calls]": "[Chamadas de ferramentas]",
        "tool": "ferramenta",
        "failed": "falhou",
        "done": "concluído",
        "cmd": "comando",
        "out": "saída",
        "Odysseus Chat": "Conversa do Odysseus",
        "Untitled": "Sem título",
        "Auto-sort failed": "Falha ao organizar automaticamente",
        "Cleaned 1 empty/throwaway chat": "1 conversa vazia ou descartável removida",
        "Cleaned {count} empty/throwaway chats": (
            "{count} conversas vazias ou descartáveis removidas"
        ),
        "Already clean": "Já está tudo organizado",
        "Sorted 1 chat into 1 folder": "1 conversa organizada em 1 pasta",
        "Sorted 1 chat into {folders} folders": (
            "1 conversa organizada em {folders} pastas"
        ),
        "Sorted {count} chats into 1 folder": (
            "{count} conversas organizadas em 1 pasta"
        ),
        "Sorted {count} chats into {folders} folders": (
            "{count} conversas organizadas em {folders} pastas"
        ),
        " — 1 unfiled chat left; select Group again": (
            " — resta 1 conversa sem pasta; selecione Agrupar novamente"
        ),
        " — {count} unfiled chats left; select Group again": (
            " — restam {count} conversas sem pasta; selecione Agrupar novamente"
        ),
        "1 unfiled chat — select Group again": (
            "1 conversa sem pasta — selecione Agrupar novamente"
        ),
        "{count} unfiled chats — select Group again": (
            "{count} conversas sem pasta — selecione Agrupar novamente"
        ),
        "All chats sorted": "Todas as conversas foram organizadas",
        "Nothing to sort": "Nada para organizar",
        "Auto-sort: {message}": "Organização automática: {message}",
        "Models sorted: {label}": "Modelos ordenados: {label}",
        "Please enter a name for the AI": "Digite um nome para a IA",
        "AI renamed to {name}": "IA renomeada para {name}",
        "Failed to rename AI: {message}": "Falha ao renomear a IA: {message}",
        "Please enter a name for the session": "Digite um nome para a sessão",
        "Session renamed to {name}": "Sessão renomeada para {name}",
        "Session: {name}{model}{rag}{version}": (
            "Sessão: {name}{model}{rag}{version}"
        ),
        "Failed to rename session": "Falha ao renomear a sessão",
        "Failed to rename session: {message}": (
            "Falha ao renomear a sessão: {message}"
        ),
        "Web search on": "Pesquisa na web ativada",
        "Web search off": "Pesquisa na web desativada",
        "Shell on": "Terminal ativado",
        "Shell off": "Terminal desativado",
        "Web Search": "Busca na web",
        "Searches the web for relevant information to include in the response. "
        "Results are fetched and summarized before the AI answers.": (
            "Pesquisa informações relevantes na web para incluir na resposta. "
            "Os resultados são coletados e resumidos antes de a IA responder."
        ),
        "Shell Access": "Acesso ao terminal",
        "Gives the AI access to a sandboxed shell for running commands, "
        "installing packages, and executing scripts. Use with caution.": (
            "Dá à IA acesso a um terminal isolado para executar comandos, instalar "
            "pacotes e rodar scripts. Use com cuidado."
        ),
        "Tool Builder": "Criador de ferramentas",
        "Create custom mini-apps and tools the AI can use. Describe what you "
        "need and the AI will build a tool you can reuse across conversations.": (
            "Crie miniaplicativos e ferramentas personalizadas que a IA possa "
            "usar. Descreva o que você precisa, e a IA criará uma ferramenta "
            "reutilizável em outras conversas."
        ),
        "Multi-round web search with source analysis. Takes longer but produces "
        "comprehensive, well-sourced answers. Your next message will trigger a "
        "deep research cycle.": (
            "Pesquisa na web em várias rodadas, com análise de fontes. Leva mais "
            "tempo, mas produz respostas abrangentes e bem fundamentadas. Sua "
            "próxima mensagem iniciará um ciclo de pesquisa profunda."
        ),
        "Group chat ready — 1 model": "Conversa em grupo pronta — 1 modelo",
        "Group chat ready — {count} models": (
            "Conversa em grupo pronta — {count} modelos"
        ),
        "Disable Nobody mode": "Desativar modo Ninguém",
        "Enable Nobody mode — no memory and no history saved": (
            "Ativar modo Ninguém — sem salvar memória nem histórico"
        ),
        "Nobody": "Ninguém",
        "Who am I? I'm nobody.": "Quem sou eu? Não sou ninguém.",
        "Temporary session — it won't be saved or activate memory.": (
            "Sessão temporária — não será salva nem ativará a memória."
        ),
        "Rearrange enabled": "Reorganização ativada",
        "Rearrange disabled": "Reorganização desativada",
        "Window": "Janela",
        "Restore {title}": "Restaurar {title}",
        "Close": "Fechar",
        "Minimize": "Minimizar",
        "Session deleted": "Sessão excluída",
        "Failed to delete session": "Falha ao excluir a sessão",
        "Failed to delete session: {message}": (
            "Falha ao excluir a sessão: {message}"
        ),
        'Delete "{name}"?': 'Excluir "{name}"?',
        "Delete": "Excluir",
        "Message Odysseus...": "Mensagem para o Odysseus...",
        "Record voice": "Gravar voz",
        "Send to group": "Enviar ao grupo",
        "Send message": "Enviar mensagem",
        "New": "Nova",
        "New chat": "Nova conversa",
        "Stop recording": "Parar gravação",
        "Added 1 file to chat": "1 arquivo adicionado à conversa",
        "Added {count} files to chat": (
            "{count} arquivos adicionados à conversa"
        ),
        "Added 1 file to attach": "1 arquivo adicionado aos anexos",
        "Added {count} files to attach": (
            "{count} arquivos adicionados aos anexos"
        ),
        "Drop files to attach": "Solte os arquivos para anexar",
        "Add an AI endpoint from Settings in the sidebar, or paste an "
        "endpoint/API key into the chat.": (
            "Adicione um endpoint de IA em Configurações na barra lateral ou "
            "cole um endpoint ou uma chave de API na conversa."
        ),
    }

    assert {key: catalog.get(key) for key in expected} == expected


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_app_uses_i18n_formatters_without_translating_runtime_data() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    required_call_sites = {
        "message singular": "t('· {count} message', { count: n })",
        "message plural": "t('· {count} messages', { count: n })",
        "untitled fallback": "meta?.name || t('Untitled')",
        "tool fallback": "|| t('tool')",
        "raw server reason": "data.reason || t('Nothing to sort')",
        "AI name placeholder": "t('AI renamed to {name}', { name: newName })",
        "session name placeholder": (
            "t('Session renamed to {name}', { name: newName })"
        ),
        "raw rename error": (
            "t('Failed to rename session: {message}', { message: e.message })"
        ),
        "model count": (
            "t('Group chat ready — {count} models', { count: picked.length })"
        ),
        "restore title": "t('Restore {title}', { title: modalTitle(modal) })",
        "file count": (
            "t('Added {count} files to chat', { count: files.length })"
        ),
        "delete confirmation name": (
            "t('Delete \"{name}\"?', { name })"
        ),
        "delete confirmation action": "confirmText: t('Delete')",
        "responsive placeholder": "t('Message Odysseus...')",
        "missing endpoint": (
            "t('Add an AI endpoint from Settings in the sidebar, or paste an "
            "endpoint/API key into the chat.')"
        ),
    }
    missing = [
        f"{context}: {snippet}"
        for context, snippet in required_call_sites.items()
        if snippet not in source
    ]
    assert not missing, "Call sites sem i18n contextual:\n" + "\n".join(missing)

    obsolete_direct_formatters = (
        "`· ${n} msg${n === 1 ? '' : 's'}`",
        "`AI renamed to ${newName}`",
        "`Session renamed to ${newName}`",
        "`${label} ${active ? 'on' : 'off'}`",
        "`Added ${files.length} file${files.length > 1 ? 's' : ''} to chat`",
        'styledConfirm(`Delete "${name}"?`, { confirmText: \'Delete\'',
    )
    assert not [item for item in obsolete_direct_formatters if item in source]


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_app_catalog_templates_render_in_portuguese_and_english() -> None:
    assert _translated_app_examples("pt-BR") == {
        "messages": "· 4 mensagens",
        "tidy": "3 conversas organizadas em 2 pastas",
        "rename": "IA renomeada para Atena",
        "restore": "Restaurar Agenda",
        "files": "3 arquivos adicionados à conversa",
        "deletePrompt": 'Excluir "Atena"?',
        "deleteAction": "Excluir",
        "endpoint": (
            "Adicione um endpoint de IA em Configurações na barra lateral ou "
            "cole um endpoint ou uma chave de API na conversa."
        ),
    }
    assert _translated_app_examples("en") == {
        "messages": "· 4 messages",
        "tidy": "Sorted 3 chats into 2 folders",
        "rename": "AI renamed to Atena",
        "restore": "Restore Agenda",
        "files": "Added 3 files to chat",
        "deletePrompt": 'Delete "Atena"?',
        "deleteAction": "Delete",
        "endpoint": (
            "Add an AI endpoint from Settings in the sidebar, or paste an "
            "endpoint/API key into the chat."
        ),
    }


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_shared_ui_catalog_contains_only_new_contextual_copy() -> None:
    catalog = _messages_for(SHARED_UI_CATALOG.name)
    expected = {
        "Copied": "Copiado",
        "Dismiss": "Fechar",
        "Confirm": "Confirmar",
        "Document": "Documento",
        "Restore {label}": "Restaurar {label}",
        "AI is processing": "A IA está processando",
        "Loading…": "Carregando…",
        "Drag to resize docked window": (
            "Arraste para redimensionar a janela encaixada"
        ),
        "Drag to resize email and draft": (
            "Arraste para redimensionar o e-mail e o rascunho"
        ),
    }
    reused = {
        "Cancel",
        "Name",
        "Save",
        "Cookbook",
        "Calendar",
        "Gallery",
        "Tasks",
        "Library",
        "Brain",
        "Notes",
        "Email",
        "Research",
        "Theme",
        "Compare",
        "Settings",
        "Shortcuts",
        "Close",
        "Minimize",
    }

    assert catalog == expected
    assert reused.isdisjoint(catalog)


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_workspace_catalog_has_complete_contextual_portuguese_copy() -> None:
    catalog = _messages_for(WORKSPACE_CATALOG.name)
    expected = {
        "Workspace: {path}\nFile tools are confined here; shell commands start "
        "here but are not sandboxed and can reach outside it.\nClick to clear.": (
            "Área de trabalho: {path}\nAs ferramentas de arquivo ficam restritas "
            "a esta pasta; os comandos do Terminal começam aqui, mas não ficam "
            "isolados e podem acessar locais fora dela.\nClique para limpar."
        ),
        "Workspace cleared": "Área de trabalho limpa",
        "Too many folders to list. Type or paste a path above to jump in.": (
            "Há pastas demais para listar. Digite ou cole um caminho acima para "
            "acessá-lo."
        ),
        "No subfolders": "Nenhuma subpasta",
        "This folder cannot be used as a workspace": (
            "Esta pasta não pode ser usada como área de trabalho"
        ),
        "Could not open folder": "Não foi possível abrir a pasta",
        "Select workspace": "Selecionar área de trabalho",
        "Type or paste a folder path, then press Enter": (
            "Digite ou cole o caminho de uma pasta e pressione Enter"
        ),
        "File tools are <strong>confined</strong> to this folder. Shell commands "
        "start here but are <strong>not sandboxed</strong> and can reach outside "
        "it. A workspace scopes the tools; it is not a security boundary.": (
            "As ferramentas de arquivo ficam <strong>restritas</strong> a esta "
            "pasta. Os comandos do Terminal começam aqui, mas <strong>não ficam "
            "isolados</strong> e podem acessar locais fora dela. Uma área de "
            "trabalho delimita as ferramentas; ela não é uma barreira de segurança."
        ),
        "Use this folder": "Usar esta pasta",
        "Workspace set: {name}": "Área de trabalho definida: {name}",
        "Could not browse folders": "Não foi possível navegar pelas pastas",
    }

    assert catalog == expected
    assert {"Cancel", "Close"}.isdisjoint(catalog)


def test_shared_ui_modules_translate_only_defaults_and_builtin_copy() -> None:
    sources = {
        "ui": UI_JS.read_text(encoding="utf-8"),
        "modal": MODAL_MANAGER_JS.read_text(encoding="utf-8"),
        "spinner": SPINNER_JS.read_text(encoding="utf-8"),
        "workspace": WORKSPACE_JS.read_text(encoding="utf-8"),
        "snap": MODAL_SNAP_JS.read_text(encoding="utf-8"),
    }
    required = {
        "ui": (
            "showToast(t('Copied'))",
            "closeBtn.setAttribute('aria-label', t('Dismiss'))",
            "confirmText === undefined ? t('Confirm') : confirmText",
            "cancelText === undefined ? t('Cancel') : cancelText",
            "title === undefined ? t('Name') : title",
            "confirmText === undefined ? t('Save') : confirmText",
        ),
        "modal": (
            "label: () => t('Cookbook')",
            "label: () => t('Calendar')",
            "label: () => t('Gallery')",
            "label: () => t('Tasks')",
            "label: () => t('Library')",
            "label: () => t('Brain')",
            "label: () => t('Notes')",
            "label: () => t('Email')",
            "label: 'Prompt'",
            "label: () => t('Research')",
            "label: () => t('Theme')",
            "label: () => t('Compare')",
            "label: () => t('Settings')",
            "label: () => t('Shortcuts')",
            "label: () => t('Document')",
            "t('Restore {label}', { label })",
            "title=\"${t('Close')}\"",
            "btn.title = t('Minimize')",
        ),
        "spinner": (
            "message === undefined ? t('AI is processing') : message",
            "text === undefined ? t('Loading…') : text",
        ),
        "workspace": (
            "t('Workspace: {path}\\nFile tools are confined here; shell commands "
            "start here but are not sandboxed and can reach outside it.\\nClick "
            "to clear.', { path })",
            "uiModule.showToast(t('Workspace cleared'))",
            "t('Too many folders to list. Type or paste a path above to jump in.')",
            "t('No subfolders')",
            "t('This folder cannot be used as a workspace')",
            "uiModule.showError(t('Could not open folder'))",
            "t('Select workspace')",
            "t('Type or paste a folder path, then press Enter')",
            "t('Use this folder')",
            "t('Workspace set: {name}', { name: _basename(_curPath) })",
            "uiModule.showError(t('Could not browse folders'))",
        ),
        "snap": (
            "handle.title = t('Drag to resize docked window')",
            "stripe.title = t('Drag to resize email and draft')",
        ),
    }
    missing = [
        f"{module}: {snippet}"
        for module, snippets in required.items()
        for snippet in snippets
        if snippet not in sources[module]
    ]
    assert not missing, "Call sites compartilhados sem i18n:\n" + "\n".join(missing)

    ui_source = sources["ui"]
    assert "textSpan.textContent = msg" in ui_source
    assert "btn.textContent = actionLabel" in ui_source
    assert "msgEl.textContent = message" in ui_source
    assert "input.placeholder = placeholder || ''" in ui_source

    side_effect_catalog_import = re.compile(
        r"""import\s+['"][^'"]*i18n/[^'"]+\.pt-BR\.js['"]"""
    )
    offenders = [
        module
        for module, source in sources.items()
        if side_effect_catalog_import.search(source)
    ]
    assert not offenders, (
        "Catálogos devem ser carregados centralmente, não pelos módulos: "
        + ", ".join(offenders)
    )


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_shared_workspace_templates_render_in_portuguese_and_english() -> None:
    assert _translated_shared_workspace_examples("pt-BR") == {
        "confirm": "Confirmar",
        "cancel": "Cancelar",
        "cookbook": "Catálogo",
        "prompt": "Prompt",
        "restore": "Restaurar Calendário",
        "processing": "A IA está processando",
        "loading": "Carregando…",
        "workspaceTitle": (
            "Área de trabalho: D:/Projetos/Atena\nAs ferramentas de arquivo ficam "
            "restritas a esta pasta; os comandos do Terminal começam aqui, mas "
            "não ficam isolados e podem acessar locais fora dela.\nClique para limpar."
        ),
        "workspaceSet": "Área de trabalho definida: Atena",
    }
    assert _translated_shared_workspace_examples("en") == {
        "confirm": "Confirm",
        "cancel": "Cancel",
        "cookbook": "Cookbook",
        "prompt": "Prompt",
        "restore": "Restore Calendário",
        "processing": "AI is processing",
        "loading": "Loading…",
        "workspaceTitle": (
            "Workspace: D:/Projetos/Atena\nFile tools are confined here; shell "
            "commands start here but are not sandboxed and can reach outside it."
            "\nClick to clear."
        ),
        "workspaceSet": "Workspace set: Atena",
    }


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_modal_snap_uses_catalog_loaded_in_real_app_import_order() -> None:
    assert _rendered_modal_snap_titles("pt-BR") == [
        "Arraste para redimensionar a janela encaixada",
        "Arraste para redimensionar o e-mail e o rascunho",
    ]
    assert _rendered_modal_snap_titles("en") == [
        "Drag to resize docked window",
        "Drag to resize email and draft",
    ]

    imports = _app_import_specifiers()
    central_imports = {
        "./js/i18n.js",
        "./js/languagePref.js",
        *(f"./js/i18n/{catalog.name}" for catalog in CATALOGS),
    }
    assert central_imports.issubset(imports)
    assert imports[:2] == ["./js/i18n.js", "./js/languagePref.js"]
    assert set(imports[2 : len(central_imports)]) == central_imports - set(imports[:2])


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_registered_modal_label_is_translated_lazily_when_dock_renders() -> None:
    assert _rendered_registered_modal_label("pt-BR") == {
        "label": "E-mail",
        "title": "Restaurar E-mail",
        "internalLabel": "Email",
    }
    assert _rendered_registered_modal_label("en") == {
        "label": "Email",
        "title": "Restore Email",
        "internalLabel": "Email",
    }
