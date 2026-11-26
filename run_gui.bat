@echo off
setlocal
if not defined PRISM_GUI_PYTHON set "PRISM_GUI_PYTHON=%~dp0.venv-gui\Scripts\python.exe"
if not exist "%PRISM_GUI_PYTHON%" (
  echo GUI Python not found: "%PRISM_GUI_PYTHON%" 1>&2
  echo Follow the source environment setup in README.md first. 1>&2
  goto :failed
)
set "PYTHONPATH=%~dp0src"
set "PYTHONIOENCODING=utf-8"
"%PRISM_GUI_PYTHON%" -c "import sys, struct; sys.exit(0 if sys.version_info[:3] == (3,8,10) and struct.calcsize('P') == 8 else 'GUI requires CPython 3.8.10 x64')"
if errorlevel 1 goto :failed
"%PRISM_GUI_PYTHON%" "%~dp0prism_launcher.py" %*
if errorlevel 1 goto :failed
exit /b 0

:failed
pause 1>&2
exit /b 1
