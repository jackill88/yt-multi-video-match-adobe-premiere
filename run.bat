@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo Python is not on PATH. Install Python 3.11+ and try again.
  pause
  exit /b 1
)

python -c "import customtkinter, numpy, scipy, librosa, soundfile" >nul 2>&1
if errorlevel 1 (
  echo Installing dependencies...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Failed to install dependencies.
    pause
    exit /b 1
  )
)

python -m app.main
if errorlevel 1 pause
endlocal
