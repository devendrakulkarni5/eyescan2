@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
  echo Creating environment...
  python -m venv .venv && .venv\Scripts\python.exe -m pip install -r requirements.txt
)
start "" .venv\Scripts\pythonw.exe app.py
