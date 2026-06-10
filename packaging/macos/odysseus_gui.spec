# odysseus_gui.spec — lightweight PyInstaller BUNDLE for GUI only.
#
# This builds OdysseusGUI.app — a proper macOS .app bundle whose
# Contents/MacOS/OdysseusGUI is a PyInstaller Mach-O executable.
# Because it IS a .app bundle, macOS correctly shows its Info.plist
# CFBundleDisplayName and icon in the Dock for the entire session.
#
# This bundle does NOT contain the FastAPI app stack, ML libs, or uvicorn.
# It only contains run.py in gui mode + pywebview + pyobjc.
# ODYSSEUS_MODE=gui is baked into LSEnvironment so no wrapper script needed.

import os
from PyInstaller.utils.hooks import collect_data_files, collect_all

SPEC_DIR  = os.path.dirname(SPEC)
STAGING   = os.path.join(SPEC_DIR, 'build', 'staging')

# Icon — .icns built during Step 5 of build.sh
# Icon built during Step 5 of build.sh and stored in build/
ICON_PATH = os.path.join(SPEC_DIR, 'build', 'odysseus.icns')
# Fallback: look in RES dir after assembly
if not os.path.exists(ICON_PATH):
    ICON_PATH = None

APP_NAME    = os.environ.get('ODYSSEUS_APP_NAME',    'Odysseus')
APP_VERSION = os.environ.get('ODYSSEUS_APP_VERSION', '1.0.1')

# Collect pywebview data files
_wv_datas, _wv_bins, _wv_hidden = collect_all('webview')

a = Analysis(
    [os.path.join(STAGING, 'run.py')],
    pathex=[STAGING],
    binaries=_wv_bins,
    datas=_wv_datas,
    hiddenimports=[
        # PyWebView + PyObjC (macOS WKWebView backend)
        'webview',
        'webview.platforms',
        'webview.platforms.cocoa',
        'webview.util',
        'webview.window',
        'webview.http',
        'webview.js',
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
        # Stdlib used by gui path
        'pathlib',
        'socket',
        'threading',
        'time',
        'os',
        'sys',
    ] + _wv_hidden,
    hookspath=[],
    excludes=[
        # Explicitly exclude heavy server-only deps to keep bundle small
        'uvicorn', 'fastapi', 'starlette', 'chromadb', 'onnxruntime',
        'fastembed', 'sqlalchemy', 'aiosqlite', 'numpy', 'torch',
        'transformers', 'tokenizers', 'httpx', 'aiohttp',
        'passlib', 'bcrypt', 'jose', 'caldav', 'vobject',
        'markdown', 'pypdf', 'bs4', 'lxml', 'qrcode', 'pyotp',
        'tkinter', 'matplotlib', 'IPython', 'jupyter',
        'setuptools', 'distutils', 'test', 'unittest',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Odysseus',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    target_arch='arm64',
    codesign_identity=None,
    entitlements_file=None,
)

# BUNDLE — this is what makes it a real .app with Dock identity
gui_app = BUNDLE(
    exe,
    a.binaries,
    a.datas,
    name='Odysseus.app',
    icon=ICON_PATH if os.path.exists(ICON_PATH) else None,
    bundle_identifier='com.odysseus.app.gui',
    info_plist={
        'CFBundleIconFile':           'odysseus',  # matches odysseus.icns
        'CFBundleName':              APP_NAME,
        'CFBundleDisplayName':       APP_NAME,
        'CFBundleShortVersionString': APP_VERSION,
        'CFBundleVersion':           APP_VERSION,
        'CFBundlePackageType':       'APPL',
        'CFBundleExecutable':        'Odysseus',
        'LSMinimumSystemVersion':    '12.0',
        'NSHighResolutionCapable':   True,
        'LSUIElement':               False,
        # Bake ODYSSEUS_MODE=gui so no wrapper script is needed
        'LSEnvironment': {
            'ODYSSEUS_MODE': 'gui',
        },
    },
)
