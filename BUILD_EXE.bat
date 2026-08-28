@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title LeadForge - Build EXE
color 0A

echo(
echo ==================================================
echo            LeadForge  -  Build Windows EXE
echo ==================================================
echo(

REM ---------------------------------------------------------------- version --
REM Accept the version as an argument (BUILD_EXE.bat 1.2.3) or ask for it.
set "VERSION=%~1"
if "!VERSION!"=="" (
    set /p "VERSION=Enter version [e.g. 1.0.0], blank = 1.0.0 : "
)
if "!VERSION!"=="" set "VERSION=1.0.0"

REM Strip a leading v / V so "v1.0.0" works too.
if /i "!VERSION:~0,1!"=="v" set "VERSION=!VERSION:~1!"

REM Validate x.y.z
echo !VERSION!| findstr /R /C:"^[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*$" >nul
if errorlevel 1 (
    echo(
    echo   [ERROR] Version must look like 1.0.0  ^(you entered: !VERSION!^)
    echo(
    pause
    exit /b 1
)

set "APPNAME=LeadForge"
set "OUTNAME=!APPNAME!-!VERSION!"

REM Optional 2nd arg "debug" -> build with a console window so startup errors
REM are visible instead of the app silently failing to appear.
set "WINARG=--windowed"
if /i "%~2"=="debug" (
    set "WINARG=--console"
    set "OUTNAME=!APPNAME!-!VERSION!-debug"
)

echo   Building version : !VERSION!
if /i "%~2"=="debug" echo   Mode             : DEBUG ^(console window shown^)
echo   Output file      : releases\!OUTNAME!.exe
echo(

REM ----------------------------------------------------------------- python --
set "PY=%~dp0venv\Scripts\python.exe"
if not exist "!PY!" (
    echo   [i] No venv found - using system Python.
    set "PY=python"
)
"!PY!" --version >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] Python not found. Install Python 3.10+ or create the venv:
    echo           python -m venv venv ^&^& venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)
for /f "delims=" %%v in ('"!PY!" --version 2^>^&1') do echo   Python           : %%v

REM ------------------------------------------------------------ dependencies --
echo(
echo   [1/5] Checking build dependencies...
"!PY!" -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo         Installing PyInstaller ^(one time^)...
    "!PY!" -m pip install --quiet --upgrade pyinstaller
    if errorlevel 1 (
        echo   [ERROR] Could not install PyInstaller.
        pause
        exit /b 1
    )
)
"!PY!" -c "import PyQt5, selenium, undetected_chromedriver, lxml" >nul 2>&1
if errorlevel 1 (
    echo         Installing app requirements ^(one time^)...
    "!PY!" -m pip install --quiet -r requirements.txt
    if errorlevel 1 (
        echo   [ERROR] Could not install requirements.
        pause
        exit /b 1
    )
)
echo         OK

REM ------------------------------------------------------------------- icon --
echo   [2/5] Preparing icon...
if not exist "app_icon.ico" (
    echo         Generating app_icon.ico...
    "!PY!" tools\make_icon.py
)
if not exist "%~dp0app_icon.ico" (
    echo   [WARN] Icon missing - building without a custom icon.
    set "ICONARG="
) else (
    set "ICONARG=--icon"
    set "ICONFILE=%~dp0app_icon.ico"
)
echo         OK

REM --------------------------------------------------------- version resource --
echo   [3/5] Writing version resource...
"!PY!" tools\gen_version_file.py !VERSION! "build\version_info.txt"
if errorlevel 1 (
    echo   [ERROR] Failed to write the version resource.
    pause
    exit /b 1
)
set "VERFILE=%~dp0build\version_info.txt"

REM ------------------------------------------------------------------ build --
echo   [4/5] Building EXE - this can take a few minutes, please wait...
echo(

if exist "releases\!OUTNAME!.exe" del /q "releases\!OUTNAME!.exe"

if defined ICONFILE (
    "!PY!" -m PyInstaller ^
        --noconfirm --clean --onefile !WINARG! ^
        --name "!OUTNAME!" ^
        --icon "!ICONFILE!" ^
        --version-file "!VERFILE!" ^
        --distpath "releases" ^
        --workpath "build\work" ^
        --specpath "build" ^
        --collect-all undetected_chromedriver ^
        --collect-all selenium ^
        --hidden-import lxml.etree ^
        --hidden-import lxml._elementpath ^
        --exclude-module tkinter ^
        --exclude-module matplotlib ^
        --exclude-module numpy ^
        main.py
) else (
    "!PY!" -m PyInstaller ^
        --noconfirm --clean --onefile !WINARG! ^
        --name "!OUTNAME!" ^
        --version-file "!VERFILE!" ^
        --distpath "releases" ^
        --workpath "build\work" ^
        --specpath "build" ^
        --collect-all undetected_chromedriver ^
        --collect-all selenium ^
        --hidden-import lxml.etree ^
        --hidden-import lxml._elementpath ^
        --exclude-module tkinter ^
        --exclude-module matplotlib ^
        --exclude-module numpy ^
        main.py
)

if errorlevel 1 goto :buildfailed
if not exist "releases\!OUTNAME!.exe" goto :buildfailed

REM ------------------------------------------------------------------ done --
echo(
echo   [5/5] Done.
echo(
echo ==================================================
echo    BUILD SUCCESSFUL
echo ==================================================
for %%A in ("releases\!OUTNAME!.exe") do set "SIZE=%%~zA"
set /a SIZEMB=!SIZE! / 1048576
echo   File    : %~dp0releases\!OUTNAME!.exe
echo   Version : !VERSION!
echo   Size    : !SIZEMB! MB
echo(
echo   This is a single self-contained EXE - it needs no Python
echo   installed. Google Chrome must be installed on the machine.
echo(
choice /C YN /N /M "   Open the releases folder now? [Y/N] "
if !errorlevel!==1 explorer "%~dp0releases"
echo(
pause
exit /b 0

:buildfailed
echo(
echo ==================================================
echo    BUILD FAILED
echo ==================================================
echo   Scroll up for the PyInstaller error.
echo(
echo   Tips:
echo     * Close the app / any running LeadForge EXE and retry.
echo     * Your antivirus may block PyInstaller - allow it and retry.
echo     * To see startup errors in a console window, build a debug
echo       copy with:   BUILD_EXE.bat !VERSION! debug
echo(
pause
exit /b 1
