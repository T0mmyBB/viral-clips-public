# pianifica.ps1 - le attivita' giornaliere: pannello di controllo e pubblicazione.
#
#   09:00  ViralClips-Dashboard   dashboard/vigila.py - alza il pannello di controllo e apre il browser
#   15:15  ViralClips-Pubblica    publish/esegui.py - pubblica le clip il cui momento e' arrivato
#          (e di nuovo alle 19:15, 21:15 e 23:15)
#
# Perche' proprio cosi'
# --------------------
# L'AGGIORNAMENTO NON E' QUI, ED E' UNA SCELTA (9 settembre 2026). Il radar non
# gira piu' da solo alle 06:00: lo lancia il pulsante "Aggiorna" del pannello,
# che fa il giro del radar E le metriche - cioe' un passo in piu' di quanto
# facesse l'attivita' pianificata. Chi guarda i dati e' anche chi decide quando
# rinfrescarli.
#
# La conseguenza va detta, non nascosta: senza il giro delle 06:00 il catalogo
# e' vecchio quanto l'ultimo clic, e possono passare giorni. Per questo la prima
# riga del pannello dice QUANDO il radar ha girato: e' li' che si legge se cio'
# che si sta guardando vale ancora. Rimettere il giro automatico e' una riga -
# vedi in fondo a questo file.
#
# -StartWhenAvailable e' la voce che conta su un portatile: se alle 09:00 il PC
# e' spento, l'attivita' parte appena si accende invece di saltare il giorno.
# Senza, un'attivita' giornaliera diventa un'attivita' dei giorni in cui il PC
# era acceso a quell'ora, e non lo scopri finche' non guardi le date.
#
# Il pannello di controllo gira solo con l'utente collegato (-LogonType Interactive): apre un
# browser, e un browser aperto in una sessione che non esiste non lo vede nessuno.
#
# Niente caratteri fuori ASCII in questo file. PowerShell 5.1 legge un .ps1 senza
# BOM come cp1252, e i tre byte di un trattino lungo diventano tre caratteri di
# cui l'ultimo (0x94) e' una virgoletta di chiusura: dentro una stringa la
# termina a meta' e il file non parte piu'. Nei commenti e' innocuo, nelle
# stringhe no - e la differenza non si vede leggendo.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$vpy  = "$here\.venv\Scripts\python.exe"

if (-not (Test-Path $vpy)) { throw "manca $vpy - lo crea setup.ps1" }

$oraDashboard = "09:00"
# Piu' passaggi e non uno: l'esecutore chiede "cosa e' scaduto", non "che ora e'",
# quindi un orario mancato per l'ibernazione viene recuperato dal passaggio dopo
# invece di saltare la clip. Il ritmo non si scavalca comunque - a decidere se
# si puo' davvero pubblicare resta ritmo.py dentro l'orchestratore.
#
# Questi orari NON sono liberi: un passaggio deve cadere subito dopo ogni
# batch_time di calendario.py (19:00, 21:00, 23:00 italiane, scelte sul fuso
# americano). Una clip non puo' uscire prima del primo passaggio successivo al
# suo "quando", quindi spostare gli orari del calendario senza spostare questi
# non sposta niente: con i vecchi 09:15/13:15/18:15 una clip prevista per le
# 23:00 sarebbe uscita il mattino dopo, cioe' alle 03:15 a New York - esattamente
# l'ora da cui la si voleva togliere.
#
# Il primo passaggio, le 15:15, non ha un batch_time suo: e' il recupero di cio'
# che la macchina ha dormito durante la notte. Cade alle 09:15 ET, che e' l'ora
# meno peggio per una clip in ritardo.
#
# 21:45 e 00:15 sono i passaggi di Acme Show (19:00, 21:30, 00:00); 21:15 e 23:15
# restano per le consegne business, che escono ancora alle 19:00, 21:00, 23:00.
$orePubblica  = @("15:15", "19:15", "21:15", "21:45", "23:15", "00:15", "03:15")

