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
CATALOGS = tuple(sorted(CATALOG_DIR.glob("*.pt-BR.js")))
HAS_NODE = shutil.which("node") is not None

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
