@echo off
:: Usage: release.bat patch|minor|major|X.Y.Z [--notes "text"] [--no-publish] | release.bat --check
cd /d "%~dp0"
".venv\Scripts\python.exe" tools\release.py %*
if errorlevel 1 ( echo. & echo Release did not complete. & exit /b 1 )
