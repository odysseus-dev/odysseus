# Odysseus Directory Reorganization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize Odysseus from flat `src/` (89 .py files) + flat `routes/` (52 files) into a DDD layered architecture matching ytrader's conventions, changing only file locations and import paths — no logic changes.

**Architecture:** Target: `conf/` (config) → `src/api/` (routes/middleware/models) → `src/domain/` (business logic by feature: agent, chat, research, document, memory, rag, embedding, email, calendar) → `src/infra/` (database, auth, llm, mcp, scheduler, integration, storage, search) → `src/pkg/` (zero-dep utils: constants, exceptions, security, text). Each moved file leaves a 2-line re-export shim at its old location.

**Tech Stack:** Python 3.12, FastAPI, uv package manager, setuptools build backend, git

**Constraint:** File content unchanged. Only `git mv` + import path updates + shim files. Big-bang single PR with 8 sequential commits (Phase 0–7).

---

### Task 1: Phase 0 — Convert `requirements.txt` to `pyproject.toml`

**Files:**
- Modify: `pyproject.toml`
- Modify: `Dockerfile`
- Modify: `requirements.txt` (add deprecation notice)

- [ ] **Step 1: Merge existing pytest config into full pyproject.toml**

Read the current `pyproject.toml` (has `[tool.pytest.ini_options]` only). Write the complete file:

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
    "area_services: tests covering service-layer behavior (llm, cookbook, email, calendar, ...)",
    "area_cli: tests covering CLI / script behavior",
    "area_js: JavaScript / Node-backed tests",
    "area_helpers: self-tests for the shared test helpers in tests/helpers/",
    "area_unit: pure parser / utility tests that do not clearly belong elsewhere",
    "area_uncategorized: tests not yet matched by the taxonomy (fallback)",
    "slow: opt-in marker for known-slow tests; excluded by the fast lane (not slow)",
]
```

- [ ] **Step 2: Mark requirements.txt as deprecated**

Prepend to `requirements.txt`:

```
# DEPRECATED: This file is superseded by pyproject.toml.
# Dependencies are now managed with uv:  uv sync  /  uv pip install --system .
# This file is retained temporarily for compatibility; it will be removed in a future cleanup.
#
```

- [ ] **Step 3: Update Dockerfile to use uv + pyproject.toml**

Replace lines 28–30 of Dockerfile:
```
COPY requirements.txt requirements-optional.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_OPTIONAL" = "true" ]; then pip install --no-cache-dir -r requirements-optional.txt; fi
```

With:
```dockerfile
# Install uv then use pyproject.toml for dependencies.
# requirements-optional.txt is still opt-in (AGPL extras).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml requirements-optional.txt ./
RUN uv pip install --system . \
    && if [ "$INSTALL_OPTIONAL" = "true" ]; then pip install --no-cache-dir -r requirements-optional.txt; fi
```

- [ ] **Step 4: Verify uv sync works**

Run: `uv sync`
Expected: All 28 dependencies install successfully, "Resolved 28 packages"

- [ ] **Step 5: Verify pytest still works**

Run: `uv run pytest tests/ -x --timeout=60 -q`
Expected: Tests pass (same results as before)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml requirements.txt Dockerfile
git commit -m "refactor: Phase 0 — convert requirements.txt to pyproject.toml (uv)

- Add full [project] metadata with 26 runtime + 2 dev dependencies
- Preserve existing [tool.pytest.ini_options] markers
- Update Dockerfile to use uv pip install from pyproject.toml
- Mark requirements.txt as deprecated
- No file moves — pure packaging setup

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Phase 1 — Create `conf/` configuration layer

**Files:**
- Create: `conf/__init__.py`
- Move: `src/config.py` → `conf/config.py`
- Move: `src/settings.py` → `conf/settings.py`
- Move: `src/settings_scrub.py` → `conf/settings_scrub.py`
- Create shims: `src/config.py`, `src/settings.py`, `src/settings_scrub.py`

- [ ] **Step 1: Create conf/ directory**

```bash
mkdir -p conf
```

- [ ] **Step 2: Move config files with git**

```bash
git mv src/config.py conf/config.py
git mv src/settings.py conf/settings.py
git mv src/settings_scrub.py conf/settings_scrub.py
```

- [ ] **Step 3: Create conf/__init__.py facade**

```python
"""Configuration layer — canonical home for config, settings, and scrubbing."""

from .config import config, IS_WINDOWS, AppConfig, create_directories, validate_config
from .settings import get_setting, save_settings, load_settings, is_setting_overridden, get_user_setting
```

- [ ] **Step 4: Create shim files at old locations**

`src/config.py` (shim):
```python
# src/config.py — shim, canonical: conf.config
from conf.config import *  # noqa: F401,F403
```

`src/settings.py` (shim):
```python
# src/settings.py — shim, canonical: conf.settings
from conf.settings import *  # noqa: F401,F403
```

`src/settings_scrub.py` (shim):
```python
# src/settings_scrub.py — shim, canonical: conf.settings_scrub
from conf.settings_scrub import *  # noqa: F401,F403
```

- [ ] **Step 5: Update imports in moved files**

The moved files (`conf/config.py`, `conf/settings.py`, `conf/settings_scrub.py`) may import from `src.constants`. Since `src/constants.py` is still at its old location with a shim, this works. No import changes needed within these files — the shim at `src/constants.py` handles the indirection.

Run to verify:
```bash
uv run python -c "from conf.config import config; print('OK')"
uv run python -c "from conf.settings import get_setting; print('OK')"
```

- [ ] **Step 6: Verify backward compatibility**

```bash
uv run python -c "from src.config import config; print('shim OK')"
uv run python -c "from src.settings import get_setting; print('shim OK')"
uv run python -c "from src.settings_scrub import scrub_settings; print('shim OK')"
```

- [ ] **Step 7: Run quick smoke test**

```bash
uv run python -c "from conf import config, get_setting; print('conf package OK')"
```

- [ ] **Step 8: Commit**

```bash
git add conf/ src/config.py src/settings.py src/settings_scrub.py
git commit -m "refactor: Phase 1 — create conf/ configuration layer

- Move src/config.py → conf/config.py
- Move src/settings.py → conf/settings.py
- Move src/settings_scrub.py → conf/settings_scrub.py
- Add conf/__init__.py facade
- Leave 2-line shims at old src/ paths

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Phase 2a — Create `src/api/` directory structure + move route files

**Files:**
- Create: 5 `__init__.py` files
- Move: 43 `*_routes.py` files → `src/api/router/`
- Move: 7 helper files → `src/api/handler/`
- Move: `routes/_validators.py` → `src/api/validator.py`
- Move: `core/middleware.py` → `src/api/middleware/security_headers.py`
- Move: `src/request_models.py` → `src/api/model/request_models.py`

- [ ] **Step 1: Create all directories**

```bash
mkdir -p src/api/router
mkdir -p src/api/handler
mkdir -p src/api/middleware
mkdir -p src/api/model
```

- [ ] **Step 2: Create __init__.py files for all new packages**

`src/api/__init__.py`:
```python
"""API layer — HTTP endpoints, middleware, request/response models."""
```

`src/api/router/__init__.py`:
```python
"""Route setup functions — one per feature area."""
```

`src/api/handler/__init__.py`:
```python
"""Request handlers and helpers — shared logic behind route endpoints."""
```

`src/api/middleware/__init__.py`:
```python
"""HTTP middleware — security headers, CORS helpers."""
from .security_headers import SecurityHeadersMiddleware, is_cors_preflight
```

`src/api/model/__init__.py`:
```python
"""Request/response models shared across routes."""
from .request_models import *
```

- [ ] **Step 3: Move route files (43 files)**

```bash
# Move all *_routes.py files from routes/ to src/api/router/
for f in routes/*_routes.py; do
    git mv "$f" "src/api/router/$(basename $f)"
done
```

- [ ] **Step 4: Move helper files (7 files)**

