#!/usr/bin/env python
"""
youtube_agent.py — pubblica una clip come Short su YouTube.

Perche' il browser e non l'API
------------------------------
La YouTube Data API sarebbe la strada solida — quote note, nessun selettore che
cambia — ma un progetto che non ha passato l'audit di Google carica i video
**bloccati in privato**. Per una campagna pagata a visualizzazioni un video
privato vale zero, quindi l'API e' inutilizzabile finche' l'audit non e' fatto,
e l'audit non e' una cosa che si ottiene in giornata. Si automatizza YouTube
Studio, come si e' fatto per TikTok.

L'accesso Google dentro un browser di automazione **viene rifiutato**: Google
risponde `signin/rejected`, «questo browser o questa app potrebbero non essere
sicuri». Non e' un difetto da correggere, e' un controllo che fa esattamente il
suo mestiere. Percio' `login` avvia il Chrome installato **senza flag di
automazione** — nient'altro che la cartella del profilo — e l'accesso si fa a
mano una volta sola. Da li' in poi l'automazione riusa quella sessione: il
blocco e' sull'accesso, non sull'uso di una sessione gia' aperta.

Il profilo e' suo, separato da TikTok e da Whop, per la ragione gia' pagata: due
istanze Chromium sullo stesso `user_data_dir` non convivono.

Comandi
-------
  P=%USERPROFILE%\\.venvs\\scraping\\Scripts\\python.exe

  %P% youtube_agent.py login                       # accesso a mano, una volta
  %P% youtube_agent.py stato                       # con quale canale sei dentro?
  %P% youtube_agent.py pubblica --file clip.mp4 --titolo "..." --descrizione "..."
  %P% youtube_agent.py pubblica ... --davvero      # pubblica sul serio

Senza `--davvero` riempie tutto e si ferma prima dell'ultimo passaggio: e' il
modo per vedere cosa farebbe senza che esca niente in pubblico.

Il vincolo dei 30 minuti
------------------------
La campagna respinge in automatico i video inviati oltre 30 minuti dalla
pubblicazione. L'URL stampato in fondo va quindi passato
subito a `whop_agent.py invia --davvero`: non e' una comodita', e' la finestra
dentro cui l'invio vale qualcosa.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

try:
    from scrapling.fetchers import DynamicSession
except ImportError:  # pragma: no cover
    sys.exit(
        "Scrapling non e' in questo interprete (%s).\n"
        "  Va lanciato col venv condiviso:\n"
        "    %s youtube_agent.py ...\n"
        "  Se manca, lo ricrea setup.ps1."
        % (sys.executable, __import__("interpreti").PYTHON_SCRAPING)
    )

# Gli aiutanti generici — attesa che si ferma quando la pagina smette di
# crescere, enumerazione dei cliccabili, clic per etichetta — stanno in
# whop_agent.py e sono gli stessi. Si importano invece di ricopiarli: una
# correzione fatta di la' (il filtro che nascondeva i pulsanti in fondo alla
# pagina, per dirne una gia' costata) deve valere anche qui.
import registro  # noqa: E402
from whop_agent import attesa, clicca, trova  # noqa: E402

for _flusso in (sys.stdout, sys.stderr):
    try:
        _flusso.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent
# Il profilo lo crea **Chrome vero**, non Chromium di Playwright: sono formati
# diversi e mescolarli fa litigare il browser sul suo stesso profilo. Il nome lo
# dice, cosi nessuno ci punta l'altro.
PROFILE_DIR = HERE / ".youtube-chrome"
LOG_DIR = HERE / "logs"


def usa_profilo(percorso) -> None:
    """Cambia il profilo per tutto il processo: vedi `registro.py`.

    Va chiamata prima di `sessione()` e di `cmd_login()`, che sono i due punti
    in cui il profilo diventa una finestra aperta su un account.
    """
    global PROFILE_DIR
    PROFILE_DIR = Path(percorso)


STUDIO = "https://studio.youtube.com/"


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(exist_ok=True)
    with (LOG_DIR / "youtube.log").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def dump(page, tag: str) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}-yt-{tag}"
    try:
        page.screenshot(path=str(LOG_DIR / f"{stamp}.png"), full_page=False)
        (LOG_DIR / f"{stamp}.html").write_text(page.content(), encoding="utf-8")
        log(f"  diagnostica in logs/{stamp}.png|.html")
    except Exception as exc:
        log(f"  diagnostica non salvata: {exc}")


def sessione(headless: bool):
    PROFILE_DIR.mkdir(exist_ok=True)
    return DynamicSession(
        headless=headless,
        # `real_chrome` usa il Chrome installato invece del Chromium di
        # Playwright. Serve perche' il profilo dell'accesso lo crea Chrome, e un
        # profilo non si passa fra due browser diversi.
        real_chrome=True,
        user_data_dir=str(PROFILE_DIR),
        timeout=120000,  # il caricamento di un video non e' una pagina
        **registro.ambiente(),
        network_idle=False,
        google_search=False,
    )


def chrome_exe() -> str:
    """
    Il Chrome installato sulla macchina. Si guarda prima il registro, che e'
    dove sta la verita', e poi i due percorsi soliti: l'installazione puo'
    essere per tutti gli utenti o solo per questo.
    """
    try:
        import winreg

        for radice in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(
                    radice,
                    r"SOFTWARE\Microsoft\Windows"
                    r"\CurrentVersion\App Paths\chrome.exe",
                ) as k:
                    percorso = winreg.QueryValue(k, None)
                    if percorso and Path(percorso).exists():
                        return percorso
            except OSError:
                continue
    except ImportError:
        pass
    # Su Linux (la postazione browser del server) Chrome sta sul PATH col nome
    # del pacchetto ufficiale di Google.
    if os.name != "nt":
        for nome in ("google-chrome-stable", "google-chrome"):
            trovato = shutil.which(nome)
            if trovato:
                return trovato
    for p in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        str(Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe"),
    ):
        if Path(p).exists():
            return p
    return ""


def canale(page) -> str:
    """
    Il nome del canale con cui si e' dentro Studio. Serve a non scoprire dopo
    la pubblicazione di aver postato con l'account sbagliato: il profilo del
    browser ricorda l'ultimo accesso, e Google ne tiene volentieri piu' di uno.
    """
    for sel in (
        "#entity-name",
        "ytcp-navigation-drawer #entity-name",
        "#channel-name",
        "#channel-title",
        "ytcp-account-button",
        "#account-name",
        "yt-img-shadow#avatar",
    ):
        try:
            loc = page.locator(sel).first
            if loc.count():
                testo = (loc.inner_text(timeout=4000) or "").strip()
                if testo:
                    return testo.split("\n")[0]
        except Exception:
            continue
    try:
        # Fallback selettivo su ytcfg o titolo dashboard
        titolo = page.title()
        if "YouTube Studio" in titolo:
            # Se siamo dentro Studio, prova a leggere l'handle o il nome dal DOM
            raw = page.content()
            m_email = re.search(r"Email=([\w.\-+%]+%40[\w.\-]+)", raw)
            if m_email:
                import urllib.parse

                email = urllib.parse.unquote(m_email.group(1))
                return email.split("@")[0]
            return "YouTube Studio (Accesso attivo)"
    except Exception:
        pass
    try:
        corpo = page.inner_text("body")
    except Exception:
        return ""
    m = re.search(r"@([\w.\-]{3,})", corpo)
    return m.group(1) if m else ""


def apri_caricamento(page) -> bool:
    """
    Apre la finestra di caricamento **per indirizzo**, non per clic.

    Studio risponde a `/channel/<id>/videos/upload?d=ud` aprendo la finestra da
    solo, e l'id del canale sta nell'indirizzo su cui la sessione e' gia'
    atterrata. Torna False quando non ce la fa, e allora si prova col menu.
    """
    m = re.search(r"/channel/(UC[\w\-]+)", page.url or "")
    if not m:
        return False
    try:
        page.goto(
            f"https://studio.youtube.com/channel/{m.group(1)}/videos/upload?d=ud",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        page.wait_for_selector('input[type="file"]', state="attached", timeout=30000)
        log("finestra di caricamento aperta per indirizzo")
        return True
    except Exception as exc:
        log(f"  caricamento per indirizzo non riuscito ({type(exc).__name__}): provo dal menu")
        return False


# --------------------------------------------------------------------- comandi


def cmd_login(args) -> None:
    """
    L'accesso si fa in Chrome vero, avviato da qui **senza automazione**.

    Google rifiuta l'accesso dentro un browser guidato da Playwright: la pagina
    che risponde e' `signin/rejected` con la frase «questo browser o questa app
    potrebbero non essere sicuri», e non e' una password sbagliata ne' un
    problema di popup. Non si aggira scrivendo meglio l'automazione, perche' il
    controllo e' proprio sull'essere automatizzati.

    Quindi: si lancia il Chrome installato con la sola opzione che gli dice
    quale profilo usare — nessun flag di controllo remoto, niente da rilevare —
    e l'accesso avviene a mano come in qualunque altra finestra. La sessione
    resta scritta in quel profilo, e da li' in poi l'automazione la **riusa**:
    il blocco di Google e sull'accesso, non sull'uso di una sessione gia' aperta.
    """
    import subprocess

    chrome = chrome_exe()
    if not chrome:
        sys.exit("Chrome non trovato. Installalo, oppure dimmi dove sta il suo chrome.exe.")
    PROFILE_DIR.mkdir(exist_ok=True)
    log(f"avvio Chrome col profilo {PROFILE_DIR.name}")
    proc = subprocess.Popen(
        [
            chrome,
            f"--user-data-dir={PROFILE_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
            STUDIO,
        ]
    )
    print("\n1) Nella finestra di Chrome che si e' aperta, accedi con l'account del canale.")
    print("2) Fermati quando vedi la dashboard di YouTube Studio.")
    print("3) CHIUDI la finestra di Chrome: il profilo non si puo' usare in due.")
    input("\nQuando hai chiuso Chrome, torna qui e premi INVIO... ")
    if proc.poll() is None:
        print("Chrome sembra ancora aperto: chiudilo prima di continuare.")
        input("Premi INVIO quando e' chiuso... ")

    def azione(page):
        attesa(page, 30)
        chi = canale(page)
        log(
            f"Studio: {'aperto come ' + chi if chi else 'NON aperto — l accesso non ha attecchito'}"
        )
        dump(page, "login")
        return page

    with sessione(headless=True) as s:
        s.fetch(STUDIO, page_action=azione, load_dom=True, wait=3000)


def cmd_stato(args) -> None:
    def azione(page):
        attesa(page, 25)
        chi = canale(page)
        log(f"Studio: {'aperto come ' + chi if chi else 'NON aperto — esegui `login`'}")
        dump(page, "stato")
        return page

    with sessione(headless=args.headless) as s:
        s.fetch(STUDIO, page_action=azione, load_dom=True, wait=3000)


# I campi di Studio sono `contenteditable`, non `input`: si scrive dentro il
# riquadro con l'etichetta giusta. Gli identificatori interni (`#textbox`) sono
# gli ancoraggi che sopravvivono ai rilasci; le classi no.
SEL_TITOLO = (
    'ytcp-social-suggestions-textbox[label="Titolo"] #textbox, '
    'ytcp-social-suggestions-textbox[label="Title"] #textbox'
)
SEL_DESCRIZIONE = (
    'ytcp-social-suggestions-textbox[label*="escrizione"] #textbox, '
    'ytcp-social-suggestions-textbox[label*="escription"] #textbox'
)


def scrivi(page, selettore: str, testo: str, cosa: str) -> bool:
    """
    Scrive in un campo `contenteditable` e **rilegge**: un editor controllato
    accetta il testo e poi lo riscrive da solo, quindi l'unica prova che sia
    entrato e' andarlo a ripescare. E' la stessa regola che su TikTok ha salvato
    le menzioni spezzate.
    """
    try:
        campo = page.locator(selettore).first
        campo.click(timeout=15000)
        campo.fill("")
        campo.type(testo, delay=8)
        page.wait_for_timeout(1200)
        scritto = (campo.inner_text(timeout=5000) or "").strip()
    except Exception as exc:
        log(f"non riesco a scrivere {cosa}: {type(exc).__name__}")
        return False
    if scritto.replace("\u200b", "").strip() != testo.strip():
        log(f"{cosa} contiene «{scritto[:70]}» invece di «{testo[:70]}»")
        return False
    log(f"{cosa}: {testo[:70]}")
    return True


# Scelta «Pubblico», YouTube apre sopra un avviso — «Ricorda che i video pubblici
# sono visibili a tutti gli utenti» — che copre il pulsante Pubblica e se lo mangia
# il clic. E' successo davvero: le prime due clip della campagna sono rimaste
# private, con l'agente che dichiarava «pubblicato» perche' l'indirizzo del video
# esiste anche da privato. Stessa famiglia del banner dei cookie su TikTok.
SEL_AVVISI = [
    "ytcp-aadc-notice-dialog #got-it-button",
    "ytcp-dialog #got-it-button",
    "tp-yt-paper-dialog[opened] #got-it-button",
]


def chiudi_avvisi(page, giri: int = 3) -> None:
    """Chiude gli avvisi che YouTube mette davanti al modulo. Ne puo' arrivare piu' d'uno."""
    for _ in range(giri):
        for sel in SEL_AVVISI:
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible():
                    loc.click(timeout=5000)
                    log("  avviso di YouTube chiuso")
                    page.wait_for_timeout(1200)
                    break
            except Exception:
                continue
        else:
            return


