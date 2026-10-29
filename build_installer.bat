@echo off
cd /d "%~dp0"
if not exist "dist\EyeScan2\Keeper.exe" (
    echo Run build_exe.bat first.
    pause & exit /b 1
)
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
    echo Inno Setup 6 not found. Install it with:
    echo     winget install JRSoftware.InnoSetup
    echo ...or zip dist\EyeScan2 instead.
    pause & exit /b 1
)
"%ISCC%" installer.iss || (pause & exit /b 1)
for /f %%v in ('".venv\Scripts\python.exe" tools\release.py --version-only') do set "VER=%%v"
echo.
echo Installer: installer\Keeper-Setup-%VER%.exe
echo To publish it as an update: publish_release.bat "what changed"
pause
