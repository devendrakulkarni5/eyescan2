@echo off
setlocal EnableDelayedExpansion
title Safe Backup Restore - Folders + ZIP - .git Protected
color 0E

:: =============================================================
:: CONFIGURATION (auto-detected, works in any project folder)
:: Handles BOTH backup folders (create_backup*.bat) and
:: backup .zip files (create_backup_zip.bat / *_all_zip.bat).
:: =============================================================
for %%I in ("%CD%") do set PROJECT=%%~nxI
set LOGFILE=deploy.log
set MAX_DIRS=20

:: =============================================================
:: MAIN MENU
:: =============================================================
:MENU
cls
echo ============================================================
echo       Safe Backup Restore - Folders + ZIP - .git Protected
echo ============================================================
echo.
echo   Project : %PROJECT%
echo   Folder  : %CD%
echo.
echo ------------------------------------------------------------
echo   IMPORTANT: This script will NEVER overwrite your .git
echo   directory. Your repository history and remote links
echo   are always safe.
echo ------------------------------------------------------------
echo.
echo  [1] Pick from Recent Backups  - folders and .zip
echo  [2] Enter Backup Path Manually
echo  [3] Exit
echo.

set /p CHOICE=Select Option: 

if /I "%CHOICE%"=="1" goto PICKBACKUP
if /I "%CHOICE%"=="2" goto MANUALENTER
if /I "%CHOICE%"=="3" exit

goto MENU


:: =============================================================
:: PICK FROM RECENT BACKUPS (folders + .zip, latest %MAX_DIRS%)
:: =============================================================
:PICKBACKUP
cls
echo ============================================================
echo       Recent Backups - Folders and ZIP (Latest %MAX_DIRS%)
echo ============================================================
echo.
echo   #   Created                    Backup
echo   ---- -------------------------  --------------------------------

:: Collect from backups\ (folders + zips) and current folder
:: (backup* folders, backup*.zip files) using PowerShell
set DIRCOUNT=0

for /f "tokens=1,2 delims=|" %%A in ('powershell -NoProfile -Command "$items=@(); if(Test-Path backups){ Get-ChildItem -Path backups -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^backup' -and ($_.PSIsContainer -or $_.Extension -eq '.zip') } | ForEach-Object { $items += [PSCustomObject]@{Name='backups\'+$_.Name; SortKey=$_.CreationTime; Display=$_.CreationTime.ToString('yyyy-MM-dd  h:mm:ss tt')} } }; Get-ChildItem -Path . -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^backup' -and $_.Name -ne 'backups' } | ForEach-Object { $items += [PSCustomObject]@{Name=$_.Name; SortKey=$_.CreationTime; Display=$_.CreationTime.ToString('yyyy-MM-dd  h:mm:ss tt')} }; Get-ChildItem -Path . -File -Filter 'backup*.zip' -ErrorAction SilentlyContinue | ForEach-Object { $items += [PSCustomObject]@{Name=$_.Name; SortKey=$_.CreationTime; Display=$_.CreationTime.ToString('yyyy-MM-dd  h:mm:ss tt')} }; $items | Sort-Object SortKey -Descending | Select-Object -First %MAX_DIRS% | ForEach-Object { Write-Host ($_.Display.PadRight(27) + '|' + $_.Name) }"') do (
    if !DIRCOUNT! lss %MAX_DIRS% (
        set /a DIRCOUNT+=1
        set "DIR_!DIRCOUNT!=%%B"
        set "NUM= !DIRCOUNT!"
        set "NUM=!NUM:~-2!"
        echo   [!NUM!]  %%A  %%B
    )
)

if !DIRCOUNT!==0 (
    echo   No backups found.
    echo.
    echo   Create backups using create_backup_zip.bat or
    echo   create_backup_all_zip.bat, or copy backups into the
    echo   'backups\' subdirectory.
    echo.
    pause
    goto MENU
)

echo.
echo   [M]  Enter path manually
echo   [0]  Back to menu
echo.
echo ------------------------------------------------------------
set /p SEL=Select backup (1-%DIRCOUNT% or M): 

if /I "%SEL%"=="0" goto MENU
if /I "%SEL%"=="M" goto MANUALENTER

:: Validate selection
if not defined DIR_%SEL% (
    echo.
    echo   Invalid selection.
    pause
    goto PICKBACKUP
)

set "BACKUPPATH=!DIR_%SEL%!"

:: Resolve to absolute path (works for folders and .zip files)
for %%F in ("!BACKUPPATH!") do set "BACKUPPATH=%%~fF"

goto SAFECHECK


:: =============================================================
:: MANUAL PATH ENTRY
:: =============================================================
:MANUALENTER
cls
echo ============================================================
echo         Enter Backup Path
echo ============================================================
echo.
echo   You can enter a full path or a relative path.
echo   A backup folder or a .zip file both work.
echo   Examples:
echo     backups\backup_20260715_120000
echo     backups\backup_26-09-2026_11-30-05.zip
echo     D:\Old PC\globe-backup
echo.

set /p BACKUPPATH=Backup folder or zip path: 

if "%BACKUPPATH%"=="" (
    echo   No path entered.
    pause
    goto MENU
)

if not exist "%BACKUPPATH%" (
    echo.
    echo   ERROR: Not found: %BACKUPPATH%
    pause
    goto MANUALENTER
)

:: Must be a folder or a .zip file
for %%F in ("%BACKUPPATH%") do set "SRC_EXT=%%~xF"
if /I not "!SRC_EXT!"==".zip" (
    if not exist "%BACKUPPATH%\" (
        echo.
        echo   ERROR: Source is not a folder and not a .zip file.
        pause
        goto MANUALENTER
    )
)

