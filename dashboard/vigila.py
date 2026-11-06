"""Tiene su il pannello e lo rialza quando il codice cambia.

    python vigila.py [--porta 8787]

**Perche' esiste.** Il pannello e' il posto da cui si lavora, e chi lo modifica e' Claude
mentre ci lavori dentro. Ma il codice Python vive nel processo: cambiarlo non basta,
bisogna ammazzare e rilanciare. In una sola giornata e' successo quattro volte, e ogni
volta l'errore che si vedeva era «azione sconosciuta: <cosa nuova>» — un messaggio che
non dice affatto «riavvia», e che manda a cercare il difetto dove non c'e'. Con il
pannello come unico strumento, quel riavvio nessuno lo puo' fare.

Quindi: questo processo guarda i `.py` della cartella e, quando uno cambia, rialza il
figlio. I file statici (`app.js`, `index.html`, `style.css`) non servono qui — quelli il
browser li rilegge da solo con un Ctrl+F5, perche' il server li serve dal disco a ogni
richiesta.

**Non riavvia mentre un lavoro gira.** Un render da venti minuti o una pubblicazione a
meta' non si interrompono per una modifica al codice: si aspetta che il giro finisca. La
domanda «sta girando qualcosa?» la si fa al pannello stesso, che e' l'unico a saperlo.

**Un riavvio non e' un successo finche' la pagina non risponde.** Dopo aver rialzato il
figlio si aspetta un 200 vero: un processo che occupa la porta senza servire e' gia'
successo su questa macchina, e da fuori sembra identico a uno sano.

**Un sorvegliante di troppo si ritira, non insiste.** Se la porta e' gia' servita da
un'altra plancia, questo processo non ne alza una seconda: il figlio troverebbe la porta
occupata e uscirebbe subito, e «uscito subito» qui vuol dire «rialzalo», due volte al
secondo per sempre. E' successo il 9 settembre 2026 — l'attivita' pianificata ha alzato un
secondo sorvegliante mentre quello del giorno prima era ancora vivo, e nessuno dei due
segnalava un guasto: la pagina rispondeva 200 (era l'altro a servirla), quindi anche il
controllo «risponde?» diceva di si'.

**E un figlio che muore in fretta si riprova piano.** Se il pannello non parte per un
difetto nel suo codice, rilanciarlo ogni due secondi riempie il log e non lo aggiusta;
ma nemmeno si smette, perche' la correzione arriva da una modifica al file e va colta
appena atterra. Quindi la pausa cresce fino a `PAUSA_MAX`, e torna a zero al primo avvio
che regge.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

QUI = Path(__file__).resolve().parent

# Ogni quanto si guarda il disco. Due secondi: abbastanza da non far aspettare
# chi ha appena chiesto una modifica, abbastanza poco da non pesare.
PAUSA = 2.0

# Quanto si aspetta che il figlio risponda dopo un riavvio, prima di dire che
# non ce l'ha fatta.
ATTESA_VIVO = 25

# Sotto questa soglia di vita il figlio non e' "morto durante il servizio": non
# e' proprio partito. Si conta come tentativo fallito e si rallenta.
VITA_BREVE = 15.0

# Il tetto della pausa fra due tentativi falliti di fila.
PAUSA_MAX = 60.0

# Il codice con cui `server.py` dice «non ho servito niente, la porta e' gia' di
# un'altra plancia». Deve coincidere con `server.PORTA_OCCUPATA`; non lo si
# importa perche' un sorvegliante che importa il codice sorvegliato muore
# insieme al suo primo errore di sintassi — cioe' quando serve di piu'. Se i due
# numeri divergono resta comunque il controllo sulla porta, che non usa codici.
PORTA_OCCUPATA = 3


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", file=sys.stderr, flush=True)


# Sta nella cartella ma non e' il pannello: `apri.py` e' il collegamento sul
# desktop, gira e finisce. Riavviare il server perche' e' cambiato lui vuol dire
# far cadere la pagina di chi sta lavorando per un file che quella pagina non
# usa.
FUORI_DAL_PANNELLO = {"apri.py"}


def sorgenti() -> dict[str, float]:
    """I file il cui cambiamento richiede un riavvio, col loro momento di modifica."""
    fuori = {}
    for p in sorted(QUI.glob("*.py")):
        if p.name == Path(__file__).name:
            continue  # se cambia questo, lo rilancia una persona
        if p.name in FUORI_DAL_PANNELLO:
            continue
        try:
            fuori[p.name] = p.stat().st_mtime
        except OSError:
            continue
    return fuori


def risponde(porta: int, entro: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{porta}/", timeout=entro) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def occupato(porta: int) -> bool:
    """Se il pannello sta eseguendo un lavoro adesso.

    Nel dubbio risponde **si'**: non sapere se un render sta girando e riavviare
    lo stesso e' il modo di ammazzarlo. Si riprova al giro dopo.
    """
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{porta}/api/aggiorna", timeout=3) as r:
            import json

            return bool(json.loads(r.read().decode("utf-8")).get("in_corso"))
    except Exception:
        return True


def avvia(porta: int) -> subprocess.Popen:
    exe = Path(sys.executable)
    accanto = exe.with_name("python.exe")
    cmd = [
        str(accanto if accanto.exists() else exe),
        "server.py",
        "--porta",
        str(porta),
        "--no-apri",
    ]
    return subprocess.Popen(cmd, cwd=str(QUI))


def ferma(figlio: subprocess.Popen) -> None:
    figlio.terminate()
    try:
        figlio.wait(timeout=10)
    except subprocess.TimeoutExpired:
        figlio.kill()
        figlio.wait(timeout=10)


def aspetta_vivo(porta: int) -> bool:
    scade = time.time() + ATTESA_VIVO
    while time.time() < scade:
        if risponde(porta):
            return True
        time.sleep(1)
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description="Tiene su il pannello e lo rialza da solo")
    ap.add_argument("--porta", type=int, default=8787)
    args = ap.parse_args()

    # Un secondo sorvegliante non serve a niente e fa danno: si accorge subito
    # che qualcuno sta gia' servendo e se ne va prima di alzare un figlio.
    if risponde(args.porta):
        log(
            f"c'e' gia' una plancia viva su http://127.0.0.1:{args.porta}/ — non ne alzo "
            f"una seconda"
        )
        return

    figlio = avvia(args.porta)
    nato = time.monotonic()
    if aspetta_vivo(args.porta):
        log(f"pannello su http://127.0.0.1:{args.porta}/ (pid {figlio.pid})")
    else:
        log("ATTENZIONE: il pannello non risponde dopo l'avvio")
    visti = sorgenti()
    falliti = 0

    while True:
        time.sleep(PAUSA)

        # Il figlio morto da solo si rialza comunque: un guasto non deve
        # lasciare il pannello spento fino al prossimo intervento umano.
        if figlio.poll() is not None:
            codice = figlio.returncode
            # ...tranne quando a servire e' un'altra plancia. Qui rialzare e'
            # un ciclo infinito, non una cura: il figlio ritroverebbe la porta
            # occupata e riuscirebbe subito, all'infinito.
            if codice == PORTA_OCCUPATA or risponde(args.porta):
                log(f"la porta {args.porta} e' servita da un'altra plancia: mi ritiro")
                return

            falliti = falliti + 1 if time.monotonic() - nato < VITA_BREVE else 0
            log(f"il pannello e' uscito da solo (codice {codice}), lo rialzo")
            figlio = avvia(args.porta)
            nato = time.monotonic()
            aspetta_vivo(args.porta)
            visti = sorgenti()
            if falliti:
                # Non parte. Si continua a riprovare — la correzione arriva da
                # una modifica al file — ma piano, e dicendo quanto si aspetta.
                attesa = min(PAUSA_MAX, PAUSA * 2**falliti)
                log(f"{falliti} avvii falliti di fila: riprovo fra {attesa:.0f}s")
                time.sleep(attesa)
            continue

        adesso = sorgenti()
        cambiati = [n for n, t in adesso.items() if visti.get(n) != t]
        if not cambiati:
            continue

        if occupato(args.porta):
            log(f"cambiati {', '.join(cambiati)} — aspetto: c'e' un lavoro in corso")
            continue

        log(f"cambiati {', '.join(cambiati)} — riavvio")
        ferma(figlio)
        figlio = avvia(args.porta)
        nato = time.monotonic()
        visti = adesso
        falliti = 0
        if aspetta_vivo(args.porta):
            log("riavviato e risponde")
        else:
            log("ATTENZIONE: dopo il riavvio il pannello non risponde")


if __name__ == "__main__":
    # Il log contiene trattini lunghi, e su console cp1252 un carattere fuori
    # tabella fa morire chi lo scrive: il sorvegliante non puo' cadere sul
    # proprio messaggio.
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
