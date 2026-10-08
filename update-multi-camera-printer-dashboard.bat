@echo off
setlocal
cd /d "%~dp0"
set "MCPD_HOME=%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Multi Camera Printer Dashboard is not installed in this folder.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" manage.py update
pause
