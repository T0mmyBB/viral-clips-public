# Setup una tantum per la skill viral-clips.
# Idempotente: si puo rilanciare senza danni, salta cio che e gia a posto.
#
#   powershell -ExecutionPolicy Bypass -File setup.ps1
#
# Installa: ffmpeg, Python 3.12 dedicato, venv con le dipendenze,
# modello YuNet per il tracking del volto, font per i sottotitoli.

$ErrorActionPreference = 'Stop'

$SkillRoot   = Split-Path -Parent $PSScriptRoot
$ProjectRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $SkillRoot))
$VenvDir     = Join-Path $ProjectRoot '.venv'
$ModelsDir   = Join-Path $SkillRoot 'assets\models'
$FontsDir    = Join-Path $SkillRoot 'assets\fonts'

function Step($msg)  { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Ok($msg)    { Write-Host "  OK   $msg" -ForegroundColor Green }
function Info($msg)  { Write-Host "  ..   $msg" -ForegroundColor DarkGray }
function Warn($msg)  { Write-Host "  WARN $msg" -ForegroundColor Yellow }

New-Item -ItemType Directory -Force -Path $ModelsDir, $FontsDir | Out-Null

# Aggiunge le posizioni tipiche di winget al PATH della sessione corrente:
# subito dopo l'installazione il PATH di processo non e ancora aggiornato.
function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user    = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user"
}

# Scarica un file provando piu URL in sequenza. Verifica che il risultato sia
# davvero un binario e non un puntatore git-lfs o una pagina di errore.
function Download-First {
    param([string[]]$Urls, [string]$Dest, [int]$MinBytes = 1024)

    foreach ($url in $Urls) {
        try {
            Info "provo $url"
            Invoke-WebRequest -Uri $url -OutFile $Dest -UseBasicParsing -TimeoutSec 120
            $size = (Get-Item $Dest).Length
            if ($size -lt $MinBytes) {
                Warn "scaricato solo $size byte, troppo piccolo - scarto"
                Remove-Item $Dest -Force -ErrorAction SilentlyContinue
                continue
            }
            # Un puntatore git-lfs e un file di testo che inizia cosi.
            $head = [System.IO.File]::ReadAllBytes($Dest)[0..([Math]::Min(40, $size - 1))]
            $headStr = [System.Text.Encoding]::ASCII.GetString($head)
            if ($headStr -like 'version https://git-lfs*') {
                Warn "ricevuto un puntatore git-lfs invece del file - scarto"
                Remove-Item $Dest -Force -ErrorAction SilentlyContinue
                continue
            }
            Ok "$([System.IO.Path]::GetFileName($Dest)) ($([math]::Round($size/1KB)) KB)"
            return $true
        } catch {
            Warn "fallito: $($_.Exception.Message)"
        }
    }
    return $false
}

# --------------------------------------------------------------------------
Step '1/7  ffmpeg'
# --------------------------------------------------------------------------
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Ok "gia presente: $((Get-Command ffmpeg).Source)"
} else {
    Info 'installo Gyan.FFmpeg via winget (puo richiedere qualche minuto)'
    # --source winget e obbligatorio: senza, winget interroga anche msstore,
    # che qui non e raggiungibile e fa fallire il comando con un ambiguity error.
    winget install --id Gyan.FFmpeg --exact --silent --source winget `
        --accept-source-agreements --accept-package-agreements --disable-interactivity
    Refresh-Path
    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
        Ok 'ffmpeg installato'
    } else {
        throw "ffmpeg non risulta sul PATH dopo l'installazione. Riapri il terminale e rilancia setup.ps1."
    }
}

# --------------------------------------------------------------------------
Step '2/7  Python 3.12'
# --------------------------------------------------------------------------
# Il Python di sistema e il 3.14: troppo recente perche ctranslate2 e opencv
# abbiano wheel affidabili. Usiamo un 3.12 dedicato e lo lasciamo isolato.
function Find-Python312 {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:ProgramFiles 'Python312\python.exe'),
        'C:\Python312\python.exe'
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }

    # Ultimo tentativo: il launcher py, se conosce un 3.12.
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            $p = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $p -and (Test-Path $p)) { return $p }
        } catch { }
    }
    return $null
}

$py312 = Find-Python312
if ($py312) {
    Ok "gia presente: $py312"
} else {
    Info 'installo Python 3.12 via winget'
    winget install --id Python.Python.3.12 --exact --silent --source winget `
        --accept-source-agreements --accept-package-agreements --disable-interactivity
    Refresh-Path
    $py312 = Find-Python312
    if (-not $py312) {
        throw "Python 3.12 non trovato dopo l'installazione. Riapri il terminale e rilancia setup.ps1."
    }
    Ok "installato: $py312"
}

