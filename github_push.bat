@echo off
setlocal EnableDelayedExpansion
title GitHub Deployment Tool
color 0A

:: =====================================================
:: Configuration
:: =====================================================
set PROJECT=Keeper
set BRANCH=main
set LOGFILE=deploy.log

:: =====================================================
:: Header
:: =====================================================
cls
echo.
echo ==========================================================
echo            GitHub Deployment Utility
echo ==========================================================
echo.
echo   Project : %PROJECT%
echo   Branch  : %BRANCH%
echo   Folder  : %CD%
echo.

:: =====================================================
:: Check Git
:: =====================================================
where git >nul 2>&1
if errorlevel 1 (
    color 0C
    echo ERROR: Git is not installed or not available in PATH.
    echo.
    pause
    exit /b
)

:: =====================================================
:: Internet Check
:: =====================================================
echo Checking internet connection...
ping -n 1 github.com >nul

if errorlevel 1 (
    color 0C
    echo.
    echo ERROR: Internet connection unavailable.
    echo.
    pause
    exit /b
)

echo OK
echo.

:: =====================================================
:: Current Status
:: =====================================================
echo Current Repository Status
echo ----------------------------------------------------------
git status --short
echo ----------------------------------------------------------
echo.

:: =====================================================
:: Stage Changes
:: =====================================================
echo Staging files...
git add .

git diff --cached --quiet
if %errorlevel%==0 (
    color 0E
    echo.
    echo No changes detected.
    echo.
    pause
    exit /b
)

echo Done.
echo.

:: =====================================================
:: Commit Message
:: =====================================================
set /p MESSAGE=Commit Message (Press ENTER for Auto Update): 

if "%MESSAGE%"=="" (
    for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format \"dd-MM-yyyy hh:mm tt\""') do set MESSAGE=Auto Update - %%i IST
)

echo.
echo Commit:
echo %MESSAGE%
echo.

:: =====================================================
:: Commit
:: =====================================================
git commit -m "%MESSAGE%"

if errorlevel 1 (
    color 0C
    echo.
    echo Commit failed.
    pause
    exit /b
)

:: =====================================================
:: Push
:: =====================================================
echo.
echo Uploading to GitHub...
git push origin %BRANCH%

if errorlevel 1 (
    color 0C
    echo.
    echo Push failed.
    pause
    exit /b
)

:: =====================================================
:: Log Deployment
:: =====================================================
echo [%DATE% %TIME%] %MESSAGE%>>%LOGFILE%

:: =====================================================
:: Success
:: =====================================================
color 0A

echo.
echo ==========================================================
echo               Deployment Successful
echo ==========================================================
echo.
echo Project    : %PROJECT%
echo Branch     : %BRANCH%
echo Status     : Uploaded to GitHub
echo Log File   : %LOGFILE%
echo.

git log -1 --oneline

echo.
echo ==========================================================
pause