@echo off
:: ---------------------------------------------------------------------
:: Launcher for OpenCode AI CLI
:: Automatically detects the current folder and sets it as context.
:: ---------------------------------------------------------------------

:: 1. Set the window title to the current folder name
title OpenCode AI CLI - %~dp0

:: 2. Switch to the directory where this .bat file is located
::    The /d flag ensures it works even if you switch drives (e.g., C: to S:)
cd /d "%~dp0"

:: 3. Visual confirmation (Optional)
cls
echo ========================================================
echo   Starting OpenCode AI in:
echo   %CD%
echo ========================================================
echo.

:: 4. Launch the OpenCode AI CLI tool
opencode

:: 5. Pause at the end so you can see errors if it crashes (Optional)
:: pause