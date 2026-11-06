#!/usr/bin/env python
"""
ospita.py — un indirizzo pubblico per una clip, per il tempo di una pubblicazione.

    python ospita.py <file.mp4>            # espone, verifica da fuori, chiude
    python ospita.py <file.mp4> --tieni    # resta su finche' non si preme Invio

Perche' esiste
--------------
L'API di Buffer non riceve file: vuole un indirizzo https **pubblico e diretto**
e va a prendersi il video quando il post esce (`buffer_agent.py`). Le clip
stanno su questo disco, e il progetto non carica video da nessuna parte. Quindi
il file non si sposta: si apre un tunnel verso un server locale che serve **quel
file solo**, sotto un percorso che nessuno puo' indovinare, e lo si chiude appena
il post e' uscito.

Il tunnel e' `cloudflared` in modalita' rapida (`trycloudflare.com`): gratis,
senza conto, un indirizzo nuovo a ogni lancio. Non e' un servizio garantito, ed
e' per questo che l'indirizzo si **prova da fuori** prima di consegnarlo: un
tunnel che non risponde scoperto da Buffer e' un post fallito, scoperto qui e'
un tentativo da rifare.

Il server non e' il pannello. Quello ha pulsanti che scrivono; questo risponde a
un percorso e basta, e tutto il resto e' 404.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# Dove lo mette `setup.ps1`, accanto agli altri strumenti esterni. Si cerca li'
# e poi sul PATH: un percorso solo sarebbe vero su questa macchina e basta.
CLOUDFLARED = ROOT / ".tools" / "cloudflared" / ("cloudflared.exe" if os.name == "nt" else "cloudflared")

# Quanto si aspetta che il tunnel dia il suo indirizzo, e poi che l'indirizzo
# risponda da fuori. Il secondo e' piu' lungo del primo: il nome esiste prima
# che il DNS lo conosca ovunque.
ATTESA_INDIRIZZO = 40.0
ATTESA_RAGGIUNGIBILE = 60.0

BLOCCO = 256 * 1024

_INDIRIZZO = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
# La riga con cui `cloudflared` dice che il tunnel e' agganciato alla rete di
# Cloudflare. L'indirizzo viene annunciato **prima**, quando il nome non esiste
# ancora.
_AGGANCIATO = "Registered tunnel connection"
# Il respiro fra l'aggancio e la prima richiesta al nome.
RESPIRO_DNS = 4.0
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def indirizzo_dal_log(testo: str) -> str | None:
    """
    L'indirizzo pubblico del tunnel, ma solo quando il tunnel e' **agganciato**.

    `cloudflared` annuncia l'indirizzo qualche secondo prima che il nome esista.
    Chiederlo in quel momento non costa un tentativo: il «non esiste» resta in
    memoria nel risolutore a monte, e il nome continua a non esistere **da
    questa macchina** per minuti — al primo collaudo, sessanta secondi di
    tentativi tutti falliti su un tunnel che da fuori rispondeva. Quindi
    l'indirizzo non si consegna finche' il log non dice che il tunnel e' su.
    """
    testo = testo or ""
    m = _INDIRIZZO.search(testo)
    return m.group(0) if m and _AGGANCIATO in testo else None


def intervallo(intestazione: str, totale: int) -> tuple[int, int] | None:
    """
    I byte chiesti da `Range: bytes=a-b`, estremi compresi, o None per tutto il file.

    Chi scarica un video da decine di MB lo fa a pezzi e riprende dopo
    un'interruzione: senza il 206 ogni ripresa ricomincia da zero. Un `Range`
    che non si capisce vale «tutto», non un errore: e' quello che fa un server
    che i `Range` non li conosce, e nessun client se ne lamenta.
    """
    m = _RANGE.match((intestazione or "").strip())
    if not m or totale <= 0:
        return None
    da, a = m.group(1), m.group(2)
    if not da and not a:
        return None
    if not da:  # bytes=-N: gli ultimi N
        inizio, fine = max(0, totale - int(a)), totale - 1
    else:
        inizio, fine = int(da), (int(a) if a else totale - 1)
    fine = min(fine, totale - 1)
    if inizio > fine:
        return None
    return inizio, fine


@dataclass
class Esposizione:
    url: str
    # Una riga per richiesta arrivata da fuori: (metodo, byte serviti). Serve a
    # sapere se chi doveva venire a prendersi il file e' venuto davvero.
    richieste: list[tuple[str, int]] = field(default_factory=list)

    def scaricato(self, totale: int) -> bool:
        """Se da fuori sono stati letti almeno tanti byte quanti ne pesa il file."""
        return sum(n for metodo, n in self.richieste if metodo == "GET") >= totale


def _gestore(file: Path, percorso: str, registro: list[tuple[str, int]]):
    totale = file.stat().st_size

    class Gestore(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):  # il log lo teniamo noi, in `registro`
            pass

        def _rispondi(self, corpo: bool) -> None:
            if self.path.split("?", 1)[0] != percorso:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            pezzo = intervallo(self.headers.get("Range", ""), totale)
            inizio, fine = pezzo if pezzo else (0, totale - 1)
            self.send_response(206 if pezzo else 200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(fine - inizio + 1))
            if pezzo:
                self.send_header("Content-Range", f"bytes {inizio}-{fine}/{totale}")
            self.end_headers()
            mandati = 0
            if corpo:
                try:
                    with file.open("rb") as fh:
                        fh.seek(inizio)
                        resta = fine - inizio + 1
                        while resta > 0:
                            dati = fh.read(min(BLOCCO, resta))
                            if not dati:
                                break
                            self.wfile.write(dati)
                            mandati += len(dati)
                            resta -= len(dati)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    # Chi scarica a pezzi abbandona le risposte in volo: e' la
                    # norma, non un guasto (stessa cosa in `dashboard/server.py`).
                    pass
            registro.append((self.command, mandati))

        def do_GET(self):
            self._rispondi(corpo=True)

        def do_HEAD(self):
            self._rispondi(corpo=False)

    return Gestore


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # Quando il tunnel chiude, le connessioni tenute aperte cadono: e' la
        # fine prevista, e venti righe di traccia su stderr la farebbero
        # sembrare un guasto proprio in fondo a una pubblicazione riuscita.
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


def trova_cloudflared() -> str:
    if CLOUDFLARED.exists():
        return str(CLOUDFLARED)
    sul_path = shutil.which("cloudflared")
    if sul_path:
        return sul_path
    sys.exit(
        f"manca cloudflared: lo cercavo in {CLOUDFLARED} e sul PATH.\n"
        "  Lo scarica publish/setup.ps1 (setup.sh su Linux); a mano e' un file solo, da\n"
        "  https://github.com/cloudflare/cloudflared/releases/latest/download/"
        + ("cloudflared-windows-amd64.exe" if os.name == "nt" else "cloudflared-linux-amd64")
    )


def raggiungibile(url: str, entro: float = ATTESA_RAGGIUNGIBILE) -> bool:
    """
    Se l'indirizzo risponde **da fuori**, con il primo byte del file.

    Si chiede un byte e non una HEAD: e' la stessa strada che fara' chi scarica,
    e un tunnel che risponde alle intestazioni ma non passa il corpo esiste.
    """
    scadenza = time.monotonic() + entro
    richiesta = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
    while time.monotonic() < scadenza:
        try:
            with urllib.request.urlopen(richiesta, timeout=10) as r:
                if r.status in (200, 206) and r.read(1):
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    return False


@contextmanager
def esposto(file: Path):
    """
    Espone `file` su un indirizzo pubblico finche' si resta dentro il blocco.

    Consegna un'`Esposizione` solo dopo aver letto l'indirizzo **da fuori**. Se
    il tunnel non parte o non risponde, alza `RuntimeError` prima che qualcuno
    abbia fatto qualcosa che non torna indietro.
    """
    file = Path(file).resolve()
    if not file.is_file() or file.stat().st_size == 0:
        raise RuntimeError(f"niente da esporre: {file} non c'e' o e' vuoto")

    visto: list[tuple[str, int]] = []
    # Il gettone e' l'unica protezione del file per i minuti in cui e' fuori:
    # 32 caratteri casuali, mai scritti in un log di terzi prima dell'uso.
    percorso = f"/{secrets.token_urlsafe(24)}/{file.name}"
    server = _Server(("127.0.0.1", 0), _gestore(file, percorso, visto))
    porta = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    tunnel = subprocess.Popen(
        [trova_cloudflared(), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{porta}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    righe: list[str] = []

    def leggi() -> None:
        # Fino alla fine, non solo fino all'indirizzo: un tubo che nessuno
        # svuota si riempie e ferma il processo dall'altra parte.
        for riga in tunnel.stderr:
            righe.append(riga)

    threading.Thread(target=leggi, daemon=True).start()
    try:
        base = None
        scadenza = time.monotonic() + ATTESA_INDIRIZZO
        while time.monotonic() < scadenza and tunnel.poll() is None:
            base = indirizzo_dal_log("".join(righe))
            if base:
                break
            time.sleep(0.5)
        if not base:
            coda = "".join(righe[-8:]).strip()
            raise RuntimeError(f"il tunnel non ha dato un indirizzo:\n{coda}")
        time.sleep(RESPIRO_DNS)
        url = base + percorso
        if not raggiungibile(url):
            raise RuntimeError(f"il tunnel c'e' ma da fuori non risponde: {base}")
        # La prova qui sopra e' nostra: chi legge `richieste` vuole sapere di
        # chi e' venuto dopo.
        visto.clear()
        yield Esposizione(url=url, richieste=visto)
    finally:
        tunnel.terminate()
        try:
            tunnel.wait(timeout=10)
        except subprocess.TimeoutExpired:
            tunnel.kill()
        server.shutdown()
        server.server_close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Un indirizzo pubblico temporaneo per una clip")
    ap.add_argument("file")
    ap.add_argument("--tieni", action="store_true", help="resta su finche' non si preme Invio")
    args = ap.parse_args()
    file = Path(args.file)
    try:
        with esposto(file) as e:
            log(f"esposto e raggiungibile da fuori: {file.name} ({file.stat().st_size} byte)")
            print(e.url, flush=True)
            if args.tieni:
                input()
                log(f"richieste arrivate: {len(e.richieste)}")
    except RuntimeError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
