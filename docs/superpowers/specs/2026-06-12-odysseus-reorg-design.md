# Design Spec: Odysseus Directory Reorganization

> **Date**: 2026-06-12
> **Status**: Draft
> **Scope**: Full reorganization (Phase 0–7), delivered as a single PR with sequential commits
> **Constraint**: Move files + update imports only. No logic/content changes.
> **Package manager**: uv (replacing pip + requirements.txt)

---

## 1. Context & Motivation

### Current Problems

| Dimension | Data | Issue |
|-----------|------|-------|
| `src/` flat `.py` files | **94** | No domain-based sub-packages |
| `routes/` flat files | **52** | No feature-area grouping |
| `core/` ↔ `src/` coupling | Circular imports | `core/__init__.py` imports from `src.llm_core`; `src/database.py` re-exports from `core.database` |
| `tool_implementations.py` | 4,031 lines, 33 tools | All tools in one file |
| `agent_loop.py` | 2,941 lines | Core loop + prompt building + verification |
| `core/database.py` | 96.6 KB, 86+ classes/functions | ORM models + migrations + helpers mixed |

A new contributor cannot infer module boundaries from the directory structure. "Where does email logic live?" requires grepping across 94 files.

### Goal

Reorganize into a DDD-style layered architecture (referencing [ytrader](https://github.com/user/ytrader)'s patterns), making the codebase navigable by directory structure alone.

### Constraints

- **No logic changes** — files are moved; only import statements are updated
- **Backward compatible** — re-export shims at old paths keep existing `from core.*`, `from src.*`, `from routes.*` imports working
- **Single PR** — all phases in one PR with sequential commits, easy to review holistically

---

## 2. Target Architecture

```
odysseus/
├── main.py                       # ← app.py (renamed entry point)
├── pyproject.toml                # ← full packaging with uv
│
├── conf/                         # 🆕 Configuration layer
│   ├── __init__.py               #   facade: re-exports config, settings
│   ├── config.py                 #   ← src/config.py
│   ├── settings.py               #   ← src/settings.py
│   └── settings_scrub.py         #   ← src/settings_scrub.py
│
├── src/
│   ├── api/                      # 🆕 API layer (routes + middleware + models)
│   │   ├── __init__.py
│   │   ├── router/               #   ← routes/*_routes.py (flat, 40+ files)
│   │   ├── handler/              #   ← routes/*_helpers.py, email_pollers.py, device_flow.py
│   │   ├── middleware/           #   ← core/middleware.py
│   │   ├── model/                #   ← src/request_models.py
│   │   └── validator.py          #   ← routes/_validators.py
│   │
│   ├── domain/                   # 🆕 Domain layer (business logic by feature)
│   │   ├── __init__.py
│   │   ├── agent/                #   Agent loop, runs, actions, tools
│   │   │   ├── __init__.py
│   │   │   ├── agent_loop.py     #     ← src/agent_loop.py (2941 lines)
│   │   │   ├── agent_runs.py     #     ← src/agent_runs.py
│   │   │   ├── action_intents.py #     ← src/action_intents.py
│   │   │   ├── builtin_actions.py#     ← src/builtin_actions.py (2259 lines)
│   │   │   ├── ai_interaction.py #     ← src/ai_interaction.py
│   │   │   ├── session_actions.py#     ← src/session_actions.py
│   │   │   ├── session_search.py #     ← src/session_search.py
│   │   │   ├── assistant_log.py  #     ← src/assistant_log.py
│   │   │   └── tools/            #     ← src/tool_*.py + src/agent_tools/*
│   │   │       ├── __init__.py   #       facade (was src/agent_tools/__init__.py)
│   │   │       ├── tool_implementations.py  # 4031 lines, 33 do_* functions
│   │   │       ├── tool_execution.py
│   │   │       ├── tool_parsing.py
│   │   │       ├── tool_schemas.py
│   │   │       ├── tool_policy.py
│   │   │       ├── tool_security.py
│   │   │       ├── tool_index.py
│   │   │       ├── tool_utils.py
│   │   │       ├── document_tools.py     # ← src/agent_tools/document_tools.py
│   │   │       ├── filesystem_tools.py   # ← src/agent_tools/filesystem_tools.py
│   │   │       ├── subprocess_tools.py   # ← src/agent_tools/subprocess_tools.py
│   │   │       └── web_tools.py          # ← src/agent_tools/web_tools.py
│   │   ├── chat/
│   │   │   ├── __init__.py
│   │   │   ├── chat_handler.py   #     ← src/chat_handler.py
│   │   │   ├── chat_helpers.py   #     ← src/chat_helpers.py
│   │   │   ├── chat_processor.py #     ← src/chat_processor.py
│   │   │   └── preset_manager.py #     ← src/preset_manager.py
│   │   ├── research/
│   │   │   ├── __init__.py
│   │   │   ├── deep_research.py  #     ← src/deep_research.py
│   │   │   ├── research_handler.py#    ← src/research_handler.py
│   │   │   ├── research_utils.py #     ← src/research_utils.py
│   │   │   └── visual_report.py  #     ← src/visual_report.py
│   │   ├── context/
│   │   │   ├── __init__.py
│   │   │   ├── context_budget.py #     ← src/context_budget.py
│   │   │   ├── context_compactor.py#   ← src/context_compactor.py
│   │   │   └── model_context.py  #     ← src/model_context.py
│   │   ├── document/
│   │   │   ├── __init__.py
│   │   │   ├── document_actions.py#    ← src/document_actions.py
│   │   │   ├── document_processor.py#  ← src/document_processor.py
│   │   │   ├── pdf_forms.py      #     ← src/pdf_forms.py
│   │   │   ├── pdf_form_doc.py   #     ← src/pdf_form_doc.py
│   │   │   ├── pdf_runtime.py    #     ← src/pdf_runtime.py
│   │   │   ├── markitdown_runtime.py#  ← src/markitdown_runtime.py
│   │   │   ├── youtube_handler.py#     ← src/youtube_handler.py
│   │   │   └── generated_images.py#    ← src/generated_images.py
│   │   ├── memory/
│   │   │   ├── __init__.py
│   │   │   ├── memory.py         #     ← src/memory.py
│   │   │   ├── memory_provider.py#     ← src/memory_provider.py
│   │   │   ├── memory_vector.py  #     ← src/memory_vector.py
│   │   │   └── chroma_client.py  #     ← src/chroma_client.py
│   │   ├── rag/
│   │   │   ├── __init__.py
│   │   │   ├── rag_manager.py    #     ← src/rag_manager.py
│   │   │   ├── rag_singleton.py  #     ← src/rag_singleton.py
│   │   │   ├── rag_vector.py     #     ← src/rag_vector.py
│   │   │   └── personal_docs.py  #     ← src/personal_docs.py
│   │   ├── embedding/
│   │   │   ├── __init__.py
│   │   │   ├── embeddings.py     #     ← src/embeddings.py
│   │   │   └── embedding_lanes.py#     ← src/embedding_lanes.py
│   │   ├── email/
│   │   │   ├── __init__.py
│   │   │   └── email_thread_parser.py# ← src/email_thread_parser.py
│   │   └── calendar/
│   │       ├── __init__.py
│   │       ├── caldav_sync.py    #     ← src/caldav_sync.py
│   │       └── caldav_writeback.py#    ← src/caldav_writeback.py
│   │
│   ├── infra/                    # 🆕 Infrastructure layer
│   │   ├── __init__.py
│   │   ├── database/
│   │   │   ├── __init__.py
│   │   │   ├── database.py       #     ← core/database.py (96.6K)
│   │   │   ├── models.py         #     ← core/models.py
│   │   │   └── session_manager.py#     ← core/session_manager.py
│   │   ├── auth/
│   │   │   ├── __init__.py
│   │   │   ├── auth.py           #     ← core/auth.py (24.9K)
│   │   │   ├── auth_helpers.py   #     ← src/auth_helpers.py
│   │   │   └── api_key_manager.py#     ← src/api_key_manager.py
│   │   ├── llm/
│   │   │   ├── __init__.py
│   │   │   ├── llm_core.py       #     ← src/llm_core.py (2130 lines)
│   │   │   ├── endpoint_resolver.py#   ← src/endpoint_resolver.py
│   │   │   └── model_discovery.py#     ← src/model_discovery.py
│   │   ├── mcp/
│   │   │   ├── __init__.py
│   │   │   ├── mcp_manager.py    #     ← src/mcp_manager.py
│   │   │   ├── mcp_oauth.py      #     ← src/mcp_oauth.py
│   │   │   └── builtin_mcp.py    #     ← src/builtin_mcp.py
│   │   ├── scheduler/
│   │   │   ├── __init__.py
│   │   │   ├── bg_jobs.py        #     ← src/bg_jobs.py
│   │   │   ├── bg_monitor.py     #     ← src/bg_monitor.py
│   │   │   ├── task_scheduler.py #     ← src/task_scheduler.py
│   │   │   ├── task_endpoint.py  #     ← src/task_endpoint.py
│   │   │   ├── event_bus.py      #     ← src/event_bus.py
│   │   │   ├── cleanup_service.py#     ← src/cleanup_service.py
│   │   │   ├── service_health.py #     ← src/service_health.py
│   │   │   ├── readiness.py      #     ← src/readiness.py
│   │   │   ├── webhook_manager.py#     ← src/webhook_manager.py
│   │   │   └── cookbook_serve_lifecycle.py# ← src/cookbook_serve_lifecycle.py
│   │   ├── integration/
│   │   │   ├── __init__.py
│   │   │   ├── integrations.py   #     ← src/integrations.py
│   │   │   ├── copilot.py        #     ← src/copilot.py
│   │   │   └── chatgpt_subscription.py# ← src/chatgpt_subscription.py
│   │   ├── storage/
│   │   │   ├── __init__.py
│   │   │   ├── secret_storage.py #     ← src/secret_storage.py
│   │   │   ├── upload_handler.py #     ← src/upload_handler.py
│   │   │   ├── upload_limits.py  #     ← src/upload_limits.py
│   │   │   └── atomic_io.py      #     ← core/atomic_io.py
│   │   └── search/
│   │       └── __init__.py        #     ← src/search/ (entire sub-package)
│   │
│   ├── pkg/                      # 🆕 Shared utilities (zero external deps)
│   │   ├── __init__.py
│   │   ├── constants.py          #     ← src/constants.py (44+ importers, most critical)
│   │   ├── exceptions.py         #     ← src/exceptions.py (core/exceptions.py stays as shim; merge deferred)
│   │   ├── platform_compat.py    #     ← core/platform_compat.py
│   │   ├── tls_overrides.py      #     ← src/tls_overrides.py
│   │   ├── io.py                 #     ← src/app_helpers.py
│   │   ├── time.py               #     ← src/user_time.py
│   │   ├── security/
│   │   │   ├── __init__.py
│   │   │   ├── prompt_security.py#     ← src/prompt_security.py
│   │   │   ├── url_safety.py     #     ← src/url_safety.py
│   │   │   ├── url_security.py   #     ← src/url_security.py
│   │   │   └── rate_limiter.py   #     ← src/rate_limiter.py
│   │   └── text/
│   │       ├── __init__.py
│   │       ├── text_helpers.py   #     ← src/text_helpers.py
│   │       ├── topic_analyzer.py #     ← src/topic_analyzer.py
│   │       └── goal_based_extractor.py# ← src/goal_based_extractor.py
│   │
│   └── app_initializer.py        # Composition root (stays at src/ level)
│
├── core/                         # 🔄 Becomes backward-compat shim layer
│   ├── __init__.py               #   Updated to re-export from src/infra/*, src/pkg/*
│   ├── constants.py              #   Shim → src.pkg.constants
│   ├── exceptions.py             #   Shim → src.pkg.exceptions
│   ├── database.py               #   Shim → src.infra.database.database
│   ├── models.py                 #   Shim → src.infra.database.models
│   ├── session_manager.py        #   Shim → src.infra.database.session_manager
│   ├── auth.py                   #   Shim → src.infra.auth.auth
│   ├── middleware.py             #   Shim → src.api.middleware.security_headers
│   ├── atomic_io.py              #   Shim → src.infra.storage.atomic_io
│   └── platform_compat.py        #   Shim → src.pkg.platform_compat
│
├── routes/                       # 🔄 Becomes backward-compat shim layer
│   ├── __init__.py               #   Empty (unchanged)
│   └── *.py                      #   Each file becomes shim → src.api.router.* or src.api.handler.*
│
├── services/                     # ✅ Unchanged (already well-structured)
├── companion/                    # ✅ Unchanged (standalone module)
├── mcp_servers/                  # ✅ Unchanged (standalone module)
├── static/                       # ✅ Unchanged (frontend assets)
├── tests/                        # ✅ Unchanged (old import paths work via shims)
├── scripts/                      # ✅ Unchanged
├── docker/                       # ✅ Unchanged
├── docs/                         # ✅ Unchanged
└── integrations/                 # ✅ Unchanged
```

<!-- 中文备注：不变模块的理由
- services/ 已按功能域划分子包（search/, memory/, docs/, research/, hwfit/, shell/ 等）
- companion/ 是独立设备配对系统（3 文件）
- mcp_servers/ 是 4 个独立 MCP 服务
- static/ 是纯前端资源
- tests/ 有 538 个测试文件，通过 shim 保持旧 import 路径可用
-->

---

## 3. Import Path Mapping

### Critical path changes (most-imported modules)

| Old import | New canonical import | Approx. references |
|------------|---------------------|-------------------|
| `from src.config import config` | `from conf.config import config` | ~15 |
| `from src.settings import get_setting` | `from conf.settings import get_setting` | ~20 |
| `from core.database import SessionLocal` | `from src.infra.database.database import SessionLocal` | 36+ |
| `from core.auth import AuthManager` | `from src.infra.auth.auth import AuthManager` | ~10 |
| `from core.constants import BASE_DIR` | `from src.pkg.constants import BASE_DIR` | 8 |
| `from core.exceptions import SessionNotFoundError` | `from src.pkg.exceptions import SessionNotFoundError` | ~5 |
| `from core.middleware import SecurityHeadersMiddleware` | `from src.api.middleware.security_headers import SecurityHeadersMiddleware` | 2 |
| `from src.llm_core import stream_llm` | `from src.infra.llm.llm_core import stream_llm` | ~10 |
| `from src.agent_loop import stream_agent_loop` | `from src.domain.agent.agent_loop import stream_agent_loop` | 6 |
| `from src.tool_implementations import do_*` | `from src.domain.agent.tools.tool_implementations import do_*` | ~5 |
| `from src.chat_handler import ChatHandler` | `from src.domain.chat.chat_handler import ChatHandler` | ~5 |
| `from routes.chat_routes import setup_chat_routes` | `from src.api.router.chat_routes import setup_chat_routes` | in app.py only |
| `from src.constants import DATA_DIR` | `from src.pkg.constants import DATA_DIR` | 44+ |
| `from src.prompt_security import untrusted_context_message` | `from src.pkg.security.prompt_security import untrusted_context_message` | ~5 |
| `from services.search import *` | *(unchanged)* | — |

### Unchanged imports

All `from services.*`, `from companion.*`, `from mcp_servers.*` imports remain exactly as-is.

---

## 4. Backward Compatibility Strategy

Every moved file leaves a **2-line re-export shim** at its original location:

```python
# src/constants.py — shim, canonical: src.pkg.constants
from src.pkg.constants import *  # noqa: F401,F403
```

### Shim chain examples

```
# Direct shim (most common)
src/llm_core.py  →  from src.infra.llm.llm_core import *

# Double shim (temporary, cleaned up in Phase 7)
core/constants.py  →  from src.constants import *      # existing
src/constants.py   →  from src.pkg.constants import *   # new

# core/__init__.py — updated to re-export from new locations
from src.infra.llm.llm_core import llm_call, stream_llm, LLMConfig
from src.infra.auth.auth import AuthManager
from src.infra.database.models import Session, ChatMessage
from src.pkg.constants import *
from src.pkg.exceptions import SessionNotFoundError, InvalidFileUploadError
from src.api.middleware.security_headers import SecurityHeadersMiddleware
```

### When shims are removed

Shims can be safely removed after a full import migration pass (Phase 7 cleanup) where all `from core.*`, `from src.*`, `from routes.*` references in production code and tests are updated to new paths.

---

## 5. Execution Phases (8 commits in 1 PR)

### Phase 0: `requirements.txt` → `pyproject.toml`

**No file moves. Pure addition.**

New `pyproject.toml`:

```toml
[project]
name = "odysseus"
version = "1.0.0"
description = "Self-hosted AI workspace with memory, research, and multi-modal capabilities"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    "fastapi",
    "uvicorn",
    "python-multipart",
    "python-dotenv",
    "httpx",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "SQLAlchemy",
    "pypdf",
    "beautifulsoup4",
    "charset-normalizer",
    "numpy",
    "chromadb-client",
    "fastembed",
    "youtube-transcript-api",
    "markdown",
    "nh3",
    "icalendar",
    "python-dateutil",
    "caldav",
    "cryptography",
    "bcrypt",
    "mcp",
    "pyotp",
    "qrcode[pil]",
    "croniter",
]

[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio"]

[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["."]
include = ["src*", "core*", "routes*", "services*", "conf*", "companion*", "mcp_servers*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
    "area_security: tests covering auth, owner-scope, SSRF, XSS, confinement, redaction",
    "area_routes: tests covering HTTP route / API behavior",
    "area_services: tests covering service-layer behavior",
    "area_cli: tests covering CLI / script behavior",
    "area_js: JavaScript / Node-backed tests",
    "area_helpers: self-tests for the shared test helpers in tests/helpers/",
    "area_unit: pure parser / utility tests",
    "area_uncategorized: tests not yet matched by the taxonomy",
    "slow: opt-in marker for known-slow tests",
]
```

**Dockerfile update**:

```dockerfile
# Before:
# RUN pip install --no-cache-dir -r requirements.txt

# After:
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml .
RUN uv pip install --system .
```

Keep `requirements.txt` temporarily (marked `# DEPRECATED: use pyproject.toml + uv`).

**Risk**: LOW

---

### Phase 1: Create `conf/` — Configuration layer

**3 files moved.**

| Old | New |
|-----|-----|
| `src/config.py` | `conf/config.py` |
| `src/settings.py` | `conf/settings.py` |
| `src/settings_scrub.py` | `conf/settings_scrub.py` |

Create `conf/__init__.py`:
```python
from .config import config, IS_WINDOWS, AppConfig, create_directories, validate_config
from .settings import get_setting, save_settings, load_settings, is_setting_overridden, get_user_setting
```

Shims at old locations:
```python
# src/config.py
from conf.config import *  # noqa: F401,F403

# src/settings.py
from conf.settings import *  # noqa: F401,F403

# src/settings_scrub.py
from conf.settings_scrub import *  # noqa: F401,F403
```

**Risk**: LOW. `conf/config.py` depends on `src.constants` which does not move in this phase.

---

### Phase 2: Create `src/api/` — Route layer restructure

**52 files moved (highest volume).**

**Route files** → `src/api/router/` (flat, all `*_routes.py`):
- `routes/admin_wipe_routes.py` → `src/api/router/admin_wipe_routes.py`
- `routes/chat_routes.py` → `src/api/router/chat_routes.py`
- `routes/session_routes.py` → `src/api/router/session_routes.py`
- ... (40+ more route files)

**Helper files** → `src/api/handler/`:
- `routes/chat_helpers.py` → `src/api/handler/chat_helpers.py`
- `routes/email_helpers.py` → `src/api/handler/email_helpers.py`
- `routes/cookbook_helpers.py` → `src/api/handler/cookbook_helpers.py`
- `routes/document_helpers.py` → `src/api/handler/document_helpers.py`
- `routes/gallery_helpers.py` → `src/api/handler/gallery_helpers.py`
- `routes/email_pollers.py` → `src/api/handler/email_pollers.py`
- `routes/device_flow.py` → `src/api/handler/device_flow.py`

**Other**:
- `routes/_validators.py` → `src/api/validator.py`
- `core/middleware.py` → `src/api/middleware/security_headers.py`
- `src/request_models.py` → `src/api/model/request_models.py`

**Key file: `app.py`** — 35+ import lines change:
```python
# Before:
from routes.chat_routes import setup_chat_routes, SESSION_COOKIE
# After:
from src.api.router.chat_routes import setup_chat_routes, SESSION_COOKIE
```

Create 5 new `__init__.py` files: `src/api/`, `src/api/router/`, `src/api/handler/`, `src/api/middleware/`, `src/api/model/`.

Leave shims in `routes/` for each moved file.

**Risk**: MEDIUM-HIGH. Large file count but each move is mechanical. `app.py` is the highest-impact file.

---

### Phase 3: Create `src/infra/` — Infrastructure layer

**23 files moved (core/ → 5, src/ → 18).**

**From `core/`**:
| Old | New |
|-----|-----|
| `core/database.py` | `src/infra/database/database.py` |
| `core/models.py` | `src/infra/database/models.py` |
| `core/session_manager.py` | `src/infra/database/session_manager.py` |
| `core/auth.py` | `src/infra/auth/auth.py` |
| `core/atomic_io.py` | `src/infra/storage/atomic_io.py` |

**From `src/`**:
| Old | New |
|-----|-----|
| `src/llm_core.py` | `src/infra/llm/llm_core.py` |
| `src/endpoint_resolver.py` | `src/infra/llm/endpoint_resolver.py` |
| `src/model_discovery.py` | `src/infra/llm/model_discovery.py` |
| `src/mcp_manager.py` | `src/infra/mcp/mcp_manager.py` |
| `src/mcp_oauth.py` | `src/infra/mcp/mcp_oauth.py` |
| `src/builtin_mcp.py` | `src/infra/mcp/builtin_mcp.py` |
| `src/bg_jobs.py` | `src/infra/scheduler/bg_jobs.py` |
| `src/bg_monitor.py` | `src/infra/scheduler/bg_monitor.py` |
| `src/task_scheduler.py` | `src/infra/scheduler/task_scheduler.py` |
| `src/task_endpoint.py` | `src/infra/scheduler/task_endpoint.py` |
| `src/event_bus.py` | `src/infra/scheduler/event_bus.py` |
| `src/integrations.py` | `src/infra/integration/integrations.py` |
| `src/copilot.py` | `src/infra/integration/copilot.py` |
| `src/chatgpt_subscription.py` | `src/infra/integration/chatgpt_subscription.py` |
| `src/secret_storage.py` | `src/infra/storage/secret_storage.py` |
| `src/upload_handler.py` | `src/infra/storage/upload_handler.py` |
| `src/upload_limits.py` | `src/infra/storage/upload_limits.py` |
| `src/auth_helpers.py` | `src/infra/auth/auth_helpers.py` |

**Critical: `core/__init__.py`** must be rewritten to re-export from new locations while preserving its public API:
```python
# core/__init__.py — backward-compat facade
from src.infra.llm.llm_core import llm_call, llm_call_async, stream_llm, list_model_ids, normalize_model_id, LLMConfig  # noqa: E501
from src.infra.auth.auth import AuthManager  # noqa: F401
from src.pkg.constants import *  # noqa: F401,F403
from src.api.middleware.security_headers import SecurityHeadersMiddleware  # noqa: F401
from src.pkg.exceptions import SessionNotFoundError, InvalidFileUploadError  # noqa: F401
from src.infra.database.models import Session, ChatMessage  # noqa: F401
from src.infra.database.session_manager import SessionManager  # noqa: F401
```

Create 8 new `__init__.py` files: `src/infra/`, `src/infra/database/`, `src/infra/auth/`, `src/infra/llm/`, `src/infra/mcp/`, `src/infra/scheduler/`, `src/infra/integration/`, `src/infra/storage/`.

Also move `src/search/` → `src/infra/search/` (entire sub-package).

**Risk**: HIGH. `core.database.SessionLocal` has 36+ importers. `core/__init__.py` has complex import chains that must be preserved exactly.

---

### Phase 4: Create `src/domain/` — Business logic by feature

**45 files moved (largest phase).**

See Section 2 target architecture for full file mapping. Summary:

| Domain sub-package | Files | Source |
|-------------------|-------|--------|
| `src/domain/agent/` | 8 files | `src/agent_loop.py`, `agent_runs.py`, `action_intents.py`, `builtin_actions.py`, `ai_interaction.py`, `session_actions.py`, `session_search.py`, `assistant_log.py` |
| `src/domain/agent/tools/` | 13 files | `src/tool_*.py` (8 files) + `src/agent_tools/*` (4 files) + `__init__.py` facade |
| `src/domain/chat/` | 4 files | `chat_handler.py`, `chat_helpers.py`, `chat_processor.py`, `preset_manager.py` |
| `src/domain/research/` | 4 files | `deep_research.py`, `research_handler.py`, `research_utils.py`, `visual_report.py` |
| `src/domain/context/` | 3 files | `context_budget.py`, `context_compactor.py`, `model_context.py` |
| `src/domain/document/` | 9 files | `document_actions.py`, `document_processor.py`, `pdf_forms.py`, `pdf_form_doc.py`, `pdf_runtime.py`, `markitdown_runtime.py`, `youtube_handler.py`, `generated_images.py` |
| `src/domain/memory/` | 4 files | `memory.py`, `memory_provider.py`, `memory_vector.py`, `chroma_client.py` |
| `src/domain/rag/` | 4 files | `rag_manager.py`, `rag_singleton.py`, `rag_vector.py`, `personal_docs.py` |
| `src/domain/embedding/` | 2 files | `embeddings.py`, `embedding_lanes.py` |
| `src/domain/email/` | 1 file | `email_thread_parser.py` |
| `src/domain/calendar/` | 2 files | `caldav_sync.py`, `caldav_writeback.py` |

Create 11 new `__init__.py` files (one per sub-package).

**Key: `src/agent_tools/__init__.py`** facade must be rewritten:
```python
# src/agent_tools/__init__.py — shim pointing to new tools location
from src.domain.agent.tools import *  # noqa: F401,F403
```

**Risk**: MEDIUM-HIGH. `src/domain/agent/tools/__init__.py` (the new facade) must replicate the exact re-export chain from the old `src/agent_tools/__init__.py`.

---

### Phase 5: Create `src/pkg/` — Shared utilities

**12 files moved.**

| Old | New |
|-----|-----|
| `src/constants.py` | `src/pkg/constants.py` |
| `src/exceptions.py` | `src/pkg/exceptions.py` |
| `src/tls_overrides.py` | `src/pkg/tls_overrides.py` |
| `src/prompt_security.py` | `src/pkg/security/prompt_security.py` |
| `src/url_safety.py` | `src/pkg/security/url_safety.py` |
| `src/url_security.py` | `src/pkg/security/url_security.py` |
| `src/rate_limiter.py` | `src/pkg/security/rate_limiter.py` |
| `src/text_helpers.py` | `src/pkg/text/text_helpers.py` |
| `src/topic_analyzer.py` | `src/pkg/text/topic_analyzer.py` |
| `src/goal_based_extractor.py` | `src/pkg/text/goal_based_extractor.py` |
| `src/user_time.py` | `src/pkg/time.py` |
| `src/app_helpers.py` | `src/pkg/io.py` |
| `core/platform_compat.py` | `src/pkg/platform_compat.py` |

**Critical: `src/constants.py`** — the most-imported module (44+ references). Its shim must be bulletproof:
```python
# src/constants.py — shim, canonical: src.pkg.constants
from src.pkg.constants import *  # noqa: F401,F403
from src.pkg.constants import internal_api_base  # noqa: F401  # explicit for named imports
```

**Double shim chain** (temporary):
```
core/constants.py  →  from src.constants import *      # existing, unchanged
src/constants.py   →  from src.pkg.constants import *   # new
```

Create 3 new `__init__.py` files: `src/pkg/`, `src/pkg/security/`, `src/pkg/text/`.

**Risk**: MEDIUM. `src/constants.py` has 44+ importers; shim correctness is critical.

---

### Phase 6: Remaining standalone files

**5 files moved.**

| Old | New |
|-----|-----|
| `src/cleanup_service.py` | `src/infra/scheduler/cleanup_service.py` |
| `src/service_health.py` | `src/infra/scheduler/service_health.py` |
| `src/readiness.py` | `src/infra/scheduler/readiness.py` |
| `src/webhook_manager.py` | `src/infra/scheduler/webhook_manager.py` |
| `src/api_key_manager.py` | `src/infra/auth/api_key_manager.py` |

**Risk**: LOW. Each file is standalone with few cross-dependencies.

---

### Phase 7: Rename entry point + cleanup

1. **Rename**: `app.py` → `main.py`
2. **Update Dockerfile**: `CMD ["uvicorn", "main:app", ...]` (was `app:app`)
3. **Update `pyproject.toml`**: `[tool.setuptools.packages.find]` to reflect final structure
4. **Optional**: Remove all backward-compatibility shim files from `src/`, `core/`, `routes/`
5. **Remove `requirements.txt`**: if Docker and all tooling use `pyproject.toml`

**Risk**: MEDIUM. `app:app` → `main:app` is a user-visible breaking change for anyone running `uvicorn app:app` directly. Must be clearly documented in the PR description.

---

## 6. New `__init__.py` Files (Complete List)

30 new `__init__.py` files required:

| # | Location | Purpose |
|---|----------|---------|
| 1 | `conf/__init__.py` | Facade: config, settings |
| 2 | `src/api/__init__.py` | Package marker |
| 3 | `src/api/router/__init__.py` | Package marker |
| 4 | `src/api/handler/__init__.py` | Package marker |
| 5 | `src/api/middleware/__init__.py` | Package marker |
| 6 | `src/api/model/__init__.py` | Package marker |
| 7 | `src/infra/__init__.py` | Package marker |
| 8 | `src/infra/database/__init__.py` | Re-export: database, models, session_manager |
| 9 | `src/infra/auth/__init__.py` | Re-export: auth, auth_helpers |
| 10 | `src/infra/llm/__init__.py` | Re-export: llm_core, endpoint_resolver |
| 11 | `src/infra/mcp/__init__.py` | Re-export: mcp_manager |
| 12 | `src/infra/scheduler/__init__.py` | Re-export: bg_jobs, task_scheduler |
| 13 | `src/infra/integration/__init__.py` | Package marker |
| 14 | `src/infra/storage/__init__.py` | Package marker |
| 15 | `src/infra/search/__init__.py` | Moved from `src/search/` sub-package (preserves existing re-exports) |
| 16 | `src/domain/__init__.py` | Package marker |
| 17 | `src/domain/agent/__init__.py` | Re-export: agent_loop, agent_runs |
| 18 | `src/domain/agent/tools/__init__.py` | Facade (was src/agent_tools/__init__.py) |
| 19 | `src/domain/chat/__init__.py` | Re-export: chat_handler |
| 20 | `src/domain/research/__init__.py` | Re-export: deep_research |
| 21 | `src/domain/context/__init__.py` | Package marker |
| 22 | `src/domain/document/__init__.py` | Package marker |
| 23 | `src/domain/memory/__init__.py` | Re-export: memory, chroma_client |
| 24 | `src/domain/rag/__init__.py` | Re-export: rag_manager |
| 25 | `src/domain/embedding/__init__.py` | Re-export: embeddings |
| 26 | `src/domain/email/__init__.py` | Re-export: email_thread_parser |
| 27 | `src/domain/calendar/__init__.py` | Re-export: caldav_sync |
| 28 | `src/pkg/__init__.py` | Package marker |
| 29 | `src/pkg/security/__init__.py` | Package marker |
| 30 | `src/pkg/text/__init__.py` | Package marker |

---

## 7. Risk Assessment Summary

| Phase | Files Moved | Risk | Highest-risk module |
|-------|------------|------|-------------------|
| 0 | 0 (add only) | LOW | — |
| 1 | 3 | LOW | `src/config.py` (depends on `src.constants`) |
| 2 | 52 | MEDIUM-HIGH | `app.py` (35+ import lines) |
| 3 | 23 | **HIGH** | `core/__init__.py`, `core/database.py` (36+ SessionLocal importers) |
| 4 | 45 | MEDIUM-HIGH | `src/agent_tools/__init__.py` facade chain |
| 5 | 12 | MEDIUM | `src/constants.py` (44+ importers) |
| 6 | 5 | LOW | — |
| 7 | 0 (rename + cleanup) | MEDIUM | `app.py` → `main.py` (user-visible breaking change) |

---

## 8. Verification Plan

After each phase:

1. **`uv sync`** — dependencies install successfully
2. **`uv run pytest tests/ -x --timeout=60`** — core tests pass
3. **`uv run python -c "from core.database import SessionLocal; print('OK')"`** — shim works
4. **`uv run uvicorn app:app --host 0.0.0.0 --port 8000`** (briefly) — server starts without import errors

After all phases:

1. **Full test suite**: `uv run pytest tests/`
2. **Import audit**: `grep -r "from core\.\|from routes\.\|from src\." src/ routes/ core/` — verify all point to shims or new locations
3. **Docker build**: `docker compose build` succeeds
4. **Runtime smoke test**: `curl http://localhost:8000/api/v1/health` returns 200

---

## 9. Files That Stay Unchanged

These directories/files are **not moved** and require **no changes**:

- `services/` — already well-structured by feature domain
- `companion/` — standalone device pairing module
- `mcp_servers/` — 4 standalone MCP servers
- `static/` — frontend assets (JS, CSS, HTML)
- `tests/` — 538 test files (import paths remain valid via shims)
- `scripts/` — CLI scripts
- `docker/` — Docker configuration files
- `docs/` — documentation
- `integrations/` — third-party integration configs
- `.github/` — CI/CD workflows
- `setup.py` — first-time setup script (not a packaging script)

---

## Appendix A: Dependency Direction Rules

Following ytrader's DDD conventions, the target architecture enforces these dependency rules:

| Layer | May import from | Must NOT import from |
|-------|----------------|---------------------|
| `conf/` | `src/pkg/` | `src/api/`, `src/infra/`, `src/domain/` |
| `src/api/` | `src/domain/`, `src/infra/`, `conf/`, `src/pkg/` | — |
| `src/domain/` | `src/pkg/` | `src/api/`, `src/infra/`, `conf/` |
| `src/infra/` | `src/domain/` (interfaces only), `conf/`, `src/pkg/` | `src/api/` |
| `src/pkg/` | nothing (zero external deps) | everything else |

> **Note**: This spec only performs the directory reorganization. Enforcing these rules (e.g., via linting or import checks) is a follow-up task.

---

## Appendix B: Current `core/__init__.py` (reference)

```python
from src.llm_core import (
    llm_call, llm_call_async, stream_llm,
    list_model_ids, normalize_model_id, LLMConfig,
)
from .auth import AuthManager
from .constants import *
from .middleware import SecurityHeadersMiddleware
from .exceptions import (
    SessionNotFoundError,
    InvalidFileUploadError,
)
from .models import Session, ChatMessage
from .session_manager import SessionManager
```

This public API must be preserved exactly after reorganization.
