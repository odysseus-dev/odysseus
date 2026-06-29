# Localização pt-BR da Interface — Plano de Implementação

> **Para agentes executores:** SUB-SKILL OBRIGATÓRIA: use `superpowers:subagent-driven-development` para executar cada tarefa, com revisão de conformidade e qualidade antes de avançar. Os passos usam caixas de seleção para rastreamento.

**Objetivo:** concluir a infraestrutura de idioma pt-BR/inglês e localizar todas as superfícies visíveis da interface e dos previews do Odysseus, mantendo inglês como fallback e opção selecionável.

**Arquitetura:** strings-fonte em inglês continuam sendo as chaves de tradução. Dicionários pt-BR são registrados uma vez pelo carregamento central de `static/app.js`; módulos dinâmicos chamam `t()`/`window.t()` e o HTML estático é traduzido por `translateDOM()`. Cada lote deve manter fronteiras de dicionário claras e não duplicar imports laterais nos módulos.

**Stack:** JavaScript ES modules, HTML, Python/pytest, Node.js 22.

---

## Invariantes

- O idioma aceito é exclusivamente `pt-BR` ou `en`; qualquer outro valor normaliza para `pt-BR`.
- `pt-BR` é o padrão e todos os textos de interface/preview cobertos aparecem em português.
- `en` preserva as strings-fonte sem mutar o DOM.
- `data-i18n-skip` protege texto e atributos de toda a subárvore.
- Identificadores, código, URLs, rotas, logs de depuração, IDs de modelo, valores `data-*`, regex e conteúdo do usuário não são traduzidos.
- Placeholders usam nomes estáveis (`{name}`, `{count}`, `{reason}`) e preservam valores dinâmicos.
- Os dicionários são carregados centralmente por `static/app.js`; módulos não importam os próprios dicionários.
- Cada chave inglesa duplicada deve ter a mesma tradução em todos os dicionários; a auditoria final falha em colisões divergentes.

### Tarefa 1: endurecer e testar o runtime i18n

**Arquivos:**

- Criar: `tests/test_i18n_js.py`
- Modificar: `static/js/i18n.js`
- Modificar: `static/js/languagePref.js`
- Modificar: `static/index.html`
- Modificar: `static/js/i18n/README.md`

- [x] **Passo 1: escrever testes falhos para normalização, fallback, interpolação, opt-out e reconciliação**

  O teste Python deve executar módulos ES reais com `node --input-type=module -e`, como `tests/test_model_sort_js.py`. Cobrir:

  ```text
  normalizeLang('pt-BR') -> 'pt-BR'
  normalizeLang('en') -> 'en'
  normalizeLang('pt-br' | '' | null | objeto) -> 'pt-BR'
  t() traduz em pt-BR, preserva inglês em en e interpola placeholders
  translateDOM() não altera atributos abaixo de data-i18n-skip
  resposta remota diferente chama setLang() uma única vez
  resposta remota igual não recarrega
  ```

- [x] **Passo 2: executar RED**

  Executar:

  ```powershell
  rtk pytest -q tests/test_i18n_js.py
  ```

  Esperado: falhas por ausência de `normalizeLang`, opt-out incompleto e reconciliação sem `setLang`.

- [x] **Passo 3: implementar a correção mínima**

  Em `i18n.js`, exportar e reutilizar:

  ```js
  const SUPPORTED_LANGS = new Set(['pt-BR', 'en']);
  export function normalizeLang(value) {
    return typeof value === 'string' && SUPPORTED_LANGS.has(value) ? value : 'pt-BR';
  }
  ```

  Aplicar a normalização em `activeLang()` e `setLang()`. No laço de atributos de `translateDOM()`, usar `isSkipped(el)`. Em `languagePref.js`, quando o valor remoto normalizado diferir do ativo, chamar `setLang(remoteLang)`; quando for igual, apenas sincronizar o seletor. O bootstrap inline deve aplicar a mesma allowlist e sempre sincronizar `document.documentElement.lang`.

- [x] **Passo 4: executar GREEN e regressões de preferências**

  ```powershell
  rtk pytest -q tests/test_i18n_js.py tests/test_prefs_routes.py tests/test_prefs_atomic_write.py tests/test_prefs_single_user_no_clobber.py
  rtk proxy node --check static/js/i18n.js
  rtk proxy node --check static/js/languagePref.js
  ```

- [x] **Passo 5: revisar conformidade e qualidade; corrigir findings importantes**

### Tarefa 2: localizar HTML, orquestrador e UI compartilhada

**Arquivos:**

