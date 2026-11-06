# setup.ps1 — prepara l'ambiente dell'agente (una volta sola)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

if (-not (Test-Path "$here\.venv")) {
    Write-Host "Creo l'ambiente virtuale..."
    try   { & py -3.12 -m venv "$here\.venv" }
    catch { & python -m venv "$here\.venv" }
}

$vpy = "$here\.venv\Scripts\python.exe"
& $vpy -m pip install --upgrade pip
& $vpy -m pip install playwright
& $vpy -m playwright install chromium

# Scrapling non sta qui dentro: serve anche al radar e al registro delle
# metriche, quindi vive in un venv condiviso fuori dai progetti. Un venv non si
# copia mai tra macchine (percorsi assoluti scritti alla creazione), quindi
# questo blocco lo ricrea dove serve invece di versionarlo.
$shared = "$env:USERPROFILE\.venvs\scraping"
if (-not (Test-Path $shared)) {
    Write-Host "Creo il venv condiviso per lo scraping in $shared ..."
    try   { & py -3.12 -m venv $shared }
    catch { & python -m venv $shared }
}
$spy = "$shared\Scripts\python.exe"
& $spy -m pip install --upgrade pip
& $spy -m pip install "scrapling[fetchers]"
# Obbligatorio dopo OGNI installazione o aggiornamento di Scrapling: le sue
# dipendenze possono cambiare la versione di Playwright, e ogni versione cerca
# una revisione di Chromium col numero suo. Quella gia' in cache non vale, e
# l'errore arriva solo al primo avvio del browser — cioe' in mezzo a un login.
& $spy -m playwright install chromium

# cloudflared: il tunnel con cui `ospita.py` rende una clip raggiungibile da
# Buffer per i minuti di una pubblicazione. Un file solo, in `.tools` accanto
# agli altri strumenti esterni (ignorata da git, mai esportata).
$cf = Join-Path (Split-Path -Parent $here) ".tools\cloudflared\cloudflared.exe"
if (-not (Test-Path $cf)) {
    Write-Host "Scarico cloudflared in $cf ..."
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $cf) | Out-Null
    Invoke-WebRequest -UseBasicParsing -OutFile $cf `
        -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
}

Write-Host ""
Write-Host "Fatto. Passi successivi (login una tantum ciascuno):"
Write-Host "  .\.venv\Scripts\python.exe publish_agent.py login     # TikTok"
Write-Host "  $spy whop_agent.py login                              # Whop"
Write-Host "  la chiave di Buffer in .buffer\chiave.txt             # TikTok tramite Buffer"
Write-Host "  il token di Meta in .meta\<gruppo>.txt                # Instagram dall'API"
