# -*- mode: python ; coding: utf-8 -*-


from pathlib import Path


root = Path(SPECPATH)
datas = [
    (str(root / 'assets'), 'assets'),
    (str(root / 'release' / 'agent' / 'README.md'), '.'),
    (str(root / 'artifacts' / 'p11-build' / 'BUILD_INFO.json'), '.'),
]
excludes = ['tkinter', 'prism.functional_app', 'prism.app', 'prism.gui_generation',
            'prism.gui_writes', 'pytest', 'PyInstaller']

cli = Analysis(
    [str(root / 'agent_cli_launcher.py')], pathex=[str(root / 'src')],
    binaries=[], datas=datas, hiddenimports=[], hookspath=[], hooksconfig={},
    runtime_hooks=[], excludes=excludes, noarchive=False,
)
mcp = Analysis(
    [str(root / 'agent_mcp_launcher.py')], pathex=[str(root / 'src')],
    binaries=[], datas=datas, hiddenimports=[], hookspath=[], hooksconfig={},
    runtime_hooks=[], excludes=excludes, noarchive=False,
)

cli_pyz = PYZ(cli.pure)
mcp_pyz = PYZ(mcp.pure)
cli_exe = EXE(
    cli_pyz, cli.scripts, [], exclude_binaries=True, name='prism-cli',
    console=True, contents_directory='.',
)
mcp_exe = EXE(
    mcp_pyz, mcp.scripts, [], exclude_binaries=True, name='prism-mcp',
    console=True, contents_directory='.',
)
coll = COLLECT(
    cli_exe, mcp_exe, cli.binaries, cli.datas, mcp.binaries, mcp.datas,
    strip=False, upx=False, name='PRISM-Agent-Win10',
)
