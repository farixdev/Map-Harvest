# -*- mode: python ; coding: utf-8 -*-


from PyInstaller.utils.hooks import collect_data_files

# tzdata is data, not code, so nothing imports it and PyInstaller would leave it
# out -- and without it every IANA zone in the sending-window picker silently
# resolves to the build machine's clock. lxml and the Qt platform plugins are
# reached dynamically for the same reason.
a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=collect_data_files('tzdata') + [('app_icon.ico', '.')],
    hiddenimports=['tzdata', 'zoneinfo'],
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
    a.binaries,
    a.datas,
    [],
    name='MapHarvest',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['app_icon.ico'],
)
