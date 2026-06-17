# Architecture Runtime Inventory

> **Purpose**: Phase 0 planning baseline for codebase readability improvements (#4071).
> **Parent issue**: [#4082](https://github.com/pewdiepie-archdaemon/odysseus/issues/4082)
> **Last updated**: dev@b58af42 | 2026-06-16
> **Status**: Draft — to be reviewed before follow-up slices open.
> **Snapshot basis**: Importer / file / import-line counts are refreshed to `dev@b58af42` (2026-06-16) and are recomputable via the commands in §3.4. **Line counts** in §2.1 / §2.2 are a snapshot from an earlier baseline and drift as `dev` moves — recompute any of them with `wc -l <file>`. This inventory tracks structure and risk, not live metrics.

This document maps the current runtime module structure, identifies high-risk boundaries, and recommends safe first refactor slices. It does **not** move files, change imports, or alter runtime behavior.

---

## 1. Current Structure Overview

### 1.1 Top-Level Layout

```
odysseus/
├── app.py                    # FastAPI app entrypoint (1,145 lines)
├── conf/                     # Configuration (config.py, settings.py, settings_scrub.py)
├── src/                      # 95 flat .py files + 2 subdirectories
│   ├── agent_tools/          # Tool helpers: document, filesystem, subprocess, web
│   └── search/               # Search subsystem
├── routes/                   # 54 flat .py files — HTTP route handlers
├── core/                     # 10 files — database models, auth, middleware, session
├── mcp_servers/              # 5 files — MCP server implementations
├── scripts/                  # CLI tools and one-shot scripts
├── static/                   # Frontend HTML/CSS/JS
├── tests/                    # 583 test files (~54,800 lines)
└── services/                 # (exists as needed)
```

### 1.2 Directory Flatness Metric

| Directory | Flat `.py` Files | Subdirectories | Concern |
|-----------|-----------------|----------------|---------|
| `src/` | **95** | 2 (`agent_tools/`, `search/`) | No domain grouping; 95 files in one directory |
| `routes/` | **54** | 0 | All route handlers in one flat directory |
| `core/` | 10 | 0 | Manageable, but `database.py` is oversized |

---

## 2. Largest Runtime Modules

### 2.1 Python Backend

| Rank | File | Lines | Classes | Functions | Risk |
|------|------|-------|---------|-----------|------|
| 1 | `src/tool_implementations.py` | **4,032** | 0 | ~48 | **HIGH** |
| 2 | `routes/email_routes.py` | **3,245** | — | — | **MEDIUM** |
| 3 | `routes/cookbook_routes.py` | **2,969** | — | — | **MEDIUM** |
| 4 | `src/agent_loop.py` | **2,961** | 0 | ~24 | **HIGH** |
| 5 | `src/task_scheduler.py` | **2,330** | — | 5 | MEDIUM |
| 6 | `routes/model_routes.py` | **2,266** | — | — | MEDIUM |
| 7 | `core/database.py` | **2,265** | 28 | ~59 helpers | **HIGH** |
| 8 | `src/builtin_actions.py` | **2,262** | 2 | ~24 | MEDIUM |
| 9 | `src/llm_core.py` | **2,164** | — | — | MEDIUM |
| 10 | `mcp_servers/email_server.py` | 2,197 | — | — | LOW (separate process) |
| 11 | `src/visual_report.py` | 1,918 | — | — | LOW |
| 12 | `routes/gallery_routes.py` | 1,896 | — | — | LOW |
| 13 | `src/ai_interaction.py` | 1,846 | — | — | MEDIUM |
| 14 | `routes/document_routes.py` | 1,717 | — | — | LOW |
| 15 | `routes/skills_routes.py` | 1,648 | — | — | LOW |

**Heuristic**: Files > 2,000 lines with 20+ public symbols and many importers are the highest-risk splits. Files 1,000–2,000 lines are medium-risk if tightly coupled.

### 2.2 Frontend

| File | Lines | Concern |
|------|-------|---------|
| `static/style.css` | **36,653** | Entire app CSS in one file (tracked separately in #2617) |
| `static/js/document.js` | **9,776** | Single JS file for document functionality |
| `static/js/slashCommands.js` | 6,498 | |
| `static/js/settings.js` | 5,266 | |
| `static/js/emailLibrary.js` | 5,217 | |
| `static/js/notes.js` | 5,124 | |
| `static/js/chat.js` | 4,985 | |
| `static/app.js` | 4,090 | |

**Note**: Frontend modularization is tracked separately in #2617 (CSS) and is not the focus of this Phase 0 inventory. Frontend is listed here for completeness but follow-up slices should target Python backend boundaries first.

---

## 3. Import Dependency Graph

### 3.1 Who Depends on `core/database.py`

**102 files** import from `core.database` — this is the most depended-upon module:

- All route handlers (`routes/*.py`)
- Most `src/*.py` files
- `core/session_manager.py`, `core/auth.py`
- Multiple test files

**Implication**: Any split of `core/database.py` is the highest-risk refactor. It should be tackled **last**, never first.

### 3.2 Who Depends on `src/tool_implementations.py`

**17 files** import from `src.tool_implementations`:
- `src/agent_loop.py`, `src/builtin_actions.py`, `src/tool_index.py`
- `src/task_scheduler.py`, `src/tool_policy.py`
- Various tests

### 3.3 Who Depends on `src/agent_loop.py`

**22 files** import from `src.agent_loop`:

- `src/tool_policy.py`, `src/teacher_escalation.py`, `src/bg_monitor.py`
- `src/task_scheduler.py`
- Multiple test files

### 3.4 Cross-Layer Import Violations

**`src/` importing from `routes/`** (backwards dependency — domain logic depending on HTTP layer):

```
src/tool_implementations.py ──→ routes/calendar_routes.py
src/tool_implementations.py ──→ routes/cookbook_helpers.py
src/tool_implementations.py ──→ routes/email_helpers.py
src/tool_implementations.py ──→ routes/email_pollers.py
src/tool_implementations.py ──→ routes/email_routes.py
src/tool_implementations.py ──→ routes/model_routes.py
src/tool_implementations.py ──→ routes/note_routes.py
src/tool_implementations.py ──→ routes/prefs_routes.py
```

> These are **runtime imports** (inside function bodies, not at module top), which mitigates circular import risk but indicates fuzzy layer boundaries. Function-level inline imports from the HTTP layer into business logic are a code smell.

**Import counts (top-level)**:
| Direction | Count | Notes |
|-----------|-------|-------|
| `routes/` → `src/` | **374** | Expected: HTTP handlers call domain logic |
| `routes/` → `core/` | **126** | Expected: handlers access DB models |
| `src/` → `routes/` | **31** | **Unexpected**: domain logic reaching into HTTP layer (direct grep of import lines referencing `routes/`) |
| `src/` → `core/` | **106** | Acceptable but could be reduced with a data-access layer |

> **How the metrics in this document are computed** — recompute against current `dev` before treating any count as authoritative (the tree drifts; these numbers are a snapshot, not a live value):
> - `src/` flat `.py` files: `find src -maxdepth 1 -name '*.py' | wc -l`
> - `tests/` test files: `find tests -name 'test_*.py' | wc -l`
> - `core.database` importers: `grep -rlE '(from|import) +core\.database' --include='*.py' . | grep -v core/database.py | wc -l`
> - `src.agent_loop` importers: `grep -rlE '(from|import) +src\.agent_loop' --include='*.py' . | grep -v src/agent_loop.py | wc -l`
> - Cross-layer import lines: `grep -rhE '(from|import) +<pkg>' --include='*.py' <dir>/ | wc -l` (e.g. `(from|import) +routes` over `src/`)

---

## 4. Route Ownership Map

Routes can be grouped into logical feature domains. Current flat structure obscures these boundaries:

| Domain | Route Files | Total Lines | Review Complexity |
|--------|-------------|-------------|-------------------|
| **Email** | `email_routes.py`, `email_helpers.py`, `email_pollers.py` | 5,936 | HIGH — most complex domain |
| **Chat / Agent** | `chat_routes.py`, `chat_helpers.py`, `shell_routes.py`, `codex_routes.py`, `skills_routes.py` | 6,365 | HIGH — core interaction surface |
| **Cookbook** | `cookbook_routes.py`, `cookbook_helpers.py`, `cookbook_output.py` | 4,110 | MEDIUM |
| **Model / LLM** | `model_routes.py`, `assistant_routes.py`, `copilot_routes.py` | 2,764 | MEDIUM |
| **Calendar / Contacts** | `calendar_routes.py`, `contacts_routes.py` | 2,336 | MEDIUM |
| **Documents** | `document_routes.py`, `document_helpers.py` | 1,954 | LOW |
| **Auth** | `auth_routes.py`, `api_token_routes.py`, `device_flow.py` | 1,171 | LOW |
| **Tasks** | `task_routes.py` (standalone) | 1,157 | LOW |
| **Session** | `session_routes.py` (standalone) | 1,287 | LOW |
| **Gallery** | `gallery_routes.py`, `gallery_helpers.py` | 1,896 | LOW |
| **Memory** | `memory_routes.py` | — | LOW |
| **Research** | `research_routes.py` | — | LOW |
| **MCP** | `mcp_routes.py` | — | LOW |
| **Notes** | `note_routes.py` | — | LOW |
| **Other** | `prefs_routes.py`, `upload_routes.py`, `vault_routes.py`, `webhook_routes.py`, `workspace_routes.py`, `search_routes.py`, `history_routes.py`, `hwfit_routes.py`, `preset_routes.py`, `signature_routes.py`, `backup_routes.py`, `cleanup_routes.py`, `diagnostics_routes.py`, `embedding_routes.py`, `emoji_routes.py`, `font_routes.py`, `stt_routes.py`, `tts_routes.py`, `compare_routes.py`, `personal_routes.py`, `editor_draft_routes.py`, `admin_wipe_routes.py`, `chatgpt_subscription_routes.py` | 2,000+ | LOW individual, HIGH cumulative |

---

## 5. Tool Registry & Implementation Boundaries

### 5.1 Current Tool Architecture

| Component | File | Lines | Role |
|-----------|------|-------|------|
| Tool schemas | `src/tool_schemas.py` | 1,392 | JSON Schema tool definitions (Duck-TypedDict) |
| Tool index | `src/tool_index.py` | 542 | RAG-based tool retrieval from ChromaDB |
| Tool implementations | `src/tool_implementations.py` | 4,032 | 33 `do_*` functions — all tool execution logic |
| Tool security | `src/tool_security.py` | — | Owner-scoped tool blocking |
| Tool policy | `src/tool_policy.py` | — | Guide-only directive, plan-mode disabled tools |
| Tool utils | `src/tool_utils.py` | — | Shared tool helpers |

### 5.2 Tool Implementation Categories

The 33 `do_*` functions in `tool_implementations.py` fall into natural domain groups — the basis for slice 1's split in §6.2:

| Category | `do_*` functions | Count |
|----------|------------------|-------|
| **System / config** | `do_manage_skills`, `do_manage_tasks`, `do_manage_endpoints`, `do_manage_mcp`, `do_manage_webhooks`, `do_manage_tokens`, `do_manage_settings`, `do_api_call`, `do_app_api` | 9 |
| **Cookbook / model serving** | `do_download_model`, `do_serve_model`, `do_list_served_models`, `do_stop_served_model`, `do_tail_serve_output`, `do_list_downloads`, `do_cancel_download`, `do_search_hf_models`, `do_adopt_served_model`, `do_list_cookbook_servers`, `do_list_serve_presets`, `do_serve_preset`, `do_list_cached_models` | 13 |
| **Notes** | `do_manage_notes` | 1 |
| **Calendar** | `do_manage_calendar` | 1 |
| **Search** | `do_search_chats` | 1 |
| **Research** | `do_manage_research`, `do_trigger_research` | 2 |
| **Contacts** | `do_resolve_contact`, `do_manage_contact` | 2 |
| **Vault** | `do_vault_search`, `do_vault_get`, `do_vault_unlock` | 3 |
| **Image** | `do_edit_image` | 1 |
| | **Total** | **33** |

> Low-level tools (filesystem, subprocess, web fetch, document parsing) live in `src/agent_tools/`, **not** in `tool_implementations.py` — out of scope for this split.

---

## 6. Risk Assessment & Candidate Slice Ranking

> **Candidate proposals, not a committed plan.** The rankings, package shapes (e.g. `src/pkg/`, `src/domain/`, `src/infra/`, `src/api/`), split ordering, and route-grouping strategy below are **options for maintainer discussion**. Per #4082/#4071, slice ownership and order are settled by maintainers before any follow-up PR. §1–§3 above are the factual current-state inventory.

### 6.1 Risk Scale

| Level | Criteria |
|-------|----------|
| **LOW** | File has ≤3 importers AND ≤500 lines, OR is a pure refactor with clear boundaries |
| **MEDIUM** | File has 4–15 importers OR 500–1,500 lines |
| **HIGH** | File has 16+ importers OR >2,000 lines, OR has cross-layer import violations |

### 6.2 Ranked Split Candidates

| Priority | Target | Risk | Rationale |
|----------|--------|------|-----------|
| **1** | `src/tool_implementations.py` → `src/tools/*.py` | **MEDIUM** | 4,032 lines → ~10 files by tool category. Already has natural boundaries. 17 importers, tracked in #3629. Use `__init__.py` shim to keep existing imports working. |
| **2** | `routes/` → domain subdirectories (one domain per PR) | **MEDIUM** | 54 flat files. Done **one domain at a time** (e.g. a standalone PR for the email domain, then chat, …), not a broad reorganization — route modules carry helper imports, registration assumptions, and test import paths. |
| **3** | `src/agent_loop.py` → `src/agent/loop.py` + submodules | **MEDIUM-HIGH** | 2,961 lines, 24 functions. Can extract prompt building, classification, verification, and runaway detection. Tracked in #3266. |
| **4** | `src/` → `src/pkg/`, `src/domain/`, `src/infra/`, `src/api/` | **MEDIUM** | Structural reorganization. Split flat `src/` into layered packages. Must come after routes and tools are stable. |
| **5** | `routes/email_*.py` consolidation | **LOW** | Already grouped by filename prefix. Low-risk cleanup within the email domain. |
| **6** | `core/database.py` → `src/infra/database/models/*.py` | **HIGH** | 28 classes, 102 importers. Highest-risk split. Must be **last** in any sequence. Requires careful import shim strategy. |
| **7** | Frontend CSS modularization | **MEDIUM** | 36,653 lines. Tracked in #2617. Separate timeline from backend work. |
| **8** | Frontend JS modularization | **MEDIUM** | 9,776 lines in `document.js`. Introduce ES modules at minimum. |

### 6.3 Candidate First 3 Behavior-Preserving Slices

**Slice 1: Split `tool_implementations.py`** (Lowest-risk high-impact)

- Create `src/tools/` package with one file per tool category
- Add `src/tools/__init__.py` re-exporting all symbols with current names
- Update 17 importers to use new paths (can be deferred via shim)
- Validation: `python -m pytest tests/ -x -q` + manual smoke test of tool execution
- Reference: #3629

**Slice 2: Group `routes/` by domain** (one domain per PR, not a broad sweep)

Route modules carry helper imports, router registration assumptions, and test import paths, so this must be done **one domain at a time** rather than as a single reorganization PR. Example sequence (each its own PR):

- PR 2a: move the **email** domain (`email_routes.py`, `email_helpers.py`, `email_pollers.py`) → `routes/email/` + shim
- PR 2b: move the **chat/agent** domain → `routes/chat/` + shim
- PR 2c: move the **cookbook** domain → `routes/cookbook/` + shim
- …and so on per domain from §4

Each PR: add `__init__.py` re-exporting old names, update `app.py` router imports, validation `python app.py` starts clean. **No behavior change** — pure file reorganization.

**Slice 3: Extract `agent_loop.py` submodules** (Improve reviewability)

- Move prompt assembly → `src/agent/prompt.py`
- Move request classification → `src/agent/classifier.py`
- Move sub-agent verification → `src/agent/verifier.py`
- Move runaway detection → `src/agent/runaway.py`
- Move context management → `src/agent/context.py`
- Keep `src/agent/loop.py` as the main orchestration module
- Validation: `python -m pytest tests/test_agent_loop.py tests/test_loop_breaker_runaway.py -v`

---

## 7. Safety Guardrails for Follow-Up Work

Per maintainer guidance in #4082 and #4071:

- [ ] **One domain/slice per PR** — never mix multiple reorganizations
- [ ] **No behavior changes** mixed with file moves — pure reorganization only
- [ ] **Keep compatibility shims** — `__init__.py` re-exports for all existing import paths
- [ ] **Add or identify focused tests** before risky splits
- [ ] **Do not start with `core/database.py`** or broad route movement unless this inventory shows a safe boundary
- [ ] **Prefer small, reviewable slices** over large restructures
- [ ] **No packaging/runtime/tooling migration** mixed into file moves
- [ ] **No frontend framework migration** inside this stabilization lane
- [ ] **Validate with `python -m compileall`** — every PR must pass CI checks
- [ ] **Validate with `pytest`** — run the full test suite before opening each PR

---

## 8. Validation Commands

Each follow-up PR should be verifiable with these commands before submission:

```bash
# Syntax check — must pass with zero errors
python -m compileall src/ routes/ core/ conf/

# Full test suite — must match baseline pass rate
python -m pytest tests/ -x -q

# Import shim verification — existing import paths must still work
python -c "from src.tool_implementations import do_search_chats; print('OK')"

# App startup smoke test (if backend touched)
timeout 5 python app.py 2>&1 | head -5 || true
```

---

## 9. Open Questions

1. Is `#2538` (specs ground truth) the canonical behavior map baseline, and should this inventory be kept in sync with those specs once merged?
2. Should route grouping follow the domain map proposed here, or is there a different taxonomy preferred by maintainers?
3. For the `tool_implementations.py` split (#3629), is the tool categorization in §5.2 acceptable, or should it follow a different grouping?
4. Should compatibility shims (`__init__.py`) be temporary (removed in a follow-up wave) or permanent?
5. Should an ADR (Architecture Decision Record) document be started to track decisions made during this process?

---

## 10. Future Direction (NOT current state)

The following are **future refactor targets** (candidate directions **pending maintainer agreement**, not committed), recorded here so this inventory does not imply they exist today. None of them are present in the current `dev` tree:

- `main.py` — proposed rename of the `app.py` entrypoint. Today the app boots via `app.py`.
- `src/agent/` — proposed package to hold `agent_loop.py` submodules (prompt/classifier/verifier/runaway/context). Today `agent_loop.py` is a single flat file in `src/`.
- `src/infra/`, `src/domain/`, `src/pkg/`, `src/api/` — proposed layered reorganization of the flat `src/` directory (slice 4 in §6).

These become real only when the corresponding slices land.

---

## Appendix A: File Listing

### `src/` (95 files — 61 shown; run `ls src/*.py` for the full list)

```
agent_loop.py          tool_implementations.py   tool_schemas.py
tool_index.py          tool_security.py          tool_policy.py
tool_utils.py          builtin_actions.py        task_scheduler.py
llm_core.py            model_context.py          model_discovery.py
session_search.py      context_budget.py         context_compactor.py
ai_interaction.py      action_intents.py         agent_runs.py
app_helpers.py         app_initializer.py        config.py
database.py            memory.py                 memory_provider.py
secret_storage.py      prompt_security.py        url_security.py
url_safety.py          rate_limiter.py           cleanup_service.py
readiness.py           service_health.py         exceptions.py
request_models.py      assistant_log.py          bg_monitor.py
builtin_mcp.py         chat_helpers.py           chroma_client.py
document_processor.py  embedding_lanes.py        deep_research.py
research_handler.py    research_utils.py         personal_docs.py
rag_manager.py         rag_singleton.py          topic_analyzer.py
visual_report.py       youtube_handler.py        pdf_forms.py
pdf_form_doc.py        pdf_runtime.py            caldav_writeback.py
email_thread_parser.py text_helpers.py           user_time.py
teacher_escalation.py  cookbook_serve_lifecycle.py
chatgpt_subscription.py  mcp_manager.py
```

### `routes/` (54 files)

```
__init__.py    _validators.py
auth_routes.py              api_token_routes.py       device_flow.py
chat_routes.py              chat_helpers.py           shell_routes.py
codex_routes.py             skills_routes.py
email_routes.py             email_helpers.py          email_pollers.py
cookbook_routes.py          cookbook_helpers.py       cookbook_output.py
model_routes.py             assistant_routes.py       copilot_routes.py
calendar_routes.py          contacts_routes.py
document_routes.py          document_helpers.py
gallery_routes.py           gallery_helpers.py
task_routes.py              session_routes.py
note_routes.py              memory_routes.py          research_routes.py
mcp_routes.py               search_routes.py          history_routes.py
webhook_routes.py           workspace_routes.py       upload_routes.py
vault_routes.py             prefs_routes.py           preset_routes.py
signature_routes.py         personal_routes.py        hwfit_routes.py
backup_routes.py            cleanup_routes.py         diagnostics_routes.py
embedding_routes.py         emoji_routes.py           font_routes.py
stt_routes.py               tts_routes.py             compare_routes.py
editor_draft_routes.py      chatgpt_subscription_routes.py    admin_wipe_routes.py
```

### `core/` (10 files)

```
__init__.py    constants.py    database.py    models.py
auth.py        middleware.py   session_manager.py   exceptions.py
atomic_io.py   platform_compat.py
```

---

## Appendix B: Key Import Relationships

```
core/database.py  ←── 102 importers (routes/*, src/*, core/*, tests/*)
    ↑
    ├── routes/auth_routes.py
    ├── routes/email_routes.py
    ├── src/builtin_actions.py
    ├── src/task_scheduler.py
    ├── src/tool_implementations.py (inline)
    └── ...97 more

src/tool_implementations.py  ←── 17 importers
    ↑
    ├── src/agent_loop.py
    ├── src/builtin_actions.py
    ├── src/tool_index.py
    ├── src/task_scheduler.py
    ├── src/tool_policy.py
    └── ...12 more (mostly tests)

src/agent_loop.py  ←── 22 importers
    ↑
    ├── src/tool_policy.py
    ├── src/teacher_escalation.py
    ├── src/bg_monitor.py
    ├── src/task_scheduler.py
    └── 18 more (incl. tests)
```
