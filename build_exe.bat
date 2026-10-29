@echo off
setlocal
cd /d "%~dp0"
title Build EyeScan2.exe
echo ============================================
echo  Building EyeScan2.exe  (this takes a few minutes)
echo ============================================
if not exist ".venv\Scripts\python.exe" (
    py -3.13 -m venv .venv 2>nul
    if not exist ".venv\Scripts\python.exe" python -m venv .venv
    if not exist ".venv\Scripts\python.exe" ( echo Could not create .venv - install Python 3.13. & goto :err )
)
echo [1/4] Installing dependencies...
".venv\Scripts\python.exe" -m pip install -q -r requirements-build.txt || goto :err
echo [2/4] Running self-tests...
".venv\Scripts\python.exe" tests\selftest.py > "%TEMP%\eyescan2_selftest.txt" 2>&1
if errorlevel 1 ( type "%TEMP%\eyescan2_selftest.txt" & echo *** self-tests failed - not building *** & goto :err )
echo [3/4] Running PyInstaller...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean EyeScan2.spec || goto :err
echo [4/4] Verifying the built exe...
"dist\EyeScan2\EyeScan2.exe" --doctor "dist\EyeScan2\doctor.txt"
timeout /t 3 /nobreak >nul
findstr /C:"MISSING" /C:"NOT FOUND" "dist\EyeScan2\doctor.txt" >nul
if not errorlevel 1 ( type "dist\EyeScan2\doctor.txt" & echo *** the built exe is missing a component *** & goto :err )
del "dist\EyeScan2\doctor.txt" >nul 2>&1
echo.
echo SUCCESS - your app: dist\EyeScan2\EyeScan2.exe  (zip the whole dist\EyeScan2 folder to share it)
pause
goto :eof
:err
echo.
echo *** BUILD FAILED ***
pause
exit /b 1
