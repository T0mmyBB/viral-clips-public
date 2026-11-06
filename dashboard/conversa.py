"""La conversazione con Claude, dentro il pannello.

Il pannello sa gia' chiamare Claude, ma solo con domande scritte nel codice: «leggi il
brief e scegli le clip», «scrivi le didascalie». Sono i pulsanti, e fanno le cose per cui
sono stati costruiti. Tutto il resto — «la didascalia della 3 e' troppo lunga», «perche'
la 05 non e' uscita?», «sposta Contoso di una settimana» — non ha un pulsante e non puo'
averlo: sono richieste infinite, e costruire un tasto per ognuna e' il modo di non
finirle mai.

Quindi qui la domanda la scrive l'utente. Cambia **chi** formula la richiesta, non cosa
succede dopo: da `claude -p` in poi il percorso e' identico a quello dei pulsanti.

**Il filo si tiene con `--session-id`.** Senza, ogni messaggio sarebbe una conversazione
nuova che non ricorda la precedente, e «rifalla piu' corta» non avrebbe un antecedente.
L'identificativo si scrive su disco: sopravvive al riavvio del pannello, che con il
sorvegliante puo' capitare in mezzo a una conversazione.

**Lo storico su disco e' la conversazione, non un registro.** Il pannello lo rilegge per
disegnare la pagina, quindi va scritto a ogni scambio e non alla fine: un processo che
muore a meta' non deve portarsi via la domanda che l'utente ha appena fatto.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path

QUI = Path(__file__).resolve().parent
RADICE = QUI.parent
STORICO = QUI / "conversazione.json"

# Gli stessi attrezzi degli altri punti in cui il pannello chiama Claude. E'
# deliberato che possa modificare i file: e' il motivo per cui esiste questa
# casella. Non si usa `--dangerously-skip-permissions`.
ATTREZZI = ("Read", "Write", "Edit", "Glob", "Grep", "Bash")

# Scritti qui e non ereditati da `settings.json`, che sono le preferenze della
# chat. Opus a `xhigh` perche' le richieste vanno da «accorcia la didascalia» a
# «perche' la 05 non e' uscita», e con `Bash` una risposta sbagliata puo' essere
# un'azione che non torna indietro.
#
# L'id e' scritto per intero, non l'alias `opus`: l'alias lo risolve la versione
# del CLI, e il 22 settembre 2026 `claude update` l'ha spostato da Opus 5 a Opus
# 5.5 senza che nessuno lo decidesse. Con l'id pieno un CLI troppo vecchio si
# ferma con un errore che dice quale versione serve. L'effort resta `xhigh`
# perche' qui non e' stato misurato: le domande di questa casella non hanno una
# risposta di riferimento con cui confrontarle.
MODELLO = "claude-opus-5-5"
EFFORT = "xhigh"

# Un tetto alto: una richiesta puo' voler dire «renderizza», che dura decine di
# minuti. Un tetto stretto non protegge da niente e ammazza il lavoro buono.
ATTESA = 3 * 3600

_LUCCHETTO = threading.Lock()
_IN_CORSO = {"attivo": False, "domanda": None, "da": None}


def leggi() -> dict:
    try:
        return json.loads(STORICO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"sessione": None, "scambi": []}


def salva(d: dict) -> None:
    STORICO.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def stato() -> dict:
    """Cosa mostrare in pagina: lo storico e se c'e' una risposta in arrivo."""
    d = leggi()
    with _LUCCHETTO:
        corso = dict(_IN_CORSO)
    return {
        "scambi": d.get("scambi", []),
        "in_corso": corso["attivo"],
        "domanda_in_corso": corso["domanda"],
        "da": corso["da"],
        "sessione": d.get("sessione"),
    }


def _claude() -> str:
    """Il percorso vero: su Windows e' `claude.CMD` e subprocess non applica PATHEXT."""
    trovato = shutil.which("claude")
    if not trovato:
        raise RuntimeError("'claude' non trovato: serve Claude Code installato")
    return trovato


def _esegui(testo: str) -> None:
    """Una domanda, la sua risposta, e lo storico aggiornato in ogni caso."""
    d = leggi()
    sessione = d.get("sessione")
    cmd = [
        _claude(),
        "-p",
        testo,
        "--model",
        MODELLO,
        "--effort",
        EFFORT,
        "--permission-mode",
        "acceptEdits",
        "--allowed-tools",
        *ATTREZZI,
    ]
    # La prima volta si fissa l'identificativo, dopo lo si riprende. Sono due
    # flag diversi: `--session-id` crea, `--resume` continua.
    if sessione:
        cmd += ["--resume", sessione]
    else:
        sessione = str(uuid.uuid4())
        cmd += ["--session-id", sessione]

    inizio = datetime.now()
    try:
        esito = subprocess.run(
            cmd,
            cwd=str(RADICE),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=ATTESA,
        )
        risposta = (esito.stdout or "").strip()
        errore = (
            None
            if esito.returncode == 0
            else ((esito.stderr or "").strip()[-2000:] or f"uscito con {esito.returncode}")
        )
    except subprocess.TimeoutExpired:
        risposta, errore = "", f"nessuna risposta entro {ATTESA // 3600} ore"
    except Exception as exc:
        risposta, errore = "", f"{type(exc).__name__}: {exc}"

    d = leggi()  # rileggo: il file puo' essere cambiato
    d["sessione"] = sessione
    d.setdefault("scambi", []).append(
        {
            "quando": inizio.isoformat(timespec="seconds"),
            "domanda": testo,
            "risposta": risposta,
            "errore": errore,
            "secondi": round((datetime.now() - inizio).total_seconds(), 1),
        }
    )
    salva(d)
    with _LUCCHETTO:
        _IN_CORSO.update({"attivo": False, "domanda": None, "da": None})


def chiedi(testo: str) -> dict:
    """Avvia una domanda in un thread. La pagina non aspetta: chiede lo stato."""
    testo = (testo or "").strip()
    if not testo:
        raise ValueError("la domanda e' vuota")
    with _LUCCHETTO:
        if _IN_CORSO["attivo"]:
            raise ValueError("c'e' gia' una domanda in corso: aspetta la risposta")
        _IN_CORSO.update(
            {"attivo": True, "domanda": testo, "da": datetime.now().isoformat(timespec="seconds")}
        )
    threading.Thread(target=_esegui, args=(testo,), daemon=True).start()
    return stato()


def dimentica() -> dict:
    """Chiude il filo: il prossimo messaggio apre una conversazione nuova.

    Non cancella lo storico. Una conversazione lunga a un certo punto conviene
    ricominciarla — il contesto si riempie di cose vecchie — ma quello che ci si
    e' detti resta leggibile, perche' spesso e' li' che sta il perche' di una
    decisione presa giorni prima.
    """
    d = leggi()
    d["sessione"] = None
    d.setdefault("scambi", []).append(
        {
            "quando": datetime.now().isoformat(timespec="seconds"),
            "domanda": None,
            "risposta": None,
            "errore": None,
            "separatore": "da qui in poi e' una conversazione nuova",
        }
    )
    salva(d)
    return stato()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(json.dumps(chiedi(" ".join(sys.argv[1:])), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(stato(), ensure_ascii=False, indent=1))
