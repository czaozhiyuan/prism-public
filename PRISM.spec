# -*- mode: python ; coding: utf-8 -*-


from pathlib import Path


root = Path(SPECPATH)
a = Analysis(
    [str(root / 'prism_launcher.py')],
    pathex=[str(root / 'src')],
    binaries=[],
    datas=[
        (str(root / 'assets'), 'assets'),
        (str(root / 'release' / 'README.md'), '.'),
        (str(root / 'artifacts' / 'p11-build' / 'BUILD_INFO.json'), '.'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['mcp', 'fastmcp', 'prism_agent', 'prism.cli', 'pytest'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PRISM',
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name='PRISM-GUI-Win7',
)
