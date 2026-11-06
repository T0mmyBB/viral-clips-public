"""
notifica.py — un avviso di Windows quando serve una persona, e subito.

Lo usa `orchestrator.py` quando un invio Whop non va a buon fine: il post e'
gia' online, e la finestra per registrarlo nella campagna e' di trenta minuti
dalla pubblicazione. L'avviso in pagina c'e' (la coda «da inviare a mano» del
pannello), ma la pubblicazione gira da un'attivita' pianificata, cioe' quando
nessuno sta guardando il pannello: senza una notifica la finestra si chiude
prima che qualcuno se ne accorga.

Niente moduli da installare: PowerShell parla gia' con le notifiche di Windows
(`Windows.UI.Notifications`). Titolo e testo passano per variabili d'ambiente,
non dentro lo script, cosi' un apice in un titolo di clip non rompe niente.

**Non ferma mai chi la chiama.** Una notifica che non parte non e' un guasto
della pubblicazione: torna False e basta.
"""

from __future__ import annotations

import os
import subprocess
import sys
import urllib.request
from pathlib import Path

# L'identita' con cui PowerShell mostra le notifiche: registrata da Windows,
# non serve crearne una.
APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

SCRIPT = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
    "ContentType = WindowsRuntime] > $null;"
    "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
    "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
    "$r = $t.GetElementsByTagName('text');"
    "$r.Item(0).AppendChild($t.CreateTextNode($env:NOTIFICA_TITOLO)) > $null;"
    "$r.Item(1).AppendChild($t.CreateTextNode($env:NOTIFICA_TESTO)) > $null;"
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:NOTIFICA_APP)"
    ".Show([Windows.UI.Notifications.ToastNotification]::new($t))"
)


# Sul server non c'e' uno schermo da cui far comparire un avviso: la notifica va
# al telefono via ntfy, sullo stesso argomento delle altre automazioni del
# server. L'argomento e' un segreto (chi lo conosce scrive sul telefono), quindi
# sta in un file 600 fuori dal repo e non qui.
NTFY_TOPIC = Path.home() / ".config" / "ntfy-topic"


def avvisa_ntfy(titolo: str, testo: str) -> bool:
    try:
        topic = NTFY_TOPIC.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if not topic:
        return False
    richiesta = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=testo.encode("utf-8"),
        # Le intestazioni HTTP sono latin-1: un titolo con un carattere fuori
        # (un trattino lungo, un'emoji) farebbe fallire la richiesta intera.
        headers={"Title": titolo.encode("latin-1", "replace").decode("latin-1"), "Priority": "high"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(richiesta, timeout=20) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def avvisa(titolo: str, testo: str) -> bool:
    """Mostra una notifica di Windows, o la manda via ntfy fuori da Windows.
    True se e' partita."""
    if sys.platform != "win32":
        return avvisa_ntfy(titolo, testo)
    ambiente = {
        **os.environ,
        "NOTIFICA_TITOLO": titolo,
        "NOTIFICA_TESTO": testo,
        "NOTIFICA_APP": APP_ID,
    }
    try:
        esito = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", SCRIPT],
            env=ambiente,
            capture_output=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return False
    return esito.returncode == 0


if __name__ == "__main__":
    ok = avvisa(
        sys.argv[1] if len(sys.argv) > 1 else "viral-clips",
        sys.argv[2] if len(sys.argv) > 2 else "prova della notifica",
    )
    print("notifica mostrata" if ok else "notifica non partita")
    sys.exit(0 if ok else 1)
