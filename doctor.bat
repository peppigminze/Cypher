@echo off
rem Prueft die Installation (nur lesend)
cd /d "%~dp0backend"
if not exist ".venv\Scripts\python.exe" (
  echo Cypher ist noch nicht eingerichtet - bitte setup.bat ausfuehren.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m app.ops.doctor %*
set RC=%ERRORLEVEL%
pause
exit /b %RC%
