@echo off
setlocal
if not defined PRISM_AGENT_PYTHON set "PRISM_AGENT_PYTHON=%~dp0.venv-agent\Scripts\python.exe"
if not exist "%PRISM_AGENT_PYTHON%" (
  echo Agent Python not found: "%PRISM_AGENT_PYTHON%" 1>&2
  echo Run setup_agent.bat first. 1>&2
  exit /b 1
)
set "PYTHONPATH=%~dp0src"
set "PYTHONIOENCODING=utf-8"
"%PRISM_AGENT_PYTHON%" -c "import sys, struct; sys.exit(0 if sys.version_info[:3] == (3,12,13) and struct.calcsize('P') == 8 else 'Agent requires CPython 3.12.13 x64')"
if errorlevel 1 exit /b 1
"%PRISM_AGENT_PYTHON%" "%~dp0agent_cli_launcher.py" %*
exit /b %errorlevel%
