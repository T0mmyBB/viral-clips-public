"""Apre il pannello nel browser, alzandolo se non e' su.

    pythonw.exe apri.py [--porta 8787] [--server lavoro]

**Perche' non e' un `.ps1`.** E' quello che fa il collegamento sul desktop, e un
collegamento che apre una console non e' un collegamento a una pagina: e' un terminale con
un indirizzo dentro, da cui l'indirizzo si finisce per copiare a mano. `powershell.exe`
una finestra la crea comunque, anche con `-WindowStyle Hidden`; `pythonw.exe` non ne ha
proprio una. Da qui la regola: **il collegamento non deve mostrare niente che non sia la
pagina.**

**E non aggiorna niente.** Dal 9 settembre 2026 il giro del radar lo lancia il pulsante
«Aggiorna radar» del pannello, i numeri dei post «Aggiorna social». Un collegamento che aggiorna prima di aprire fa aspettare minuti
davanti a una console per una pagina che, quasi sempre, era gia' pronta — ed e' esattamente
il motivo per cui l'indirizzo si copiava a mano invece di aspettare. Il giro completo dal
desktop resta a portata: `aggiorna.ps1`.

**Un guasto senza console va detto lo stesso.** Se il pannello non si alza non c'e' nessun
posto dove leggerlo, quindi lo dice una finestrella di Windows che indica il log. Sparire
in silenzio e' il difetto che questo progetto insegue ovunque; qui sarebbe pure il piu'
facile da commettere.

**Con `--server` il pannello non si alza qui: si raggiunge** (7 ottobre 2026). Dal passaggio
al server di casa il pannello gira la' e ascolta solo su 127.0.0.1, quindi dalla rete non si
vede. Si apre un tunnel ssh che porta la stessa porta sul portatile: la pagina e' la stessa,
all'indirizzo di sempre, e nessuno sulla rete di casa puo' premere i suoi pulsanti.
"""

from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

QUI = Path(__file__).resolve().parent

# Quanto si concede al pannello per rispondere dopo che lo si e' alzato. Sopra
# `vigila.ATTESA_VIVO`, perche' qui si aspetta il sorvegliante *e* suo figlio.
ATTESA_VIVO = 30

# Come si avvia un figlio senza far comparire una finestra nera. Serve a ogni
# processo lanciato da qui: sotto pythonw.exe non c'e' console da ereditare, e
# senza questo flag Windows ne apre una nuova per il figlio.
SENZA_FINESTRA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def risponde(porta: int, entro: float = 3.0) -> bool:
    """Se il pannello **serve**, non se la porta e' aperta.

    Una porta aperta prova che un processo e' vivo, non che stia servendo: il
    6 settembre 2026 il processo che teneva la 8787 accettava la connessione e
    la chiudeva senza dire niente, e il controllo sul socket rispondeva "acceso".
    """
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{porta}/", timeout=entro) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def occupanti(porta: int) -> list[int]:
    """I pid in ascolto sulla porta. Vuoto se non si riesce a chiederlo."""
    try:
        esito = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=SENZA_FINESTRA,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    pid = []
    for riga in esito.stdout.splitlines():
        campi = riga.split()
        if len(campi) >= 5 and campi[3] == "LISTENING" and campi[1].endswith(f":{porta}"):
            try:
                pid.append(int(campi[4]))
            except ValueError:
                continue
    return pid


def libera(porta: int) -> None:
    """Toglie di mezzo chi tiene la porta **senza rispondere**.

    Si arriva qui solo dopo che `risponde()` ha detto di no: quel processo non
    sta servendo nessuno, e finche' c'e' il server nuovo non riesce a legarsi.
    """
    for pid in occupanti(porta):
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"], capture_output=True, creationflags=SENZA_FINESTRA
        )
    if occupanti(porta):
        time.sleep(2)


def alza(porta: int) -> None:
    """Avvia `vigila.py`, non `server.py`: il pannello va tenuto su, non solo acceso.

    Con `python.exe` e l'uscita su file, mai con `pythonw.exe`: il sorvegliante
    senza console non ha dove scrivere il suo log, e un guasto del sorvegliante
    invisibile e' il difetto che il sorvegliante esiste per togliere. La finestra
    non compare lo stesso, ci pensa `SENZA_FINESTRA`.
    """
    exe = Path(sys.executable)
    accanto = exe.with_name("python.exe")
    fuori = open(QUI / "vigila.log", "a", encoding="utf-8", errors="replace")
    errori = open(QUI / "vigila-errori.log", "a", encoding="utf-8", errors="replace")
    subprocess.Popen(
        [str(accanto if accanto.exists() else exe), "vigila.py", "--porta", str(porta)],
        cwd=str(QUI),
        stdout=fuori,
        stderr=errori,
        creationflags=SENZA_FINESTRA,
    )


def tunnel(porta: int, server: str) -> None:
    """Porta la porta del pannello del server su questa macchina, con ssh.

    `ExitOnForwardFailure`: se la porta qui e' presa, ssh esce invece di restare
    su senza inoltrare niente — che da fuori sembra un pannello spento. Gli
    `ServerAlive` chiudono un tunnel rimasto appeso dopo una sospensione, cosi'
    il clic dopo ne apre uno nuovo invece di trovare la porta tenuta da un morto.
    """
    errori = open(QUI / "tunnel-errori.log", "a", encoding="utf-8", errors="replace")
    subprocess.Popen(
        [
            "ssh", "-N",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-o", "BatchMode=yes",
            "-L", f"{porta}:127.0.0.1:{porta}",
            server,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=errori,
        creationflags=SENZA_FINESTRA,
    )


def avverti(testo: str) -> None:
    """L'unico modo di farsi sentire quando non c'e' una console."""
    try:
        ctypes.windll.user32.MessageBoxW(None, testo, "Pannello viral-clips", 0x10)
    except Exception:
        print(testo, file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description="Apre il pannello, alzandolo se serve")
    ap.add_argument("--porta", type=int, default=8787)
    ap.add_argument("--server", default="", help="host ssh dove gira il pannello (es. lavoro)")
    args = ap.parse_args()
    url = f"http://127.0.0.1:{args.porta}/"

    if not risponde(args.porta):
        libera(args.porta)
        if args.server:
            tunnel(args.porta, args.server)
        else:
            alza(args.porta)
        scade = time.time() + ATTESA_VIVO
        while time.time() < scade and not risponde(args.porta):
            time.sleep(1)

    # Il browser si apre solo su una pagina che c'e'. Aprirlo comunque darebbe
    # un errore di connessione, che sembra un guasto del browser e manda a
    # cercare il difetto dove non e'.
    if not risponde(args.porta):
        log = "tunnel-errori.log" if args.server else "vigila-errori.log"
        dove = f" (sul server {args.server}, via tunnel ssh)" if args.server else ""
        avverti(
            f"Il pannello non risponde su {url}{dove}.\n\n"
            f"Il perche' e' nelle ultime righe di\n{QUI / log}"
        )
        return 1

    webbrowser.open(url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
