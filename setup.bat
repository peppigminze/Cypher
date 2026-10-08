@echo off
rem Einrichtung von Cypher (Python, Oberflaeche, Stimme, Modelle, Doktor)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1"
set RC=%ERRORLEVEL%
pause
exit /b %RC%