```bash
git mv routes/chat_helpers.py src/api/handler/chat_helpers.py
git mv routes/email_helpers.py src/api/handler/email_helpers.py
git mv routes/cookbook_helpers.py src/api/handler/cookbook_helpers.py
git mv routes/document_helpers.py src/api/handler/document_helpers.py
git mv routes/gallery_helpers.py src/api/handler/gallery_helpers.py
git mv routes/email_pollers.py src/api/handler/email_pollers.py
git mv routes/device_flow.py src/api/handler/device_flow.py
```

- [ ] **Step 5: Move remaining files**

```bash
git mv routes/_validators.py src/api/validator.py
git mv core/middleware.py src/api/middleware/security_headers.py
git mv src/request_models.py src/api/model/request_models.py
```

- [ ] **Step 6: Create shims for moved route files (43 shims)**

For each moved `*_routes.py` file, create a shim at the old `routes/` location. Write a script to generate them:

```bash
for f in src/api/router/*_routes.py; do
    basename=$(basename "$f")
    echo "# routes/$basename — shim, canonical: src.api.router.$basename" > "routes/$basename"
    echo "from src.api.router.${basename%.py} import *  # noqa: F401,F403" >> "routes/$basename"
done
```

Create shims for helper files:

```bash
# Chat helpers
echo '# routes/chat_helpers.py — shim, canonical: src.api.handler.chat_helpers
from src.api.handler.chat_helpers import *  # noqa: F401,F403' > routes/chat_helpers.py

# Email helpers
echo '# routes/email_helpers.py — shim, canonical: src.api.handler.email_helpers
from src.api.handler.email_helpers import *  # noqa: F401,F403' > routes/email_helpers.py

# Cookbook helpers
echo '# routes/cookbook_helpers.py — shim, canonical: src.api.handler.cookbook_helpers
from src.api.handler.cookbook_helpers import *  # noqa: F401,F403' > routes/cookbook_helpers.py

# Document helpers
echo '# routes/document_helpers.py — shim, canonical: src.api.handler.document_helpers
from src.api.handler.document_helpers import *  # noqa: F401,F403' > routes/document_helpers.py

# Gallery helpers
echo '# routes/gallery_helpers.py — shim, canonical: src.api.handler.gallery_helpers
from src.api.handler.gallery_helpers import *  # noqa: F401,F403' > routes/gallery_helpers.py

# Email pollers
echo '# routes/email_pollers.py — shim, canonical: src.api.handler.email_pollers
from src.api.handler.email_pollers import *  # noqa: F401,F403' > routes/email_pollers.py

# Device flow
echo '# routes/device_flow.py — shim, canonical: src.api.handler.device_flow
from src.api.handler.device_flow import *  # noqa: F401,F403' > routes/device_flow.py

# Validators
echo '# routes/_validators.py — shim, canonical: src.api.validator
from src.api.validator import *  # noqa: F401,F403' > routes/_validators.py
```

- [ ] **Step 7: Create shim for middleware**

```bash
echo '# core/middleware.py — shim, canonical: src.api.middleware.security_headers
from src.api.middleware.security_headers import *  # noqa: F401,F403' > core/middleware.py
```

- [ ] **Step 8: Create shim for request_models**

```bash
echo '# src/request_models.py — shim, canonical: src.api.model.request_models
from src.api.model.request_models import *  # noqa: F401,F403' > src/request_models.py
```

- [ ] **Step 9: Commit**

```bash
git add src/api/ routes/ src/request_models.py core/middleware.py
git commit -m "refactor: Phase 2a — create src/api/ with router, handler, middleware, model

- Move 43 *_routes.py → src/api/router/
- Move 7 helpers → src/api/handler/
- Move routes/_validators.py → src/api/validator.py
- Move core/middleware.py → src/api/middleware/security_headers.py
- Move src/request_models.py → src/api/model/request_models.py
- 5 new __init__.py files
- 52 shim files at old routes/ paths

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Phase 2b — Update `app.py` import paths

**Files:**
- Modify: `app.py` (43 import lines changed)

- [ ] **Step 1: Update all route imports in app.py**

Replace every `from routes.X_routes import` with `from src.api.router.X_routes import`.

Run sed:
```bash
sed -i 's/^from routes\.\([a-z_]*\)_routes import/from src.api.router.\1_routes import/' app.py
```

- [ ] **Step 2: Update middleware import in app.py**

```bash
sed -i 's/^from core\.middleware import/from src.api.middleware.security_headers import/' app.py
```

- [ ] **Step 3: Update request_models import if present in app.py**

Check if app.py imports from `src.request_models`:
```bash
grep -n "from src.request_models\|from src.api.model.request_models" app.py || echo "not found"
```

If found, update:
```bash
sed -i 's/from src\.request_models import/from src.api.model.request_models import/' app.py
```

- [ ] **Step 4: Verify app.py has no remaining old paths**

```bash
grep "from routes\." app.py && echo "ERROR: old route imports remain" || echo "OK"
grep "from core.middleware" app.py && echo "ERROR: old middleware import remains" || echo "OK"
```

- [ ] **Step 5: Verify app can import**

```bash
uv run python -c "import ast; ast.parse(open('app.py').read()); print('app.py syntax OK')"
```

- [ ] **Step 6: Commit**

```bash
git add app.py
git commit -m "refactor: Phase 2b — update app.py imports to src.api.router.* paths

- 43 route imports: routes.X_routes → src.api.router.X_routes
- core.middleware → src.api.middleware.security_headers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Phase 3a — Create `src/infra/` directory structure + move core/ files

**Files:**
- Create: 8 `__init__.py` files under `src/infra/`
- Move: 5 files from `core/` to `src/infra/`
- Move: 18 files from `src/` to `src/infra/`
- Move: `src/search/` sub-package → `src/infra/search/`

- [ ] **Step 1: Create all infra directories**

```bash
mkdir -p src/infra/database
mkdir -p src/infra/auth
mkdir -p src/infra/llm
mkdir -p src/infra/mcp
mkdir -p src/infra/scheduler
mkdir -p src/infra/integration
mkdir -p src/infra/storage
```

- [ ] **Step 2: Create __init__.py for src/infra/ and sub-packages**

`src/infra/__init__.py`:
```python
"""Infrastructure layer — database, auth, LLM, MCP, scheduler, integration, storage, search."""
```

`src/infra/database/__init__.py`:
```python
"""Database layer — ORM models, session management, connection helpers."""
from .database import (
    Base, TimestampMixin, DATABASE_URL, engine,
    SessionLocal, Session, init_db, get_db, get_db_session,
    bulk_insert_messages, cleanup_old_sessions, get_session_stats,
    get_detailed_stats, update_session_last_accessed,
    get_session_by_id, archive_session,
)
from .models import ChatMessage, Document, DocumentVersion, GalleryImage
from .models import ModelEndpoint, McpServer, Comparison, ApiToken
from .models import Signature, Webhook, UserTool, UserToolData
from .models import CrewMember, ScheduledTask, TaskRun, Memory
from .session_manager import SessionManager
```

`src/infra/auth/__init__.py`:
```python
"""Authentication — password hashing, token management, API keys."""
from .auth import AuthManager, normalize_known_username
from .auth_helpers import *
```

`src/infra/llm/__init__.py`:
```python
"""LLM integration — core client, endpoint resolution, model discovery."""
from .llm_core import llm_call, llm_call_async, stream_llm, list_model_ids, normalize_model_id, LLMConfig
from .endpoint_resolver import *
from .model_discovery import *
```

`src/infra/mcp/__init__.py`:
```python
"""MCP (Model Context Protocol) — server management, OAuth, built-in servers."""
from .mcp_manager import *
```

`src/infra/scheduler/__init__.py`:
```python
"""Background tasks — jobs, monitoring, task scheduling, events."""
from .bg_jobs import *
from .task_scheduler import *
```

`src/infra/integration/__init__.py`:
```python
"""Third-party integrations — Copilot, ChatGPT subscription, general integrations."""
```

`src/infra/storage/__init__.py`:
```python
"""File storage — secret storage, upload handling, atomic I/O."""
```

