@echo off
REM Single entry point for GrimoireAssist.
REM  - First run (or after the venv is deleted): creates the venv and installs deps.
REM  - Every run: launches the app windowless (no lingering console) and exits.
REM This is safe to use directly OR via the desktop/taskbar shortcut, and it
REM rebuilds the environment automatically if it's ever missing (e.g. fresh setup).
setlocal
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"
set "VENV_PYW=.venv\Scripts\pythonw.exe"

if not exist "%VENV_PY%" (
    echo [GrimoireAssist] First-time setup: creating the virtual environment...
    py -3.12 -m venv .venv || python -m venv .venv
    "%VENV_PY%" -m pip install --upgrade pip
    "%VENV_PY%" -m pip install -r requirements.txt
)

REM Prefer pythonw (no console window); fall back to python if it's missing.
if exist "%VENV_PYW%" (
    start "" "%VENV_PYW%" -m grimoireassist %*
) else (
    start "" "%VENV_PY%" -m grimoireassist %*
)

endlocal