# --------------------------------------------------------------------------
Step '3/7  virtualenv e dipendenze'
# --------------------------------------------------------------------------
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'

if (Test-Path $VenvPython) {
    Ok "venv gia presente: $VenvDir"
} else {
    Info "creo il venv in $VenvDir"
    & $py312 -m venv $VenvDir
    if (-not (Test-Path $VenvPython)) { throw 'creazione del venv fallita' }
    Ok 'venv creato'
}

Info 'aggiorno pip'
& $VenvPython -m pip install --upgrade pip --quiet --disable-pip-version-check

# av serve a faster-whisper per decodificare l'audio; opencv per il tracking.
# scenedetect rileva i cambi scena molto meglio del filtro select di ffmpeg e
# si appoggia a opencv e numpy che ci sono gia. La variante scenedetect-core
# sarebbe piu snella ma su PyPI e interamente ritirata: usiamo il pacchetto pieno.
# Niente torch, niente mediapipe: su CPU senza CUDA non servono e pesano 3 GB.
$packages = @('faster-whisper', 'opencv-python', 'numpy', 'yt-dlp', 'scenedetect')
Info "installo: $($packages -join ', ')"
& $VenvPython -m pip install --upgrade @packages --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw 'installazione delle dipendenze fallita' }
Ok 'dipendenze installate'

# Ruff a versione fissata, fuori da --upgrade: una versione nuova porta regole
# nuove, e riformattare il progetto deve essere una scelta, non un aggiornamento.
# L'estensione di VS Code usa questo (.vscode/settings.json, ruff.path).
Info 'installo ruff 0.16.9'
& $VenvPython -m pip install 'ruff==0.16.9' --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw 'installazione di ruff fallita' }
Ok 'ruff installato'

# --------------------------------------------------------------------------
Step '4/7  modello YuNet (rilevamento volti)'
# --------------------------------------------------------------------------
$YuNet = Join-Path $ModelsDir 'face_detection_yunet.onnx'
if ((Test-Path $YuNet) -and (Get-Item $YuNet).Length -gt 100KB) {
    Ok 'gia presente'
} else {
    # La forma github.com/.../raw/... risolve correttamente i file git-lfs,
    # mentre raw.githubusercontent.com restituirebbe solo il puntatore.
    $ok = Download-First -MinBytes 100000 -Dest $YuNet -Urls @(
        'https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx',
        'https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx'
    )
    if (-not $ok) {
        throw "download di YuNet fallito. Scaricalo a mano da https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet e salvalo come $YuNet"
    }
}

# --------------------------------------------------------------------------
Step '5/7  font per i sottotitoli'
# --------------------------------------------------------------------------
# Font statico (non variabile): libass gestisce male le istanze dei font
# variabili e il peso uscirebbe sbagliato nel burn-in.
$FontFile = Join-Path $FontsDir 'ArchivoBlack-Regular.ttf'
if ((Test-Path $FontFile) -and (Get-Item $FontFile).Length -gt 20KB) {
    Ok 'gia presente'
} else {
    $ok = Download-First -MinBytes 20000 -Dest $FontFile -Urls @(
        'https://github.com/google/fonts/raw/main/ofl/archivoblack/ArchivoBlack-Regular.ttf',
        'https://raw.githubusercontent.com/google/fonts/main/ofl/archivoblack/ArchivoBlack-Regular.ttf'
    )
    if (-not $ok) {
        # Non fatale: Windows ha gia font pesanti utilizzabili.
        $fallback = "$env:WINDIR\Fonts\ariblk.ttf"   # Arial Black
        if (Test-Path $fallback) {
            Copy-Item $fallback $FontFile -Force
            Warn "download fallito, uso Arial Black di sistema come ripiego"
        } else {
            Warn 'nessun font scaricato: i sottotitoli useranno il default di sistema'
        }
    }
}

