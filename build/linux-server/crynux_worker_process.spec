# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import copy_metadata

scipy_hiddenimports = collect_submodules('scipy')
scipy_datas = collect_data_files('scipy')
rfc3987_syntax_datas = collect_data_files('rfc3987_syntax')
binaries = []
metadata_packages = [
    ('diffusers', True),
    ('transformers', True),
    ('accelerate', False),
    ('attrs', False),
    ('compel', False),
    ('controlnet-aux', False),
    ('datasets', False),
    ('einops', False),
    ('fsspec', False),
    ('huggingface-hub', False),
    ('imageio', False),
    ('jsonschema', False),
    ('lazy-loader', False),
    ('mediapipe', False),
    ('networkx', False),
    ('numba', False),
    ('numpy', False),
    ('onnxruntime', False),
    ('onnxruntime-gpu', False),
    ('pandas', False),
    ('peft', False),
    ('pydantic', False),
    ('pydantic-settings', False),
    ('pymatting', False),
    ('rembg', False),
    ('scipy', False),
    ('sentencepiece', False),
    ('sympy', False),
    ('tiktoken', False),
    ('torch', False),
    ('torchvision', False),
    ('urllib3', False),
    ('websockets', False),
    ('whatthepatch', False),
]
metadata_datas = []
for package_name, recursive in metadata_packages:
    metadata_datas += copy_metadata(package_name, recursive=recursive)

a = Analysis(
    ['worker/crynux_worker_process.py'],
    pathex=[],
    binaries=binaries,
    datas=scipy_datas + rfc3987_syntax_datas + metadata_datas,
    hiddenimports=[
        "diffusers.pipelines.stable_diffusion_xl.pipeline_output",
    ] + scipy_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    module_collection_mode={
        'diffusers': 'py',
        'transformers': 'py',
        'torch': 'py',
        'sd_task': 'py',
        'gpt_task': 'py',
        'crynux_worker': 'py',
    },
    excludes=[
        "pkg_resources",
        "setuptools",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    name='crynux_worker_process',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
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
    name='crynux_worker_process',
)
