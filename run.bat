@echo off
REM Convenience launcher: creates a venv on first run, installs deps, starts the app.
setlocal
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo [GrimoireAssist] Creating virtual environment...
    py -3.12 -m venv .venv || python -m venv .venv
    "%VENV_PY%" -m pip install --upgrade pip
    "%VENV_PY%" -m pip install -r requirements.txt
)

REM Call the venv's python directly so we never fall back to a global interpreter.
"%VENV_PY%" -m grimoireassist %*
endlocal
