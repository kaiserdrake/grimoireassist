@echo off
REM Publish the latest existing build as a GitHub release with the .7z attached.
REM
REM This does NOT rebuild: it releases whatever build.bat last produced in
REM dist\. The version and tag are derived from the newest
REM GrimoireAssist-v*-win64.7z filename, so a fresh build can be released
REM straight away without re-running build.bat after a version bump.
REM
REM Prerequisites:
REM   - build.bat has been run (and the exe smoke-tested!)
REM   - gh CLI authenticated once: gh auth login
REM   - the version bump commit is pushed
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM Pick the most recently built archive (dir /o-d = by date, newest first).
set "ASSET="
set "STEM="
for /f "delims=" %%F in ('dir /b /o-d "dist\GrimoireAssist-v*-win64.7z" 2^>nul') do (
    if not defined ASSET (
        set "ASSET=dist\%%F"
        set "STEM=%%~nF"
    )
)
if not defined ASSET (
    echo [release] No build found in dist\ - run build.bat first.
    exit /b 1
)

REM Derive the version from the filename: GrimoireAssist-v<version>-win64(.7z)
set "VERSION=!STEM:GrimoireAssist-v=!"
set "VERSION=!VERSION:-win64=!"
set "TAG=v!VERSION!"

echo [release] Releasing !TAG! from existing build: !ASSET!

gh auth status >nul 2>&1 || (
    echo [release] gh CLI is not authenticated - run: gh auth login
    exit /b 1
)
gh release view "!TAG!" >nul 2>&1 && (
    echo [release] Release !TAG! already exists.
    echo [release] Bump __version__ in grimoireassist\__init__.py, rebuild, retry.
    exit /b 1
)

echo [release] Tagging !TAG! and pushing the tag...
git tag "!TAG!" 2>nul
git push origin "!TAG!" || goto :fail

echo [release] Creating GitHub release !TAG! (asset upload is ~1.5 GB, be patient)...
gh release create "!TAG!" "!ASSET!" --title "GrimoireAssist !TAG!" --generate-notes || goto :fail

echo.
echo [release] Published:
gh release view "!TAG!" --json url --jq .url
exit /b 0

:fail
echo [release] FAILED - see output above.
exit /b 1
