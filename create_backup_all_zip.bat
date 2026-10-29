@echo off
setlocal enabledelayedexpansion

:: =============================================================
:: FULL ZIP BACKUP
:: ZIP backup generated from the exclusion rules of Create_backup_all.bat
:: Uses built-in tar.exe (bsdtar, ships with Windows 10 1803+).
:: Output: backups\backup_yyyyMONdd_HH-mm-ss_FullBK.zip
:: =============================================================

:: The console code page decides how 'dir /b' encodes file names. On a
:: non-UTF-8 page, non-ASCII names come back as '?' and tar can never find
:: them, so switch this console to UTF-8 and restore the old page on exit.
set "OLDCP="
for /f "tokens=2 delims=:" %%C in ('chcp') do set "OLDCP=%%C"
set "OLDCP=!OLDCP: =!"
chcp 65001 >nul

set "PROJECT_DIR=%~dp0"
set "PROJECT_DIR=!PROJECT_DIR:~0,-1!"

set "BACKUP_DIR=!PROJECT_DIR!\backups"

:: Date as yyyyMONdd (e.g. 2026JAN24); time stays HH-mm-ss
set "MONTHS=JANFEBMARAPRMAYJUNJULAUGSEPOCTNOVDEC"
set "MRAW=%date:~-7,2%"
set "MRAW=!MRAW: =0!"
set /a "MM=1!MRAW!-100"
set /a "MOFF=(MM-1)*3"
for %%M in (!MOFF!) do set "MON=!MONTHS:~%%M,3!"
set "TIMESTAMP=%date:~-4,4%!MON!%date:~-10,2%_%time:~0,2%-%time:~3,2%-%time:~6,2%"
set "TIMESTAMP=!TIMESTAMP: =0!"

set "ZIP_NAME=backup_!TIMESTAMP!_FullBK.zip"
set "SUFFIX=%~1"
if defined SUFFIX set "ZIP_NAME=backup_!TIMESTAMP!_FullBK_!SUFFIX!.zip"
set "ZIP_PATH=!BACKUP_DIR!\!ZIP_NAME!"

if not exist "!BACKUP_DIR!" mkdir "!BACKUP_DIR!"

echo Creating ZIP backup: !ZIP_NAME!

:: Rules carried over from Create_backup_all.bat (robocopy /XD /XF -> tar --exclude)
:: Secrets/runtime data are excluded on purpose: this ZIP lives inside the web
:: root (even though .htaccess 404s /backups/), so it must never carry
:: db_config.php, the data-dir override, or studio/admin/data/.
:: List top-level items explicitly instead of passing '.', so zip entries have
:: no './' prefix (a './' prefix breaks opening HTML from inside the zip).
:: Names are passed to tar as arguments (cmd hands native tools real Unicode),
:: because tar -T decodes its list with the ANSI code page and therefore fails
:: on non-ASCII names. The list file is kept only as a fallback for name sets
:: too long for a command line.
set "LIST_FILE=!TEMP!\backup_list_!RANDOM!.txt"
set "ARGS="
if exist "!LIST_FILE!" del "!LIST_FILE!"
for /f "delims=" %%I in ('dir /b /a "!PROJECT_DIR!"') do (
    set "SKIP="
    if /i "%%I"==".git" set "SKIP=1"
    if /i "%%I"=="backups" set "SKIP=1"
    if /i "%%I"==".gemini" set "SKIP=1"
    if /i "%%I"==".venv" set "SKIP=1"
    if /i "%%I"=="build" set "SKIP=1"
    if /i "%%I"=="dist" set "SKIP=1"
    if /i "%%I"=="node_modules" set "SKIP=1"
    if not defined SKIP (
        >>"!LIST_FILE!" echo %%I
        set "ARGS=!ARGS! "%%I""
    )
)

set "TAR_INPUT=-- !ARGS!"
for %%F in ("!LIST_FILE!") do if %%~zF GEQ 7000 set "TAR_INPUT=-T "!LIST_FILE!""

tar.exe -a -cf "!ZIP_PATH!" -C "!PROJECT_DIR!" --exclude=studio/admin/db_config.php --exclude=studio/admin/.data-dir.override.php --exclude=studio/admin/data --exclude=*.bat --exclude=*.md --exclude=*.exe --exclude=__pycache__ --exclude=*.pyc --exclude=*.pyo --exclude=.pytest_cache --exclude=.mypy_cache --exclude=.ruff_cache --exclude=.cache --exclude=.idea --exclude=.vscode --exclude=node_modules --exclude=.venv --exclude=venv --exclude=*.log --exclude=*.tmp --exclude=*.temp --exclude=*.bak --exclude=*.swp --exclude=Thumbs.db --exclude=desktop.ini --exclude=.DS_Store !TAR_INPUT!
set "TAR_RC=!ERRORLEVEL!"
del "!LIST_FILE!" >nul 2>&1
cmd /c exit !TAR_RC!

if errorlevel 1 (
    echo Error: tar.exe failed with exit code !ERRORLEVEL!
    if exist "!ZIP_PATH!" del "!ZIP_PATH!"
    pause
    if defined OLDCP chcp !OLDCP! >nul
    endlocal
    exit /b 1
)

if not exist "!ZIP_PATH!" (
    echo Backup failed: ZIP file was not created.
    pause
    if defined OLDCP chcp !OLDCP! >nul
    endlocal
    exit /b 1
)

for %%F in ("!ZIP_PATH!") do set "ZIP_SIZE=%%~zF"
if !ZIP_SIZE! LSS 100 (
    echo Backup failed: ZIP is empty - nothing matched the filters.
    if exist "!ZIP_PATH!" del "!ZIP_PATH!"
    pause
    if defined OLDCP chcp !OLDCP! >nul
    endlocal
    exit /b 1
)

tar.exe -tf "!ZIP_PATH!" >nul 2>&1
if errorlevel 1 (
    echo Backup failed: ZIP integrity check failed.
    pause
    if defined OLDCP chcp !OLDCP! >nul
    endlocal
    exit /b 1
)

for /f %%C in ('tar.exe -tf "!ZIP_PATH!" ^| find /c /v ""') do set "ENTRYCOUNT=%%C"

echo Backup created successfully: !ZIP_PATH!
echo   Entries: !ENTRYCOUNT!  Size: !ZIP_SIZE! bytes
if defined OLDCP chcp !OLDCP! >nul
endlocal
exit /b 0