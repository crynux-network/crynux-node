# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--identity", action="store")
options = parser.parse_args()

a = Analysis(
    ['crynux-node/src/app/main.py'],
    pathex=[],
    binaries=[
        ('dist/crynux_worker_process', '.'),
    ],
    datas=[
        ('data', 'data'),
        ('res', 'res'),
        ('webui', 'webui'),
    ] + collect_data_files('crynux_server.contracts.abi'),
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
    name='crynux_node',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=options.identity,
    entitlements_file='entitlements.plist',
    icon=['res/icon.icns'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='crynux_node',
)
app = BUNDLE(
    coll,
    name='Crynux Node.app',
    icon='res/icon.icns',
    bundle_identifier='ai.crynux.node',
    info_plist={
        'CFBundleShortVersionString': '3.1.0',
    },
)