- Modificar: `static/js/i18n/index-html.pt-BR.js`
- Modificar: `static/js/i18n/app.pt-BR.js`
- Modificar: `static/js/i18n/shared-ui.pt-BR.js`
- Modificar: `static/js/i18n/workspace-misc.pt-BR.js`
- Modificar conforme as chamadas dinâmicas encontradas: `static/index.html`, `static/app.js`, `static/js/ui.js`, `static/js/modalManager.js`, `static/js/spinner.js`, `static/js/escMenuStack.js`, `static/js/workspace.js`, `static/js/sidebar-layout.js`, `static/js/section-management.js`, `static/js/tileManager.js`, `static/js/modalSnap.js`, `static/js/windowDrag.js`, `static/js/windowResize.js`, `static/js/toolWindowZOrder.js`, `static/js/dragSort.js`
- Criar/Modificar: `tests/test_i18n_catalogs.py`

- [ ] **Passo 1: criar teste falho de catálogos**

  O teste deve importar todos os `*.pt-BR.js`, verificar sintaxe, impedir valores vazios, detectar chaves com traduções divergentes e exigir as strings visíveis do HTML estático.

- [ ] **Passo 2: executar RED**

  ```powershell
  rtk pytest -q tests/test_i18n_catalogs.py
  ```

- [ ] **Passo 3: preencher os quatro dicionários e envolver strings dinâmicas**

  Usar chaves inglesas exatas. Templates dinâmicos devem virar, por exemplo:

  ```js
  window.t('Sorted: {label}', { label })
  window.t('Could not open {name}', { name })
  ```

  Não adicionar imports de dicionário aos módulos: `static/app.js` já faz o carregamento central.

- [ ] **Passo 4: executar GREEN e sintaxe dos arquivos tocados**

  ```powershell
  rtk pytest -q tests/test_i18n_catalogs.py tests/test_i18n_js.py tests/test_app_static_mime.py
  rtk proxy node --check static/app.js
  ```

- [ ] **Passo 5: revisar conformidade e qualidade; corrigir findings importantes**

### Tarefa 3: localizar documentos, editor e biblioteca

**Arquivos:**

- Modificar: `static/js/i18n/document.pt-BR.js`
- Modificar: `static/js/i18n/document-library.pt-BR.js`
- Modificar: `static/js/document.js`, `static/js/codeRunner.js`, `static/js/signature.js`, `static/js/emojiPicker.js`, `static/js/documentLibrary.js`
- Modificar: `tests/test_i18n_catalogs.py`

- [ ] **Passo 1:** adicionar ao teste as strings obrigatórias das superfícies de documento, preview, assinatura, execução e biblioteca.
- [ ] **Passo 2:** executar o teste e confirmar RED por chaves ausentes.
- [ ] **Passo 3:** preencher os dois dicionários e substituir somente literais visíveis por `t()`.
- [ ] **Passo 4:** executar:

  ```powershell
  rtk pytest -q tests/test_i18n_catalogs.py tests/test_document_ai_preview_refresh_js.py tests/test_document_diff_discard_on_update_js.py tests/test_emoji_shortcodes_js.py
  ```

- [ ] **Passo 5:** revisar conformidade e qualidade; corrigir findings importantes.

### Tarefa 4: localizar comunicação e produtividade

**Arquivos:**

- Modificar: `static/js/i18n/email-library.pt-BR.js`, `static/js/i18n/email-inbox.pt-BR.js`, `static/js/i18n/calendar.pt-BR.js`, `static/js/i18n/tasks.pt-BR.js`, `static/js/i18n/notes.pt-BR.js`
- Modificar consumidores: `static/js/emailLibrary.js`, `static/js/emailLibrary/*.js`, `static/js/emailInbox.js`, `static/js/calendar.js`, `static/js/calendar/*.js`, `static/js/tasks.js`, `static/js/notes.js`
- Modificar: `tests/test_i18n_catalogs.py`

- [ ] **Passo 1:** exigir no teste strings de inbox, composição, calendário, recorrência, tarefas e notas.
- [ ] **Passo 2:** executar RED.
- [ ] **Passo 3:** preencher os cinco dicionários e envolver strings dinâmicas com placeholders.
- [ ] **Passo 4:** executar:

  ```powershell
  rtk pytest -q tests/test_i18n_catalogs.py tests/test_calendar_utils_dates_js.py tests/test_notes_z_order_js.py tests/test_notes_select_esc_listener_js.py tests/test_notes_search_reset_on_reopen_js.py tests/test_email_linkify_security_js.py
  ```

- [ ] **Passo 5:** revisar conformidade e qualidade; corrigir findings importantes.

### Tarefa 5: localizar configurações, modelos, administração e Cookbook

**Arquivos:**