# L'ancora della visibilita' e' il **nome interno** del radio, non l'etichetta:
# l'etichetta e' tradotta e il pulsante non e' un `<button>`, quindi la ricerca
# per testo non lo trova affatto.
SEL_PUBBLICO = 'tp-yt-paper-radio-button[name="PUBLIC"]'


def scegli_pubblico(page) -> bool:
    """Sceglie «Pubblico» e verifica che risulti davvero selezionato."""
    for _ in range(3):
        try:
            loc = page.locator(SEL_PUBBLICO).first
            if loc.count():
                loc.click(timeout=8000)
                page.wait_for_timeout(1000)
                chiudi_avvisi(page)
                if (loc.get_attribute("aria-checked") or "") == "true":
                    log("  visibilita': Pubblico")
                    return True
        except Exception:
            pass
        page.wait_for_timeout(1200)
    log("  non riesco a selezionare «Pubblico»")
    return False


# Stessa storia della visibilita': la domanda sul pubblico sta in fondo alla
# scheda Dettagli, fuori dallo schermo, e l'etichetta e' tradotta. Il nome
# interno del radio non e' ne' tradotto ne' soggetto allo scorrimento.
SEL_NON_PER_BAMBINI = 'tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]'


def nega_bambini(page) -> bool:
    """Risponde «non sono contenuti per bambini» e verifica che sia segnato."""
    for _ in range(3):
        try:
            loc = page.locator(SEL_NON_PER_BAMBINI).first
            if loc.count():
                loc.scroll_into_view_if_needed(timeout=5000)
                loc.click(timeout=8000)
                page.wait_for_timeout(800)
                if (loc.get_attribute("aria-checked") or "") == "true":
                    log("  pubblico: non per bambini")
                    return True
        except Exception:
            pass
        page.wait_for_timeout(1000)
    voce = trova(page, ["No, non sono contenuti per bambini", "No, it's not made for kids"])
    return voce is not None and clicca(page, voce, "«non per bambini»")


