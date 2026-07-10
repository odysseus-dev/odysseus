# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.building.datastruct import Tree

a = Analysis(
    ['installer.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('.env.example', '.'),
        ('package.json', '.'),
        ('app.py', '.'),
        ('core', 'core'),
        ('routes', 'routes'),
        ('src', 'src'),
        ('services', 'services'),
        ('config', 'config'),
        ('static', 'static'),
        ('mcp_servers', 'mcp_servers'),
        ('integrations', 'integrations'),
        ('companion', 'companion'),
        ('connect-android-pc.bat', '.'),
        ('launch-windows.ps1', '.'),
        ('requirements.txt', '.'),
        ('setup.py', '.')
    ],
    hiddenimports=['win32com.client'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# Keep source backups, bytecode caches, and one-off patch helpers out of the
# distributable while retaining the real production runtime trees.
def _keep_runtime_data(entry):
    path = entry[0].replace('\\', '/')
    parts = path.split('/')
    return not (
        path.endswith('.bak')
        or path.endswith('.pyc')
        or '__pycache__' in parts
        or path.endswith('scripts/patch_fix.py')
    )


a.datas = [entry for entry in a.datas if _keep_runtime_data(entry)]
a.datas += [entry for entry in Tree('scripts', prefix='scripts')
            if _keep_runtime_data(entry)]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Simple-Signal-Extension-Setup',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=False,
    icon=['static\\icon.ico'],
)
