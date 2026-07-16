# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Odysseus Desktop.
Produces a single .exe that embeds the harness + all deps.

Build:
    venv\Scripts\python.exe -m PyInstaller odysseus-desktop.spec

Output:
    dist\Odysseus Desktop.exe
"""

import sys
from pathlib import Path

repo_root = Path(SPECPATH)  # directory containing this .spec file

a = Analysis(
    [str(repo_root / "odysseus-desktop.py")],
    pathex=[str(repo_root)],
    binaries=[],
    datas=[
        # Include the backend source so the harness can spawn uvicorn
        (str(repo_root / "app.py"), "."),
        (str(repo_root / "setup.py"), "."),
        (str(repo_root / "requirements.txt"), "."),
        (str(repo_root / "src"), "src"),
        (str(repo_root / "routes"), "routes"),
        (str(repo_root / "core"), "core"),
        (str(repo_root / "config"), "config"),
        (str(repo_root / "static"), "static"),
        (str(repo_root / "scripts"), "scripts"),
        (str(repo_root / "services"), "services"),
        (str(repo_root / "mcp_servers"), "mcp_servers"),
        (str(repo_root / "integrations"), "integrations"),
        (str(repo_root / "companion"), "companion"),
        # Include the venv's site-packages for runtime deps
        (str(repo_root / "venv" / "Lib" / "site-packages"), "site-packages"),
    ],
    hiddenimports=[
        # pywebview deps
        "webview",
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
        # pystray deps
        "pystray",
        "pystray._win32",
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
        # FastAPI / starlette / uvicorn (for backend)
        "fastapi",
        "starlette",
        "uvicorn",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        # Odysseus backend
        "sqlalchemy",
        "jinja2",
        "python_multipart",
        "aiofiles",
        "httpx",
        "chromadb",
        "chromadb.config",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "pandas",
        "numpy",
        "scipy",
        "jupyter",
        "IPython",
        "ipykernel",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Odysseus Desktop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,              # GUI app — no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(repo_root / "static" / "favicon.ico") if (repo_root / "static" / "favicon.ico").exists() else None,
)
