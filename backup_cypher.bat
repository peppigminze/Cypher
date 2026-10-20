@echo off
rem Backup erstellen; einspielen mit: backup_cypher.bat --restore backups\cypher-backup-....zip
cd /d "%~dp0backend"
".venv\Scripts\python.exe" -m app.ops.backup %*
set RC=%ERRORLEVEL%
pause
exit /b %RC%
