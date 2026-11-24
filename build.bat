@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PY=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"

where python >nul 2>&1
if errorlevel 1 (
  if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Python is not on PATH. Install Python 3.11+ and try again.
    exit /b 1
  )
)

echo Using: %PY%
echo.
echo Recommended: build from a clean venv ^(avoids pulling Anaconda extras^):
echo   python -m venv .venv
echo   .venv\Scripts\activate
echo   pip install -r requirements.txt -r requirements-build.txt
echo   build.bat
echo.

echo Installing runtime + build dependencies...
"%PY%" -m pip install -r requirements.txt -r requirements-build.txt
if errorlevel 1 (
  echo Failed to install dependencies.
  exit /b 1
)

echo Cleaning previous build output...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo Running PyInstaller...
"%PY%" -m PyInstaller --noconfirm --clean concert_sync.spec
if errorlevel 1 (
  echo Build failed.
  exit /b 1
)

if not exist "dist\ConcertSync\ConcertSync.exe" (
  echo Build finished but ConcertSync.exe was not found.
  exit /b 1
)

echo.
echo Build OK.
echo Output: dist\ConcertSync\ConcertSync.exe
echo Keep the whole dist\ConcertSync folder together when distributing.
echo Note: ffmpeg and ffprobe must still be on PATH.
echo.
endlocal