Get-ScheduledTask -TaskName 'ViralClips-Radar', 'ViralClips-Dashboard', 'ViralClips-Pubblica' `
    -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false

$utente = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

# --- radar ---------------------------------------------------------------
# Nessuna attivita' registrata: l'aggiornamento e' manuale, dal pulsante del
# pannello. La riga di rimozione qui sopra toglie di mezzo la vecchia
# ViralClips-Radar, cosi' rilanciare questo file basta a portare la macchina
# nello stato voluto - una pianificazione che sopravvive alla decisione che
# l'ha tolta e' il difetto peggiore di tutti, perche' continua a girare mentre
# il file dice di no.
#
# Per rimetterlo automatico: rimetti $oraRadar in cima (era "06:00", tre ore
# prima del pannello perche' il giro dura una ventina di minuti e ogni tanto
# rallenta) e togli il commento a queste cinque righe.
#
#   $azione   = New-ScheduledTaskAction -Execute $vpy -Argument "giornaliero.py" -WorkingDirectory "$here\radar"
#   $trigger  = New-ScheduledTaskTrigger -Daily -At $oraRadar
#   $impost   = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries `
#                   -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
#                   -MultipleInstances IgnoreNew
#   Register-ScheduledTask -TaskName 'ViralClips-Radar' -Action $azione -Trigger $trigger `
#       -Settings $impost -Principal $utente -Force | Out-Null
Write-Host "Non registrata: ViralClips-Radar - l'aggiornamento lo fa il pulsante del pannello"

# --- pannello di controllo -------------------------------------------------------------
# vigila.py e non server.py: tiene su il pannello e lo rialza quando il codice
# cambia. Senza, ogni modifica al server resta invisibile finche' qualcuno non
# ammazza e rilancia il processo a mano - e chi lavora solo dal pannello non
# puo' farlo. Rialza il figlio anche se muore da solo.
$azione  = New-ScheduledTaskAction -Execute $vpy -Argument "vigila.py" -WorkingDirectory "$here\dashboard"
$trigger = New-ScheduledTaskTrigger -Daily -At $oraDashboard
$impost  = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries `
               -AllowStartIfOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'ViralClips-Dashboard' -Action $azione -Trigger $trigger `
    -Settings $impost -Principal $utente -Force | Out-Null
Write-Host "Registrata: ViralClips-Dashboard ($oraDashboard) - apre http://127.0.0.1:8787/"

# --- pubblicazione -------------------------------------------------------
# -LogonType Interactive perche' i tre agenti aprono un browser con il profilo
# gia' autenticato: senza una sessione utente vera, il caricamento non parte.
# Nessun -Force sul ritmo: se l'account e' in rampa l'orchestratore rifiuta e
# la clip si rimanda al giorno dopo, che e' il comportamento voluto.
$azione  = New-ScheduledTaskAction -Execute $vpy -Argument "esegui.py --davvero" -WorkingDirectory "$here\publish"
$trigger = $orePubblica | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ }
$impost  = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries `
               -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
               -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'ViralClips-Pubblica' -Action $azione -Trigger $trigger `
    -Settings $impost -Principal $utente -Force | Out-Null
Write-Host "Registrata: ViralClips-Pubblica ($($orePubblica -join ', ')) - pubblica cio' che e' scaduto"

Write-Host ""
Write-Host "Cambiare orario: modifica le variabili in cima e rilancia questo file."
Write-Host "Fermare le pubblicazioni: Disable-ScheduledTask -TaskName ViralClips-Pubblica"
Write-Host "Ultimo esito:    Get-ScheduledTask -TaskName 'ViralClips-*' | Get-ScheduledTaskInfo"
Write-Host "Aggiornare ora:  il pulsante Aggiorna su http://127.0.0.1:8787/ (oppure, da qui,"
Write-Host "                 .venv\Scripts\python.exe giornaliero.py dentro radar\)"
Write-Host "Annullare tutto: Get-ScheduledTask -TaskName 'ViralClips-*' | Unregister-ScheduledTask -Confirm:0"
Write-Host ""
Write-Host 'Rimettere il radar automatico: togli il commento al blocco "radar" qui sopra,'
Write-Host 'rimetti $oraRadar in cima e rilancia. Il perche della decisione (9 settembre'
Write-Host '2026) sta in CLAUDE.md: qui sta solo il come.'