- Modificar: `static/js/i18n/settings.pt-BR.js`, `static/js/i18n/theme-pickers.pt-BR.js`, `static/js/i18n/admin.pt-BR.js`, `static/js/i18n/cookbook-build.pt-BR.js`, `static/js/i18n/cookbook-running.pt-BR.js`
- Modificar consumidores: `static/js/settings.js`, `static/js/theme.js`, `static/js/colorPicker.js`, `static/js/modelPicker.js`, `static/js/models.js`, `static/js/admin.js`, `static/js/providerDeviceFlow.js`, `static/js/providers.js`, `static/js/cookbook*.js`
- Modificar: `tests/test_i18n_catalogs.py`

- [ ] **Passo 1:** exigir no teste strings de configurações, seleção de modelo, device flow, build, download, serve, diagnóstico e progresso.
- [ ] **Passo 2:** executar RED.
- [ ] **Passo 3:** preencher os cinco dicionários; manter comandos, IDs, URLs, paths e logs sem tradução.
- [ ] **Passo 4:** executar:

  ```powershell
  rtk pytest -q tests/test_i18n_catalogs.py tests/test_model_sort_js.py tests/test_provider_device_flow_js.py tests/test_cookbook_progress_signal_js.py tests/test_cookbook_port_parsing_js.py tests/test_cookbook_diagnosis_js.py tests/test_cookbook_same_host_server_profiles_js.py
  ```

- [ ] **Passo 5:** revisar conformidade e qualidade; corrigir findings importantes.

### Tarefa 6: localizar galeria, pesquisa, memória, comparação e comandos

**Arquivos:**

- Modificar: `static/js/i18n/slash-commands.pt-BR.js`, `static/js/i18n/gallery-editor.pt-BR.js`, `static/js/i18n/image-editor-tools.pt-BR.js`, `static/js/i18n/compare.pt-BR.js`, `static/js/i18n/research-sessions.pt-BR.js`, `static/js/i18n/memory-skills.pt-BR.js`, `static/js/i18n/chat.pt-BR.js`
- Modificar consumidores: `static/js/slashCommands.js`, `static/js/slashAutocomplete.js`, `static/js/tourAutoplay.js`, `static/js/gallery.js`, `static/js/galleryEditor.js`, `static/js/editor/**/*.js`, `static/js/compare/**/*.js`, `static/js/research/**/*.js`, `static/js/researchSynapse.js`, `static/js/sessions.js`, `static/js/search-chat.js`, `static/js/memory.js`, `static/js/skills.js`, `static/js/chat.js`, `static/js/chatRenderer.js`, `static/js/chatStream.js`, `static/js/streamingRenderer.js`
- Modificar: `tests/test_i18n_catalogs.py`

- [ ] **Passo 1:** exigir no teste as strings visíveis das sete superfícies.
- [ ] **Passo 2:** executar RED.
- [ ] **Passo 3:** preencher os dicionários e localizar os consumidores; integrar Chat por último para evitar sobreposição com documentos, inbox, comandos e pesquisa.
- [ ] **Passo 4:** executar:

  ```powershell
  rtk pytest -q tests/test_i18n_catalogs.py tests/test_compare_js.py tests/test_slash_autocomplete_static.py tests/test_streaming_segmenter_js.py tests/test_copy_message_strips_thinking_js.py
  ```

- [ ] **Passo 5:** revisar conformidade e qualidade; corrigir findings importantes.

### Tarefa 7: auditoria de cobertura, alternância e regressão

**Arquivos:**

- Criar: `scripts/audit_i18n_catalogs.py`
- Modificar: `tests/test_i18n_catalogs.py`
- Corrigir somente os dicionários/consumidores apontados pela auditoria.

- [ ] **Passo 1:** escrever teste falho que execute a auditoria sobre `static/index.html`, `static/app.js` e `static/js/**/*.js`.
- [ ] **Passo 2:** implementar auditoria determinística que reporte arquivo/linha, ignore as categorias não traduzíveis dos invariantes e falhe em stubs vazios, chaves órfãs e colisões divergentes.
- [ ] **Passo 3:** corrigir as lacunas reais reportadas até a auditoria passar.
- [ ] **Passo 4:** executar validação completa:

  ```powershell
  rtk pytest -q tests/test_i18n_js.py tests/test_i18n_catalogs.py tests/test_prefs_routes.py tests/test_prefs_atomic_write.py tests/test_prefs_single_user_no_clobber.py
  rtk proxy node --check static/app.js
  rtk proxy node --check static/js/i18n.js
  rtk proxy node --check static/js/languagePref.js
  ```

- [ ] **Passo 5:** executar smoke em navegador alternando `pt-BR → en → pt-BR`, verificando primeiro carregamento, reload, `html[lang]`, Settings e pelo menos um preview dinâmico.
- [ ] **Passo 6:** executar a suíte completa `rtk pytest -q`; registrar duração, total, skips e qualquer falha.
- [ ] **Passo 7:** solicitar revisão final independente contra este plano e corrigir findings críticos/importantes.
