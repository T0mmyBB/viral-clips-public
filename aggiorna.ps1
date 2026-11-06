# aggiorna.ps1 - un clic solo: rinfresca tutto e apre il pannello di controllo.
#
# Cosa fa, in ordine
# ------------------
#   1. alza il server del pannello di controllo se non gira gia'
#   2. radar/giornaliero.py     catalogo, schede, brief, report per nicchia
#   3. radar/sorgenti.py        da ogni campagna ai video da clippare, a secco
#   4. dashboard/metriche.py    le views dei video gia' pubblicati, e Whop
#   5. dashboard/analitiche.py  fonti, paesi e tenuta di ogni post
#   6. apre il browser sul pannello di controllo, con i dati appena scritti
#
# Perche' il browser si apre alla FINE
# ------------------------------------
# Aprirlo prima vorrebbe dire guardare i dati di ieri mentre il giro lavora, e
# una pagina che dice il falso per venti minuti e' peggio di una pagina che
# arriva venti minuti dopo. Il server invece parte subito: se durante l'attesa
# vuoi guardare, il pannello di controllo c'e' gia' all'indirizzo qui sotto.
#
# Perche' esiste anche questo file, e non solo il pulsante
# --------------------------------------------------
# I due pulsanti del pannello, "Aggiorna radar" (passi 2-3) e "Aggiorna social"
# (passi 4-5), fanno insieme gli stessi passi (server.PASSI), ma per
# premerli il pannello deve essere gia' su: questo file lo alza, e con -SoloApri
# non fa altro. Serve anche quando si vuole vedere il giro mentre lavora - qui
# c'e' una finestra che dice a che punto e', in pagina c'e' una barra.
#
# Dal 9 settembre 2026 il radar non gira piu' da solo alle 06:00: queste due
# strade sono le uniche che aggiornano, ed e' voluto.
#
# Niente caratteri fuori ASCII dentro le stringhe: PowerShell 5.1 legge un .ps1
# senza BOM come cp1252, e un trattino lungo dentro una stringa la termina a
# meta'. Nei commenti e' innocuo, nelle stringhe no.

