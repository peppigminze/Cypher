# Diagnostic check for the Tailscale setup described in docs/MOBILE.md -
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
Write-Output "Next steps (see docs/MOBILE.md for details):"
Write-Output "  1. Tailscale admin console -> DNS: enable MagicDNS and HTTPS Certificates."
Write-Output "  2. Run once:  tailscale serve --bg 8000"
Write-Output "  3. Start Cypher (start_cypher.bat), open the menu (M) -> Handy and scan the QR code."
Write-Output "  The backend stays on 127.0.0.1 - no API_HOST/CORS changes needed."
