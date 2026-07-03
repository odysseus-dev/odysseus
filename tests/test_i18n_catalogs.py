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
CODE_RUNNER_JS = ROOT / "static" / "js" / "codeRunner.js"
SIGNATURE_JS = ROOT / "static" / "js" / "signature.js"
EMOJI_PICKER_JS = ROOT / "static" / "js" / "emojiPicker.js"
DOCUMENT_CATALOG = CATALOG_DIR / "document.pt-BR.js"
DOCUMENT_JS = ROOT / "static" / "js" / "document.js"
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


def _translated_document_auxiliary_examples(lang: str) -> dict[str, str]:
    runtime = ROOT / "static" / "js" / "i18n.js"
    script = textwrap.dedent(
        f"""
        globalThis.window = {{ __ODY_LANG: {json.dumps(lang)} }};
        globalThis.localStorage = {{ getItem: () => null }};
        const {{ t }} = await import({json.dumps(runtime.as_uri())});
        await import({json.dumps(DOCUMENT_CATALOG.as_uri())});
        console.log(JSON.stringify({{
          runtimeError: t(
            'Failed to load Python runtime: {{message}}',
            {{ message: 'Network 502' }},
          ),
          exit: t('(no output) — exit code {{code}}', {{ code: 7 }}),
          saveError: t(
            'Failed to save signature: {{message}}',
            {{ message: 'quota exceeded' }},
          ),
          emojiGroup: t('Faces & Hearts'),
          emojiTitle: t('heart-outline'),
        }}));
        """
    )
    result = _run(["node", "--input-type=module"], input_text=script)
    assert result.returncode == 0, result.stderr
    return dict(json.loads(result.stdout))


def _translated_document_editor_examples(lang: str) -> dict[str, str]:
    """Exercise editor/PDF copy through the real runtime and central catalog."""

    runtime = ROOT / "static" / "js" / "i18n.js"
    script = textwrap.dedent(
        f"""
        globalThis.window = {{ __ODY_LANG: {json.dumps(lang)} }};
        globalThis.localStorage = {{ getItem: () => null }};
        const {{ t }} = await import({json.dumps(runtime.as_uri())});
        await import({json.dumps(APP_CATALOG.as_uri())});
        await import({json.dumps(DOCUMENT_CATALOG.as_uri())});
        console.log(JSON.stringify({{
          pdfTitle: t('Export filled PDF'),
          pdfSummary: t(
            '{{filled}} of {{total}} fields filled. Review and adjust below before downloading.',
            {{ filled: 2, total: 5 }},
          ),
          pdfError: t(
            'Failed to load PDF view: {{message}}',
            {{ message: 'backend <raw>' }},
          ),
          toolbar: t('More formatting'),
          findOne: t('1 result'),
          findMany: t('{{count}} results', {{ count: 4 }}),
          annotationOne: t('AI added 1 annotation'),
          annotationMany: t(
            'AI added {{count}} annotations',
            {{ count: 3 }},
          ),
        }}));
        """
    )
    result = _run(["node", "--input-type=module"], input_text=script)
    assert result.returncode == 0, result.stderr
    return dict(json.loads(result.stdout))


def _translated_document_action_examples(lang: str) -> dict[str, str]:
    """Exercise remaining document actions through the real i18n runtime."""

    runtime = ROOT / "static" / "js" / "i18n.js"
    script = textwrap.dedent(
        f"""
        globalThis.window = {{ __ODY_LANG: {json.dumps(lang)} }};
        globalThis.localStorage = {{ getItem: () => null }};
        const {{ t }} = await import({json.dumps(runtime.as_uri())});
        await import({json.dumps(APP_CATALOG.as_uri())});
        await import({json.dumps(DOCUMENT_CATALOG.as_uri())});
        console.log(JSON.stringify({{
          attachment: t('No attachments found'),
          sendError: t(
            'Failed to send email: {{message}}',
            {{ message: 'SMTP <raw>' }},
          ),
          aiDraft: t(
            'AI draft inserted ({{model}})',
            {{ model: 'Modelo <raw>' }},
          ),
          scheduled: t(
            'Scheduled for {{time}}',
            {{ time: '30/06/2026 09:00' }},
          ),
          exportError: t(
            'Export failed: {{message}}',
            {{ message: 'backend <raw>' }},
          ),
          selectionOne: t(
            '{{line}} selected',
            {{ line: 'L2-L4' }},
          ),
          selectionMany: t(
            '{{count}} selections ({{lines}})',
            {{ count: 3, lines: 'L1, L4, L9' }},
          ),
          diffOne: t(
            '{{resolved}} / 1 change resolved',
            {{ resolved: 1 }},
          ),
          diffMany: t(
            '{{resolved}} / {{total}} changes resolved',
            {{ resolved: 2, total: 4 }},
          ),
          importError: t(
            'Import failed: {{message}}',
            {{ message: 'arquivo <raw>' }},
          ),
          deletePrompt: t(
            'Delete "{{name}}"?',
            {{ name: 'Contrato <raw>' }},
          ),
          restored: t(
            'Restored to v{{version}}',
            {{ version: 7 }},
          ),
        }}));
        """
    )
    result = _run(["node", "--input-type=module"], input_text=script)
    assert result.returncode == 0, result.stderr
    return dict(json.loads(result.stdout))


