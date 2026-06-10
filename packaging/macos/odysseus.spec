# odysseus.spec — PyInstaller spec for Odysseus (portable macOS bundle)
#
# Run via build.sh — do not run pyinstaller directly against this file
# unless you activate the odysseus build venv first.
#
# Output: a one-dir frozen bundle at
#   <distpath>/odysseus_app/
# which build.sh then copies into Odysseus.app/Contents/Resources/odysseus_app/

import sys
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# ── Repo root (one level up from this spec file) ──────────────────────────────
# SPEC lives in packaging/macos/; staging is in packaging/macos/build/staging/
# run.py is in staging, all other files symlinked from repo root
REPO = os.path.abspath(os.path.dirname(SPEC) + '/../..')
STAGING = os.path.abspath(os.path.join(os.path.dirname(SPEC), 'build', 'staging'))
# Use staging as the source dir (contains run.py + symlinks to repo)
_SRC = STAGING if os.path.isdir(STAGING) else REPO

# ── Data files ────────────────────────────────────────────────────────────────
# Every directory that Odysseus reads at runtime must be listed here.
# Format: (source_path, dest_path_inside_bundle)
datas = [
    # Entry point and main app module must both be present as data
    # so uvicorn can import 'app:app' at runtime
    (os.path.join(REPO, 'app.py'),  '.'),
    (os.path.join(_SRC, 'run.py'),  '.'),  # from staging
    # Front-end assets
    (os.path.join(REPO, 'static'),       'static'),
    # Route modules (imported dynamically via importlib in some builds)
    (os.path.join(REPO, 'routes'),       'routes'),
    # Core auth/db/middleware Python files (needed for import alongside frozen code)
    (os.path.join(REPO, 'core'),         'core'),
    # LLM core, agent loop, search, chat processor
    (os.path.join(REPO, 'src'),          'src'),
    # Service layer (docs, memory, search, hwfit …)
    (os.path.join(REPO, 'services'),     'services'),
    # MCP server definitions
    (os.path.join(REPO, 'mcp_servers'),  'mcp_servers'),
    # companion — local package for mobile pairing
    (os.path.join(REPO, 'companion'),     'companion'),
    # .env.example (bootstrap.py copies this on first run)
    (os.path.join(REPO, '.env.example'), '.'),
    # Alembic migrations if present
    *([(os.path.join(REPO, 'alembic'), 'alembic')]
      if os.path.isdir(os.path.join(REPO, 'alembic')) else []),
]

# Collect data files from libraries that need them at runtime
datas += collect_data_files('chromadb')
datas += collect_data_files('fastembed')
datas += collect_data_files('onnxruntime')
datas += collect_data_files('starlette')
datas += collect_data_files('fastapi')
datas += collect_data_files('uvicorn')
from PyInstaller.utils.hooks import collect_all
uvicorn_datas, uvicorn_binaries, uvicorn_hiddenimports = collect_all('uvicorn')
datas += uvicorn_datas
# uvicorn_hiddenimports merged into hiddenimports list after it is defined below
datas += collect_data_files('sqlalchemy')
datas += collect_data_files('jinja2')
datas += collect_data_files('certifi')
datas += collect_data_files('caldav')
datas += collect_data_files('qrcode')
datas += collect_data_files('numpy')
datas += collect_data_files('mcp')
datas += collect_data_files('webview')

