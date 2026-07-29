@echo off
rem ============================================================
rem  Production Cockpit launcher
rem  Installs requirements (first run) and starts Streamlit.
rem  Just copy the repo anywhere and double-click this file.
rem ============================================================
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found on PATH. Install Python 3.10+ first.
    pause
    exit /b 1
)

rem --- create a local virtual environment on first run
if not exist ".venv\Scripts\python.exe" (
    echo [SETUP] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create the virtual environment.
        pause
        exit /b 1
    )
)

echo [SETUP] Installing/updating requirements...
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo [ERROR] pip install failed - check your network/proxy and try again.
    pause
    exit /b 1
)

echo [RUN] Starting Production Cockpit at http://localhost:8501 ...
rem headless skips the first-run e-mail prompt; open the browser ourselves
start "" http://localhost:8501
".venv\Scripts\python.exe" -m streamlit run app.py --server.headless true
pause
