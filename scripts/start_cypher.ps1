<#
  Startet Cypher: Ollama (falls nötig), baut die Oberfläche (falls veraltet),
  startet das Backend unsichtbar im Hintergrund und öffnet den Browser.

    start_cypher.bat                 normal
    start_cypher.bat -NoBrowser      ohne Browser (Autostart)
    start_cypher.bat -Foreground     im Fenster, mit Log-Ausgabe (Fehlersuche)

  Logs: logs\backend.out.log / logs\backend.err.log und logs\cypher.log.
#>
param([switch]$NoBrowser, [switch]$Foreground)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root 'backend\.venv\Scripts\python.exe'

if (-not (Test-Path $Py)) {
    Write-Host 'Cypher ist noch nicht eingerichtet - bitte zuerst setup.bat ausführen.' -ForegroundColor Red
    exit 1
}

$Port = 8000
$EnvFile = Join-Path $Root '.env'
if (Test-Path $EnvFile) {
    $m = Select-String -Path $EnvFile -Pattern '^\s*API_PORT\s*=\s*(\d+)' | Select-Object -First 1
    if ($m) { $Port = [int]$m.Matches[0].Groups[1].Value }
}
$Url = "http://127.0.0.1:$Port"

function Test-Url([string]$u) {
    try { return (Invoke-WebRequest $u -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 } catch { return $false }
}

if (Test-Url "$Url/health") {
    Write-Host "Cypher läuft bereits: $Url" -ForegroundColor Green
    if (-not $NoBrowser) { Start-Process $Url }
    exit 0
}

# 1. Ollama - only started here if it isn't running yet. Then with
#    OLLAMA_MAX_LOADED_MODELS=1 (for this process only): two models in VRAM
#    make Windows swap to RAM and everything 10-20x slower (M0 report).
if (-not (Test-Url 'http://127.0.0.1:11434/api/version')) {
    $Ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
    if (-not $Ollama) { $Ollama = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe' }
    if (Test-Path $Ollama) {
        Write-Host 'Starte Ollama ...'
        $env:OLLAMA_MAX_LOADED_MODELS = '1'
        Start-Process -FilePath $Ollama -ArgumentList 'serve' -WindowStyle Hidden
        $deadline = (Get-Date).AddSeconds(20)
        while ((Get-Date) -lt $deadline -and -not (Test-Url 'http://127.0.0.1:11434/api/version')) { Start-Sleep -Milliseconds 500 }
    } else {
        Write-Host 'Ollama ist nicht installiert - Cypher startet, kann aber nicht antworten (ollama.com/download).' -ForegroundColor Yellow
    }
}

# 2. Web app: build it if it's missing or older than the source.
$Index = Join-Path $Root 'frontend\dist\index.html'
$NeedBuild = -not (Test-Path $Index)
if (-not $NeedBuild) {
    $Newest = Get-ChildItem (Join-Path $Root 'frontend\src') -Recurse -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($Newest -and $Newest.LastWriteTime -gt (Get-Item $Index).LastWriteTime) { $NeedBuild = $true }
}
if ($NeedBuild) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Write-Host 'Baue die Oberfläche (einmalig nach Updates) ...'
        Push-Location (Join-Path $Root 'frontend')
        try { & npm run build --silent | Out-Null } finally { Pop-Location }
    } else {
        Write-Host 'npm fehlt - die Oberfläche kann nicht gebaut werden (setup.bat).' -ForegroundColor Yellow
    }
}

# 3. Backend.
$Logs = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $Logs | Out-Null
if ($Foreground) {
    Push-Location (Join-Path $Root 'backend')
    try { & $Py 'run.py' } finally { Pop-Location }
    exit $LASTEXITCODE
}
Start-Process -FilePath $Py -ArgumentList 'run.py' -WorkingDirectory (Join-Path $Root 'backend') -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $Logs 'backend.out.log') -RedirectStandardError (Join-Path $Logs 'backend.err.log')

# 4. Wait until it answers (loading the speech models takes a moment).
Write-Host 'Starte Cypher ...'
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline -and -not (Test-Url "$Url/health")) { Start-Sleep -Milliseconds 500 }
if (-not (Test-Url "$Url/health")) {
    Write-Host 'Cypher startet nicht. Details: logs\backend.err.log - oder doctor.bat ausführen.' -ForegroundColor Red
    exit 1
}
Write-Host "Cypher läuft: $Url" -ForegroundColor Green
if (-not $NoBrowser) { Start-Process $Url }