# ── Hidden imports ────────────────────────────────────────────────────────────
# Modules that PyInstaller cannot detect via static analysis because they are
# loaded dynamically (importlib, __import__, plugin systems, etc.)
hiddenimports = [
    # FastAPI / Starlette ecosystem
    'fastapi',
    'fastapi.middleware',
    'fastapi.middleware.cors',
    'fastapi.staticfiles',
    'fastapi.templating',
    'starlette',
    'starlette.middleware',
    'starlette.middleware.sessions',
    'starlette.routing',
    'starlette.staticfiles',
    'starlette.templating',
    'uvicorn',
    'uvicorn.loops',
    'uvicorn.loops.asyncio',
    'uvicorn.loops.uvloop',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.h11_impl',
    'uvicorn.protocols.http.httptools_impl',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.websockets_impl',
    'uvicorn.protocols.websockets.wsproto_impl',
    # ASGI / async
    'anyio',
    'anyio._backends._asyncio',
    'anyio._backends._trio',
    'sniffio',
    'h11',
    # Database
    'sqlalchemy',
    'sqlalchemy.dialects',
    'sqlalchemy.dialects.sqlite',
    'sqlalchemy.dialects.sqlite.pysqlite',
    'sqlalchemy.orm',
    'sqlalchemy.ext.declarative',
    'sqlalchemy.pool',
    'aiosqlite',
    # Auth
    'passlib',
    'passlib.handlers',
    'passlib.handlers.bcrypt',
    'bcrypt',
    'jose',
    'jose.jwt',
    'jose.exceptions',
    # HTTP clients
    'httpx',
    'httpcore',
    'aiohttp',
    'aiofiles',
    # Multipart / file uploads
    'multipart',
    'python_multipart',
    # ChromaDB
    'chromadb',
    'chromadb.api',
    'chromadb.api.client',
    'chromadb.config',
    'chromadb.db',
    'chromadb.db.mixins',
    'chromadb.segment',
    'chromadb.segment.impl',
    'chromadb.segment.impl.metadata',
    'chromadb.segment.impl.vector',
    'chromadb.segment.impl.vector.local_hnsw',
    'chromadb.telemetry',
    'chromadb.telemetry.posthog',
    # fastembed (ONNX-based local embeddings)
    'fastembed',
    'fastembed.text',
    'fastembed.text.onnx_embedding',
    'onnxruntime',
    'tokenizers',
    # Pydantic (FastAPI depends on this heavily)
    'pydantic',
    'pydantic.v1',
    'pydantic_core',
    'pydantic_settings',
    # Email / calendar
    'imaplib',
    'smtplib',
    'email',
    'email.mime',
    'email.mime.multipart',
    'email.mime.text',
    'caldav',
    'vobject',
    # Markdown / document processing
    'markdown',
    'pypdf',
    'bs4',
    'lxml',
    'lxml.etree',
    # Misc utilities
    'dotenv',
    'python_dotenv',
    'yaml',
    'toml',
    'tomllib',
    'packaging',
    'PIL',
    'PIL.Image',
    'cryptography',
    'cryptography.fernet',
    'charset_normalizer',
    'certifi',
    'idna',
    # companion (local mobile pairing package)
    'companion',
    'companion.pairing',
    'companion.routes',
    # 2FA / OTP / QR
    'pyotp',
    'qrcode',
    'qrcode.image',
    'qrcode.image.pure',
    'qrcode.image.svg',
    # CalDAV / calendar sync
    'caldav',
    'vobject',
    'vobject.icalendar',
    # Numpy (used by several services)
    'numpy',
    'numpy.core',
    'numpy.lib',
    # MCP (model context protocol)
    'mcp',
    'mcp.server',
    'mcp.server.sse',
    'mcp.client',
    # Audio
    'wave',
    # PyWebView — native WKWebView window
    'webview',
    'webview.platforms',
    'webview.platforms.cocoa',
    'webview.util',
    'webview.window',
    'webview.http',
    'webview.js',
    # PyObjC (pywebview dependency — macOS native bindings)
    'objc',
    'AppKit',
    'Foundation',
    'WebKit',
    'Cocoa',
    'Quartz',
    'Security',
    'UniformTypeIdentifiers',
    'bottle',
    'proxy_tools',
    # PIL / Pillow (qrcode depends on it)
    'PIL',
    'PIL.Image',
    'PIL.ImageDraw',
    'PIL.ImageFont',
    # Standard lib modules sometimes missed
    'asyncio',
    'asyncio.events',
    'concurrent.futures',
    'multiprocessing',
    'multiprocessing.util',
    'logging.handlers',
    'importlib.metadata',
    'importlib.resources',
    'xml.etree.ElementTree',
    'sqlite3',
    'json',
    'pathlib',
    'secrets',
    # Extra imports from full codebase scan
    'dateutil',
    'dateutil.rrule',
    'dateutil.parser',
    'dateutil.relativedelta',
    'dateutil.tz',
    'six',
    'PIL.ImageFilter',
    'PIL.ImageEnhance',
    'PIL.ImageOps',
    'PIL.PngImagePlugin',
    'PIL.JpegImagePlugin',
    'PIL.GifImagePlugin',
    'PIL.WebPImagePlugin',
    'lxml.etree',
    'lxml.objectify',
    'requests',
    'requests.auth',
    'requests.adapters',
    'vobject',
    'vobject.base',
    'vobject.icalendar',
    'vobject.vcard',
    'numpy',
    'numpy.core',
    'numpy.core._multiarray_umath',
    'numpy.lib.stride_tricks',
    'mcp',
    'mcp.server',
    'mcp.server.sse',
    'mcp.client',
    'mcp.types',
    'anyio._backends._asyncio',
    'anyio._backends._trio',
    'httpcore',
    'httpcore._async',
    'httpcore._sync',
    'starlette.middleware.base',
    'starlette.middleware.gzip',
    'starlette.responses',
    'starlette.requests',
    'starlette.websockets',
    'starlette.background',
    'starlette.concurrency',
    'starlette.datastructures',
    'starlette.exceptions',
    'starlette.formparsers',
    'websockets',
    'websockets.server',
    'websockets.client',
    'websockets.exceptions',
    'fastapi.security',
    'fastapi.security.oauth2',
    'fastapi.security.http',
    'fastapi.background',
    'fastapi.responses',
    'fastapi.requests',
    'fastapi.websockets',
    'fastapi.encoders',
    'fastapi.exceptions',
    'email.mime.base',
    'email.mime.application',
    'email.encoders',
    'email.utils',
    'typing_extensions',
    'annotated_types',
]

# Merge uvicorn hidden imports collected above
hiddenimports += uvicorn_hiddenimports

# Collect all submodules of packages that use plugin architectures
hiddenimports += collect_submodules('chromadb')
hiddenimports += collect_submodules('sqlalchemy')
hiddenimports += collect_submodules('uvicorn')
hiddenimports += collect_submodules('starlette')

# ── Excluded modules (reduce bundle size) ─────────────────────────────────────
excludes = [
    'tkinter',
    'matplotlib',
    'numpy.distutils',
    'setuptools',
    'distutils',
    'test',
    'unittest',
    'IPython',
    'jupyter',
    'notebook',
    'nbformat',
    'nbconvert',
]

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    [os.path.join(_SRC, 'run.py')],
    pathex=[_SRC, REPO],
    binaries=[] + (uvicorn_binaries if 'uvicorn_binaries' in dir() else []),
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='odysseus_app',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch='arm64',
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='odysseus_app',
)
