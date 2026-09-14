# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

datas = [('static', 'static'), ('scripts', 'scripts'), ('mcp_servers', 'mcp_servers'), ('services/hwfit/data', 'services/hwfit/data'), ('config', 'config'), ('.env.example', '.env.example')]
binaries = []
hiddenimports = []
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
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
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['static/icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Odysseus',
)

# Ship the real runtime .env alongside the exe (not via the `datas` list above:
# PyInstaller's onedir layout treats a (src, dest) datas tuple's dest as a
# directory, so ('.env', '.env') would land at _internal/.env/.env instead of
# next to Odysseus.exe — which is where load_dotenv() actually looks, since it
# reads from the app's working directory). SPECPATH/DISTPATH are globals
# PyInstaller injects into this spec's execution namespace.
import shutil
_env_src = os.path.join(SPECPATH, '.env')
if os.path.isfile(_env_src):
    shutil.copy2(_env_src, os.path.join(DISTPATH, 'Odysseus', '.env'))