def _escaped_document_version_source(value: str) -> str:
    """Run document.js' real HTML escape helper against an API value."""

    script = textwrap.dedent(
        f"""
        import fs from 'node:fs';
        const source = fs.readFileSync(
          {json.dumps(str(DOCUMENT_JS))},
          'utf8',
        );
        const match = source.match(
          /function _esc\\(s\\)\\s*\\{{[\\s\\S]*?\\n  \\}}/,
        );
        if (!match) throw new Error('document _esc helper not found');
        const fnSource = match[0].replace(
          'function _esc',
          'function',
        );
        const escapeHtml = eval(`(${{fnSource}})`);
        console.log(escapeHtml({json.dumps(value)}));
        """
    )
    result = _run(["node", "--input-type=module"], input_text=script)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _run_code_runner_probe(lang: str) -> dict[str, object]:
    script = textwrap.dedent(
        f"""
        import fs from 'node:fs';
        globalThis.window = {{
          __ODY_LANG: {json.dumps(lang)},
          location: {{ origin: 'http://localhost' }},
          addEventListener() {{}},
          removeEventListener() {{}},
          isSecureContext: false,
        }};
        globalThis.localStorage = {{ getItem: () => null }};
        const makeElement = (tagName) => ({{
          tagName: String(tagName).toUpperCase(),
          style: {{}},
          className: '',
          textContent: '',
          children: [],
          addEventListener() {{}},
          appendChild(child) {{ this.children.push(child); return child; }},
          prepend(child) {{ this.children.unshift(child); return child; }},
          remove() {{}},
        }});
        globalThis.document = {{
          body: {{ appendChild() {{}} }},
          head: {{ appendChild() {{}} }},
          createElement: makeElement,
          execCommand: () => false,
          getElementById: () => null,
          querySelector: () => null,
          querySelectorAll: () => [],
          addEventListener() {{}},
        }};
        globalThis.fetch = async () => ({{
          async json() {{
            return {{ stdout: 'resultado externo <raw>', stderr: '', exit_code: 0 }};
          }},
        }});
        const history = [];
        const panel = {{
          style: {{}},
          children: [],
          _innerHTML: '',
          set innerHTML(value) {{
            this._innerHTML = String(value);
            history.push(this._innerHTML);
            if (value === '') this.children = [];
          }},
          get innerHTML() {{ return this._innerHTML; }},
          appendChild(child) {{ this.children.push(child); return child; }},
        }};
        await import({json.dumps(DOCUMENT_CATALOG.as_uri())});
        const uiStub = 'data:text/javascript,export function showToast(){{}}';
        let runnerSource = fs.readFileSync({json.dumps(str(CODE_RUNNER_JS))}, 'utf8');
        runnerSource = runnerSource
          .replace("'./ui.js'", JSON.stringify(uiStub))
          .replace("'./i18n.js'", {json.dumps(repr((ROOT / "static" / "js" / "i18n.js").as_uri()))});
        const runnerUrl = 'data:text/javascript;base64,'
          + Buffer.from(runnerSource).toString('base64');
        const runner = await import(runnerUrl);
        await runner.runServer('printf raw', panel, 'bash');
        console.log(JSON.stringify({{
          loading: history.find((value) => value.includes('code-runner-loading')),
          output: panel.children.find((child) => child.tagName === 'PRE')?.textContent,
          outputClass: panel.children.find((child) => child.tagName === 'PRE')?.className,
        }}));
        """
    )
    result = _run(["node", "--input-type=module"], input_text=script)
    assert result.returncode == 0, result.stderr
    return dict(json.loads(result.stdout))


def _rendered_signature_picker(
    lang: str, stored_name: str = "Rubrica <A>"
) -> dict[str, str]:
    script = textwrap.dedent(
        f"""
        globalThis.window = {{
          __ODY_LANG: {json.dumps(lang)},
          location: {{ origin: 'http://localhost' }},
          styledConfirm: async () => false,
        }};
        globalThis.localStorage = {{ getItem: () => null, setItem() {{}} }};
        const appended = [];
        const control = () => ({{
          onclick: null,
          addEventListener() {{}},
        }});
        globalThis.document = {{
          body: {{ appendChild(el) {{ appended.push(el); }} }},
          createElement() {{
            return {{
              className: '',
              style: {{}},
              innerHTML: '',
              remove() {{}},
              addEventListener() {{}},
              querySelector() {{ return control(); }},
              querySelectorAll() {{ return []; }},
            }};
          }},
        }};
        globalThis.fetch = async () => ({{
          ok: true,
          async json() {{
            return {{
              signatures: [{{
                id: 'sig-1',
                data_url: 'data:image/png;base64,AAAA',
                width: 20,
                height: 10,
                name: {json.dumps(stored_name)},
              }}],
            }};
          }},
        }});
        await import({json.dumps(INDEX_CATALOG.as_uri())});
        await import({json.dumps(APP_CATALOG.as_uri())});
        await import({json.dumps(DOCUMENT_CATALOG.as_uri())});
        const signatures = await import({json.dumps(SIGNATURE_JS.as_uri())});
        void signatures.pick();
        await new Promise((resolve) => setTimeout(resolve, 0));
        const html = appended[0].innerHTML;
        console.log(JSON.stringify({{
          heading: html.match(/<h4>([^<]+)<\\/h4>/)?.[1],
          closeTitle: html.match(/sig-close[^>]+title="([^"]+)"/)?.[1],
          newButton: html.match(/sig-new-tile[^>]*>([^<]+)<\\/button>/)?.[1],
          dynamicName: html.match(/white-space:nowrap;">([^<]+)<\\/div>/)?.[1],
        }}));
        """
    )
    result = _run(["node", "--input-type=module"], input_text=script)
    assert result.returncode == 0, result.stderr
    return dict(json.loads(result.stdout))


