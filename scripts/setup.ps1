<#
  Einmalige Einrichtung (oder nach einem Update erneut ausführen):
    1. Python-Umgebung backend\.venv + Pakete
    2. .env aus .env.example (falls noch keine da ist)
    3. Oberfläche bauen (Node.js/npm)
    4. Piper-Stimme herunterladen (~78 MB, einmalig)
    5. Sprachmodelle in Ollama (fragt vorher - mehrere GB)
    6. Doktor: prüft alles
  Ein Schritt, der scheitert, bricht nicht ab - am Ende steht, was fehlt.
#>
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root 'backend'
$Venv = Join-Path $Backend '.venv'
$Py = Join-Path $Venv 'Scripts\python.exe'
$Problems = New-Object System.Collections.Generic.List[string]

function Step([string]$text) { Write-Host "`n== $text ==" -ForegroundColor Cyan }
function Problem([string]$text) { $Problems.Add($text); Write-Host $text -ForegroundColor Yellow }

Step '1/6 Python'
if (-not (Test-Path $Py)) {
    if (Get-Command py -ErrorAction SilentlyContinue) { & py -3.12 -m venv $Venv }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { & python -m venv $Venv }
    if (-not (Test-Path $Py)) {
        Write-Host 'Python 3.12 fehlt. Bitte von python.org installieren (Haken bei "Add python.exe to PATH"), dann setup.bat erneut.' -ForegroundColor Red
        exit 1
    }
}
& $Py -m pip install --upgrade pip --quiet
& $Py -m pip install -r (Join-Path $Backend 'requirements.txt') --quiet
if ($LASTEXITCODE -ne 0) { Write-Host 'Paket-Installation fehlgeschlagen (siehe oben).' -ForegroundColor Red; exit 1 }
Write-Host 'Python-Umgebung bereit.'

Step '2/6 Konfiguration'
$EnvFile = Join-Path $Root '.env'
if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $Root '.env.example') $EnvFile
    Write-Host '.env angelegt (aus .env.example). Zugangs-Tokens erzeugt Cypher beim ersten Start selbst.'
} else { Write-Host '.env vorhanden - unverändert.' }

Step '3/6 Oberfläche'
if (Get-Command npm -ErrorAction SilentlyContinue) {
    Push-Location (Join-Path $Root 'frontend')
    try {
        & npm ci --no-audit --no-fund
        if ($LASTEXITCODE -eq 0) { & npm run build }
        if ($LASTEXITCODE -ne 0) { Problem 'Die Oberfläche konnte nicht gebaut werden (siehe oben).' }
    } finally { Pop-Location }
} else {
    Problem 'Node.js fehlt - bitte von nodejs.org installieren (LTS), dann setup.bat erneut.'
}

Step '4/6 Stimme'
& $Py (Join-Path $Backend 'scripts\download_piper_voice.py')
if ($LASTEXITCODE -ne 0) { Problem 'Die Stimme fehlt noch - Internetverbindung prüfen und setup.bat erneut ausführen.' }

Step '5/6 Sprachmodelle (Ollama)'
$Ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if (-not $Ollama) { $Ollama = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe' }
if (Test-Path $Ollama) {
    Push-Location $Backend
    try { $Models = & $Py -c "from app.config import get_settings as g; s=g(); print(s.LOCAL_LLM_MODEL); print(s.LOCAL_LLM_FALLBACK_MODEL or '')" } finally { Pop-Location }
    $Installed = (& $Ollama list) -join "`n"
    foreach ($Model in $Models) {
        if (-not $Model) { continue }
        if ($Installed -match [regex]::Escape($Model)) { Write-Host "$Model ist installiert."; continue }
        $answer = Read-Host "$Model fehlt (Download mehrere GB). Jetzt herunterladen? [j/n]"
        if ($answer -match '^[jJyY]') { & $Ollama pull $Model }
        if ($LASTEXITCODE -ne 0 -or $answer -notmatch '^[jJyY]') { Problem "$Model fehlt noch (später: ollama pull $Model)." }
    }
} else {
    Problem 'Ollama fehlt - bitte von ollama.com/download installieren, dann setup.bat erneut.'
}

Step '6/6 Doktor'
Push-Location $Backend
try { & $Py -m app.ops.doctor; $DoctorOk = ($LASTEXITCODE -eq 0) } finally { Pop-Location }
if (-not $DoctorOk) { $Problems.Add('Der Doktor meldet Fehler (✗ oben).') }

if ($Problems.Count -eq 0) {
    Write-Host "`nFertig. Starten mit start_cypher.bat" -ForegroundColor Green
    exit 0
}
Write-Host "`nEinrichtung unvollständig:" -ForegroundColor Red
foreach ($p in $Problems) { Write-Host "  - $p" -ForegroundColor Red }
exit 1
