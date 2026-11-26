@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
  echo uv is required to create the locked Agent environment. 1>&2
  exit /b 1
)
for /f "tokens=2" %%V in ('uv --version') do set "PRISM_UV_VERSION=%%V"
if not "%PRISM_UV_VERSION%"=="0.11.7" (
  echo Agent setup requires uv 0.11.7. 1>&2
  exit /b 1
)
if not exist ".venv-agent\Scripts\python.exe" (
  uv venv --python 3.12.13 --seed .venv-agent
  if errorlevel 1 exit /b 1
)
".venv-agent\Scripts\python.exe" -c "import sys, struct; assert sys.version_info[:3] == (3,12,13) and struct.calcsize('P') == 8, 'Agent requires CPython 3.12.13 x64'"
if errorlevel 1 exit /b 1
REM Remove packages outside the packager lock, including test-only dependencies.
uv pip sync --python ".venv-agent\Scripts\python.exe" requirements-agent-build.txt
if errorlevel 1 exit /b 1
uv pip install --python ".venv-agent\Scripts\python.exe" --no-build-isolation --no-deps -e .
if errorlevel 1 exit /b 1
".venv-agent\Scripts\python.exe" scripts\verify_agent_environment.py
if errorlevel 1 exit /b 1
".venv-agent\Scripts\python.exe" -m pip check
exit /b %errorlevel%
