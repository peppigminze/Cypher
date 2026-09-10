# Starts the JARVIS backend and local PC agent as hidden background
# processes. Used both for manual quick-start and as the action a
# logon Scheduled Task runs (see scripts/autostart.py).
#
# Deliberately NOT using `-WindowStyle Hidden` on the *task* level for
# the actual python processes here - Start-Process -WindowStyle Hidden
# below already keeps no console windows visible; logs go to files
# instead so a hidden process's errors aren't just lost.

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot 'backend\.venv\Scripts\python.exe'
$LogDir = Join-Path $RepoRoot 'logs'

if (-not (Test-Path $Python)) {
    Write-Error "Python venv not found at $Python - run the setup steps in README.md first."
    exit 1
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

Start-Process -FilePath $Python `
    -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000' `
    -WorkingDirectory (Join-Path $RepoRoot 'backend') `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $LogDir 'backend.log') `
    -RedirectStandardError (Join-Path $LogDir 'backend.err.log')

# Give the backend a moment to start listening before the agent's
# first poll, purely to avoid a noisy "connection refused" in its log.
Start-Sleep -Seconds 3

Start-Process -FilePath $Python `
    -ArgumentList '-m', 'agent.run_agent' `
    -WorkingDirectory $RepoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $LogDir 'agent.log') `
    -RedirectStandardError (Join-Path $LogDir 'agent.err.log')
