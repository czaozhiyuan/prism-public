@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=py -3.8"
if exist ".python38\python.exe" set "PYTHON_EXE=.python38\python.exe"

if exist "assets\legacy" goto :failed
if exist "assets\migration_samples" goto :failed
if exist "dist\PRISM-GUI-Win7" goto :failed

if not exist ".venv-gui\Scripts\python.exe" (
  %PYTHON_EXE% -m venv .venv-gui
  if errorlevel 1 goto :failed
)

call ".venv-gui\Scripts\activate.bat"
if errorlevel 1 goto :failed
python -c "import sys, struct; assert sys.version_info[:3] == (3,8,10) and struct.calcsize('P') == 8, 'GUI requires CPython 3.8.10 x64'"
if errorlevel 1 goto :failed
python -m pip install -r requirements-build.txt
if errorlevel 1 goto :failed
python -m pip install --no-build-isolation --no-deps -e .
if errorlevel 1 goto :failed
python -m pip check
if errorlevel 1 goto :failed
python scripts\verify_gui_environment.py
if errorlevel 1 goto :failed
python scripts\build_assets_manifest.py
if errorlevel 1 goto :failed
python scripts\write_build_info.py
if errorlevel 1 goto :failed
REM Source regression excludes only tests requiring Agent, Office, or built packages.
python -m pytest tests -q --strict-markers -m "not agent_only and not word_com and not package_smoke"
if errorlevel 1 goto :failed
python -m PyInstaller --clean PRISM.spec
if errorlevel 1 goto :failed
if not exist "dist\PRISM-GUI-Win7\PRISM.exe" goto :failed
if not exist "dist\PRISM-GUI-Win7\README.md" goto :failed
for %%D in (guides schemas prompts) do (
  if not exist "dist\PRISM-GUI-Win7\assets\%%D" goto :failed
)
if exist "dist\PRISM-GUI-Win7\assets\legacy" goto :failed
if exist "dist\PRISM-GUI-Win7\assets\migration_samples" goto :failed
python scripts\build_assets_manifest.py --root dist\PRISM-GUI-Win7 --check
if errorlevel 1 goto :failed

echo.
echo Build complete: dist\PRISM-GUI-Win7\PRISM.exe
echo Distribute the entire dist\PRISM-GUI-Win7 directory, including all dependencies and assets.
echo Release guides: dist\PRISM-GUI-Win7\assets\guides
goto :success

:failed
echo Build failed. Release is incomplete.
endlocal & exit /b 1

:success
endlocal & exit /b 0