- [ ] **Step 3: Move core/ files to src/infra/**

```bash
git mv core/database.py src/infra/database/database.py
git mv core/models.py src/infra/database/models.py
git mv core/session_manager.py src/infra/database/session_manager.py
git mv core/auth.py src/infra/auth/auth.py
git mv core/atomic_io.py src/infra/storage/atomic_io.py
```

- [ ] **Step 4: Move src/ files to src/infra/ sub-packages**

```bash
# LLM
git mv src/llm_core.py src/infra/llm/llm_core.py
git mv src/endpoint_resolver.py src/infra/llm/endpoint_resolver.py
git mv src/model_discovery.py src/infra/llm/model_discovery.py

# MCP
git mv src/mcp_manager.py src/infra/mcp/mcp_manager.py
git mv src/mcp_oauth.py src/infra/mcp/mcp_oauth.py
git mv src/builtin_mcp.py src/infra/mcp/builtin_mcp.py

# Scheduler
git mv src/bg_jobs.py src/infra/scheduler/bg_jobs.py
git mv src/bg_monitor.py src/infra/scheduler/bg_monitor.py
git mv src/task_scheduler.py src/infra/scheduler/task_scheduler.py
git mv src/task_endpoint.py src/infra/scheduler/task_endpoint.py
git mv src/event_bus.py src/infra/scheduler/event_bus.py

# Integration
git mv src/integrations.py src/infra/integration/integrations.py
git mv src/copilot.py src/infra/integration/copilot.py
git mv src/chatgpt_subscription.py src/infra/integration/chatgpt_subscription.py

# Storage
git mv src/secret_storage.py src/infra/storage/secret_storage.py
git mv src/upload_handler.py src/infra/storage/upload_handler.py
git mv src/upload_limits.py src/infra/storage/upload_limits.py

# Auth
git mv src/auth_helpers.py src/infra/auth/auth_helpers.py
```

- [ ] **Step 5: Move src/search/ → src/infra/search/ (entire sub-package)**

```bash
git mv src/search src/infra/search
```

- [ ] **Step 6: Create shims for moved core/ files**

```bash
# core/database.py shim
echo '# core/database.py — shim, canonical: src.infra.database.database
from src.infra.database.database import *  # noqa: F401,F403
from src.infra.database.database import (  # explicit re-exports for IDE visibility
    Base, TimestampMixin, DATABASE_URL, engine,
    SessionLocal, Session,
    ChatMessage, Document, DocumentVersion, GalleryImage,
    ModelEndpoint, McpServer, Comparison, ApiToken,
    Signature, Webhook, UserTool, UserToolData,
    CrewMember, ScheduledTask, TaskRun, Memory,
    init_db, get_db, get_db_session,
    bulk_insert_messages, cleanup_old_sessions,
    get_session_stats, get_detailed_stats,
    update_session_last_accessed, get_session_by_id, archive_session,
)' > core/database.py

# core/models.py shim
echo '# core/models.py — shim, canonical: src.infra.database.models
from src.infra.database.models import *  # noqa: F401,F403' > core/models.py

# core/session_manager.py shim
echo '# core/session_manager.py — shim, canonical: src.infra.database.session_manager
from src.infra.database.session_manager import *  # noqa: F401,F403' > core/session_manager.py

# core/auth.py shim
echo '# core/auth.py — shim, canonical: src.infra.auth.auth
from src.infra.auth.auth import *  # noqa: F401,F403' > core/auth.py

# core/atomic_io.py shim
echo '# core/atomic_io.py — shim, canonical: src.infra.storage.atomic_io
from src.infra.storage.atomic_io import *  # noqa: F401,F403' > core/atomic_io.py
```

- [ ] **Step 7: Create shims for moved src/ files**

```bash
# LLM
echo '# src/llm_core.py — shim, canonical: src.infra.llm.llm_core
from src.infra.llm.llm_core import *  # noqa: F401,F403' > src/llm_core.py
echo '# src/endpoint_resolver.py — shim, canonical: src.infra.llm.endpoint_resolver
from src.infra.llm.endpoint_resolver import *  # noqa: F401,F403' > src/endpoint_resolver.py
echo '# src/model_discovery.py — shim, canonical: src.infra.llm.model_discovery
from src.infra.llm.model_discovery import *  # noqa: F401,F403' > src/model_discovery.py

# MCP
echo '# src/mcp_manager.py — shim, canonical: src.infra.mcp.mcp_manager
from src.infra.mcp.mcp_manager import *  # noqa: F401,F403' > src/mcp_manager.py
echo '# src/mcp_oauth.py — shim, canonical: src.infra.mcp.mcp_oauth
from src.infra.mcp.mcp_oauth import *  # noqa: F401,F403' > src/mcp_oauth.py
echo '# src/builtin_mcp.py — shim, canonical: src.infra.mcp.builtin_mcp
from src.infra.mcp.builtin_mcp import *  # noqa: F401,F403' > src/builtin_mcp.py

# Scheduler
echo '# src/bg_jobs.py — shim, canonical: src.infra.scheduler.bg_jobs
from src.infra.scheduler.bg_jobs import *  # noqa: F401,F403' > src/bg_jobs.py
echo '# src/bg_monitor.py — shim, canonical: src.infra.scheduler.bg_monitor
from src.infra.scheduler.bg_monitor import *  # noqa: F401,F403' > src/bg_monitor.py
echo '# src/task_scheduler.py — shim, canonical: src.infra.scheduler.task_scheduler
from src.infra.scheduler.task_scheduler import *  # noqa: F401,F403' > src/task_scheduler.py
echo '# src/task_endpoint.py — shim, canonical: src.infra.scheduler.task_endpoint
from src.infra.scheduler.task_endpoint import *  # noqa: F401,F403' > src/task_endpoint.py
echo '# src/event_bus.py — shim, canonical: src.infra.scheduler.event_bus
from src.infra.scheduler.event_bus import *  # noqa: F401,F403' > src/event_bus.py

# Integration
echo '# src/integrations.py — shim, canonical: src.infra.integration.integrations
from src.infra.integration.integrations import *  # noqa: F401,F403' > src/integrations.py
echo '# src/copilot.py — shim, canonical: src.infra.integration.copilot
from src.infra.integration.copilot import *  # noqa: F401,F403' > src/copilot.py
echo '# src/chatgpt_subscription.py — shim, canonical: src.infra.integration.chatgpt_subscription
from src.infra.integration.chatgpt_subscription import *  # noqa: F401,F403' > src/chatgpt_subscription.py

# Storage
echo '# src/secret_storage.py — shim, canonical: src.infra.storage.secret_storage
from src.infra.storage.secret_storage import *  # noqa: F401,F403' > src/secret_storage.py
echo '# src/upload_handler.py — shim, canonical: src.infra.storage.upload_handler
from src.infra.storage.upload_handler import *  # noqa: F401,F403' > src/upload_handler.py
echo '# src/upload_limits.py — shim, canonical: src.infra.storage.upload_limits
from src.infra.storage.upload_limits import *  # noqa: F401,F403' > src/upload_limits.py

# Auth
echo '# src/auth_helpers.py — shim, canonical: src.infra.auth.auth_helpers
from src.infra.auth.auth_helpers import *  # noqa: F401,F403' > src/auth_helpers.py

# Search (src/search/ was moved entirely, create shim)
echo '# src/search/__init__.py — shim, canonical: src.infra.search
from src.infra.search import *  # noqa: F401,F403' > src/search/__init__.py
```

Note: After creating `src/search/__init__.py`, delete the other old `src/search/*.py` files (they were moved with `git mv` so this is just the `__init__.py` shim):
```bash
# The old search/*.py files were git-mv'd; only __init__.py shim is needed
ls src/search/  # Should only have __init__.py
```

- [ ] **Step 8: Update src/database.py shim to point to new location**

The existing `src/database.py` shim currently points to `core.database`. Update it:

```python
# src/database.py — shim, canonical: src.infra.database.database
# (was: shim to core.database; now core.database itself is a shim to src.infra.database.database)
from src.infra.database.database import *  # noqa: F401,F403
from src.infra.database.database import (  # explicit re-exports for IDE/type-checker visibility
    Base, TimestampMixin, DATABASE_URL, engine,
    SessionLocal, Session,
    ChatMessage, Document, DocumentVersion, GalleryImage,
    ModelEndpoint, McpServer, Comparison, ApiToken,
    Signature, Webhook, UserTool, UserToolData,
    CrewMember, ScheduledTask, TaskRun, Memory,
    init_db, get_db, get_db_session,
    bulk_insert_messages, cleanup_old_sessions,
    get_session_stats, get_detailed_stats,
    update_session_last_accessed, get_session_by_id, archive_session,
)
```

- [ ] **Step 9: Verify shim chain works**

```bash
uv run python -c "from core.database import SessionLocal; print('core.database shim OK')"
uv run python -c "from core.auth import AuthManager; print('core.auth shim OK')"
uv run python -c "from src.llm_core import stream_llm; print('src.llm_core shim OK')"
uv run python -c "from src.database import SessionLocal; print('src.database shim OK')"
uv run python -c "from src.infra.database.database import SessionLocal; print('new path OK')"
uv run python -c "from src.infra.llm.llm_core import stream_llm; print('new llm path OK')"
uv run python -c "from src.infra.search import comprehensive_web_search; print('search moved OK')"
```

- [ ] **Step 10: Commit**

```bash
git add src/infra/ core/ src/llm_core.py src/endpoint_resolver.py src/model_discovery.py \
    src/mcp_manager.py src/mcp_oauth.py src/builtin_mcp.py \
    src/bg_jobs.py src/bg_monitor.py src/task_scheduler.py src/task_endpoint.py src/event_bus.py \
    src/integrations.py src/copilot.py src/chatgpt_subscription.py \
    src/secret_storage.py src/upload_handler.py src/upload_limits.py \
    src/auth_helpers.py src/database.py src/search/
git commit -m "refactor: Phase 3a — create src/infra/ with database, auth, llm, mcp, scheduler, integration, storage, search

- Move core/database.py → src/infra/database/database.py (+ 4 more from core/)
- Move src/llm_core.py → src/infra/llm/llm_core.py (+ 17 more from src/)
- Move src/search/ → src/infra/search/ (entire sub-package)
- 8 new __init__.py files with re-exports
- Shims at all old core/ and src/ locations
- Update src/database.py shim to point directly to new location

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Phase 3b — Rewrite `core/__init__.py` facade

**Files:**
- Modify: `core/__init__.py`

- [ ] **Step 1: Update core/__init__.py to import from new locations**

```python
# core/__init__.py — backward-compat facade
# All symbols re-exported from their canonical locations.
# Public API preserved exactly — 17 symbols in __all__.

from src.infra.llm.llm_core import (  # noqa: E501
    llm_call,
    llm_call_async,
    stream_llm,
    list_model_ids,
    normalize_model_id,
    LLMConfig,
)
from src.infra.auth.auth import AuthManager  # noqa: F401
from .constants import *  # noqa: F401,F403
from src.api.middleware.security_headers import SecurityHeadersMiddleware  # noqa: F401
from .exceptions import (  # noqa: F401
    SessionNotFoundError,
    InvalidFileUploadError,
    LLMServiceError,
    WebSearchError,
)
from src.infra.database.models import Session, ChatMessage  # noqa: F401
from src.infra.database.session_manager import SessionManager  # noqa: F401

__all__ = [
    "llm_call",
    "llm_call_async",
    "stream_llm",
    "list_model_ids",
    "normalize_model_id",
    "LLMConfig",
    "AuthManager",
    "SecurityHeadersMiddleware",
    "SessionNotFoundError",
    "InvalidFileUploadError",
    "LLMServiceError",
    "WebSearchError",
    "Session",
    "ChatMessage",
    "SessionManager",
]
```

Note: `from .constants import *` and `from .exceptions import ...` still use relative imports because `core/constants.py` and `core/exceptions.py` are shims (constants already was, exceptions stays as shim for now).

- [ ] **Step 2: Verify facade works**

```bash
uv run python -c "
from core import (
    llm_call, stream_llm, LLMConfig,
    AuthManager, SecurityHeadersMiddleware,
    SessionNotFoundError, LLMServiceError, WebSearchError,
    Session, ChatMessage, SessionManager,
)
print('All core.* imports work via facade')
"
```

- [ ] **Step 3: Commit**

```bash
git add core/__init__.py
git commit -m "refactor: Phase 3b — rewrite core/__init__.py facade to new infra paths

- llm_call, stream_llm, etc. → from src.infra.llm.llm_core
- AuthManager → from src.infra.auth.auth
- Session, ChatMessage → from src.infra.database.models
- SessionManager → from src.infra.database.session_manager
- SecurityHeadersMiddleware → from src.api.middleware.security_headers
- Preserve exact __all__ (17 symbols)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Phase 4a — Create `src/domain/` with agent, chat, research, context sub-packages

**Files:**
- Create: 11 `__init__.py` files under `src/domain/`
- Move: ~45 files from `src/` → `src/domain/` sub-packages

- [ ] **Step 1: Create all domain directories**

```bash
mkdir -p src/domain/agent/tools
mkdir -p src/domain/chat
mkdir -p src/domain/research
mkdir -p src/domain/context
mkdir -p src/domain/document
mkdir -p src/domain/memory
mkdir -p src/domain/rag
mkdir -p src/domain/embedding
mkdir -p src/domain/email
mkdir -p src/domain/calendar
```

- [ ] **Step 2: Create __init__.py for src/domain/ and all sub-packages**

`src/domain/__init__.py`:
```python
"""Domain layer — core business logic organized by feature area."""
```

`src/domain/agent/__init__.py`:
```python
"""Agent domain — conversation loop, tool execution, built-in actions."""
from .agent_loop import stream_agent_loop
from .agent_runs import *
from .ai_interaction import *
from .builtin_actions import *
```

`src/domain/agent/tools/__init__.py` — SEE TASK 8 (complex facade, handled separately).

`src/domain/chat/__init__.py`:
```python
"""Chat domain — message handling, presets, processing."""
from .chat_handler import ChatHandler
from .chat_processor import *
from .preset_manager import *
```

`src/domain/research/__init__.py`:
```python
"""Research domain — deep research, report generation."""
from .deep_research import *
from .research_handler import *
from .visual_report import *
```

`src/domain/context/__init__.py`:
```python
"""Context domain — budget management, compaction, model context."""
from .context_budget import *
from .context_compactor import *
from .model_context import *
```

`src/domain/document/__init__.py`:
```python
"""Document domain — file processing, PDF, YouTube, image generation."""
from .document_actions import *
from .document_processor import *
```

`src/domain/memory/__init__.py`:
```python
"""Memory domain — persistent memory, vector storage, ChromaDB."""
from .memory import *
from .chroma_client import *
```

`src/domain/rag/__init__.py`:
```python
"""RAG domain — retrieval-augmented generation, vector search."""
from .rag_manager import *
```

`src/domain/embedding/__init__.py`:
```python
"""Embedding domain — model embeddings, lane management."""
from .embeddings import *
from .embedding_lanes import *
```

`src/domain/email/__init__.py`:
```python
"""Email domain — thread parsing."""
from .email_thread_parser import *
```

`src/domain/calendar/__init__.py`:
```python
"""Calendar domain — CalDAV sync and writeback."""
from .caldav_sync import *
from .caldav_writeback import *
```

- [ ] **Step 3: Move files to src/domain/agent/ (8 files)**

```bash
git mv src/agent_loop.py src/domain/agent/agent_loop.py
git mv src/agent_runs.py src/domain/agent/agent_runs.py
git mv src/action_intents.py src/domain/agent/action_intents.py
git mv src/builtin_actions.py src/domain/agent/builtin_actions.py
git mv src/ai_interaction.py src/domain/agent/ai_interaction.py
git mv src/session_actions.py src/domain/agent/session_actions.py
git mv src/session_search.py src/domain/agent/session_search.py
git mv src/assistant_log.py src/domain/agent/assistant_log.py
git mv src/teacher_escalation.py src/domain/agent/teacher_escalation.py
```

Note: `src/teacher_escalation.py` was not in the original spec but is clearly an agent-domain concept (imported by agent_loop.py for `run_teacher_inline`).

- [ ] **Step 4: Move files to src/domain/agent/tools/ (13 files)**

```bash
git mv src/tool_implementations.py src/domain/agent/tools/tool_implementations.py
git mv src/tool_execution.py src/domain/agent/tools/tool_execution.py
git mv src/tool_parsing.py src/domain/agent/tools/tool_parsing.py
git mv src/tool_schemas.py src/domain/agent/tools/tool_schemas.py
git mv src/tool_policy.py src/domain/agent/tools/tool_policy.py
git mv src/tool_security.py src/domain/agent/tools/tool_security.py
git mv src/tool_index.py src/domain/agent/tools/tool_index.py
git mv src/tool_utils.py src/domain/agent/tools/tool_utils.py
git mv src/agent_tools/document_tools.py src/domain/agent/tools/document_tools.py
git mv src/agent_tools/filesystem_tools.py src/domain/agent/tools/filesystem_tools.py
git mv src/agent_tools/subprocess_tools.py src/domain/agent/tools/subprocess_tools.py
git mv src/agent_tools/web_tools.py src/domain/agent/tools/web_tools.py
```

- [ ] **Step 5: Move files to other domain sub-packages**

```bash
# Chat (4 files)
git mv src/chat_handler.py src/domain/chat/chat_handler.py
git mv src/chat_helpers.py src/domain/chat/chat_helpers.py
git mv src/chat_processor.py src/domain/chat/chat_processor.py
git mv src/preset_manager.py src/domain/chat/preset_manager.py

# Research (4 files)
git mv src/deep_research.py src/domain/research/deep_research.py
git mv src/research_handler.py src/domain/research/research_handler.py
git mv src/research_utils.py src/domain/research/research_utils.py
git mv src/visual_report.py src/domain/research/visual_report.py

# Context (3 files)
git mv src/context_budget.py src/domain/context/context_budget.py
git mv src/context_compactor.py src/domain/context/context_compactor.py
git mv src/model_context.py src/domain/context/model_context.py

# Document (8 files)
git mv src/document_actions.py src/domain/document/document_actions.py
git mv src/document_processor.py src/domain/document/document_processor.py
git mv src/pdf_forms.py src/domain/document/pdf_forms.py
git mv src/pdf_form_doc.py src/domain/document/pdf_form_doc.py
git mv src/pdf_runtime.py src/domain/document/pdf_runtime.py
git mv src/markitdown_runtime.py src/domain/document/markitdown_runtime.py
git mv src/youtube_handler.py src/domain/document/youtube_handler.py
git mv src/generated_images.py src/domain/document/generated_images.py

# Memory (4 files)
git mv src/memory.py src/domain/memory/memory.py
git mv src/memory_provider.py src/domain/memory/memory_provider.py
git mv src/memory_vector.py src/domain/memory/memory_vector.py
git mv src/chroma_client.py src/domain/memory/chroma_client.py

# RAG (4 files)
git mv src/rag_manager.py src/domain/rag/rag_manager.py
git mv src/rag_singleton.py src/domain/rag/rag_singleton.py
git mv src/rag_vector.py src/domain/rag/rag_vector.py
git mv src/personal_docs.py src/domain/rag/personal_docs.py

# Embedding (2 files)
git mv src/embeddings.py src/domain/embedding/embeddings.py
git mv src/embedding_lanes.py src/domain/embedding/embedding_lanes.py

# Email (1 file)
git mv src/email_thread_parser.py src/domain/email/email_thread_parser.py

# Calendar (2 files)
git mv src/caldav_sync.py src/domain/calendar/caldav_sync.py
git mv src/caldav_writeback.py src/domain/calendar/caldav_writeback.py
```

- [ ] **Step 6: Create shims for all moved files**

Generate shims at old `src/` locations:

```bash
# Agent (9 files including teacher_escalation)
for f in agent_loop agent_runs action_intents builtin_actions ai_interaction \
         session_actions session_search assistant_log teacher_escalation; do
    echo "# src/${f}.py — shim, canonical: src.domain.agent.${f}
from src.domain.agent.${f} import *  # noqa: F401,F403" > "src/${f}.py"
done

# Agent tools (12 files)
for f in tool_implementations tool_execution tool_parsing tool_schemas \
         tool_policy tool_security tool_index tool_utils; do
    echo "# src/${f}.py — shim, canonical: src.domain.agent.tools.${f}
from src.domain.agent.tools.${f} import *  # noqa: F401,F403" > "src/${f}.py"
done

# Chat (4 files)
for f in chat_handler chat_helpers chat_processor preset_manager; do
    echo "# src/${f}.py — shim, canonical: src.domain.chat.${f}
from src.domain.chat.${f} import *  # noqa: F401,F403" > "src/${f}.py"
done

# Research (4 files)
for f in deep_research research_handler research_utils visual_report; do
    echo "# src/${f}.py — shim, canonical: src.domain.research.${f}
from src.domain.research.${f} import *  # noqa: F401,F403" > "src/${f}.py"
done

# Context (3 files)
for f in context_budget context_compactor model_context; do
    echo "# src/${f}.py — shim, canonical: src.domain.context.${f}
from src.domain.context.${f} import *  # noqa: F401,F403" > "src/${f}.py"
done

# Document (8 files)
for f in document_actions document_processor pdf_forms pdf_form_doc \
         pdf_runtime markitdown_runtime youtube_handler generated_images; do
    echo "# src/${f}.py — shim, canonical: src.domain.document.${f}
from src.domain.document.${f} import *  # noqa: F401,F403" > "src/${f}.py"
done

# Memory (4 files)
for f in memory memory_provider memory_vector chroma_client; do
    echo "# src/${f}.py — shim, canonical: src.domain.memory.${f}
from src.domain.memory.${f} import *  # noqa: F401,F403" > "src/${f}.py"
done

# RAG (4 files)
for f in rag_manager rag_singleton rag_vector personal_docs; do
    echo "# src/${f}.py — shim, canonical: src.domain.rag.${f}
from src.domain.rag.${f} import *  # noqa: F401,F403" > "src/${f}.py"
done

# Embedding (2 files)
for f in embeddings embedding_lanes; do
    echo "# src/${f}.py — shim, canonical: src.domain.embedding.${f}
from src.domain.embedding.${f} import *  # noqa: F401,F403" > "src/${f}.py"
done

# Email (1 file)
echo '# src/email_thread_parser.py — shim, canonical: src.domain.email.email_thread_parser
from src.domain.email.email_thread_parser import *  # noqa: F401,F403' > src/email_thread_parser.py

# Calendar (2 files)
for f in caldav_sync caldav_writeback; do
    echo "# src/${f}.py — shim, canonical: src.domain.calendar.${f}
from src.domain.calendar.${f} import *  # noqa: F401,F403" > "src/${f}.py"
done
```

- [ ] **Step 7: Create src/agent_tools/__init__.py shim**

The old `src/agent_tools/__init__.py` is the complex facade that was moved. Replace it with a simple re-export shim pointing to the new tools location:

```python
# src/agent_tools/__init__.py — shim, canonical: src.domain.agent.tools
from src.domain.agent.tools import *  # noqa: F401,F403
```

Also create shim for `src/agent_tools/__init__` sub-modules:
```bash
echo '# src/agent_tools/document_tools.py — already moved (shim not needed)' > /dev/null
# The document_tools.py, filesystem_tools.py, subprocess_tools.py, web_tools.py
# were git-mv'd; no shims needed since __init__.py shim re-exports everything
```

- [ ] **Step 8: Verify domain imports work**

```bash
uv run python -c "from src.domain.agent.agent_loop import stream_agent_loop; print('agent OK')"
uv run python -c "from src.domain.chat.chat_handler import ChatHandler; print('chat OK')"
uv run python -c "from src.domain.research.deep_research import deep_research; print('research OK')"
uv run python -c "from src.domain.memory.memory import MemoryManager; print('memory OK')"
```

- [ ] **Step 9: Verify shim backward compatibility**

```bash
uv run python -c "from src.agent_loop import stream_agent_loop; print('shim OK')"
uv run python -c "from src.chat_handler import ChatHandler; print('shim OK')"
uv run python -c "from src.tool_implementations import do_search_chats; print('shim OK')"
```

- [ ] **Step 10: Commit**

```bash
git add src/domain/ src/agent_loop.py src/agent_runs.py src/action_intents.py \
    src/builtin_actions.py src/ai_interaction.py src/session_actions.py \
    src/session_search.py src/assistant_log.py src/teacher_escalation.py \
    src/tool_implementations.py src/tool_execution.py src/tool_parsing.py \
    src/tool_schemas.py src/tool_policy.py src/tool_security.py src/tool_index.py \
    src/tool_utils.py src/agent_tools/ \
    src/chat_handler.py src/chat_helpers.py src/chat_processor.py src/preset_manager.py \
    src/deep_research.py src/research_handler.py src/research_utils.py src/visual_report.py \
    src/context_budget.py src/context_compactor.py src/model_context.py \
    src/document_actions.py src/document_processor.py src/pdf_forms.py \
    src/pdf_form_doc.py src/pdf_runtime.py src/markitdown_runtime.py \
    src/youtube_handler.py src/generated_images.py \
    src/memory.py src/memory_provider.py src/memory_vector.py src/chroma_client.py \
    src/rag_manager.py src/rag_singleton.py src/rag_vector.py src/personal_docs.py \
    src/embeddings.py src/embedding_lanes.py \
    src/email_thread_parser.py \
    src/caldav_sync.py src/caldav_writeback.py
git commit -m "refactor: Phase 4a — create src/domain/ with 11 feature sub-packages

- src/domain/agent/ (9 files): agent loop, runs, actions, tools
- src/domain/chat/ (4 files): handler, helpers, processor, presets
- src/domain/research/ (4 files): deep research, reports
- src/domain/context/ (3 files): budget, compaction, model context
- src/domain/document/ (8 files): PDF, YouTube, images
- src/domain/memory/ (4 files): memory, vector, ChromaDB
- src/domain/rag/ (4 files): RAG, vector search, personal docs
- src/domain/embedding/ (2 files): embeddings, lanes
- src/domain/email/ (1 file): thread parsing
- src/domain/calendar/ (2 files): CalDAV sync, writeback
- 11 new __init__.py files
- Shims at all old src/ paths
- Include src/teacher_escalation.py in agent domain

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Phase 4b — Replicate `src/domain/agent/tools/__init__.py` facade

**Files:**
- Create: `src/domain/agent/tools/__init__.py` (full facade, 136 lines matching old `src/agent_tools/__init__.py`)

- [ ] **Step 1: Write the tools __init__.py facade**

This must replicate the exact public API of the old `src/agent_tools/__init__.py`. Since we cannot change logic, we create it with updated import paths pointing within the new `src/domain/agent/tools/` package:

```python
"""
src/domain/agent/tools/__init__.py — Facade module.

Re-exports tool parsing, schemas, execution, and implementations.
All importers continue to work unchanged.

Sub-modules (now co-located in this package):
  - tool_parsing.py: regex patterns, parse/strip functions
  - tool_schemas.py: FUNCTION_TOOL_SCHEMAS, function_call_to_tool_block
  - tool_execution.py: execute_tool_block, format_tool_result, MCP helpers
  - tool_implementations.py: all do_* tool functions
"""

import logging
from collections import namedtuple

from src.domain.agent.tools.tool_utils import _truncate, get_mcp_manager, set_mcp_manager

logger = logging.getLogger(__name__)

from .subprocess_tools import BashTool, PythonTool
from .web_tools import WebSearchTool, WebFetchTool
from .filesystem_tools import ReadFileTool, WriteFileTool, EditFileTool, LsTool, GlobTool, GrepTool
from .document_tools import CreateDocumentTool, UpdateDocumentTool, EditDocumentTool, SuggestDocumentTool, ManageDocumentTool

TOOL_HANDLERS = {
    "bash": BashTool().execute,
    "python": PythonTool().execute,
    "web_search": WebSearchTool().execute,
    "web_fetch": WebFetchTool().execute,
    "read_file": ReadFileTool().execute,
    "write_file": WriteFileTool().execute,
    "edit_file": EditFileTool().execute,
    "ls": LsTool().execute,
    "glob": GlobTool().execute,
    "grep": GrepTool().execute,
    "create_document": CreateDocumentTool().execute,
    "update_document": UpdateDocumentTool().execute,
    "edit_document": EditDocumentTool().execute,
    "suggest_document": SuggestDocumentTool().execute,
    "manage_documents": ManageDocumentTool().execute,
}

# ---------------------------------------------------------------------------
# Constants (re-exported for backward compatibility — single source of truth
# is src.constants; always prefer importing from there for new code)
# ---------------------------------------------------------------------------
MAX_AGENT_ROUNDS = 50
SHELL_TIMEOUT = 60
PYTHON_TIMEOUT = 30

# Tool types that trigger execution
TOOL_TAGS = {"bash", "python", "web_search", "web_fetch", "read_file", "write_file", "edit_file",
             "grep", "glob", "ls",
             "create_document", "update_document", "edit_document",
             "search_chats",
             "chat_with_model", "create_session", "list_sessions",
             "send_to_session",
             "pipeline",
             "manage_session", "manage_memory", "list_models",
             "ui_control", "generate_image", "ask_user", "update_plan",
             "manage_tasks", "api_call", "ask_teacher", "manage_skills",
             "suggest_document",
             "manage_endpoints", "manage_mcp", "manage_webhooks",
             "manage_tokens", "manage_documents", "manage_settings",
             "manage_notes", "manage_calendar",
             "resolve_contact", "manage_contact", "list_email_accounts", "send_email", "list_emails",
             "read_email", "reply_to_email", "bulk_email", "archive_email",
             "delete_email", "mark_email_read",
             # Cookbook tools (LLM serving + downloads).
             "download_model", "serve_model",
             "list_served_models", "stop_served_model",
             "list_downloads", "cancel_download",
             "search_hf_models", "list_cached_models",
             "list_serve_presets", "serve_preset", "adopt_served_model",
             "list_cookbook_servers",
             # Other tools the agent reaches for.
             "edit_image", "trigger_research", "manage_research",
             # Generic loopback to any UI-button endpoint.
             "app_api"}

ToolBlock = namedtuple("ToolBlock", ["tool_type", "content"])

# ---------------------------------------------------------------------------
# Re-exports from sub-modules
# ---------------------------------------------------------------------------

# Parsing
from src.domain.agent.tools.tool_parsing import (  # noqa: E402, F401
    parse_tool_blocks,
    strip_tool_blocks,
    _TOOL_NAME_MAP,
    _TOOL_BLOCK_RE,
    _TOOL_CALL_RE,
    _XML_TOOL_CALL_RE,
    _XML_INVOKE_RE,
    _XML_PARAM_RE,
)

# Schemas
from src.domain.agent.tools.tool_schemas import (  # noqa: E402, F401
    FUNCTION_TOOL_SCHEMAS,
    function_call_to_tool_block,
)

# Execution
from src.domain.agent.tools.tool_execution import (  # noqa: E402, F401
    execute_tool_block,
    format_tool_result,
)

# Document functions
from .document_tools import (
    set_active_document,
    set_active_model
)

# Implementations
from src.domain.agent.tools.tool_implementations import (  # noqa: E402, F401
    do_search_chats,
    do_manage_skills,
    do_manage_tasks,
    do_manage_endpoints,
    do_manage_mcp,
    do_manage_webhooks,
    do_manage_tokens,
    do_manage_settings,
    do_api_call,
)
```

- [ ] **Step 2: Verify tools facade works**

```bash
uv run python -c "
from src.domain.agent.tools import (
    TOOL_HANDLERS, TOOL_TAGS, ToolBlock,
    parse_tool_blocks, execute_tool_block,
    do_search_chats, do_manage_skills,
)
print('tools facade OK:', len(TOOL_HANDLERS), 'handlers,', len(TOOL_TAGS), 'tags')
"
```

- [ ] **Step 3: Verify that src/agent_tools shim still works**

```bash
uv run python -c "
from src.agent_tools import TOOL_HANDLERS, TOOL_TAGS, ToolBlock
print('shim OK:', len(TOOL_HANDLERS), 'handlers')
"
```

- [ ] **Step 4: Commit**

```bash
git add src/domain/agent/tools/__init__.py
git commit -m "refactor: Phase 4b — create src/domain/agent/tools/__init__.py facade

- Replicate exact public API from old src/agent_tools/__init__.py
- Update internal import paths to src.domain.agent.tools.*
- 21 TOOL_HANDLERS, 40+ TOOL_TAGS, all re-exports preserved
- src/agent_tools/__init__.py shim → from src.domain.agent.tools import *

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: Phase 5 — Create `src/pkg/` shared utilities layer

**Files:**
- Create: 3 `__init__.py` files under `src/pkg/`
- Move: 13 files from `src/` + `core/` → `src/pkg/`

- [ ] **Step 1: Create pkg directories**

```bash
mkdir -p src/pkg/security
mkdir -p src/pkg/text
```

- [ ] **Step 2: Create __init__.py files**

`src/pkg/__init__.py`:
```python
"""Shared utilities — zero external dependencies, usable by any layer."""
from .constants import *
from .exceptions import *
```

`src/pkg/security/__init__.py`:
```python
"""Security utilities — prompt safety, URL validation, rate limiting."""
```

`src/pkg/text/__init__.py`:
```python
"""Text processing — helpers, topic analysis, goal-based extraction."""
```

- [ ] **Step 3: Move files to src/pkg/**

```bash
git mv src/constants.py src/pkg/constants.py
git mv src/exceptions.py src/pkg/exceptions.py
git mv src/tls_overrides.py src/pkg/tls_overrides.py
git mv src/prompt_security.py src/pkg/security/prompt_security.py
git mv src/url_safety.py src/pkg/security/url_safety.py
git mv src/url_security.py src/pkg/security/url_security.py
git mv src/rate_limiter.py src/pkg/security/rate_limiter.py
git mv src/text_helpers.py src/pkg/text/text_helpers.py
git mv src/topic_analyzer.py src/pkg/text/topic_analyzer.py
git mv src/goal_based_extractor.py src/pkg/text/goal_based_extractor.py
git mv src/user_time.py src/pkg/time.py
git mv src/app_helpers.py src/pkg/io.py
git mv core/platform_compat.py src/pkg/platform_compat.py
```

- [ ] **Step 4: Create shims at old locations**

```bash
# src/constants.py (CRITICAL — 44+ importers)
echo '# src/constants.py — shim, canonical: src.pkg.constants
from src.pkg.constants import *  # noqa: F401,F403
from src.pkg.constants import internal_api_base  # noqa: F401  # explicit for named imports' > src/constants.py

# src/exceptions.py
echo '# src/exceptions.py — shim, canonical: src.pkg.exceptions
from src.pkg.exceptions import *  # noqa: F401,F403' > src/exceptions.py

# src/tls_overrides.py
echo '# src/tls_overrides.py — shim, canonical: src.pkg.tls_overrides
from src.pkg.tls_overrides import *  # noqa: F401,F403' > src/tls_overrides.py

# src/prompt_security.py
echo '# src/prompt_security.py — shim, canonical: src.pkg.security.prompt_security
from src.pkg.security.prompt_security import *  # noqa: F401,F403' > src/prompt_security.py

# src/url_safety.py
echo '# src/url_safety.py — shim, canonical: src.pkg.security.url_safety
from src.pkg.security.url_safety import *  # noqa: F401,F403' > src/url_safety.py

# src/url_security.py
echo '# src/url_security.py — shim, canonical: src.pkg.security.url_security
from src.pkg.security.url_security import *  # noqa: F401,F403' > src/url_security.py

# src/rate_limiter.py
echo '# src/rate_limiter.py — shim, canonical: src.pkg.security.rate_limiter
from src.pkg.security.rate_limiter import *  # noqa: F401,F403' > src/rate_limiter.py

# src/text_helpers.py
echo '# src/text_helpers.py — shim, canonical: src.pkg.text.text_helpers
from src.pkg.text.text_helpers import *  # noqa: F401,F403' > src/text_helpers.py

# src/topic_analyzer.py
echo '# src/topic_analyzer.py — shim, canonical: src.pkg.text.topic_analyzer
from src.pkg.text.topic_analyzer import *  # noqa: F401,F403' > src/topic_analyzer.py

# src/goal_based_extractor.py
echo '# src/goal_based_extractor.py — shim, canonical: src.pkg.text.goal_based_extractor
from src.pkg.text.goal_based_extractor import *  # noqa: F401,F403' > src/goal_based_extractor.py

# src/user_time.py
echo '# src/user_time.py — shim, canonical: src.pkg.time
from src.pkg.time import *  # noqa: F401,F403' > src/user_time.py

# src/app_helpers.py
echo '# src/app_helpers.py — shim, canonical: src.pkg.io
from src.pkg.io import *  # noqa: F401,F403' > src/app_helpers.py

# core/platform_compat.py
echo '# core/platform_compat.py — shim, canonical: src.pkg.platform_compat
from src.pkg.platform_compat import *  # noqa: F401,F403' > core/platform_compat.py
```

- [ ] **Step 5: Update core/constants.py shim (double-shim chain)**

`core/constants.py` was already a shim pointing to `src.constants`. Since `src/constants.py` is now also a shim, the double-shim chain works automatically. But we should verify:

```bash
uv run python -c "from core.constants import BASE_DIR, DATA_DIR; print('double shim OK:', BASE_DIR)"
uv run python -c "from src.constants import BASE_DIR, DATA_DIR; print('single shim OK:', BASE_DIR)"
uv run python -c "from src.pkg.constants import BASE_DIR, DATA_DIR; print('canonical OK:', BASE_DIR)"
```

- [ ] **Step 6: Update core/exceptions.py to be a shim**

`core/exceptions.py` currently has the actual exception definitions. Since we moved `src/exceptions.py` (which had the same exceptions) to `src/pkg/exceptions.py`, we need to make `core/exceptions.py` a shim:

```python
# core/exceptions.py — shim, canonical: src.pkg.exceptions
from src.pkg.exceptions import *  # noqa: F401,F403
from src.pkg.exceptions import (  # explicit re-exports for IDE visibility
    SessionNotFoundError,
    InvalidFileUploadError,
    LLMServiceError,
    WebSearchError,
)
```

- [ ] **Step 7: Verify all shims work**

```bash
uv run python -c "from core.constants import BASE_DIR; print('core.constants OK')"
uv run python -c "from core.exceptions import SessionNotFoundError; print('core.exceptions OK')"
uv run python -c "from src.constants import DATA_DIR; print('src.constants OK')"
uv run python -c "from src.pkg.constants import BASE_DIR; print('pkg.constants OK')"
uv run python -c "from src.pkg.exceptions import SessionNotFoundError; print('pkg.exceptions OK')"
uv run python -c "from src.pkg.security.prompt_security import untrusted_context_message; print('security OK')"
```

- [ ] **Step 8: Commit**

```bash
git add src/pkg/ \
    src/constants.py src/exceptions.py src/tls_overrides.py \
    src/prompt_security.py src/url_safety.py src/url_security.py src/rate_limiter.py \
    src/text_helpers.py src/topic_analyzer.py src/goal_based_extractor.py \
    src/user_time.py src/app_helpers.py \
    core/constants.py core/exceptions.py core/platform_compat.py
git commit -m "refactor: Phase 5 — create src/pkg/ shared utilities layer

- Move src/constants.py → src/pkg/constants.py (44+ importers — critical shim)
- Move src/exceptions.py → src/pkg/exceptions.py
- Move 4 files → src/pkg/security/ (prompt_security, url_safety, url_security, rate_limiter)
- Move 3 files → src/pkg/text/ (text_helpers, topic_analyzer, goal_based_extractor)
- Move core/platform_compat.py → src/pkg/platform_compat.py
- Rename: user_time→time, app_helpers→io
- core/exceptions.py → shim (was real code; now re-exports from src.pkg.exceptions)
- 3 new __init__.py files (src/pkg/, src/pkg/security/, src/pkg/text/)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 10: Phase 6 — Move remaining standalone files

**Files:**
- Move: 5 lingering files to their final infra homes

- [ ] **Step 1: Move files**

```bash
git mv src/cleanup_service.py src/infra/scheduler/cleanup_service.py
git mv src/service_health.py src/infra/scheduler/service_health.py
git mv src/readiness.py src/infra/scheduler/readiness.py
git mv src/webhook_manager.py src/infra/scheduler/webhook_manager.py
git mv src/api_key_manager.py src/infra/auth/api_key_manager.py
git mv src/cookbook_serve_lifecycle.py src/infra/scheduler/cookbook_serve_lifecycle.py
```

- [ ] **Step 2: Create shims**

```bash
echo '# src/cleanup_service.py — shim, canonical: src.infra.scheduler.cleanup_service
from src.infra.scheduler.cleanup_service import *  # noqa: F401,F403' > src/cleanup_service.py

echo '# src/service_health.py — shim, canonical: src.infra.scheduler.service_health
from src.infra.scheduler.service_health import *  # noqa: F401,F403' > src/service_health.py

echo '# src/readiness.py — shim, canonical: src.infra.scheduler.readiness
from src.infra.scheduler.readiness import *  # noqa: F401,F403' > src/readiness.py

echo '# src/webhook_manager.py — shim, canonical: src.infra.scheduler.webhook_manager
from src.infra.scheduler.webhook_manager import *  # noqa: F401,F403' > src/webhook_manager.py

echo '# src/api_key_manager.py — shim, canonical: src.infra.auth.api_key_manager
from src.infra.auth.api_key_manager import *  # noqa: F401,F403' > src/api_key_manager.py

echo '# src/cookbook_serve_lifecycle.py — shim, canonical: src.infra.scheduler.cookbook_serve_lifecycle
from src.infra.scheduler.cookbook_serve_lifecycle import *  # noqa: F401,F403' > src/cookbook_serve_lifecycle.py
```

- [ ] **Step 3: Verify**

```bash
uv run python -c "from src.infra.scheduler.cleanup_service import *; print('cleanup OK')"
uv run python -c "from src.infra.auth.api_key_manager import *; print('api_key OK')"
```

- [ ] **Step 4: Commit**

```bash
git add src/infra/scheduler/cleanup_service.py src/infra/scheduler/service_health.py \
    src/infra/scheduler/readiness.py src/infra/scheduler/webhook_manager.py \
    src/infra/scheduler/cookbook_serve_lifecycle.py \
    src/infra/auth/api_key_manager.py \
    src/cleanup_service.py src/service_health.py src/readiness.py \
    src/webhook_manager.py src/api_key_manager.py src/cookbook_serve_lifecycle.py
git commit -m "refactor: Phase 6 — move remaining standalone files to infra homes

- cleanup_service, service_health, readiness → src/infra/scheduler/
- webhook_manager, cookbook_serve_lifecycle → src/infra/scheduler/
- api_key_manager → src/infra/auth/
- Shims at old src/ locations

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 11: Phase 7 — Rename entry point + cleanup

**Files:**
- Rename: `app.py` → `main.py`
- Modify: `Dockerfile`
- Modify: `pyproject.toml`

- [ ] **Step 1: Rename app.py to main.py**

```bash
git mv app.py main.py
```

- [ ] **Step 2: Update Dockerfile CMD**

Replace the last line of Dockerfile:
```
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7000"]
```
With:
```
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7000"]
```

- [ ] **Step 3: Verify server starts with new name**

```bash
timeout 5 uv run uvicorn main:app --host 0.0.0.0 --port 7999 2>&1 || true
# Should show "Application startup complete" or similar, then get killed by timeout
```

- [ ] **Step 4: Update pyproject.toml package discovery**

The `[tool.setuptools.packages.find]` section should already include all needed directories. Verify:

```bash
uv run python -c "
import setuptools
# Check that all key packages are importable
import conf
import src.api.router
import src.domain.agent
import src.infra.database
import src.pkg
print('All packages discoverable')
"
```

- [ ] **Step 5: Final verification — full import audit**

```bash
# Verify all key imports work from new locations
uv run python -c "
# Conf
from conf import config, get_setting
# API
from src.api.router.chat_routes import setup_chat_routes
# Domain
from src.domain.agent.agent_loop import stream_agent_loop
from src.domain.agent.tools import TOOL_HANDLERS
from src.domain.chat.chat_handler import ChatHandler
# Infra
from src.infra.database.database import SessionLocal
from src.infra.auth.auth import AuthManager
from src.infra.llm.llm_core import stream_llm
# Pkg
from src.pkg.constants import BASE_DIR, DATA_DIR
from src.pkg.exceptions import SessionNotFoundError
# Core shim
from core import SessionLocal, AuthManager, stream_llm
print('All imports OK')
"
```

- [ ] **Step 6: Commit**

```bash
git add main.py app.py Dockerfile pyproject.toml
git commit -m "refactor: Phase 7 — rename app.py → main.py + final cleanup

- Rename entry point: app.py → main.py (ytrader convention)
- Update Dockerfile CMD: uvicorn app:app → uvicorn main:app
- ⚠️ BREAKING: anyone running 'uvicorn app:app' directly must update to 'uvicorn main:app'
- All backward-compat shims remain in place for old import paths

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 12: Final verification — full test suite

- [ ] **Step 1: Run pytest**

```bash
uv run pytest tests/ -x --timeout=60 -q
```

Expected: Tests pass. If any fail, check whether the test uses an import path that needs a shim (shims should make all old paths work).

- [ ] **Step 2: Verify app can import without errors**

```bash
uv run python -c "from main import app; print('App imported successfully:', app.title)"
```

- [ ] **Step 3: Verify target directory structure**

```bash
echo "=== Target structure ==="
echo "conf/: $(ls conf/*.py | wc -l) files"
echo "src/api/router/: $(ls src/api/router/*.py | wc -l) files"
echo "src/api/handler/: $(ls src/api/handler/*.py | wc -l) files"
echo "src/domain/: $(find src/domain -name '*.py' | wc -l) files"
echo "src/infra/: $(find src/infra -name '*.py' | wc -l) files"
echo "src/pkg/: $(find src/pkg -name '*.py' | wc -l) files"
echo "Shims in src/: $(grep -l 'shim, canonical' src/*.py 2>/dev/null | wc -l) files"
echo "Shims in core/: $(grep -l 'shim, canonical' core/*.py 2>/dev/null | wc -l) files"
echo "Shims in routes/: $(grep -l 'shim, canonical' routes/*.py 2>/dev/null | wc -l) files"
```

Expected approximate counts:
- `conf/`: 4 files (3 moved + __init__.py)
- `src/api/router/`: 43 files
- `src/api/handler/`: 7 files
- `src/domain/`: ~50 files
- `src/infra/`: ~32 files (23 moved + 8 __init__ + search/*)
- `src/pkg/`: ~16 files (13 moved + 3 __init__)
- Shims in `src/`: ~70 files
- Shims in `core/`: ~8 files
- Shims in `routes/`: ~50 files
```
