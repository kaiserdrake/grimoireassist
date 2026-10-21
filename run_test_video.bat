@echo off
REM Launch GrimoireAssist against the synthetic test clip instead of a camera.
REM  - Builds samples\terms_test.mp4 with tools\make_terms_video.py if it is missing
REM    (about a minute). Delete the file to regenerate it after editing the script.
REM  - Then starts the app through run.bat with --video, so the clip loops as input.
REM Region coordinates for F9 are listed at the top of tools\make_terms_video.py.
REM Extra arguments are passed through, e.g.: run_test_video.bat --some-flag
setlocal
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"
set "CLIP=samples\terms_test.mp4"

if not exist "%VENV_PY%" (
    echo [test video] No virtual environment yet - run run.bat once first.
    exit /b 1
)

if not exist "%CLIP%" (
    echo [test video] Building %CLIP% ...
    "%VENV_PY%" tools\make_terms_video.py || (
        echo [test video] Could not build the clip - see the error above.
        exit /b 1
    )
)

call "%~dp0run.bat" --video "%CLIP%" %*
endlocal
