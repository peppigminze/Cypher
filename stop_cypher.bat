@echo off
rem Beendet Cypher sauber
cd /d "%~dp0backend"
".venv\Scripts\python.exe" -m app.ops.stop
set RC=%ERRORLEVEL%
if not %RC%==0 pause
exit /b %RC%
