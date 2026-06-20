# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

a = Analysis(
    ['crynux-node/src/app/main.py'],
    pathex=[],
    binaries=[],
    datas=collect_data_files('crynux_server.contracts.abi'),
    hiddenimports=['aiosqlite', "crynux_server.contracts.abi"],
    collect_submodules=["crynux_server.contracts.abi"],
    collect_data=["crynux_server.contracts.abi"],
    module_collection_mode={
        'crynux_server': 'py',
    },
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Crynux Node',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    icon=['res/icon.ico'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Crynux Node',
)