:: Resolve to absolute path
pushd "%BACKUPPATH%" 2>nul
if not errorlevel 1 (
    set "BACKUPPATH=!CD!"
    popd
) else (
    for %%F in ("%BACKUPPATH%") do set "BACKUPPATH=%%~fF"
)

goto SAFECHECK


:: =============================================================
:: SAFETY CHECK & PREVIEW
:: =============================================================
:SAFECHECK
cls
echo ============================================================
echo         SAFETY CHECK
echo ============================================================
echo.

:: Detect ZIP vs folder source
set "IS_ZIP=0"
for %%F in ("%BACKUPPATH%") do if /I "%%~xF"==".zip" set "IS_ZIP=1"

set FILECOUNT=0
if "!IS_ZIP!"=="1" (
    echo   Source : %BACKUPPATH%  [ZIP archive]
    echo.
    echo   Verifying archive integrity...
    tar.exe -tf "%BACKUPPATH%" >nul 2>&1
    if errorlevel 1 (
        echo.
        echo   ERROR: ZIP is corrupt or unreadable. Aborting.
        pause
        goto MENU
    )
    for /f %%C in ('tar.exe -tf "%BACKUPPATH%" ^| find /c /v ""') do set FILECOUNT=%%C
    echo   OK - !FILECOUNT! entries verified.
) else (
    echo   Source : %BACKUPPATH%  [Folder]
    for /f %%F in ('dir /s /b "%BACKUPPATH%\*" 2^>nul ^| findstr /v /i "\.bat$" ^| find /c /v ""') do set FILECOUNT=%%F
)
echo   Target : %CD%
echo.
echo ------------------------------------------------------------
echo   PROTECTED (will NOT be overwritten):
echo.
echo     - .git\           (repository history ^& remote links)
echo     - *.bat           (all batch scripts)
echo     - deploy.log      (deployment history)
echo     - backups\        (backup archive folder)
echo ------------------------------------------------------------
echo.
echo   Files that will be restored:
echo.

if "!IS_ZIP!"=="1" (
    set PREVCOUNT=0
    for /f "tokens=*" %%E in ('tar.exe -tf "%BACKUPPATH%" 2^>nul') do (
        if !PREVCOUNT! lss 30 (
            set /a PREVCOUNT+=1
            echo     %%E
        )
    )
    echo     ...showing up to 30 entries - restore copies all of them.
) else (
    :: Show files that would be copied
    powershell -NoProfile -Command ^
        "$src='%BACKUPPATH%'; Get-ChildItem -Path $src -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.FullName -notmatch '\\\.git\\' -and $_.Extension -ne '.bat' -and $_.FullName -notmatch '\\backups\\' -and $_.Name -ne 'deploy.log' } | ForEach-Object { $rel = $_.FullName.Replace($src+'\',''); $sz = if($_.Length -gt 1MB){'{0:N1} MB' -f ($_.Length/1MB)} elseif($_.Length -gt 1KB){'{0:N1} KB' -f ($_.Length/1KB)} else{'{0} B' -f $_.Length}; Write-Host ('  ' + $rel.PadRight(50) + $sz) }"
)

echo.
set /p CONFIRM=Proceed with restore? (Y/N): 

if /I not "%CONFIRM%"=="Y" (
    echo.
    echo   Restore cancelled.
    pause
    goto MENU
)

goto DORESTORE


:: =============================================================
:: PERFORM THE RESTORE
:: =============================================================
:DORESTORE
cls
echo ============================================================
echo         Restoring Files...
echo ============================================================
echo.
echo   Source: %BACKUPPATH%
echo.

if "!IS_ZIP!"=="1" goto RESTORE_ZIP

echo   Copying folder (excluding .git, *.bat, deploy.log, backups\)...
echo.
robocopy "%BACKUPPATH%" "%CD%" /E /XD ".git" "backups" /XF "*.bat" "deploy.log"
set "ROBO_ERR=!ERRORLEVEL!"
goto RESTORE_DONE

:RESTORE_ZIP
:: Extract to a temp staging folder first, then apply the exact same
:: robocopy protection rules used for folder restores. Never extract
:: a zip directly into the project (it could land inside .git\).
set "STAGE=%TEMP%\zip_restore_%RANDOM%"
if exist "!STAGE!" rd /s /q "!STAGE!"
mkdir "!STAGE!"

echo   Extracting ZIP to staging folder...
echo.
tar.exe -xf "%BACKUPPATH%" -C "!STAGE!"
if errorlevel 1 (
    echo.
    echo   ERROR: Extraction failed. Nothing was copied.
    rd /s /q "!STAGE!"
    pause
    goto MENU
)

echo.
echo   Copying staged files (excluding .git, *.bat, deploy.log, backups\)...
echo.
robocopy "!STAGE!" "%CD%" /E /XD ".git" "backups" /XF "*.bat" "deploy.log"
set "ROBO_ERR=!ERRORLEVEL!"
rd /s /q "!STAGE!"

:RESTORE_DONE
echo.
echo ============================================================

if !ROBO_ERR! geq 8 (
    echo   WARNING: Some files may not have copied correctly.
    echo   Check the output above for details.
) else (
    echo   Restore complete. !FILECOUNT! entries processed.
)

echo.
echo   Source : %BACKUPPATH%
echo ------------------------------------------------------------
echo   REMINDER: .git was NOT touched.
echo   Run 'git status' to review changes.
echo ============================================================

echo [%DATE% %TIME%] Restored from: %BACKUPPATH%>>%LOGFILE%

echo.
pause
goto MENU
