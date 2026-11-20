@echo off
setlocal
cd /d "%~dp0"
title ekagra setup
echo.
echo  === ekagra setup (one time) ===
echo.

set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo  Python 3.9 or newer is needed. Install it from https://www.python.org/downloads/
  echo  ^(tick "Add python.exe to PATH" during install^), then run this file again.
  pause
  exit /b 1
)

echo  [1/4] Creating private Python environment...
if not exist .venv %PY% -m venv .venv || goto :fail

echo  [2/4] Installing libraries (a minute or two)...
.venv\Scripts\python -m pip install --quiet --upgrade pip
.venv\Scripts\python -m pip install --quiet -r requirements.txt || goto :fail

echo  [3/4] Installing the app-window component...
.venv\Scripts\python -m pip install --quiet "pywebview>=5" || echo        (skipped - the app will open in an Edge app window instead)
.venv\Scripts\python -m pip install --quiet pywin32 || echo        (skipped - taskbar pinning may show the Python icon)

if not exist .env copy /y .env.example .env >nul

echo  [4/4] Creating Desktop and Start Menu shortcuts (with the ekagra icon)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0make_shortcut.ps1" || goto :fail

echo.
echo  Done! Double-click "ekagra" on your Desktop.
echo  The first time it opens, it will ask for your API keys.
echo.
pause
exit /b 0

:fail
echo.
echo  Setup failed - see the messages above.
pause
exit /b 1