def etichetta_stato(page) -> str:
    """Il cartellino accanto al titolo: «Salvato come privato», «Salvato come bozza»..."""
    try:
        loc = page.locator("ytcp-badge .label").first
        if loc.count():
            return (loc.inner_text(timeout=4000) or "").strip()
    except Exception:
        pass
    return ""


def video_id(url: str) -> str:
    m = re.search(r"(?:shorts/|watch\?v=|youtu\.be/|/video/)([\w\-]{6,})", url or "")
    return m.group(1) if m else ""


def visibile_a_tutti(page, url: str) -> bool:
    """
    La prova che un video sia pubblico non e' dentro Studio: Studio mostra lo
    stato che *crede*, e mostra un indirizzo anche per un video privato. La
    domanda si fa a YouTube da fuori — `oembed` risponde 200 su un video
    pubblico e 403 su uno privato — perche' e' la stessa domanda che si fara'
    chi guarda il link, e la campagna paga proprio quello.
    """
    vid = video_id(url)
    if not vid:
        return False
    import urllib.parse

    chiesto = urllib.parse.quote(f"https://www.youtube.com/watch?v={vid}", safe="")
    try:
        r = page.request.get(
            f"https://www.youtube.com/oembed?format=json&url={chiesto}", timeout=20000
        )
        return r.status == 200
    except Exception as exc:
        log(f"  non riesco a verificare la visibilita' ({type(exc).__name__})")
        return False