def _saved_empty_signature_name(lang: str) -> dict[str, str]:
    script = textwrap.dedent(
        f"""
        globalThis.window = {{
          __ODY_LANG: {json.dumps(lang)},
          location: {{ origin: 'http://localhost' }},
        }};
        globalThis.localStorage = {{ getItem: () => null, setItem() {{}} }};
        const ctx = {{
          save() {{}}, restore() {{}}, fillRect() {{}},
          getImageData() {{
            return {{ data: new Uint8ClampedArray([255, 255, 255, 255]) }};
          }},
        }};
        const canvas = {{
          width: 1,
          height: 1,
          style: {{}},
          getContext: () => ctx,
          addEventListener() {{}},
          toDataURL: () => 'data:image/png;base64,AAAA',
        }};
        const save = {{ disabled: true, onclick: null }};
        const name = {{ value: '' }};
        const slider = {{ value: '7', addEventListener() {{}} }};
        const controls = new Map([
          ['.sig-canvas', canvas],
          ['.sig-smoothness', slider],
          ['.sig-smoothness-val', {{ textContent: '' }}],
          ['.sig-save', save],
          ['.sig-name', name],
          ['.sig-close', {{ onclick: null }}],
          ['.sig-cancel', {{ onclick: null }}],
          ['.sig-clear', {{ onclick: null }}],
          ['.sig-undo', {{ onclick: null }}],
        ]);
        const overlay = {{
          className: '',
          style: {{}},
          innerHTML: '',
          remove() {{}},
          addEventListener() {{}},
          querySelector(selector) {{ return controls.get(selector); }},
        }};
        globalThis.document = {{
          body: {{ appendChild() {{}} }},
          createElement(tagName) {{
            if (tagName === 'canvas') return canvas;
            return overlay;
          }},
        }};
        let postedName = null;
        globalThis.fetch = async (_url, options) => {{
          const payload = JSON.parse(options.body);
          postedName = payload.name;
          return {{
            ok: true,
            async json() {{
              return {{
                id: 'sig-1',
                data_url: payload.data,
                width: payload.width,
                height: payload.height,
                name: payload.name,
              }};
            }},
          }};
        }};
        await import({json.dumps(DOCUMENT_CATALOG.as_uri())});
        const signatures = await import({json.dumps(SIGNATURE_JS.as_uri())});
        const captured = signatures.capture();
        await save.onclick();
        const result = await captured;
        console.log(JSON.stringify({{
          postedName,
          returnedName: result.name,
        }}));
        """
    )
    result = _run(["node", "--input-type=module"], input_text=script)
    assert result.returncode == 0, result.stderr
    return dict(json.loads(result.stdout))


