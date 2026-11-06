#!/usr/bin/env python
"""
server.py — il pannello di controllo. `http.server` della libreria standard, niente di
nuovo da installare: un solo utente, su localhost, con dati che stanno gia'
su disco. Vedi il "perche'" completo in README.md.

    python server.py                # alza il server e apre il browser
    python server.py --porta 8800   # un'altra porta
    python server.py --no-apri      # non apre il browser da solo

Rotte
-----
  GET  /                         la pagina (static/index.html)
  GET  /api/snapshot             tutto quello che sa stato.py
  GET  /api/verifica/youtube?url=...     prova esterna, sincrona (e' HTTP puro)
  GET  /api/storico/<post_id url-encoded>   la serie di misure di un post
  GET  /clip/<cartella>/<file>   il video, per l'anteprima nel browser
  GET  /api/aggiorna             a che punto e' il giro di aggiornamento
  POST /api/aggiorna             {cosa: radar | social | tutto}: lancia l'aggiornamento
                                 e torna subito (radar e sorgenti; o views, analitiche e Whop)
  POST /api/lavora               prepara o renderizza una campagna, in locale
  POST /api/scelte               sceglie una campagna, o la sposta fra attive e concluse
  POST /api/whop-mano            registra un invio Whop fatto a mano (o lo annulla)
  POST /api/pubblicato-mano      registra un post pubblicato a mano, col suo link

Quello che NON c'e' apposta: nessuna rotta che pubblica o invia niente. La
dashboard mostra il comando da incollare — un clic distratto su un pannello
e' una pubblicazione pubblica, e questa e' ancora la base. «Aggiorna» non fa
eccezione: legge il catalogo e le views, non manda niente a nessuno.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE.parent / "output"
STATIC_DIR = HERE / "static"

sys.path.insert(0, str(HERE))
import conversa  # noqa: E402
import metriche  # noqa: E402
import stato  # noqa: E402
import verifica  # noqa: E402

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".mp4": "video/mp4",
    ".json": "application/json",
}

# Quanto si legge per volta mandando un file. Serve a tenere la memoria piatta:
# le clip di `output/` hanno mediana 32.7 MB e arrivano a 71.6, e leggerle
# intere per mandarle era **tutto il file in RAM per ogni anteprima aperta** —
# per giunta su un server a thread, dove due schede aperte insieme sono due
# copie.
BLOCCO = 256 * 1024


class FuoriDalFile(Exception):
    """Un `Range` scritto bene ma che cade fuori dal contenuto: e' un 416."""


def intervallo_chiesto(intestazione: str, totale: int) -> tuple[int, int] | None:
    """
    Il pezzo di file chiesto da `Range: bytes=...`, o None se va servito tutto.

    Serve al `<video>` dell'anteprima: senza, il browser non puo' saltare a un
    istante — deve scaricare dall'inizio, e la barra di scorrimento o non si
    muove o riparte da capo. Con il 206 chiede solo i byte del punto in cui lo
    si trascina.

    **Un intervallo solo.** La forma a piu' pezzi (`bytes=0-9,20-29`) e' legale
    ma nessun browser la usa per un video, e risponderle vorrebbe dire scrivere
    un `multipart/byteranges`. Lo standard permette sempre di rispondere con
    l'intero contenuto, quindi qui None non e' una resa: e' l'uscita corretta.
    Vale anche per una sintassi incomprensibile — si serve tutto, e il browser
    se la cava.

    Fuori dal file invece **non** si serve tutto: sarebbe rispondere una cosa
    diversa da quella chiesta fingendo che vada bene. Si alza `FuoriDalFile`,
    che chi chiama traduce in 416.
    """
    if not intestazione:
        return None
    unita, _, valore = intestazione.partition("=")
    if unita.strip().lower() != "bytes" or "," in valore:
        return None
    inizio_txt, trattino, fine_txt = valore.strip().partition("-")
    if not trattino:
        return None
    try:
        if not inizio_txt:
            # `bytes=-N`: gli **ultimi** N byte, non «da qui a N».
            ultimi = int(fine_txt)
            if ultimi <= 0:
                raise FuoriDalFile(f"«ultimi {ultimi} byte» non vuol dire niente")
            inizio, fine = max(0, totale - ultimi), totale - 1
        else:
            inizio = int(inizio_txt)
            fine = int(fine_txt) if fine_txt else totale - 1
    except ValueError:
        return None
    fine = min(fine, totale - 1)
    if inizio > fine or inizio >= totale or inizio < 0:
        raise FuoriDalFile(f"chiesto {inizio}-{fine} di un file da {totale} byte")
    return inizio, fine


