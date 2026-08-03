# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_dir = Path(SPEC).resolve().parent

a = Analysis(
    ['launcher.py'],
    pathex=[str(project_dir)],
    binaries=[
        (str(project_dir / 'hardware-helper.exe'), '.'),
        (str(project_dir / 'iperf3.exe'), '.'),
        (str(project_dir / 'cygwin1.dll'), '.'),
        (str(project_dir / 'cygcrypto-3.dll'), '.'),
        (str(project_dir / 'cygz.dll'), '.'),
    ],
    datas=[
        (str(project_dir / 'config.example.json'), '.'),
    ],
    hiddenimports=[],
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
    name='BeacnAgent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
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
    name='BeacnAgent',
)
