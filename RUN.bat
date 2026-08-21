@echo off
REM MapHarvest launcher.
REM
REM Prefers the bundled venv, because that is where the app's dependencies live
REM and a system Python will start and then fail on the first import. Falls back
REM to whatever "python" is on PATH and says so, rather than exiting silently on
REM a machine where the venv was never created.

setlocal
cd /d "%~dp0"

set "PYEXE=%~dp0venv\Scripts\pythonw.exe"
set "PYCON=%~dp0venv\Scripts\python.exe"

if exist "%PYEXE%" goto :launch

echo.
echo   The bundled environment was not found at:
echo     %~dp0venv\
echo.
echo   Create it once with:
echo     python -m venv venv
echo     venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
echo   Trying the system Python instead...
echo.
where pythonw.exe >nul 2>&1
if errorlevel 1 (
    echo   No Python found on PATH either. Install Python 3.11+ and try again.
    echo.
    pause
    exit /b 1
)
set "PYEXE=pythonw.exe"
set "PYCON=python.exe"

:launch
REM pythonw has no console, so a crash before the window opens would vanish.
REM Check the imports with the console build first and show the error if any.
"%PYCON%" -c "import PyQt5, selenium, lxml" 2>nul
if errorlevel 1 (
    echo.
    echo   A dependency is missing. Install them with:
    echo     venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    "%PYCON%" -c "import PyQt5, selenium, lxml"
    echo.
    pause
    exit /b 1
)

start "" "%PYEXE%" "%~dp0main.py"
endlocal