param(
    [switch]$Veloce,          # salta i brief: il passo piu' lento, ~13 minuti
    [switch]$SoloApri,        # non rinfresca niente, apre e basta
    [int]$Porta = 8787
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$vpy  = "$here\.venv\Scripts\python.exe"
# pythonw non serve piu': il pannello lo tiene vigila.py, che deve poter scrivere
# il suo log e sotto pythonw non ha dove.
$pannello = "http://127.0.0.1:$Porta/"

if (-not (Test-Path $vpy)) { throw "manca $vpy - lo crea setup.ps1" }

function Passo($titolo) {
    Write-Host ""
    Write-Host ("=== " + $titolo + " " + ("=" * [Math]::Max(0, 60 - $titolo.Length))) -ForegroundColor Cyan
}

function Esegui($exe, $argomenti, $cartella, $seFallisce) {
    # Gli script di questo progetto scrivono la **diagnostica su stderr** e il
    # risultato su stdout: e' il contratto, non un incidente. Ma con
    # $ErrorActionPreference = "Stop" PowerShell trasforma ogni riga di stderr
    # di un programma esterno in un errore terminante, quindi il primo
    # "===== catalogo =====" fermava tutto il giro. Qui dentro si torna a
    # "Continue" e si guarda l'unica cosa che dice davvero come e' andata:
    # il codice di uscita.
    $precedente = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    Push-Location $cartella
    try {
        # Una riga di stderr arriva qui dentro un ErrorRecord, e il suo
        # ToString() stampa "System.Management.Automation.RemoteException"
        # invece del testo: il messaggio vero sta nell'eccezione.
        & $exe $argomenti 2>&1 | ForEach-Object {
            $testo = if ($_ -is [System.Management.Automation.ErrorRecord]) {
                $_.Exception.Message
            } else { "$_" }
            if ($testo.Trim()) { Write-Host "  $testo" }
        }
        $uscita = $LASTEXITCODE
    } finally {
        Pop-Location
        $ErrorActionPreference = $precedente
    }
    if ($uscita -ne 0) { Write-Host "  $seFallisce" -ForegroundColor Yellow }
    return $uscita
}

$inizio = Get-Date
Write-Host ""
Write-Host "  PANNELLO DI CONTROLLO viral-clips" -ForegroundColor White
Write-Host "  $pannello"

# --- 1. il server ---------------------------------------------------------
# Non si chiede se la porta e' aperta: si chiede se il pannello di controllo risponde.
# Il 6 settembre il processo che teneva la porta accettava la connessione e la
# chiudeva senza dire niente; il controllo sul socket diceva "server gia'
# acceso" e il pannello di controllo non si apriva. Una porta aperta prova che un processo e'
# vivo, non che stia servendo - ed e' la stessa specie di prova debole della
# cartella di profilo che dimostra il login.
$acceso = $false
try {
    $risposta = Invoke-WebRequest -Uri $pannello -TimeoutSec 8 -UseBasicParsing
    $acceso = ($risposta.StatusCode -eq 200)
} catch {
    $acceso = $false
}

if (-not $acceso) {
    # Se qualcuno tiene la porta senza rispondere va tolto di mezzo, altrimenti
    # il server nuovo non riesce a legarsi e si finisce dov'eravamo.
    $occupanti = Get-NetTCPConnection -LocalPort $Porta -State Listen -ErrorAction SilentlyContinue
    foreach ($o in $occupanti) {
        Write-Host ("  tolgo il processo " + $o.OwningProcess + " che teneva la porta senza rispondere")
        Stop-Process -Id $o.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    if ($occupanti) { Start-Sleep -Seconds 2 }

    # vigila.py, non server.py: sorveglia i sorgenti e rialza il pannello quando
    # cambiano, o quando muore da solo. Sotto pythonw non ha console e non puo'
    # scrivere il suo log, quindi si usa python.exe con l'uscita su file - senza,
    # un guasto del sorvegliante sarebbe invisibile, che e' il difetto che il
    # sorvegliante esiste per togliere.
    Start-Process -FilePath $vpy -ArgumentList "vigila.py --porta $Porta" `
        -WorkingDirectory "$here\dashboard" -WindowStyle Hidden `
        -RedirectStandardOutput "$here\dashboard\vigila.log" `
        -RedirectStandardError "$here\dashboard\vigila-errori.log"

    # E dopo si controlla di nuovo, invece di dare per riuscito un avvio.
    $vivo = $false
    foreach ($tentativo in 1..10) {
        Start-Sleep -Seconds 1
        try {
            if ((Invoke-WebRequest -Uri $pannello -TimeoutSec 5 -UseBasicParsing).StatusCode -eq 200) {
                $vivo = $true
                break
            }
        } catch { }
    }
    if ($vivo) {
        Write-Host "  server alzato e risponde"
    } else {
        Write-Host "  ATTENZIONE: il server non risponde su $pannello" -ForegroundColor Yellow
    }
} else {
    Write-Host "  server gia' acceso e risponde"
}

if (-not $SoloApri) {
    # --- 2. il radar ------------------------------------------------------
    # Ogni passo gira **dentro la sua cartella**: `giornaliero.py` chiama
    # catalogo.py, dettagli.py e briefs.py per nome relativo, e da un'altra
    # cartella non li trova. Lo stesso motivo per cui l'attivita' pianificata
    # ha un -WorkingDirectory.
    Passo "radar: catalogo, schede, brief, report"
    $argomenti = @("giornaliero.py")
    if ($Veloce) { $argomenti += "--salta-brief" }
    Esegui $vpy $argomenti "$here\radar" `
        "il radar si e' fermato: il pannello di controllo mostra l'ultima lettura buona" | Out-Null

    # --- 3. le sorgenti --------------------------------------------------
    # Dopo il radar, perche' legge le schede e i brief che il radar ha appena
    # riscritto. Con la cache delle impronte riapre la rete solo per le campagne
    # cambiate.
    Passo "sorgenti: da ogni campagna ai video da clippare"
    Esegui $vpy @("sorgenti.py") "$here\radar" `
        "sorgenti non risolte: il pannello di controllo mostra l'ultima lettura buona" | Out-Null

    # --- 4. le metriche ---------------------------------------------------
    # Vanno dopo il radar e non prima: leggono le clip gia' pubblicate, che il
    # radar non tocca, ma se il giro e' lungo e' meglio che le views siano le
    # piu' recenti delle due cose che il pannello di controllo mostra affiancate.
    Passo "metriche: views dei video pubblicati"
    Esegui $vpy @("metriche.py", "--youtube", "--account", "--whop") "$here\dashboard" `
        "metriche incomplete: la riga GUASTO dice quale account non si e' letto" | Out-Null

    # --- 5. le analitiche per post ----------------------------------------
    # Fonti, paesi, tenuta di ogni post: il passo piu' lungo dei social, circa un
    # quarto d'ora, perche' apre una pagina per post.
    Passo "analitiche: fonti, paesi e tenuta di ogni post"
    Esegui $vpy @("analitiche.py", "aggiorna") "$here\dashboard" `
        "analitiche incomplete: la riga sopra dice quale account non si e' letto" | Out-Null
}

# --- 6. il browser --------------------------------------------------------
$durata = (Get-Date) - $inizio
Write-Host ""
Write-Host ("  pronto in {0:mm\:ss}" -f $durata) -ForegroundColor Green
Start-Process $pannello
Start-Sleep -Seconds 2
