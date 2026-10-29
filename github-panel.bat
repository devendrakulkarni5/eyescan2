@echo off
setlocal EnableDelayedExpansion
title GitHub Developer Control Center
color 0A

:: =============================================================
:: CONFIGURATION
:: =============================================================
set PROJECT=wxSat Globe
set BRANCH=main
set REPO=https://github.com/devendrakulkarni5/globe
set LOGFILE=deploy.log

:: =============================================================
:: MAIN MENU
:: =============================================================
:MENU
cls

for /f "tokens=*" %%i in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set CURBRANCH=%%i
if "%CURBRANCH%"=="" set CURBRANCH=Not a Git Repository

for /f "tokens=*" %%i in ('git rev-parse --short HEAD 2^>nul') do set LASTHASH=%%i

echo ============================================================
echo             GitHub Developer Control Center
echo ============================================================
echo.
echo   Project : %PROJECT%
echo   Branch  : %CURBRANCH%
echo   Commit  : %LASTHASH%
echo   Folder  : %CD%
echo.

echo ------------------------------------------------------------
git status --short
echo ------------------------------------------------------------
echo.

echo  [1] Push Current Project
echo  [2] Git Status
echo  [3] Commit History
echo  [4] Pull Latest Changes
echo  [5] Open GitHub Repository
echo  [6] Create Version Tag
echo  [7] Fetch Latest
echo  [8] Clean Untracked Files
echo  [9] Show Branches
echo  [A] Change Branch
echo  [B] Backup Current Branch
echo  [C] Repository Information
echo  [D] Open Project Folder
echo  [F] Force Push (Overwrite Remote)
echo  [E] Exit
echo.

set /p CHOICE=Select Option: 

if /I "%CHOICE%"=="1" goto PUSH
if /I "%CHOICE%"=="2" goto STATUS
if /I "%CHOICE%"=="3" goto HISTORY
if /I "%CHOICE%"=="4" goto PULL
if /I "%CHOICE%"=="5" goto OPENREPO
if /I "%CHOICE%"=="6" goto TAG
if /I "%CHOICE%"=="7" goto FETCH
if /I "%CHOICE%"=="8" goto CLEAN
if /I "%CHOICE%"=="9" goto BRANCHES
if /I "%CHOICE%"=="A" goto SWITCH
if /I "%CHOICE%"=="B" goto BACKUP
if /I "%CHOICE%"=="C" goto INFO
if /I "%CHOICE%"=="D" goto FOLDER
if /I "%CHOICE%"=="F" goto FORCEPUSH
if /I "%CHOICE%"=="E" exit

goto MENU


:PUSH
cls
echo.
echo Staging Files...
git add .

git diff --cached --quiet
if %errorlevel%==0 (
    echo.
    echo No changes found.
    pause
    goto MENU
)

echo.
set /p MSG=Commit Message (ENTER = Auto Update): 

if "%MSG%"=="" (
    for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format \"dd-MM-yyyy hh:mm tt\""') do set MSG=Auto Update - %%i IST
)

echo.
git commit -m "%MSG%"

if errorlevel 1 (
    pause
    goto MENU
)

git push origin %BRANCH%

echo [%DATE% %TIME%] %MSG%>>%LOGFILE%

echo.
echo Successfully Uploaded.
pause
goto MENU


:STATUS
cls
git status
pause
goto MENU


:HISTORY
cls
git log --graph --decorate --oneline --all -20
pause
goto MENU


:PULL
cls
git pull origin %BRANCH%
pause
goto MENU


:OPENREPO
start "" "%REPO%"
goto MENU


:TAG
cls
set /p TAG=Version Tag (Example v1.2.0): 

if "%TAG%"=="" goto MENU

git tag %TAG%
git push origin %TAG%

pause
goto MENU


:FETCH
cls
git fetch --all
pause
goto MENU


:CLEAN
cls
echo WARNING!
echo This removes ALL untracked files.
echo.

set /p CONFIRM=Continue? (Y/N): 

if /I "%CONFIRM%"=="Y" (
    git clean -fd
)

pause
goto MENU


:BRANCHES
cls
git branch -a
pause
goto MENU


:SWITCH
cls
git branch

echo.
set /p NEWBRANCH=Branch Name: 

if "%NEWBRANCH%"=="" goto MENU

git checkout %NEWBRANCH%
pause
goto MENU


:BACKUP
cls

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set NOW=%%i

set BACKUP=backup_%NOW%

git checkout -b %BACKUP%

echo Created:
echo %BACKUP%

pause
goto MENU


:INFO
cls

echo Repository Information
echo.
git remote -v
echo.
git branch
echo.
git config user.name
git config user.email
echo.
git log -1
echo.
echo ------------------------------------------------------------
echo   TIP: Use 'restore.bat' to safely restore from backups.
echo   It will NEVER overwrite your .git directory.
echo ------------------------------------------------------------

pause
goto MENU


:FORCEPUSH
cls
echo ============================================================
echo            WARNING: FORCE PUSH
echo ============================================================
echo.
echo This will OVERWRITE the remote branch '%BRANCH%'
echo with your local code. Remote changes will be LOST.
echo.
echo Current local commit:
git log --oneline -1
echo.

set /p CONFIRM=Type YES to force push: 
if /I not "%CONFIRM%"=="YES" (
    echo.
    echo Force push cancelled.
    pause
    goto MENU
)

echo.
echo Staging all files...
git add .

git diff --cached --quiet
if %errorlevel%==0 (
    set /p MSG=No staged changes. Commit message (ENTER = skip commit):
    if "!MSG!"=="" goto FORCEPUSH_DO
)

echo.
set /p MSG=Commit Message (ENTER = Force Update): 

if "%MSG%"=="" (
    for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format \"dd-MM-yyyy hh:mm tt\""') do set MSG=Force Update - %%i IST
)

echo.
git commit -m "%MSG%"

:FORCEPUSH_DO
echo.
echo Force pushing to origin %BRANCH%...
git push --force origin %BRANCH%

if errorlevel 1 (
    echo.
    echo FORCE PUSH FAILED!
    pause
    goto MENU
)

echo [%DATE% %TIME%] FORCE PUSH: %MSG%>>%LOGFILE%

echo.
echo Force push successful!
pause
goto MENU


:FOLDER
start .
goto MENU