def _rendered_emoji_picker(
    lang: str, query: str | None = None
) -> dict[str, object]:
    script = textwrap.dedent(
        f"""
        class FakeClassList {{
          constructor(owner) {{ this.owner = owner; }}
          contains(name) {{
            return String(this.owner.className || '').split(/\\s+/).includes(name);
          }}
        }}
        class FakeElement {{
          constructor(tagName) {{
            this.tagName = String(tagName).toUpperCase();
            this.className = '';
            this.classList = new FakeClassList(this);
            this.children = [];
            this.parentNode = null;
            this.style = {{}};
            this.listeners = {{}};
            this.value = '';
            this.selectionStart = 0;
            this.selectionEnd = 0;
            this.isContentEditable = false;
          }}
          set innerHTML(value) {{
            this._innerHTML = String(value);
            if (value === '') this.children = [];
          }}
          get innerHTML() {{ return this._innerHTML || ''; }}
          appendChild(child) {{
            child.parentNode = this;
            this.children.push(child);
            return child;
          }}
          addEventListener(type, listener) {{ this.listeners[type] = listener; }}
          removeEventListener() {{}}
          dispatch(type) {{
            this.listeners[type]?.({{
              type,
              target: this,
              preventDefault() {{}},
              stopPropagation() {{}},
              stopImmediatePropagation() {{}},
            }});
          }}
          dispatchEvent() {{}}
          focus() {{}}
          remove() {{
            if (this.parentNode) {{
              this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
            }}
          }}
          contains(candidate) {{
            return candidate === this || this.children.some((child) => child.contains(candidate));
          }}
          getBoundingClientRect() {{
            return {{ left: 10, right: 30, top: 10, bottom: 30, width: 20, height: 20 }};
          }}
          querySelector(selector) {{ return this.querySelectorAll(selector)[0] || null; }}
          querySelectorAll(selector) {{
            const className = selector.startsWith('.') ? selector.slice(1) : null;
            const found = [];
            const visit = (node) => {{
              for (const child of node.children) {{
                if (className && child.classList.contains(className)) found.push(child);
                visit(child);
              }}
            }};
            visit(this);
            return found;
          }}
        }}
        const body = new FakeElement('body');
        globalThis.window = {{
          __ODY_LANG: {json.dumps(lang)},
          innerWidth: 1200,
          innerHeight: 800,
          getSelection: () => null,
        }};
        globalThis.localStorage = {{ getItem: () => null }};
        globalThis.document = {{
          body,
          createElement: (tagName) => new FakeElement(tagName),
          getElementById: () => null,
          querySelectorAll: () => [],
          addEventListener() {{}},
          removeEventListener() {{}},
        }};
        globalThis.getComputedStyle = () => ({{
          display: 'block',
          visibility: 'visible',
          zIndex: '250',
        }});
        globalThis.requestAnimationFrame = (callback) => callback();
        globalThis.setTimeout = (callback) => {{ callback(); return 0; }};
        const target = new FakeElement('textarea');
        target.value = 'A';
        target.selectionStart = target.selectionEnd = 1;

        await import({json.dumps(DOCUMENT_CATALOG.as_uri())});
        const emoji = await import({json.dumps(EMOJI_PICKER_JS.as_uri())});
        const trigger = emoji.createEmojiButton(() => target);
        trigger.dispatch('click');
        const picker = body.querySelector('.emoji-picker');
        const search = picker.querySelector('.emoji-picker-search');
        const initialGroup = picker.querySelector('.emoji-picker-group-name').textContent;
        const initialTitle = picker.querySelector('.emoji-picker-item').title;
        search.value = {json.dumps(query or ("sorriso" if lang == "pt-BR" else "grin"))};
        search.dispatch('input');
        const filteredItem = picker.querySelector('.emoji-picker-item');
        const filteredTitle = filteredItem?.title || null;
        filteredItem?.dispatch('click');
        console.log(JSON.stringify({{
          triggerTitle: trigger.title,
          searchPlaceholder: search.placeholder,
          initialGroup,
          initialTitle,
          filteredTitle,
          inserted: target.value,
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
def test_shared_ui_catalog_contains_required_contextual_copy() -> None:
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

    assert {key: catalog.get(key) for key in expected} == expected
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
        # tourHints.js
        "Pro tip:": "Dica:",
        "drag any window's title bar to a screen edge to snap it. Drag to "
        "the top for fullscreen.": (
            "arraste a barra de título de qualquer janela até a borda da tela "
            "para encaixá-la. Arraste até o topo para tela cheia."
        ),
        "Got it": "Entendi",
        # section-management.js
        "Collapse section": "Recolher seção",
        # fileHandler.js
        "file": "arquivo",
        "files": "arquivos",
        "pasted-image": "imagem-colada",
        "Remove all": "Remover todos",
        "Remove attachment": "Remover anexo",
        "image": "imagem",
        "Upload failed": "Falha no envio",
        "Max {n} files allowed": "Máximo de {n} arquivos permitidos",
        # censor.js
        "Click to reveal {label}": "Clique para revelar {label}",
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


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_document_catalog_covers_editor_pdf_toolbar_and_import_copy() -> None:
    catalog = _messages_for(DOCUMENT_CATALOG.name)
    expected = {
        "Scroll left": "Rolar para a esquerda",
        "Scroll right": "Rolar para a direita",
        "Untitled": "Sem título",
        "Document actions": "Ações do documento",
        "Version history": "Histórico de versões",
        "Unlink from chat (kept in the Library)": (
            "Desvincular da conversa (mantido na Biblioteca)"
        ),
        "New document — start typing": "Novo documento — comece a digitar",
        "New document": "Novo documento",
        "Export filled PDF": "Exportar PDF preenchido",
        "Loading field values…": "Carregando valores dos campos…",
        "Fetching mapping…": "Carregando mapeamento…",
        "Download PDF": "Baixar PDF",
        "{filled} of {total} fields filled. Review and adjust below before downloading.": (
            "{filled} de {total} campos preenchidos. Revise e ajuste abaixo antes "
            "de baixar."
        ),
        "Jump to:": "Ir para:",
        "Jump to page {page}": "Ir para a página {page}",
        "↑ Top": "↑ Início",
        "↓ Bottom": "↓ Fim",
        "Jump to the last page (signature fields are usually here)": (
            "Ir para a última página (os campos de assinatura geralmente ficam aqui)"
        ),
        "Page {page}": "Página {page}",
        "Remove signature from this field": "Remover assinatura deste campo",
        "Change": "Alterar",
        "Sign here": "Assine aqui",
        "Today": "Hoje",
        "Set to today's date": "Definir como a data de hoje",
        "— (none) —": "— (nenhum) —",
        "Building PDF…": "Gerando PDF…",
        "Error: {message}": "Erro: {message}",
        "Failed to load preview: {message}": (
            "Falha ao carregar a pré-visualização: {message}"
        ),
        "Undo failed": "Falha ao desfazer",
        "Loading PDF…": "Carregando PDF…",
        "Failed to load PDF view: {message}": (
            "Falha ao carregar a visualização do PDF: {message}"
        ),
        "Type…": "Digite…",
        "Delete annotation": "Excluir anotação",
        "Drag to move": "Arraste para mover",
        "Drag to resize": "Arraste para redimensionar",
        "Text annotation options": "Opções da anotação de texto",
        "Line spacing": "Espaçamento entre linhas",
        "What should the AI fill in?\n"
        '(e.g. "My name is Jane Doe, address 123 Main St, dob 1990-01-15")': (
            "O que a IA deve preencher?\n"
            '(por exemplo, "Meu nome é Jane Doe, endereço 123 Main St, '
            'data de nascimento 1990-01-15")'
        ),
        "Thinking…": "Pensando…",
        "AI found nothing to fill": "A IA não encontrou nada para preencher",
        "AI added 1 annotation": "A IA adicionou 1 anotação",
        "AI added {count} annotations": "A IA adicionou {count} anotações",
        "AI fill failed: {message}": "Falha no preenchimento por IA: {message}",
        "AI fill": "Preencher com IA",
        "Editing…": "Editando…",
        "Saving…": "Salvando…",
        "Saved": "Salvo",
        "Collapse panel": "Recolher painel",
        "Hide panel": "Ocultar painel",
        "Undo (Ctrl+Z)": "Desfazer (Ctrl+Z)",
        "Run / Preview": "Executar / Pré-visualizar",
        "editing": "editando",
        "Export PDF": "Exportar PDF",
        "Toggle PDF view": "Alternar visualização do PDF",
        "Hide email fields": "Ocultar campos do e-mail",
        "Show email fields": "Mostrar campos do e-mail",
        "No recipient · No subject": "Sem destinatário · Sem assunto",
        "No recipient": "Sem destinatário",
        "No subject": "Sem assunto",
        "To": "Para",
        "Show Cc/Bcc": "Mostrar Cc/Cco",
        "Bcc": "Cco",
        "Hide Cc/Bcc": "Ocultar Cc/Cco",
        "Subject": "Assunto",
        "Edit or preview": "Editar ou pré-visualizar",
        "Edit source (Ctrl+Alt+M to toggle)": (
            "Editar fonte (Ctrl+Alt+M para alternar)"
        ),
        "Preview (Ctrl+Alt+M to toggle)": (
            "Pré-visualizar (Ctrl+Alt+M para alternar)"
        ),
        "Code or run": "Código ou execução",
        "Edit code": "Editar código",
        "Draft a reply with AI (Fast / Full + optional context)": (
            "Criar rascunho de resposta com IA (Rápido / Completo + contexto opcional)"
        ),
        "Reply": "Responder",
        "Font size": "Tamanho da fonte",
        "Compare changes": "Comparar alterações",
        "Bold (Ctrl+B)": "Negrito (Ctrl+B)",
        "Italic (Ctrl+I)": "Itálico (Ctrl+I)",
        "Strikethrough": "Tachado",
        "Heading": "Título",
        "List": "Lista",
        "Link": "Hiperlink",
        "Insert image": "Inserir imagem",
        "Code": "Código",
        "Horizontal rule": "Linha horizontal",
        "Add text box (then click on PDF)": (
            "Adicionar caixa de texto (depois clique no PDF)"
        ),
        "Add checkmark (then click on PDF)": (
            "Adicionar marca de seleção (depois clique no PDF)"
        ),
        "Add signature (then click on PDF)": (
            "Adicionar assinatura (depois clique no PDF)"
        ),
        "sign": "assinar",
        "Reload PDF view": "Recarregar visualização do PDF",
        "More formatting": "Mais opções de formatação",
        "Find...": "Localizar...",
        "Previous": "Anterior",
        "Next": "Próximo",
        "Document content...": "Conteúdo do documento...",
        "Close email": "Fechar e-mail",
        "Send email (Ctrl+Enter)": "Enviar e-mail (Ctrl+Enter)",
        "Send": "Enviar",
        "More send options": "Mais opções de envio",
        "Save Draft": "Salvar rascunho",
        "Schedule Send...": "Agendar envio...",
        "Mark Unread": "Marcar como não lido",
        "Copy document": "Copiar documento",
        "Export as…": "Exportar como…",
        "Export options": "Opções de exportação",
        "Version History": "Histórico de versões",
        "Unlink": "Desvincular",
        "Edit": "Editar",
        "Table View": "Visualização em tabela",
        "Run": "Executar",
        "Download": "Baixar",
        "Send signed reply": "Enviar resposta assinada",
        "Import from library": "Importar da biblioteca",
        "Import from device": "Importar do dispositivo",
        "Filled PDF (.pdf)": "PDF preenchido (.pdf)",
        "Export Markdown": "Exportar Markdown",
        "Print as PDF": "Imprimir como PDF",
        "Export as Word": "Exportar como Word",
        "1 result": "1 resultado",
        "{count} results": "{count} resultados",
    }
    reused = {
        "Cancel",
        "Close",
        "Delete",
        "Loading…",
        "Preview",
        "Save",
        "Save failed",
    }

    assert {key: catalog.get(key) for key in expected} == expected
    assert reused.isdisjoint(catalog)


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_document_catalog_covers_remaining_actions_and_states() -> None:
    catalog = _messages_for(DOCUMENT_CATALOG.name)
    expected = {
        "No attachments found": "Nenhum anexo encontrado",
        "Your message mentions an attachment, but nothing is attached. Send anyway?": (
            "Sua mensagem menciona um anexo, mas nada foi anexado. Enviar mesmo assim?"
        ),
        "Go back": "Voltar",
        "Send anyway": "Enviar mesmo assim",
        "To and body are required": "Destinatário e corpo são obrigatórios",
        "Reply body is empty": "O corpo da resposta está vazio",
        "Sending": "Enviando",
        "Send canceled": "Envio cancelado",
        "Send failed ({status})": "Falha no envio ({status})",
        "Message sent": "Mensagem enviada",
        "View Message": "Ver mensagem",
        "Failed to send": "Falha ao enviar",
        "Failed to send email: {message}": "Falha ao enviar e-mail: {message}",
        "Failed to send email": "Falha ao enviar e-mail",
        "Saving...": "Salvando...",
        "Draft saved to mailbox": "Rascunho salvo na caixa de e-mail",
        "Failed to save draft": "Falha ao salvar o rascunho",
        "Saving draft timed out": "O salvamento do rascunho excedeu o tempo limite",
        "Draft": "Rascunho",
        "Add context (optional)": "Adicionar contexto (opcional)",
        "Shorter, faster draft": "Rascunho mais curto e rápido",
        "Fast": "Rápido",
        "Fuller reply with more context": "Resposta mais completa, com mais contexto",
        "Full": "Completo",
        "Drafting...": "Criando rascunho...",
        "AI draft inserted ({model})": "Rascunho de IA inserido ({model})",
        "Failed to generate reply": "Falha ao gerar a resposta",
        "Failed to generate AI reply": "Falha ao gerar a resposta com IA",
        "Schedule Send": "Agendar envio",
        "Quick presets": "Opções rápidas",
        "In 1 hour": "Em 1 hora",
        "In 3 hours": "Em 3 horas",
        "Tomorrow 9am": "Amanhã às 9h",
        "Monday 9am": "Segunda-feira às 9h",
        "Or pick a specific time": "Ou escolha um horário específico",
        "Schedule": "Agendar",
        "Please pick a time": "Escolha um horário",
        "Scheduled for {time}": "Agendado para {time}",
        "Failed to schedule": "Falha ao agendar",
        "Export failed: {message}": "Falha na exportação: {message}",
        "Save failed: {status}": "Falha ao salvar: {status}",
        "Reply to the sender with this filled file attached": (
            "Responder ao remetente com este arquivo preenchido em anexo"
        ),
        "Attach": "Anexar",
        "Insert link": "Inserir link",
        "Link text (optional)": "Texto do link (opcional)",
        "Insert": "Inserir",
        "Selected regions — type in chat to edit": (
            "Trechos selecionados — digite na conversa para editar"
        ),
        "{line} selected": "{line} selecionada",
        "{count} selections ({lines})": "{count} seleções ({lines})",
        "Clear all selections": "Limpar todas as seleções",
        "Close all suggestions": "Fechar todas as sugestões",
        "Accept": "Aceitar",
        "Skip": "Ignorar",
        "Accept All": "Aceitar tudo",
        "Reject All": "Rejeitar tudo",
        "Accept change": "Aceitar alteração",
        "Reject change": "Rejeitar alteração",
        "{resolved} / 1 change resolved": "{resolved} / 1 alteração resolvida",
        "{resolved} / {total} changes resolved": (
            "{resolved} / {total} alterações resolvidas"
        ),
        "+1 more change": "+1 alteração adicional",
        "+{count} more changes": "+{count} alterações adicionais",
        'Reply draft ready — "{filename}" attached': (
            'Rascunho de resposta pronto — "{filename}" anexado'
        ),
        "Document saved": "Documento salvo",
        "Autosave failed": "Falha no salvamento automático",
        "Failed to save document": "Falha ao salvar o documento",
        "Import failed: {message}": "Falha na importação: {message}",
        "Exported as HTML": "Exportado como HTML",
        "Failed to load PDF library": "Falha ao carregar a biblioteca de PDF",
        "Exporting PDF...": "Exportando PDF...",
        "Failed to load DOCX library": "Falha ao carregar a biblioteca DOCX",
        "Exported as DOCX": "Exportado como DOCX",
        "this document": "este documento",
        "Document deleted": "Documento excluído",
        "Failed to delete document": "Falha ao excluir o documento",
        "latest": "mais recente",
        "Restore": "Restaurar",
        "Failed to load versions": "Falha ao carregar as versões",
        "Restored to v{version}": "Restaurado para a v{version}",
        "Failed to restore version": "Falha ao restaurar a versão",
    }

    assert {key: catalog.get(key) for key in expected} == expected


def test_document_editor_uses_contextual_i18n_without_translating_runtime_data() -> None:
    source = DOCUMENT_JS.read_text(encoding="utf-8")
    required_call_sites = {
        "central import": "import { t } from './i18n.js';",
        "escaped PDF heading": "${_esc(t('Export filled PDF'))}",
        "PDF summary placeholders": (
            "t('{filled} of {total} fields filled. Review and adjust below before "
            "downloading.', { filled: filledNow, total })"
        ),
        "escaped raw PDF error": (
            "_escHtml(t('Failed to load PDF view: {message}', "
            "{ message: e.message || String(e) }))"
        ),
        "annotation singular": "t('AI added 1 annotation')",
        "annotation plural": (
            "t('AI added {count} annotations', { count: proposed.length })"
        ),
        "toolbar": "${_esc(t('More formatting'))}",
        "find placeholder": "${_esc(t('Find...'))}",
        "find singular": "t('1 result')",
        "find plural": "t('{count} results', { count: _findMatches.length })",
        "stable PDF field label": "label.textContent = f.label || f.name",
        "stable annotation value": "input.value = ann.value || ''",
        "stable user instruction": (
            "body: JSON.stringify({ instruction: instruction.trim() })"
        ),
    }
    missing = [
        f"{context}: {snippet}"
        for context, snippet in required_call_sites.items()
        if snippet not in source
    ]
    assert not missing, "Call sites do editor sem i18n seguro:\n" + "\n".join(missing)

    side_effect_catalog_import = re.compile(
        r"""import\s+['"][^'"]*i18n/[^'"]+\.pt-BR\.js['"]"""
    )
    assert not side_effect_catalog_import.search(source)


def test_document_remaining_actions_use_contextual_i18n_and_preserve_data() -> None:
    source = DOCUMENT_JS.read_text(encoding="utf-8")
    required_call_sites = {
        "attachment warning": "${_esc(t('No attachments found'))}",
        "send status": "document.createTextNode(t('Sending'))",
        "raw send error": "data.error || t('Failed to send')",
        "send error placeholder": (
            "t('Failed to send email: {message}', { message: e.message })"
        ),
        "draft state": "btn.textContent = t('Saving...')",
        "AI context": "${_esc(t('Add context (optional)'))}",
        "raw AI model": (
            "t('AI draft inserted ({model})', { model: data.model_used || 'AI' })"
        ),
        "schedule time": (
            "t('Scheduled for {time}', "
            "{ time: new Date(localDt).toLocaleString() })"
        ),
        "export error": (
            "t('Export failed: {message}', { message: e.message })"
        ),
        "PDF status": "t('Save failed: {status}', { status: res.status })",
        "attach label": "${_esc(t('Attach'))}",
        "link modal": "${_esc(t('Insert link'))}",
        "selection singular": "t('{line} selected', { line: labels[0] })",
        "selection plural": (
            "t('{count} selections ({lines})', "
            "{ count: _selections.length, lines: labels.join(', ') })"
        ),
        "raw suggestion reason": "${_esc(sugg.reason)}",
        "diff plural": (
            "t('{resolved} / {total} changes resolved', "
            "{ resolved, total: _diffChunks.length })"
        ),
        "raw filename": (
            "t('Reply draft ready — \"{filename}\" attached', "
            "{ filename: att.filename })"
        ),
        "raw import error": (
            "t('Import failed: {message}', { message: err.message || err })"
        ),
        "raw delete title": (
            "t('Delete \"{name}\"?', { name })"
        ),
        "restore version": (
            "t('Restored to v{version}', { version: num })"
        ),
    }
    missing = [
        f"{context}: {snippet}"
        for context, snippet in required_call_sites.items()
        for _ in [None]
        if snippet not in source
    ]
    assert not missing, "Ações restantes sem i18n seguro:\n" + "\n".join(missing)


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_document_remaining_action_templates_translate_and_preserve_values() -> None:
    assert _translated_document_action_examples("pt-BR") == {
        "attachment": "Nenhum anexo encontrado",
        "sendError": "Falha ao enviar e-mail: SMTP <raw>",
        "aiDraft": "Rascunho de IA inserido (Modelo <raw>)",
        "scheduled": "Agendado para 30/06/2026 09:00",
        "exportError": "Falha na exportação: backend <raw>",
        "selectionOne": "L2-L4 selecionada",
        "selectionMany": "3 seleções (L1, L4, L9)",
        "diffOne": "1 / 1 alteração resolvida",
        "diffMany": "2 / 4 alterações resolvidas",
        "importError": "Falha na importação: arquivo <raw>",
        "deletePrompt": 'Excluir "Contrato <raw>"?',
        "restored": "Restaurado para a v7",
    }
    assert _translated_document_action_examples("en") == {
        "attachment": "No attachments found",
        "sendError": "Failed to send email: SMTP <raw>",
        "aiDraft": "AI draft inserted (Modelo <raw>)",
        "scheduled": "Scheduled for 30/06/2026 09:00",
        "exportError": "Export failed: backend <raw>",
        "selectionOne": "L2-L4 selected",
        "selectionMany": "3 selections (L1, L4, L9)",
        "diffOne": "1 / 1 change resolved",
        "diffMany": "2 / 4 changes resolved",
        "importError": "Import failed: arquivo <raw>",
        "deletePrompt": 'Delete "Contrato <raw>"?',
        "restored": "Restored to v7",
    }


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_document_version_history_escapes_malicious_api_source() -> None:
    source = DOCUMENT_JS.read_text(encoding="utf-8")
    assert "${_esc(v.source || '')}" in source
    assert "${_esc(v.summary)}" in source
    assert _escaped_document_version_source(
        '<img src=x onerror="alert(1)">'
    ) == '&lt;img src=x onerror=&quot;alert(1)&quot;&gt;'


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_document_editor_templates_translate_pdf_toolbar_and_plurals() -> None:
    assert _translated_document_editor_examples("pt-BR") == {
        "pdfTitle": "Exportar PDF preenchido",
        "pdfSummary": (
            "2 de 5 campos preenchidos. Revise e ajuste abaixo antes de baixar."
        ),
        "pdfError": "Falha ao carregar a visualização do PDF: backend <raw>",
        "toolbar": "Mais opções de formatação",
        "findOne": "1 resultado",
        "findMany": "4 resultados",
        "annotationOne": "A IA adicionou 1 anotação",
        "annotationMany": "A IA adicionou 3 anotações",
    }
    assert _translated_document_editor_examples("en") == {
        "pdfTitle": "Export filled PDF",
        "pdfSummary": (
            "2 of 5 fields filled. Review and adjust below before downloading."
        ),
        "pdfError": "Failed to load PDF view: backend <raw>",
        "toolbar": "More formatting",
        "findOne": "1 result",
        "findMany": "4 results",
        "annotationOne": "AI added 1 annotation",
        "annotationMany": "AI added 3 annotations",
    }


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_document_catalog_covers_runner_signature_and_emoji_picker_copy() -> None:
    catalog = _messages_for(DOCUMENT_CATALOG.name)
    expected = {
        "Copy": "Copiar",
        "Copied!": "Copiado!",
        "Copy failed": "Falha ao copiar",
        "Copy output": "Copiar saída",
        "Failed to load Pyodide": "Falha ao carregar o Pyodide",
        "Loading Python runtime (first time ~10 MB)...": (
            "Carregando o ambiente Python (primeira vez: ~10 MB)..."
        ),
        "Failed to load Python runtime: {message}": (
            "Falha ao carregar o ambiente Python: {message}"
        ),
        "Running...": "Executando...",
        "Execution timed out (10 s)": "A execução excedeu o tempo limite (10 s)",
        "(no output)": "(sem saída)",
        "Running on server...": "Executando no servidor...",
        "(no output) — exit code {code}": (
            "(sem saída) — código de saída {code}"
        ),
        "Exit code: {code}": "Código de saída: {code}",
        "Execution failed: {message}": "Falha na execução: {message}",
        "Popup blocked — please allow popups for this site.": (
            "O pop-up foi bloqueado — permita pop-ups para este site."
        ),
        "Opened in new window": "Aberto em uma nova janela",
        "Draw your signature": "Desenhe sua assinatura",
        "Smoothness": "Suavização",
        "Name (optional, e.g. 'Full' or 'Initials')": (
            "Nome (opcional, por exemplo, 'Completa' ou 'Iniciais')"
        ),
        "Clear": "Limpar",
        "Undo": "Desfazer",
        "Signature": "Assinatura",
        "Failed to save signature: {message}": (
            "Falha ao salvar a assinatura: {message}"
        ),
        "Choose a signature": "Escolha uma assinatura",
        "Draw new signature": "Desenhar nova assinatura",
        "No saved signatures yet — draw one above.": (
            "Ainda não há assinaturas salvas — desenhe uma acima."
        ),
        "Delete this signature?": "Excluir esta assinatura?",
        "Insert icon": "Inserir ícone",
        "Search…": "Pesquisar…",
        "Faces & Hearts": "Rostos e corações",
        "Checks & Marks": "Marcas de seleção e símbolos",
        "Arrows": "Setas",
        "Math & Punctuation": "Matemática e pontuação",
        "Currency & Misc": "Moedas e diversos",
        "grin": "sorriso",
        "heart-outline": "contorno de coração",
        "star": "estrela",
        "star-outline": "contorno de estrela",
        "sparkle": "brilho",
        "moon": "lua",
        "check": "marca de seleção",
        "cross": "xis",
        "cross-heavy": "xis grosso",
        "star-filled": "estrela preenchida",
        "star-empty": "estrela vazia",
        "dot": "ponto",
        "circle": "círculo",
        "square-filled": "quadrado preenchido",
        "square-empty": "quadrado vazio",
        "diamond": "losango",
        "diamond-empty": "losango vazio",
        "dagger": "óbelo",
        "arrow-right": "seta para a direita",
        "arrow-left": "seta para a esquerda",
        "arrow-up": "seta para cima",
        "arrow-down": "seta para baixo",
        "arrow-r-dbl": "seta dupla para a direita",
        "arrow-l-dbl": "seta dupla para a esquerda",
        "plus-minus": "mais ou menos",
        "multiply": "multiplicação",
        "divide": "divisão",
        "approx": "aproximadamente",
        "not-equal": "diferente",
        "lte": "menor ou igual",
        "gte": "maior ou igual",
        "infinity": "infinito",
        "pi": "número pi",
        "sum": "somatório",
        "delta": "símbolo delta",
        "root": "raiz quadrada",
        "degree": "grau",
        "section": "seção",
        "pilcrow": "parágrafo",
        "bullet": "marcador",
        "ellipsis": "reticências",
        "em-dash": "travessão",
        "quote-l": "aspas angulares à esquerda",
        "quote-r": "aspas angulares à direita",
        "quote-dbl": "aspas duplas",
        "euro": "símbolo do euro",
        "pound": "libra",
        "yen": "iene",
        "dollar": "dólar",
        "cent": "centavo",
        "percent": "porcentagem",
        "per-mille": "por mil",
        "number": "número",
    }
    reused = {"Copied", "Close", "Cancel", "Save", "Delete"}
    emoji_source = EMOJI_PICKER_JS.read_text(encoding="utf-8")
    visible_emoji_keys = set(
        re.findall(r"""name:\s*'([^']+)'""", emoji_source)
    ) | set(
        re.findall(r"""\['[^']+',\s*'([^']+)',\s*I\(""", emoji_source)
    )

    assert {key: catalog.get(key) for key in expected} == expected
    assert visible_emoji_keys <= expected.keys()
    assert reused.isdisjoint(catalog)


def test_document_auxiliary_modules_translate_copy_without_touching_runtime_data() -> None:
    sources = {
        "runner": CODE_RUNNER_JS.read_text(encoding="utf-8"),
        "signature": SIGNATURE_JS.read_text(encoding="utf-8"),
        "emoji": EMOJI_PICKER_JS.read_text(encoding="utf-8"),
    }
    required = {
        "runner": (
            "import { t } from './i18n.js'",
            "uiModule.showToast(t('Copied'))",
            "t('Failed to load Python runtime: {message}', { message: e.message })",
            "t('(no output) — exit code {code}', { code: data.exit_code })",
            "t('Exit code: {code}', { code: data.exit_code })",
            "t('Execution failed: {message}', { message: e.message })",
            "showOutput(panel, data.stderr, true)",
            "stdoutEl.textContent = data.stdout",
            "showOutput(panel, data.error, true)",
            "showOutput(panel, e.message, true)",
        ),
        "signature": (
            "import { t } from './i18n.js'",
            "${t('Draw your signature')}",
            "${t(\"Name (optional, e.g. 'Full' or 'Initials')\")}",
            "t('Failed to save signature: {message}', { message: e.message })",
            "const displayName = s.name === 'Signature' ? t('Signature') : (s.name || '')",
            "${_esc(displayName)}",
            "window.styledConfirm(t('Delete this signature?'),",
            "confirmText: t('Delete')",
        ),
        "emoji": (
            "import { t } from './i18n.js'",
            "btn.title = t('Insert icon')",
            "search.placeholder = t('Search…')",
            "header.textContent = t(group.name)",
            "btn.title = t(label)",
            "_normalizeSearchText(t(item[1])).includes(f)",
            "_insertEmoji(char)",
        ),
    }
    missing = [
        f"{module}: {snippet}"
        for module, snippets in required.items()
        for snippet in snippets
        if snippet not in sources[module]
    ]
    assert not missing, "Call sites auxiliares sem i18n seguro:\n" + "\n".join(missing)

    side_effect_catalog_import = re.compile(
        r"""import\s+['"][^'"]*i18n/[^'"]+\.pt-BR\.js['"]"""
    )
    assert not [
        module
        for module, source in sources.items()
        if side_effect_catalog_import.search(source)
    ]


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_document_auxiliary_templates_preserve_placeholders_in_both_languages() -> None:
    assert _translated_document_auxiliary_examples("pt-BR") == {
        "runtimeError": "Falha ao carregar o ambiente Python: Network 502",
        "exit": "(sem saída) — código de saída 7",
        "saveError": "Falha ao salvar a assinatura: quota exceeded",
        "emojiGroup": "Rostos e corações",
        "emojiTitle": "contorno de coração",
    }
    assert _translated_document_auxiliary_examples("en") == {
        "runtimeError": "Failed to load Python runtime: Network 502",
        "exit": "(no output) — exit code 7",
        "saveError": "Failed to save signature: quota exceeded",
        "emojiGroup": "Faces & Hearts",
        "emojiTitle": "heart-outline",
    }


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_code_runner_executes_with_translated_status_and_verbatim_stdout() -> None:
    assert _run_code_runner_probe("pt-BR") == {
        "loading": '<div class="code-runner-loading">Executando no servidor...</div>',
        "output": "resultado externo <raw>",
        "outputClass": "code-runner-pre",
    }
    assert _run_code_runner_probe("en") == {
        "loading": '<div class="code-runner-loading">Running on server...</div>',
        "output": "resultado externo <raw>",
        "outputClass": "code-runner-pre",
    }


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_signature_picker_renders_translated_copy_and_escaped_dynamic_name() -> None:
    assert _rendered_signature_picker("pt-BR") == {
        "heading": "Escolha uma assinatura",
        "closeTitle": "Fechar",
        "newButton": "+ Desenhar nova assinatura",
        "dynamicName": "Rubrica &lt;A&gt;",
    }
    assert _rendered_signature_picker("en") == {
        "heading": "Choose a signature",
        "closeTitle": "Close",
        "newButton": "+ Draw new signature",
        "dynamicName": "Rubrica &lt;A&gt;",
    }


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_signature_default_name_stays_canonical_in_api_and_localizes_on_render() -> None:
    assert _saved_empty_signature_name("pt-BR") == {
        "postedName": "Signature",
        "returnedName": "Signature",
    }
    assert _rendered_signature_picker("pt-BR", "Signature")["dynamicName"] == (
        "Assinatura"
    )
    assert _rendered_signature_picker("en", "Signature")["dynamicName"] == "Signature"


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_emoji_picker_translates_search_and_titles_without_changing_character() -> None:
    assert _rendered_emoji_picker("pt-BR") == {
        "triggerTitle": "Inserir ícone",
        "searchPlaceholder": "Pesquisar…",
        "initialGroup": "Rostos e corações",
        "initialTitle": "sorriso",
        "filteredTitle": "sorriso",
        "inserted": "A☻︎",
    }
    assert _rendered_emoji_picker("en") == {
        "triggerTitle": "Insert icon",
        "searchPlaceholder": "Search…",
        "initialGroup": "Faces & Hearts",
        "initialTitle": "grin",
        "filteredTitle": "grin",
        "inserted": "A☻︎",
    }


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_emoji_picker_search_ignores_portuguese_diacritics() -> None:
    circle = _rendered_emoji_picker("pt-BR", "circulo")
    assert {
        "filteredTitle": circle["filteredTitle"],
        "inserted": circle["inserted"],
    } == {
        "filteredTitle": "círculo",
        "inserted": "A○︎",
    }

    number = _rendered_emoji_picker("pt-BR", "numero")
    assert {
        "filteredTitle": number["filteredTitle"],
        "inserted": number["inserted"],
    } == {
        "filteredTitle": "número",
        "inserted": "A№︎",
    }
