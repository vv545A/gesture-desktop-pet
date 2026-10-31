@echo off
rem ============================================================
rem  Gesture desktop pet - one-click launcher
rem  Pure ASCII script; project path resolved at runtime via %~dp0
rem
rem  Usage:
rem    run_pet.bat              normal (camera + pet)
rem    run_pet.bat --demo       no camera, simulated hands
rem    run_pet.bat --dry-run    recognize only, never send keys
rem
rem  Interpreter lookup: .venv here -> .venv in parent -> CV_PYTHON -> PATH
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

set "PY="
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY if exist "%~dp0..\.venv\Scripts\python.exe" set "PY=%~dp0..\.venv\Scripts\python.exe"
if not defined PY if defined CV_PYTHON set "PY=%CV_PYTHON%"
if not defined PY (
    where python >nul 2>&1
    if not errorlevel 1 set "PY=python"
)

if not defined PY (
    echo [error] Python not found.
    echo   Install Python 3.11 - 3.13 from https://www.python.org/downloads/
    pause
    exit /b 1
)

rem Dependency pre-check: fail fast with a readable message instead of a traceback
"%PY%" -c "import PyQt6, mediapipe, cv2, numpy" >nul 2>&1
if errorlevel 1 (
    echo [error] Interpreter lacks required packages:
    echo     %PY%
    echo.
    echo   Install them with:  "%PY%" -m pip install -r requirements.txt
    echo   ^(PyQt6 is the UI layer; mediapipe/opencv are the perception layer^)
    pause
    exit /b 1
)

if "%~1"=="" (
    echo Starting desktop pet...  ^(Ctrl+C to quit^)
    echo   tip: "run_pet.bat --demo" shows the pet without a camera
)

"%PY%" -m pet %*
if errorlevel 1 pause
