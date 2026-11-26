@echo off
setlocal
cd /d "%~dp0"

if exist "dist\PRISM-Agent-Win10" goto :failed
call setup_agent.bat
if errorlevel 1 goto :failed
".venv-agent\Scripts\python.exe" scripts\build_assets_manifest.py
if errorlevel 1 goto :failed
".venv-agent\Scripts\python.exe" scripts\write_build_info.py
if errorlevel 1 goto :failed
".venv-agent\Scripts\python.exe" -m PyInstaller --clean PRISM-Agent.spec
if errorlevel 1 goto :failed
if not exist "dist\PRISM-Agent-Win10\prism-cli.exe" goto :failed
if not exist "dist\PRISM-Agent-Win10\prism-mcp.exe" goto :failed
if not exist "dist\PRISM-Agent-Win10\README.md" goto :failed
".venv-agent\Scripts\python.exe" scripts\build_assets_manifest.py --root dist\PRISM-Agent-Win10 --check
if errorlevel 1 goto :failed

echo Build complete: dist\PRISM-Agent-Win10
echo Distribute the entire PRISM-Agent-Win10 directory.
endlocal & exit /b 0

:failed
echo Agent build failed. Release is incomplete.
endlocal & exit /b 1