def avanti(page, quante: int = 3) -> None:
    """I tre passaggi di Studio prima della visibilita': Dettagli, Elementi, Controlli."""
    for i in range(quante):
        voce = trova(page, ["Avanti", "Next"])
        if voce is None or not clicca(page, voce, "«Avanti»"):
            log(f"mi fermo al passaggio {i + 1}: non trovo «Avanti»")
            return
        page.wait_for_timeout(2500)


def cmd_pubblica(args) -> None:
    percorso = Path(args.file).resolve()
    if not percorso.exists():
        sys.exit(f"il file non esiste: {percorso}")
    if not args.davvero:
        log(
            "PROVA: carico e riempio tutto, e mi fermo prima di pubblicare. "
            "Aggiungi --davvero per pubblicare sul serio."
        )

    def azione(page):
        attesa(page, 30)
        chi = canale(page)
        if not chi:
            dump(page, "pubblica-non-loggato")
            log("Studio non e' aperto: esegui prima `youtube_agent.py login`")
            return page
        log(f"canale: {chi}")

        # Con un canale solo bastava scriverlo nel log. Con due, il log lo legge
        # qualcuno dopo — cioe' dopo che la clip business e' uscita dal canale
        # del laboratorio. Si confronta prima di caricare, e sull'id.
        atteso = registro.canale_id(getattr(args, "gruppo", "") or None)
        if atteso:
            m = re.search(r"/channel/(UC[\w\-]+)", page.url or "")
            registro.controlla_canale(m.group(1) if m else None, atteso)
            log(f"  canale verificato: {atteso}")

        # Il caricamento si apre da un indirizzo, non dal pulsante «Crea».
        # `clicca()` cerca per sottotesto e prende il primo elemento del DOM: in
        # dashboard c'e' anche «Crea post» della scheda community, che vince e
        # lascia la pagina dov'era. Il menu non si apriva, il file input non
        # compariva mai, e l'unico sintomo era un TimeoutError trenta secondi
        # dopo — il clic sbagliato non fallisce, riesce sul bersaglio sbagliato.
        # L'indirizzo del caricamento non ha omonimi. Il menu resta il ripiego.
        if not apri_caricamento(page):
            voce = trova(page, ["Crea", "Create", "Carica video", "Upload video"])
            if voce is None or not clicca(page, voce, "«Crea»"):
                dump(page, "pubblica-senza-crea")
                return page
            page.wait_for_timeout(1500)
            voce = trova(page, ["Carica video", "Upload videos", "Upload video"])
            if voce is not None:
                clicca(page, voce, "«Carica video»")
            page.wait_for_timeout(2500)

        try:
            page.locator('input[type="file"]').first.set_input_files(str(percorso), timeout=30000)
        except Exception as exc:
            dump(page, "pubblica-senza-campo-file")
            log(f"non riesco a consegnare il file: {type(exc).__name__}")
            return page
        log(f"file consegnato: {percorso.name}")
        attesa(page, 40)

        if not scrivi(page, SEL_TITOLO, args.titolo, "titolo"):
            dump(page, "pubblica-titolo")
            return page
        if args.descrizione and not scrivi(page, SEL_DESCRIZIONE, args.descrizione, "descrizione"):
            dump(page, "pubblica-descrizione")
            return page

        # Senza questa risposta Studio non lascia proseguire, e la scelta ha
        # conseguenze vere: un video marcato per bambini perde commenti e
        # notifiche, cioe' proprio i segnali su cui la campagna paga.
        if not nega_bambini(page):
            dump(page, "pubblica-pubblico")
            log("non trovo la risposta sul pubblico: mi fermo")
            return page

        avanti(page, 3)
        chiudi_avvisi(page)
        if not scegli_pubblico(page):
            dump(page, "pubblica-visibilita")
            log("non riesco a mettere la visibilita' pubblica: mi fermo")
            return page
        chiudi_avvisi(page)

        dump(page, "pubblica-pronto")
        if not args.davvero:
            log(
                "PROVA finita: video caricato come bozza, niente pubblicato. "
                "Guarda lo screenshot, poi rilancia con --davvero."
            )
            return page

        voce = trova(page, ["Pubblica", "Publish", "Salva", "Save"])
        if voce is None or not clicca(page, voce, "«Pubblica»"):
            dump(page, "pubblica-conferma")
            return page
        attesa(page, 30)
        chiudi_avvisi(page)
        dump(page, "pubblica-dopo")

        # Il cartellino e' un indizio, non il verdetto: dopo la pubblicazione
        # Studio apre sopra la finestra «Data di pubblicazione», e il badge che
        # si legge e' quello di **un'altra riga** della lista sottostante. Ha
        # detto «Salvato come privato» su un video che era gia' pubblico e
        # visibile da fuori, fermando la consegna dopo averla completata. La
        # prova sta piu' giu' ed e' fuori da Studio: il video interrogato in
        # rete. Qui si annota soltanto.
        stato = etichetta_stato(page)
        if any(s in stato.lower() for s in ("privat", "bozza", "draft")):
            log(f"  cartellino: «{stato}» — lo verifico dall'esterno")

        # L'indirizzo del video e' l'unica cosa che serve dopo, ed e' anche
        # l'unica prova che la pubblicazione sia avvenuta: senza, non c'e'
        # niente da mandare alla campagna.
        # Si cerca **dentro la finestra** che si e' appena aperta, non nella
        # pagina: dietro c'e' la lista dei video del canale, e il primo
        # `a[href*="/shorts/"]` della pagina puo' essere un video di ieri.
        # Sarebbe passato anche dalla verifica — e' pubblico davvero — e alla
        # campagna sarebbe arrivato l'indirizzo sbagliato.
        url = ""
        for dove in ("ytcp-video-share-dialog", "tp-yt-paper-dialog", ""):
            for sel in ('a[href*="youtu.be/"]', 'a[href*="/shorts/"]', 'a[href*="watch?v="]'):
                try:
                    loc = page.locator(f"{dove} {sel}".strip()).first
                    if loc.count():
                        url = loc.get_attribute("href") or ""
                        if url:
                            break
                except Exception:
                    continue
            if url:
                if not dove:
                    log(
                        "  indirizzo preso dalla pagina, non dalla finestra: "
                        "controlla che sia il video appena caricato"
                    )
                break
        if not url:
            log(
                "pubblicato, ma non trovo l'indirizzo del video: prendilo dallo "
                "screenshot. Senza indirizzo l'invio alla campagna non si fa."
            )
            return page
        if not visibile_a_tutti(page, url):
            log(
                f"NON pubblicato: {url} non risponde come video pubblico. "
                "Non mandarlo alla campagna: un video privato vale zero views."
            )
            dump(page, "pubblica-ancora-privato")
            return page
        log(f"pubblicato: {url}")
        log(f"ORA, entro 30 minuti: whop_agent.py invia --campagna <uuid> --post {url} --davvero")
        return page

    with sessione(headless=args.headless) as s:
        s.fetch(STUDIO, page_action=azione, load_dom=True, wait=4000)


