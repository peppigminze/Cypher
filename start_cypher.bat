@echo off
rem Startet Cypher (Optionen: -NoBrowser, -Foreground)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_cypher.ps1" %*
set RC=%ERRORLEVEL%
if not %RC%==0 pause
exit /b %RC%
