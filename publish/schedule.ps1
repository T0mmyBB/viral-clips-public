# schedule.ps1 — quattro finestre giornaliere, UNA clip per volta.
#
# Il ritmo e' la parte che conta: un account che pubblica a raffica viene
# strozzato in silenzio. Quattro esecuzioni distanti ore l'una dall'altra
# danno post naturalmente distanziati, e il tetto giornaliero dentro
# publish_agent.py fa da secondo freno (rampa inclusa per gli account nuovi).
#
# Le attivita' girano con l'utente corrente: PC acceso e non in standby.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$vpy  = "$here\.venv\Scripts\python.exe"
$times = @("09:00", "12:30", "17:00", "20:30")

Get-ScheduledTask -TaskName 'ViralClips-Publish-*' -ErrorAction SilentlyContinue |
    Unregister-ScheduledTask -Confirm:$false

foreach ($t in $times) {
    $name = "ViralClips-Publish-$($t.Replace(':',''))"
    $action  = New-ScheduledTaskAction -Execute $vpy -Argument "publish_agent.py run --limit 1" -WorkingDirectory $here
    $trigger = New-ScheduledTaskTrigger -Daily -At $t
    $set     = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $set -Force | Out-Null
    Write-Host "Registrata: $name ($t) — 1 clip"
}
Write-Host ""
Write-Host "Massimo 4 clip al giorno, una per finestra. Il tetto reale lo decide"
Write-Host "publish_agent.py in base a 'max_per_day' e 'account_started' in publish.json."
Write-Host ""
Write-Host "Per annullare tutto:"
Write-Host "  Get-ScheduledTask -TaskName 'ViralClips-Publish-*' | Unregister-ScheduledTask -Confirm:0"