# --------------------------------------------------------------- aggiornamento

# Due tasti, due domande diverse (1 ottobre 2026). «Aggiorna radar» rilegge il
# catalogo delle campagne, e serve quando si cerca o si sceglie cosa lavorare;
# «Aggiorna social» rilegge i nostri numeri — views, segnali, analitiche per post,
# invii e pagamenti Whop — e serve ogni volta che si guarda come va. Insieme
# facevano mezz'ora per avere dei numeri che cambiano ogni ora, e le analitiche per
# post non le lanciava nessuno. Le sorgenti stanno col radar perche' leggono le
# schede e i brief che il radar ha appena riscritto; Whop sta coi social perche' e'
# un'altra lettura degli stessi post.
PASSI_RADAR = (
    ("radar", HERE.parent / "radar", ["giornaliero.py"]),
    ("sorgenti", HERE.parent / "radar", ["sorgenti.py"]),
)
PASSI_SOCIAL = (
    ("metriche", HERE, ["metriche.py", "--youtube", "--account", "--whop"]),
    ("analitiche", HERE, ["analitiche.py", "aggiorna"]),
)
# Il giro completo, radar per primo: e' quello di `aggiorna.ps1` dal desktop, e
# quello che lancia una pagina rimasta aperta da prima dei due tasti.
PASSI = PASSI_RADAR + PASSI_SOCIAL
AGGIORNAMENTI = {
    "radar": (PASSI_RADAR, "aggiornamento radar"),
    "social": (PASSI_SOCIAL, "aggiornamento social"),
    "tutto": (PASSI, "aggiornamento"),
}

# Lo stato del giro. Il lavoro e' di questo processo e muore con lui; il suo
# **resoconto** no. Fino al 7 ottobre 2026 stava solo in memoria: la notte
# prima la catena di Elliott Caldwell e' fallita dopo la trascrizione, il
# pannello si e' riavviato due minuti dopo per una modifica al codice, e
# l'errore — le venti righe che dicevano perche' — e' sparito con lui. Ora si
# scrive su disco a ogni passo, e un giro trovato «in corso» all'avvio si
# dichiara interrotto invece di svanire.
GIRO_FILE = HERE / "ultimo_lavoro.json"
GIRO_VUOTO = {
    "in_corso": False,
    "che": None,
    "iniziato_il": None,
    "finito_il": None,
    "passo": None,
    "passi": [],
    "esito": None,
}


