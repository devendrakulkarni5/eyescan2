@echo off
:: ---------------------------------------------------------------------
:: Launcher for Claude Code CLI
:: Automatically detects the current folder and sets it as context.
:: ---------------------------------------------------------------------

:: 1. Set the window title to the current folder name
title Claude Code CLI - %~dp0

:: 2. Switch to the directory where this .bat file is located
::    The /d flag ensures it works even if you switch drives (e.g., C: to S:)
cd /d "%~dp0"

:: 3. Visual confirmation (Optional)
cls
echo ========================================================
echo   Starting Claude Code in:
echo   %CD%
echo ========================================================
echo.

:: 4. Launch the Claude Code CLI tool
::    "call" is used because npm installs claude as a .cmd wrapper;
::    without it, the script would not return here afterwards.
call claude

:: 5. Pause at the end so you can see errors if it crashes (Optional)
:: pause
