@echo off
:: Manual publish. Order: edit APP_VERSION (bridge.py) + AppVersion (installer.iss) -> build_exe.bat -> build_installer.bat -> this.
:: Uploads installer\Keeper-Setup-<version>.exe to the public releases repo so installed apps offer the update.
:: Usage: publish_release.bat "what changed"      (notes optional; defaults to the CHANGELOG section)
cd /d "%~dp0"
if "%~1"=="" (
    ".venv\Scripts\python.exe" tools\release.py --publish-only
) else (
    ".venv\Scripts\python.exe" tools\release.py --publish-only --notes "%~1"
)
pause
