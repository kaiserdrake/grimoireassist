@echo off
REM Build the portable distribution:
REM   dist\GrimoireAssist\GrimoireAssist.exe  (one-folder PyInstaller build)
REM   dist\GrimoireAssist-v<version>-win64.7z (what you actually distribute)
REM
REM 7z (LZMA2), not zip: deflate leaves the archive at ~2.5 GiB, over GitHub's
REM 2 GiB release-asset limit; LZMA2 lands at ~1.5 GiB. Requires 7-Zip.
REM
REM Always builds from a dedicated clean venv (.venv-build) so stray dev
REM packages never end up in the bundle. Installs CUDA torch BEFORE
REM requirements.txt so easyocr doesn't pull the CPU wheel: one build serves
REM everyone: NVIDIA machines use the GPU automatically (ocr.gpu: auto),
REM everything else silently falls back to CPU.
setlocal
cd /d "%~dp0"

set "VENV=.venv-build"
set "PY=%VENV%\Scripts\python.exe"

REM Optional version argument: `build.bat 1.2.3` stamps __version__ into
REM grimoireassist\__init__.py (the single source of truth) before building, so
REM the exe and the archive name both carry that version. With no argument the
REM current __version__ is used unchanged. The bump is NOT auto-committed -
REM commit it yourself before releasing.
set "NEWVER=%~1"
if defined NEWVER (
    echo %NEWVER%|findstr /r "^[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*$" >nul || (
        echo [build] Invalid version "%NEWVER%" - expected X.Y.Z, e.g. 1.0.2
        exit /b 1
    )
)

if not exist "%PY%" (
    echo [build] Creating build venv...
    py -3.12 -m venv %VENV% || python -m venv %VENV%
)

if defined NEWVER (
    echo [build] Stamping __version__ = %NEWVER% into grimoireassist\__init__.py ...
    "%PY%" -c "import re,sys; f='grimoireassist/__init__.py'; s=open(f,encoding='utf-8-sig').read(); q=chr(34); n,c=re.subn('(?m)^__version__ = .*$', '__version__ = '+q+'%NEWVER%'+q, s); open(f,'w',encoding='utf-8').write(n) if c else sys.exit('[build] __version__ line not found in '+f)" || goto :fail
    echo [build] Version set. Remember to commit the bump before release.bat.
)

echo [build] Installing dependencies...
"%PY%" -m pip install --upgrade pip || goto :fail
"%PY%" -m pip install torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124 || goto :fail
"%PY%" -m pip install -r requirements.txt || goto :fail
"%PY%" -m pip install "pyinstaller>=6.11,<7" || goto :fail

echo [build] Running PyInstaller...
"%PY%" -m PyInstaller GrimoireAssist.spec --noconfirm || goto :fail

"%PY%" -c "import grimoireassist; print(grimoireassist.__version__)" > "%TEMP%\ga_version.txt"
set /p VERSION=<"%TEMP%\ga_version.txt"
set "ARCHIVE=dist\GrimoireAssist-v%VERSION%-win64.7z"
set "SEVENZIP=%ProgramFiles%\7-Zip\7z.exe"

if not exist "%SEVENZIP%" (
    echo [build] 7-Zip not found at "%SEVENZIP%" - install it from https://www.7-zip.org/
    goto :fail
)
echo [build] Packing %ARCHIVE% ...
if exist "%ARCHIVE%" del "%ARCHIVE%"
"%SEVENZIP%" a -t7z -mx=7 -md=64m -mmt=on "%ARCHIVE%" .\dist\GrimoireAssist || goto :fail

echo.
echo [build] Done.
echo [build]   run:        dist\GrimoireAssist\GrimoireAssist.exe
echo [build]   distribute: %ARCHIVE%
echo [build] Smoke-test the exe (ideally on a machine without Python), then
echo [build] publish with release.bat.
exit /b 0

:fail
echo [build] FAILED - see output above.
exit /b 1
