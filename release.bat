@echo off
REM Publish the current build as a GitHub release with the .7z attached.
REM
REM Prerequisites:
REM   - build.bat has been run (and the exe smoke-tested!)
REM   - gh CLI authenticated once: gh auth login
REM   - the version bump commit is pushed
setlocal
cd /d "%~dp0"

set "PY=.venv-build\Scripts\python.exe"
if not exist "%PY%" (
    echo [release] No build venv - run build.bat first.
    exit /b 1
)
"%PY%" -c "import grimoireassist; print(grimoireassist.__version__)" > "%TEMP%\ga_version.txt"
set /p VERSION=<"%TEMP%\ga_version.txt"
set "TAG=v%VERSION%"
set "ASSET=dist\GrimoireAssist-v%VERSION%-win64.7z"

if not exist "%ASSET%" (
    echo [release] %ASSET% not found - run build.bat first.
    exit /b 1
)
gh auth status >nul 2>&1 || (
    echo [release] gh CLI is not authenticated - run: gh auth login
    exit /b 1
)
gh release view "%TAG%" >nul 2>&1 && (
    echo [release] Release %TAG% already exists.
    echo [release] Bump __version__ in grimoireassist\__init__.py, rebuild, retry.
    exit /b 1
)

echo [release] Tagging %TAG% and pushing the tag...
git tag "%TAG%" 2>nul
git push origin "%TAG%" || goto :fail

echo [release] Creating GitHub release %TAG% (asset upload is ~1.5 GB, be patient)...
gh release create "%TAG%" "%ASSET%" --title "GrimoireAssist %TAG%" --generate-notes || goto :fail

echo.
echo [release] Published:
gh release view "%TAG%" --json url --jq .url
exit /b 0

:fail
echo [release] FAILED - see output above.
exit /b 1