# --------------------------------------------------------------------------
Step '6/7  grafiche animate (HyperFrames)'
# --------------------------------------------------------------------------
# Versioni fissate in grafiche/package-lock.json. --ignore-scripts: nessuna
# dipendenza ne ha bisogno (codice letto il 27/10/2026). Il Chrome non si
# scarica qui: grafiche.py usa il chrome-headless-shell di Playwright.
# npm scrive su stderr anche quando va tutto bene, e con 'Stop' PowerShell 5.1
# lo tratterebbe come un errore: si guarda il codice di uscita.
$Grafiche = Join-Path $SkillRoot 'grafiche'
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Warn 'npm non trovato: senza Node 22 o piu le clip escono senza grafiche'
} elseif (Test-Path (Join-Path $Grafiche 'node_modules\hyperframes')) {
    Ok 'gia presente'
} else {
    $env:HYPERFRAMES_NO_TELEMETRY = '1'
    $env:DO_NOT_TRACK = '1'
    Push-Location $Grafiche
    $ErrorActionPreference = 'Continue'
    npm ci --ignore-scripts --no-fund --no-audit 2>&1 | Out-Null
    $ErrorActionPreference = 'Stop'
    Pop-Location
    if ($LASTEXITCODE -eq 0) { Ok 'HyperFrames installato' }
    else { Warn "npm ci fallito (codice $LASTEXITCODE): le clip escono senza grafiche" }
}

# --------------------------------------------------------------------------
Step '7/7  modello Whisper (small) e verifica finale'
# --------------------------------------------------------------------------
$WhisperCache = Join-Path $ModelsDir 'whisper'
New-Item -ItemType Directory -Force -Path $WhisperCache | Out-Null

Info 'pre-scarico il modello Whisper small (~460 MB, solo la prima volta)'
$dl = @"
import os, sys
os.environ['HF_HUB_DISABLE_SYMLINKS_WARNING'] = '1'
from faster_whisper import WhisperModel
WhisperModel('small', device='cpu', compute_type='int8', download_root=r'$WhisperCache')
print('whisper small pronto')
"@
$dl | & $VenvPython -
if ($LASTEXITCODE -ne 0) { throw 'download del modello Whisper fallito' }
Ok 'modello Whisper pronto'

Step 'Hook di commit'
# Ricorda PROGRESS.md quando il commit tocca la pipeline. Non blocca: avvisa.
# Sta fuori da .git/ nel repository perche .git/hooks non e versionato, quindi
# senza questa copia l'hook esisterebbe solo sulla macchina di chi l'ha scritto.
$HookSrc = Join-Path $SkillRoot 'hooks\pre-commit'
$HookDst = Join-Path $ProjectRoot '.git\hooks\pre-commit'
if (-not (Test-Path (Split-Path $HookDst))) {
    Info 'nessuna cartella .git/hooks: non e un repository git, salto'
} elseif (-not (Test-Path $HookSrc)) {
    Warn "hook non trovato in $HookSrc"
} else {
    Copy-Item $HookSrc $HookDst -Force
    Ok 'hook pre-commit installato'
}

Step 'Verifica'
& $VenvPython -c @"
import sys, cv2, numpy, faster_whisper, yt_dlp, scenedetect
print(f'  python         {sys.version.split()[0]}')
print(f'  opencv         {cv2.__version__}')
print(f'  numpy          {numpy.__version__}')
print(f'  faster-whisper {faster_whisper.__version__ if hasattr(faster_whisper, \"__version__\") else \"ok\"}')
print(f'  yt-dlp         {yt_dlp.version.__version__}')
print(f'  scenedetect    {scenedetect.__version__}')
cv2.FaceDetectorYN  # verifica che il detector sia disponibile in questa build
print('  FaceDetectorYN disponibile')
"@
if ($LASTEXITCODE -ne 0) { throw 'verifica delle dipendenze fallita' }

$ffv = (& ffmpeg -version 2>&1 | Select-Object -First 1)
Write-Host "  $ffv"

Write-Host "`nSetup completato." -ForegroundColor Green
Write-Host "Python del venv: $VenvPython"
