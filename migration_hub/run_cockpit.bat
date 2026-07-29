@echo off
rem ============================================================
rem  Production Cockpit launcher
rem  Installs Python (if missing), requirements and starts
rem  Streamlit. Copy the repo anywhere and double-click.
rem ============================================================
setlocal
cd /d "%~dp0"

set "PYTHON=python"
where python >nul 2>nul
if not errorlevel 1 goto :have_python

rem --- no Python on PATH: check the default per-user install location
for /d %%D in ("%LocalAppData%\Programs\Python\Python3*") do (
    if exist "%%D\python.exe" set "PYTHON=%%D\python.exe" & goto :have_python
)

echo [SETUP] Python not found - installing Python 3.12 (per-user, no admin needed)...
set "PY_SETUP=%TEMP%\python-3.12.10-amd64.exe"
curl -L -o "%PY_SETUP%" https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe
if errorlevel 1 (
    echo [ERROR] Download failed - check network/proxy, or install Python
    echo         manually from https://www.python.org/downloads/
    pause
    exit /b 1
)
"%PY_SETUP%" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
if errorlevel 1 (
    echo [ERROR] Python installation failed.
    pause
    exit /b 1
)
del "%PY_SETUP%" >nul 2>nul
rem PATH of this console is stale after install - use the full path
for /d %%D in ("%LocalAppData%\Programs\Python\Python3*") do (
    if exist "%%D\python.exe" set "PYTHON=%%D\python.exe"
)
if "%PYTHON%"=="python" (
    echo [ERROR] Python installed but not found - restart this script.
    pause
    exit /b 1
)
echo [SETUP] Python installed.

:have_python
rem --- create a local virtual environment on first run
if not exist ".venv\Scripts\python.exe" (
    echo [SETUP] Creating virtual environment...
    "%PYTHON%" -m venv .venv
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
