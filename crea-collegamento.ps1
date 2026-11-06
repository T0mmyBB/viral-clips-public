# crea-collegamento.ps1 - mette sul desktop l'icona che apre il pannello di controllo.
#
#   powershell -ExecutionPolicy Bypass -File crea-collegamento.ps1
#   powershell -ExecutionPolicy Bypass -File crea-collegamento.ps1 -Aggiorna
#   powershell -ExecutionPolicy Bypass -File crea-collegamento.ps1 -Aggiorna -Veloce
#   powershell -ExecutionPolicy Bypass -File crea-collegamento.ps1 -Rimuovi
#
# Un clic apre il browser sul pannello, e nient'altro: niente console, nessuna
# attesa. Sotto c'e' `pythonw.exe dashboard\apri.py`, che alza il pannello se
# non e' su e poi apre la pagina.
#
# **Perche' non aggiorna piu' (9 settembre 2026).** Il collegamento puntava ad
# `aggiorna.ps1 -Veloce`: apriva una console, ci lavorava cinque minuti e apriva
# il browser alla fine. Chi voleva solo guardare il pannello si trovava davanti
# un terminale con dentro un indirizzo, e finiva per copiarselo a mano - cioe'
# il collegamento faceva fare piu' lavoro di quanto ne togliesse. Ora il giro lo
# lancia il pulsante "Aggiorna" della pagina, che e' dove si guardano i dati che
# rinfresca. Con -Aggiorna si riottiene il collegamento di prima.
#
# Il desktop si chiede a Windows e non si costruisce come "$env:USERPROFILE\Desktop":
# con OneDrive attivo quella cartella esiste ancora ma non e' quella che si vede,
# e il collegamento finirebbe in un desktop fantasma.
#
# Niente caratteri fuori ASCII dentro le stringhe (vedi la nota in pianifica.ps1).

param(
    [switch]$Aggiorna,   # il collegamento rifa il giro del radar prima di aprire
    [switch]$Veloce,     # con -Aggiorna: salta i brief, ~5 minuti invece di ~20
    [switch]$Rimuovi,
    [string]$Nome = "Pannello di controllo viral-clips",
    # Dal 7 ottobre 2026 il pannello gira sul server di casa: il collegamento apre
    # un tunnel ssh verso quell'host invece di alzare il pannello qui. Vuoto = qui.
    [string]$Server = "lavoro"
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = Join-Path $desktop "$Nome.lnk"

if ($Rimuovi) {
    if (Test-Path $lnk) { Remove-Item $lnk; Write-Host "Tolto: $lnk" }
    else { Write-Host "Non c'era niente da togliere: $lnk" }
    return
}

$aggiornaPs = "$here\aggiorna.ps1"
$apri       = "$here\dashboard\apri.py"
$pyw        = "$here\.venv\Scripts\pythonw.exe"
$icona      = "$here\dashboard\plancia.ico"
if (-not (Test-Path $aggiornaPs)) { throw "manca $aggiornaPs" }
if (-not (Test-Path $apri)) { throw "manca $apri" }
if (-not (Test-Path $pyw)) { throw "manca $pyw - lo crea setup.ps1" }
if (-not (Test-Path $icona)) {
    Write-Host "manca l'icona, la genero"
    & "$here\.venv\Scripts\python.exe" "$here\dashboard\icona.py"
}

$shell = New-Object -ComObject WScript.Shell
$scorciatoia = $shell.CreateShortcut($lnk)

if ($Aggiorna) {
    # La strada lunga, con la console: si vede il giro mentre lavora, e il
    # browser si apre alla fine sui dati appena scritti.
    $argomenti = "-NoProfile -ExecutionPolicy Bypass -File `"$aggiornaPs`""
    if ($Veloce) { $argomenti += " -Veloce" }
    $scorciatoia.TargetPath   = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $scorciatoia.Arguments    = $argomenti
    $scorciatoia.Description  = "Rinfresca radar e metriche, poi apre il pannello su http://127.0.0.1:8787/"
    $scorciatoia.WindowStyle  = 1
} else {
    # pythonw.exe e non python.exe, e nemmeno powershell.exe: e' l'unico dei tre
    # che non apre una finestra nera - "nascosta" per powershell vuol dire che
    # compare e sparisce, non che non c'e'. Un collegamento a una pagina deve
    # mostrare la pagina e basta.
    $scorciatoia.TargetPath   = $pyw
    $scorciatoia.Arguments    = "`"$apri`""
    $scorciatoia.Description  = "Apre il pannello su http://127.0.0.1:8787/ (lo alza se non e' su)"
    if ($Server) {
        $scorciatoia.Arguments   = "`"$apri`" --server $Server"
        $scorciatoia.Description = "Apre il pannello del server $Server su http://127.0.0.1:8787/ (tunnel ssh)"
    }
    $scorciatoia.WindowStyle  = 7
}

$scorciatoia.WorkingDirectory = "$here\dashboard"
$scorciatoia.IconLocation     = "$icona,0"
$scorciatoia.Save()

Write-Host ""
Write-Host "Creato: $lnk"
if ($Aggiorna) {
    Write-Host "  un clic: radar + metriche, poi apre il pannello" -NoNewline
    if ($Veloce) { Write-Host " (senza i brief, ~5 minuti)" } else { Write-Host " (giro intero, ~20 minuti)" }
} else {
    Write-Host "  un clic: apre il pannello nel browser, subito e senza console"
    Write-Host "  l'aggiornamento lo fa il pulsante Aggiorna dentro la pagina"
}
Write-Host ""
Write-Host "Giro completo a mano:      powershell -File aggiorna.ps1"
Write-Host "Collegamento che aggiorna: powershell -File crea-collegamento.ps1 -Aggiorna"
Write-Host "Togliere il collegamento:  powershell -File crea-collegamento.ps1 -Rimuovi"