def carica_giro(percorso: Path = GIRO_FILE) -> dict:
    try:
        giro = {**GIRO_VUOTO, **json.loads(percorso.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(GIRO_VUOTO)
    if giro.get("in_corso"):
        # Nessun processo vivo lo porta avanti: chi l'aveva lanciato non c'e'
        # piu'. «In corso» per sempre terrebbe spenti i pulsanti.
        giro.update(in_corso=False, passo=None, esito="interrotto")
    return giro


def salva_giro() -> None:
    """Scrive il resoconto del giro. Chi chiama tiene gia' `LUCCHETTO`."""
    try:
        provvisorio = GIRO_FILE.with_suffix(".tmp")
        provvisorio.write_text(json.dumps(GIRO, ensure_ascii=False, indent=1), encoding="utf-8")
        provvisorio.replace(GIRO_FILE)
    except OSError:
        pass


GIRO = carica_giro()
LUCCHETTO = threading.Lock()


def interprete() -> str:
    """
    Il python del venv, mai `sys.executable`.

    Il server gira sotto `pythonw.exe`, che non ha console: lanciarci dentro un
    figlio significa non poterne leggere l'uscita. Il fratello `python.exe` sta
    sempre accanto.
    """
    exe = Path(sys.executable)
    accanto = exe.with_name("python.exe")
    return str(accanto if accanto.exists() else exe)


# 0xC0000142: il processo figlio non e' riuscito ad avviarsi. Non e' un errore
# dello script — lanciato a mano finisce con 0 — ma di Windows, che dopo molte
# creazioni di processo da una sessione nascosta (il radar ne apre parecchi, e
# uno e' un browser) non ne fa partire un altro. Un secondo tentativo dopo
# qualche secondo passa. Ritentare un errore vero sarebbe sbagliato: questo si
# riconosce dal codice, ed e' l'unico che si ritenta.
AVVIO_FALLITO = 3221225794


# Il tetto di un passo che non ne dichiara uno: basta al radar e ai social. I
# lavori sulle clip dichiarano il loro, perche' un'ora era il tetto di tutto e la
# catena intera se ne concede dieci (`automatico.ATTESA`): sul server, dove la
# trascrizione va a due volte il tempo reale, un podcast da un'ora veniva
# interrotto a meta' — con l'errore di un timeout, non di un guasto.
ATTESA_PASSO = 3600
ORA = 3600


def _esegui(argomenti, cartella, attesa: int = ATTESA_PASSO) -> tuple[int, list[str]]:
    for tentativo in (1, 2):
        try:
            esito = subprocess.run(
                [interprete(), *argomenti],
                cwd=str(cartella),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=attesa,
            )
            # Venti righe e non tre. Le tre bastavano finche' l'errore lo leggeva
            # qualcuno con il terminale accanto; se il pannello e' l'unico posto
            # da cui si lavora, quelle tre righe sono **tutto** cio' che si sa di
            # un guasto, e la riga che spiega sta quasi sempre sopra le ultime tre
            # — sotto ci finiscono le righe di chiusura dello stack.
            coda = (esito.stderr or "").strip().splitlines()[-20:]
            if esito.returncode == AVVIO_FALLITO and tentativo == 1:
                time.sleep(8)
                continue
            return esito.returncode, coda
        except Exception as exc:
            return -1, [f"{type(exc).__name__}: {exc}"]
    return AVVIO_FALLITO, ["Windows non ha avviato il processo, due volte"]


def _gira(passi=PASSI) -> None:
    """Esegue i passi in fila. Gira in un thread: la pagina non aspetta."""
    for passo in passi:
        nome, cartella, argomenti = passo[:3]
        attesa = passo[3] if len(passo) > 3 else ATTESA_PASSO
        with LUCCHETTO:
            GIRO["passo"] = nome
        inizio = datetime.now()
        codice, coda = _esegui(argomenti, cartella, attesa)
        with LUCCHETTO:
            GIRO["passi"].append(
                {
                    "passo": nome,
                    "codice": codice,
                    "secondi": round((datetime.now() - inizio).total_seconds(), 1),
                    "coda": coda,
                }
            )
            salva_giro()
    with LUCCHETTO:
        GIRO["in_corso"] = False
        GIRO["passo"] = None
        GIRO["finito_il"] = datetime.now().isoformat(timespec="seconds")
        GIRO["esito"] = "ok" if all(p["codice"] == 0 for p in GIRO["passi"]) else "con errori"
        salva_giro()


def avvia(passi=PASSI, che: str = "aggiornamento") -> dict:
    """
    Lancia un lavoro se non ne sta gia' girando uno. Torna sempre lo stato.

    Uno alla volta, di proposito: sono tutti lavori che pesano sulla stessa
    macchina — il radar sulla rete, la trascrizione su tutti i core — e due
    insieme non finiscono prima, finiscono peggio. Il secondo clic trova quello
    in corso invece di aprirne un altro.
    """
    with LUCCHETTO:
        if GIRO["in_corso"]:
            return dict(GIRO)
        GIRO.update(
            {
                "in_corso": True,
                "che": che,
                "iniziato_il": datetime.now().isoformat(timespec="seconds"),
                "finito_il": None,
                "passo": None,
                "passi": [],
                "esito": None,
            }
        )
        salva_giro()
    threading.Thread(target=_gira, args=(passi,), daemon=True).start()
    with LUCCHETTO:
        return dict(GIRO)


RADICE = HERE.parent
SCRIPT = RADICE / ".claude" / "skills" / "viral-clips" / "scripts" / "run.py"


AUTOMATICO = SCRIPT.parent / "automatico.py"


def passi_lavoro(corpo: dict) -> tuple:
    """
    I passi del lavoro chiesto dalla pagina: preparare, renderizzare, o tutto.

    **«Prepara» e «renderizza» restano due**, perche' in mezzo c'e' il giudizio
    editoriale e chi vuole vedere le clip prima di pagarle il render deve poterlo
    fare. «Tutto» non li fonde: chiama `automatico.py`, che invoca Claude fra
    l'uno e l'altro e verifica quello che risponde. La differenza fra i due
    percorsi non e' quali passi girano — sono gli stessi — ma se qualcuno guarda
    in mezzo.
    """
    azione = corpo.get("azione")
    if azione == "tutto":
        sorgente = (corpo.get("sorgente") or "").strip()
        campagna_id = (corpo.get("campagna_id") or "").strip()
        if not sorgente.startswith(("http://", "https://")):
            raise ValueError("la sorgente deve essere un indirizzo http(s)")
        if not campagna_id:
            raise ValueError("senza campagna non c'e' nessun brief da leggere: usa «prepara»")
        return (
            (
                "tutto",
                RADICE,
                [str(AUTOMATICO), "--campagna", campagna_id, "--sorgente", sorgente],
                11 * ORA,
            ),
        )
    if azione == "prepara":
        sorgente = (corpo.get("sorgente") or "").strip()
        if not sorgente:
            raise ValueError("manca il link del video sorgente")
        if not sorgente.startswith(("http://", "https://")):
            raise ValueError("la sorgente deve essere un indirizzo http(s)")
        # La campagna viaggia col lavoro. Il pannello sa da che riga si e'
        # partiti, e finora se lo teneva: la cartella di lavoro non sapeva
        # nemmeno per chi stesse lavorando, e i paletti del brief — durata,
        # watermark che non si puo' tagliare, da quale episodio — restavano nel
        # documento che qualcuno avrebbe dovuto riaprire a mano.
        comando = [str(SCRIPT), "prepare", sorgente]
        campagna_id = (corpo.get("campagna_id") or "").strip()
        if campagna_id:
            comando += ["--campagna", campagna_id]
        return (("prepara", RADICE, comando, 3 * ORA),)
    if azione == "render":
        cartella = (corpo.get("cartella") or "").strip()
        lavoro = (RADICE / "work" / cartella).resolve()
        if not cartella or not lavoro.is_relative_to((RADICE / "work").resolve()):
            raise ValueError("cartella di lavoro non valida")
        if not (lavoro / "decisions.json").exists():
            raise ValueError("manca decisions.json: le clip non sono ancora state scelte")
        return (("render", RADICE, [str(SCRIPT), "render", str(lavoro)], 6 * ORA),)
    if azione == "pubblicazione":
        # Scrive didascalie e calendario. **Non pubblica**: alla fine esistono
        # dei file, e chi li esegue e' l'attivita' pianificata. La distinzione
        # non e' formale — se preparare e armare fossero lo stesso clic, non si
        # potrebbero piu' rigenerare le didascalie senza far partire dei post,
        # e rigenerarle e' proprio la cosa che si sbaglia e si rifa'.
        cartella = (corpo.get("cartella") or "").strip()
        uscita = (RADICE / "output" / cartella).resolve()
        if not cartella or not uscita.is_relative_to((RADICE / "output").resolve()):
            raise ValueError("cartella di uscita non valida")
        if not list(uscita.glob("*.mp4")):
            raise ValueError("nessuna clip renderizzata in questa cartella")
        return (
            (
                "prepara la pubblicazione",
                RADICE / "publish",
                [str(RADICE / "publish" / "prepara.py"), "--cartella", str(uscita)],
            ),
        )
    raise ValueError(f"azione sconosciuta: {azione}")


def iscrivi_in_sottofondo(campagna_id: str, gruppo: str) -> None:
    """
    Entra nella campagna appena scelta, sul conto Whop del gruppo.

    Fuori dalla fila dei lavori (`avvia`): e' un browser per un minuto, non pesa
    sulla trascrizione, e aspettare la fine di un render per iscriversi
    lascerebbe la campagna scelta e non lavorabile per ore. La serratura del
    profilo Whop la prende `orchestrator.py iscrivi`. Senza gruppo non si sa su
    quale conto entrare, e non si tenta. L'esito lo scrive l'orchestratore in
    `publish/iscrizioni.json`, e la pagina lo mostra sulla campagna.
    """
    log = open(HERE / "iscrizioni.log", "a", encoding="utf-8", errors="replace")
    subprocess.Popen(
        [interprete(), "orchestrator.py", "iscrivi", "--campagna", campagna_id, "--gruppo", gruppo],
        cwd=str(RADICE / "publish"),
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
    )


# Codice di uscita di chi trova la porta gia' servita da un'altra plancia.
# Lo legge `vigila.py`, che su questo si ferma invece di rialzare il figlio.
PORTA_OCCUPATA = 3


class Server(ThreadingHTTPServer):
    """
    Uguale a `ThreadingHTTPServer`, tranne che **rifiuta una porta occupata**.

    `socketserver` mette `allow_reuse_address = 1`, che su Unix serve a
    riprendersi una porta in TIME_WAIT. Su Windows quel flag significa un'altra
    cosa: consente a due processi di legarsi **alla stessa porta viva**, senza
    errore. Il risultato e' che il ripiego qui sotto — "la porta e' occupata,
    apro quella gia' aperta" — non scattava mai, e le richieste finivano a uno
    dei due server a caso. Successo davvero: l'attivita' pianificata delle 09:00
    e un avvio a mano hanno tenuto la 8787 in due.

    **Su Linux invece il flag serve, e toglierlo e' il guasto opposto** (7
    ottobre 2026, sul server di casa). Li' due processi sulla stessa porta viva
    non si legano comunque; senza il flag, pero', dopo un riavvio la porta resta
    in TIME_WAIT per un minuto: `vigila.py` rialzava il figlio, il figlio la
    trovava «occupata» e il sorvegliante si ritirava credendo che servisse
    un'altra plancia. Pannello spento, dopo ogni modifica al codice.
    """

    allow_reuse_address = sys.platform != "win32"


class Handler(BaseHTTPRequestHandler):
    server_version = "dashboard/0.1"

    def log_message(self, fmt, *args):  # meno rumore, ma non zittito del tutto
        sys.stderr.write(f"[{self.log_date_time_string()}] {fmt % args}\n")

    def _json(self, payload, codice: int = 200) -> None:
        corpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(codice)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def _file(self, path: Path) -> None:
        """Manda un file, tutto o il pezzo chiesto, leggendolo a blocchi."""
        if not path.exists() or not path.is_file():
            self.send_error(404, "non trovato")
            return
        totale = path.stat().st_size
        try:
            pezzo = intervallo_chiesto(self.headers.get("Range", ""), totale)
        except FuoriDalFile as fuori:
            # `Content-Range: bytes */<totale>` e' la parte che conta: dice al
            # browser quanto e' lungo davvero, cosi' la richiesta dopo e' giusta.
            self.send_response(416, str(fuori))
            self.send_header("Content-Range", f"bytes */{totale}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        inizio, fine = pezzo if pezzo else (0, totale - 1)
        quanti = fine - inizio + 1 if totale else 0
        self.send_response(206 if pezzo else 200)
        self.send_header("Content-Type", MIME.get(path.suffix, "application/octet-stream"))
        # Va dichiarato **anche** nella risposta intera: e' cosi' che il browser
        # sa di poter chiedere un pezzo la prossima volta. Senza, molti non ci
        # provano nemmeno e la barra di scorrimento resta finta.
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(quanti))
        if pezzo:
            self.send_header("Content-Range", f"bytes {inizio}-{fine}/{totale}")
        self.end_headers()

        try:
            with path.open("rb") as f:
                f.seek(inizio)
                while quanti > 0:
                    dati = f.read(min(BLOCCO, quanti))
                    if not dati:
                        break
                    self.wfile.write(dati)
                    quanti -= len(dati)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Il browser ha mollato a meta' invio. **Non e' un guasto, ed e' la
            # norma da quando il 206 funziona**: trascinare la barra significa
            # abbandonare il pezzo che stava arrivando per chiederne un altro.
            # Va zittito qui, perche' altrimenti lo raccoglie `do_GET` e prova a
            # rispondere 500 su una connessione gia' chiusa — cioe' una seconda
            # risposta dentro la prima, su un socket morto.
            pass

    def do_GET(self) -> None:  # noqa: N802 (nome imposto da BaseHTTPRequestHandler)
        url = urllib.parse.urlsplit(self.path)
        parti = [p for p in url.path.split("/") if p]
        query = urllib.parse.parse_qs(url.query)

        try:
            if not parti:
                self._file(STATIC_DIR / "index.html")
            elif parti[0] == "api" and parti[1:2] == ["snapshot"]:
                self._json(stato.snapshot())
            elif parti[0] == "api" and parti[1:2] == ["conversazione"]:
                # Leggera apposta: la pagina la chiede ogni due secondi mentre
                # aspetta una risposta, e `snapshot()` costa troppo per quello.
                self._json(conversa.stato())
            elif parti[0] == "api" and parti[1:2] == ["verifica"] and parti[2:3] == ["youtube"]:
                u = (query.get("url") or [""])[0]
                self._json(verifica.verifica_youtube(u) if u else {"errore": "manca ?url="})
            elif parti[0] == "api" and parti[1:2] == ["storico"]:
                post_id = urllib.parse.unquote(url.path.split("/api/storico/", 1)[1])
                self._json(metriche.storico(post_id))
            elif parti[0] == "api" and parti[1:2] == ["metriche"]:
                self._json(metriche.ultime())
            elif parti[0] == "api" and parti[1:2] == ["aggiorna"]:
                with LUCCHETTO:
                    self._json(dict(GIRO))
            elif parti[0] == "clip" and len(parti) == 3:
                cartella, file = urllib.parse.unquote(parti[1]), urllib.parse.unquote(parti[2])
                candidato = (OUTPUT_DIR / cartella / file).resolve()
                if candidato.is_relative_to(OUTPUT_DIR.resolve()):
                    self._file(candidato)
                else:
                    self.send_error(403, "percorso fuori da output/")
            else:
                candidato = STATIC_DIR / url.path.lstrip("/")
                if candidato.resolve().is_relative_to(STATIC_DIR.resolve()) and candidato.exists():
                    self._file(candidato)
                else:
                    self.send_error(404, "rotta sconosciuta")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Chi chiedeva se n'e' andato: non c'e' piu' nessuno a cui dire 500,
            # e provarci scriverebbe su un socket chiuso.
            pass
        except Exception as exc:  # una pagina rotta non deve fermare il server
            self._json({"errore": str(exc)}, 500)

    def do_POST(self) -> None:  # noqa: N802
        """
        Le scritture esposte: la scelta di una campagna e l'invio Whop fatto a mano.

        Continua a non esserci nessuna rotta che pubblica o invia — un clic
        distratto su una plancia resta una pubblicazione pubblica. Scegliere
        una campagna invece non manda niente a nessuno: registra una decisione
        che prima viveva in un messaggio di chat, e che gli agenti leggono
        quando li lanci tu. «Inviato a mano» registra un gesto gia' fatto
        altrove, sul pannello di Whop: la pagina non invia, prende nota.
        """
        url = urllib.parse.urlsplit(self.path)
        parti = [p for p in url.path.split("/") if p]
        try:
            lunghezza = int(self.headers.get("Content-Length") or 0)
            corpo = json.loads(self.rfile.read(lunghezza) or b"{}")
            if parti[:2] == ["api", "aggiorna"]:
                # Legge il catalogo o i numeri: nessun invio, nessuna
                # pubblicazione. Torna subito, il giro continua in un thread.
                cosa = corpo.get("cosa") or "tutto"
                if cosa not in AGGIORNAMENTI:
                    self._json({"errore": f"aggiornamento sconosciuto: {cosa}"}, 400)
                    return
                self._json(avvia(*AGGIORNAMENTI[cosa]))
                return
            if parti[:2] == ["api", "lavora"]:
                # Prepara o renderizza. Gira tutto in locale: scarica, trascrive
                # e taglia. Non pubblica niente — quello resta fuori da qui.
                self._json(avvia(passi_lavoro(corpo), corpo.get("azione", "lavoro")))
                return
            if parti[:2] == ["api", "chiedi"]:
                # La domanda libera. Da qui in poi succede la stessa cosa dei
                # pulsanti — parte `claude -p` — solo che la richiesta l'ha
                # scritta l'utente invece di stare nel codice.
                self._json(conversa.chiedi(corpo.get("testo", "")))
                return
            if parti[:2] == ["api", "dimentica"]:
                self._json(conversa.dimentica())
                return
            if parti[:2] == ["api", "whop-mano"]:
                # Registra un invio fatto a mano sul pannello di Whop. Non invia
                # niente: scrive `inviato a mano` sul record di quella
                # pubblicazione, con la stessa serratura dell'esecutore.
                self._json(
                    stato.segna_inviato_a_mano(
                        (corpo.get("cartella") or "").strip(),
                        (corpo.get("clip") or "").strip(),
                        (corpo.get("piattaforma") or "").strip().lower(),
                        annulla=bool(corpo.get("annulla")),
                    )
                )
                return
            if parti[:2] == ["api", "pubblicato-mano"]:
                # Registra un post pubblicato a mano (dal telefono). Non
                # pubblica niente: scrive il record, e da li' l'esecutore
                # considera quella coppia gia' uscita.
                self._json(
                    stato.segna_pubblicato_a_mano(
                        (corpo.get("cartella") or "").strip(),
                        (corpo.get("clip") or "").strip(),
                        (corpo.get("piattaforma") or "").strip().lower(),
                        corpo.get("url") or "",
                        whop_inviato=bool(corpo.get("whop_inviato")),
                    )
                )
                return
            if parti[:2] != ["api", "scelte"]:
                self.send_error(404, "rotta sconosciuta")
                return
            if not corpo.get("campagna_id"):
                self._json({"errore": "manca campagna_id"}, 400)
                return
            nuova = not corpo.get("rimuovi") and not any(
                s.get("campagna_id") == corpo["campagna_id"] for s in stato.scelte()
            )
            scelte = stato.scrivi_scelta(
                campagna_id=corpo["campagna_id"],
                gruppo=corpo.get("gruppo"),
                stato=corpo.get("stato", "da-lanciare"),
                nota=corpo.get("nota", ""),
                rimuovi=bool(corpo.get("rimuovi")),
                nicchia=corpo.get("nicchia"),
            )
            if nuova and corpo.get("gruppo"):
                iscrivi_in_sottofondo(corpo["campagna_id"], corpo["gruppo"])
            self._json({"scelte": scelte, "lavoro": stato.lavoro()})
        except ValueError as exc:
            self._json({"errore": str(exc)}, 400)
        except Exception as exc:
            self._json({"errore": str(exc)}, 500)


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="La plancia di comando")
    ap.add_argument("--porta", type=int, default=8787)
    ap.add_argument("--no-apri", action="store_true", help="non aprire il browser da solo")
    # Sul portatile il pannello e' solo per chi ci sta davanti. Sul server lo si
    # guarda da un'altra macchina, e il container sta solo in LAN e su Tailscale:
    # li' gira con `--host 0.0.0.0`. Mai su una macchina raggiungibile da fuori,
    # perche' i pulsanti e «Chiedi» fanno lavorare la macchina per chi li preme.
    ap.add_argument("--host", default="127.0.0.1", help="indirizzo su cui ascoltare")
    args = ap.parse_args()

    url = f"http://127.0.0.1:{args.porta}/"
    try:
        httpd = Server((args.host, args.porta), Handler)
    except OSError:
        # La porta e' occupata: quasi sempre da una plancia gia' aperta. Da
        # quando l'apertura e' pianificata ogni mattina questo e' il caso
        # normale, non un errore — si apre il browser su quella e si esce
        # senza fare rumore. Un secondo server sulla stessa cartella non
        # servirebbe a niente comunque: lo stato sta nei file, non qui.
        #
        # Ma **non si esce con 0**: per chi ha lanciato questo processo lo zero
        # vuol dire "ho fatto il mio lavoro e ho finito", e il sorvegliante lo
        # legge come un figlio morto da rialzare — rialzandolo per sempre, due
        # volte al secondo. Il codice PORTA_OCCUPATA dice l'altra cosa: "non ho
        # servito niente perche' serve gia' qualcun altro".
        print(f"porta {args.porta} gia' occupata: apro la plancia esistente")
        if not args.no_apri:
            webbrowser.open(url)
        sys.exit(PORTA_OCCUPATA)
    print(f"dashboard su {url}  (Ctrl+C per fermare)")
    if not args.no_apri:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
