# Diagnostic check for the Tailscale setup described in TAILSCALE.md -
# does NOT install or configure anything, only reports the current
# state so you know what (if anything) is still missing:
#   1. is the Tailscale client installed on this machine
#   2. is the `tailscaled` background service actually running
#   3. is this machine logged into a tailnet, and what's its
#      Tailscale IPv4 address (the value to put in API_HOST/.env)
#
# Safe to re-run any time - read-only, just calls `tailscale status`/
# `tailscale ip`.

$ErrorActionPreference = 'Stop'

function Test-CommandExists([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

Write-Output "=== Cypher / Tailscale check ==="

if (-not (Test-CommandExists 'tailscale')) {
    Write-Output "[MISSING] Tailscale is not installed (or not on PATH)."
    Write-Output ""
    Write-Output "Install it from https://tailscale.com/download/windows,"
    Write-Output "then sign in (the installer opens a browser tab for this)."
    Write-Output "Re-run this script afterwards."
    exit 1
}

Write-Output "[OK] Tailscale client found: $((Get-Command tailscale).Source)"

# `tailscale status` exits non-zero (and prints "Tailscale is stopped.")
# if the service isn't running or you're logged out - don't let
# $ErrorActionPreference='Stop' turn that into a script-ending error.
$statusOutput = & tailscale status 2>&1
$statusExitCode = $LASTEXITCODE

if ($statusExitCode -ne 0) {
    Write-Output "[NOT RUNNING] Tailscale is installed but not active:"
    Write-Output "  $statusOutput"
    Write-Output ""
    Write-Output "Start the 'Tailscale' service (or open the Tailscale tray app and"
    Write-Output "sign in), then re-run this script."
    exit 1
}

Write-Output "[OK] Tailscale is running."
Write-Output ""
Write-Output "Tailscale status:"
Write-Output $statusOutput
Write-Output ""

$ip4 = (& tailscale ip -4 2>&1).Trim()
if ([string]::IsNullOrWhiteSpace($ip4)) {
    Write-Output "[WARN] Could not determine this machine's Tailscale IPv4 address."
    exit 1
}

Write-Output "[OK] This machine's Tailscale IPv4 address: $ip4"
Write-Output ""
Write-Output "Next steps (see TAILSCALE.md for details):"
Write-Output "  1. In the project's .env, set:"
Write-Output "       API_HOST=$ip4"
Write-Output "  2. Add this machine's frontend origin to CORS_ORIGINS in .env,"
Write-Output "     e.g. http://$ip4:5173"
Write-Output "  3. Make sure USER_TOKEN/AGENT_TOKEN are real random values, not"
Write-Output "     the .env.example placeholders - the backend refuses to start"
Write-Output "     otherwise once API_HOST is set to a non-localhost address."
Write-Output "  4. Start the backend with: python run.py   (from backend/)"
Write-Output "  5. On your phone (same tailnet), open http://$ip4:5173 and install the PWA."