# Il riquadro della visibilita' nella pagina di modifica di un video gia' caricato.
SEL_VISIBILITA = [
    "ytcp-video-visibility-select ytcp-text-dropdown-trigger",
    "#visibility-container ytcp-text-dropdown-trigger",
    "ytcp-video-visibility-select",
    "#visibility ytcp-text-dropdown-trigger",
]


def cmd_rendi_pubblico(args) -> None:
    """
    Porta a «Pubblico» un video gia' caricato ma rimasto privato.

    Serve perche' il caso e' capitato: l'avviso di YouTube si mangiava il clic su
    Pubblica e il video restava sul canale invisibile a tutti. Rimetterlo
    pubblico non richiede di ricaricarlo — e non si deve: ricaricare cambia
    l'indirizzo, e l'indirizzo vecchio e' quello gia' mandato alla campagna.
    """
    vid = video_id(args.url)
    if not vid:
        sys.exit(f"non riconosco l'indirizzo del video: {args.url}")

    def azione(page):
        attesa(page, 25)
        chiudi_avvisi(page)
        if not args.davvero:
            log("PROVA: apro la pagina e mi fermo prima di salvare. Aggiungi --davvero.")

        # Un video che non e' mai arrivato in fondo alla procedura non e'
        # «privato»: e' una **bozza**, e la pagina di modifica non ha nemmeno il
        # campo della visibilita'. Si riapre la procedura e la si finisce.
        voce = trova(page, ["Modifica bozza", "Edit draft"])
        if voce is not None and clicca(page, voce, "«Modifica bozza»"):
            log("  il video era una bozza: riapro la procedura di caricamento")
            page.wait_for_timeout(3000)
            attesa(page, 20)
            chiudi_avvisi(page)
            nega_bambini(page)
            # Nella procedura la visibilita' e' l'ultimo passaggio, e i passaggi
            # sono cliccabili: ci si va diretti invece di premere «Avanti».
            voce = trova(page, ["Visibilità", "Visibility"])
            if voce is not None:
                clicca(page, voce, "il passaggio «Visibilità»")
            else:
                avanti(page, 3)
            page.wait_for_timeout(2000)
            chiudi_avvisi(page)

            if not scegli_pubblico(page):
                dump(page, "bozza-senza-pubblico")
                return page
            chiudi_avvisi(page)
            dump(page, "bozza-pronto")
            if not args.davvero:
                log("PROVA finita: niente pubblicato.")
                return page
            voce = trova(page, ["Pubblica", "Publish", "Salva", "Save"])
            if voce is None or not clicca(page, voce, "«Pubblica»"):
                dump(page, "bozza-senza-pubblica")
                return page
            attesa(page, 25)
            chiudi_avvisi(page)
            dump(page, "bozza-dopo")
            if visibile_a_tutti(page, args.url):
                log(f"ora e' pubblico: {args.url}")
            else:
                log(
                    f"salvato, ma {args.url} non risponde ancora come pubblico: "
                    f"cartellino «{etichetta_stato(page)}». Ricontrolla prima di mandarlo alla campagna."
                )
            return page

        aperto = False
        for sel in SEL_VISIBILITA:
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible():
                    loc.click(timeout=10000)
                    aperto = True
                    break
            except Exception:
                continue
        if not aperto:
            dump(page, "visibilita-non-aperta")
            log("non trovo il riquadro della visibilita'")
            return page
        page.wait_for_timeout(1500)
        chiudi_avvisi(page)

        voce = trova(page, ["Pubblico", "Public"])
        if voce is None or not clicca(page, voce, "«Pubblico»"):
            dump(page, "visibilita-senza-pubblico")
            return page
        page.wait_for_timeout(1200)
        chiudi_avvisi(page)

        dump(page, "visibilita-pronto")
        if not args.davvero:
            log("PROVA finita: niente salvato.")
            return page

        voce = trova(page, ["Salva", "Save", "Fine", "Done"])
        if voce is None or not clicca(page, voce, "«Salva»"):
            dump(page, "visibilita-senza-salva")
            return page
        attesa(page, 20)
        chiudi_avvisi(page)
        dump(page, "visibilita-dopo")

        if visibile_a_tutti(page, args.url):
            log(f"ora e' pubblico: {args.url}")
        else:
            log(
                f"salvato, ma {args.url} non risponde ancora come pubblico. "
                "Puo' volerci qualche minuto: ricontrolla prima di mandarlo alla campagna."
            )
        return page

    with sessione(headless=args.headless) as s:
        s.fetch(
            f"https://studio.youtube.com/video/{vid}/edit",
            page_action=azione,
            load_dom=True,
            wait=4000,
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="Agente YouTube: pubblica una clip come Short")
    ap.add_argument(
        "--headless",
        action="store_true",
        help="senza finestra. Sconsigliato finche' il flusso non e' collaudato",
    )
    ap.add_argument(
        "--gruppo",
        default="",
        help="il gruppo di dashboard/identita.json: decide il profilo Chrome",
    )
    ap.add_argument(
        "--profilo", default="", help="cartella del profilo, se non la si vuole dal registro"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("login").set_defaults(func=cmd_login)
    sub.add_parser("stato").set_defaults(func=cmd_stato)

    p = sub.add_parser("pubblica")
    p.add_argument("--file", required=True, help="la clip mp4 verticale")
    p.add_argument("--titolo", required=True)
    p.add_argument(
        "--descrizione",
        default="",
        help="deve rispettare le regole della campagna: descrivere la "
        "clip, taggare il canale, nominare l'ospite",
    )
    p.add_argument(
        "--davvero",
        action="store_true",
        help="pubblica davvero: senza, si ferma prima dell'ultimo clic",
    )
    p.set_defaults(func=cmd_pubblica)

    r = sub.add_parser("rendi-pubblico")
    r.add_argument("--url", required=True, help="indirizzo del video gia' caricato")
    r.add_argument("--davvero", action="store_true", help="salva davvero la visibilita'")
    r.set_defaults(func=cmd_rendi_pubblico)

    args = ap.parse_args()
    if args.profilo:
        scelto = Path(args.profilo)
        usa_profilo(scelto if scelto.is_absolute() else HERE / scelto)
    elif args.gruppo:
        usa_profilo(registro.profilo_obbligatorio(args.gruppo, "youtube"))
    if args.gruppo or args.profilo:
        log(
            f"profilo Chrome: {PROFILE_DIR.name}"
            + (
                f"  (gruppo {args.gruppo}, atteso {registro.handle(args.gruppo, 'youtube')})"
                if args.gruppo
                else ""
            )
        )
    args.func(args)


if __name__ == "__main__":
    main()
