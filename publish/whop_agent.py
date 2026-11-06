#!/usr/bin/env python
"""
whop_agent.py — la parte Whop dell'operazione: entrare nelle campagne e
registrarci i post.

Perche' esiste separato da publish_agent.py
-------------------------------------------
contentrewards.com e' solo la vetrina. Il `joinHref` di ogni campagna punta a
`whop.com/experiences/<exp>/campaigns/<id>`: l'iscrizione, la verifica del
pubblico e l'invio delle clip stanno **su Whop**, che e' un'altra applicazione
con un altro login. Tenerla in un file suo evita di rimettere le mani
nell'agente TikTok, che funziona ed e' costato caro.

Il profilo del browser e' **separato** da quello di TikTok (`.whop-profile` e
non `.browser-profile`) per la ragione gia' pagata: due istanze Chromium sullo
stesso `user_data_dir` non convivono, e con profili distinti l'agente Whop e
quello di pubblicazione possono girare senza intralciarsi.

Si appoggia a Scrapling (BSD-3) invece che a Playwright nudo per due cose che
qui servono davvero: i selettori adattivi, che ritrovano un elemento dopo un
rilascio del sito, e la sessione persistente con `user_data_dir`. Il controllo
resta pieno: `page_action` riceve la pagina Playwright vera.

L'interprete non e' quello del progetto
---------------------------------------
Scrapling sta in un venv **condiviso**, fuori da qualunque progetto:

    %USERPROFILE%\\.venvs\\scraping\\Scripts\\python.exe

Serve a piu' cose che al solo video (radar, Whop, registro delle metriche), e
un venv per progetto vorrebbe dire installarlo di nuovo ogni volta. Sta fuori
dal repo delle skill perche' un venv non si copia mai: ha i percorsi assoluti
scritti dentro alla creazione. Lo rifa' `setup.ps1`.

Comandi
-------
  P=%USERPROFILE%\\.venvs\\scraping\\Scripts\\python.exe

  %P% whop_agent.py login                    # login Whop (una tantum, a mano)
  %P% whop_agent.py stato                    # sei loggato? con quale account?
  %P% whop_agent.py apri --url <url>         # ricognizione: dumpa una pagina
  %P% whop_agent.py join --url <joinHref>    # entra nella campagna
  %P% whop_agent.py join --url <...> --prova # mostra cosa farebbe, senza cliccare
  %P% whop_agent.py invia --campagna <uuid> --post <url>            # prova
  %P% whop_agent.py invia --campagna <uuid> --post <url> --davvero  # invia

Il vincolo dei 30 minuti
------------------------
Il modulo d'invio dichiara che un video va inviato **entro 30 minuti dalla
pubblicazione**, altrimenti e' respinto in automatico. Non e' un dettaglio del
modulo: vuol dire che pubblicare e registrare sono la stessa catena, e che una
coda serale di invii accumulati sarebbe interamente scartata.
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from whop_radice import radice_app  # noqa: E402,F401  (vive fuori: serve anche a chi non ha Scrapling)

try:
    from scrapling.fetchers import DynamicSession
except ImportError:  # pragma: no cover
    sys.exit(
        "Scrapling non e' in questo interprete (%s).\n"
        "  Questo script va lanciato col venv condiviso:\n"
        "    %s whop_agent.py ...\n"
        "  Se manca, lo ricrea setup.ps1."
        % (sys.executable, __import__("interpreti").PYTHON_SCRAPING)
    )

# La console di Windows e' cp1252 e le pagine sono piene di simboli che li' non
# esistono (il tasto Comando, le emoji dei pulsanti). Stampandoli si muore con
# UnicodeEncodeError proprio mentre si sta guardando cosa c'e' sulla pagina,
# cioe' si perde l'unica cosa per cui il comando era stato lanciato. Stessa
# medicina della pipeline video: uscita forzata a UTF-8.
for _flusso in (sys.stdout, sys.stderr):
    try:
        _flusso.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent
PROFILE_DIR = HERE / ".whop-profile"
LOG_DIR = HERE / "logs"


def usa_profilo(percorso) -> None:
    """
    Cambia il profilo Whop per tutto il processo.

    Serve perche' **su Whop ogni account regge un solo profilo per piattaforma**
    — un TikTok, un Instagram — quindi tre nicchie vogliono tre account Whop,
    uno per mail di nicchia. Con una cartella sola, il secondo accesso
    sovrascriverebbe la sessione del primo e nessuno se ne accorgerebbe finche'
    un invio non finisce nella campagna dell'account sbagliato.
    """
    global PROFILE_DIR
    PROFILE_DIR = Path(percorso)


WHOP_HOME = "https://whop.com/"
WHOP_LOGIN = "https://whop.com/login"

# Serve solo ad aprire il pannello dell'applicazione per il secondo login:
# quale campagna sia e' indifferente, il login e' uno per tutte. Questa e'
# Initech x Clipfarm.
CAMPAGNA_ESEMPIO = (
    "https://whop.com/experiences/exp_sKCcnfigfcLoSb/campaigns/f354afb5-bc73-4090-b723-35f65dbba276"
)

# Il pannello di un provider. Dentro ci si muove **a clic**: le rotte
# dell'applicazione chieste di prima mano rimbalzano qui, quindi questo e'
# l'unico indirizzo che si scrive, e il resto e' navigazione.
APP_CLIP_FARM = "https://whop.com/clip-farm-official/exp_sKCcnfigfcLoSb/app/"

# Le etichette contano piu' dei selettori: Whop rigenera le classi a ogni
# rilascio ma il testo del pulsante e' il contratto con l'utente. La lingua la
# decide il browser, quindi vanno tenute tutte quelle che puo' servire.
JOIN_LABELS = [
    "Iscriviti gratuitamente",
    "Iscriviti",
    "Unisciti",
    "Join for free",
    "Join free",
    "Join",
    "Get access",
    "Continue",
    "Continua",
]
JOINED_LABELS = [
    "Apri",
    "Open",
    "Vai a",
    "Go to",
    "Gestisci",
    "Manage",
    "Iscritto",
    "Joined",
    "Membro",
    "Member",
]


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(exist_ok=True)
    with (LOG_DIR / "whop.log").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def dump(page, tag: str) -> None:
    """
    Screenshot + HTML: l'unico modo per capire cosa e' cambiato dopo un rilascio.

    L'HTML della pagina e' **la cornice**, non il contenuto: `page.content()`
    torna il documento di whop.com, e l'applicazione vive in un riquadro di un
    altro dominio. I dump di una serata intera sono usciti da 1.1 MB di CSS con
    dentro zero pulsanti, e sembravano completi. Si salva anche il documento del
    riquadro dell'applicazione, che e' quello su cui si mappano i selettori.
    """
    LOG_DIR.mkdir(exist_ok=True)
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}-whop-{tag}"
    scritti = []
    try:
        page.screenshot(path=str(LOG_DIR / f"{stamp}.png"), full_page=False)
        scritti.append(".png")
    except Exception as exc:
        log(f"  screenshot non salvato: {exc}")
    try:
        (LOG_DIR / f"{stamp}.html").write_text(page.content(), encoding="utf-8")
        scritti.append(".html")
    except Exception as exc:
        log(f"  HTML della cornice non salvato: {exc}")
    dentro = riquadro(page)
    if dentro is not page.main_frame:
        try:
            (LOG_DIR / f"{stamp}-app.html").write_text(dentro.content(), encoding="utf-8")
            scritti.append("-app.html")
        except Exception as exc:
            log(f"  HTML dell'applicazione non salvato: {exc}")
    log(f"  diagnostica in logs/{stamp}{'|'.join(scritti)}")


# Elementi cliccabili visibili, con il loro testo: si sceglie per etichetta,
# non per classe. Torna anche le coordinate, che servono quando il clic normale
# viene intercettato da un pannello di passaggio.
SCAN_CLICKABILI = """() => {
  const out = [];
  for (const el of document.querySelectorAll('button,a,[role="button"]')) {
    const t = (el.innerText || '').trim();
    if (!t || t.length > 60) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 5 || r.height < 5) continue;
    // Niente filtro sulla posizione verticale: la scartava a tre schermate di
    // distanza, e in fondo a una scheda di campagna — dopo brief, regole e
    // grafico — ci sta esattamente «Submit clip», cioe' l'unico pulsante per
    // cui si stava guardando la pagina. Playwright scorre da solo prima di
    // cliccare, quindi essere fuori vista non e' un ostacolo: era solo un
    // modo per non vederlo.
    out.push({tag: el.tagName, testo: t, href: el.getAttribute('href') || '',
              x: r.x + r.width / 2, y: r.y + r.height / 2, area: r.width * r.height});
  }
  return out;
}"""


# I campi di un modulo non compaiono fra i cliccabili, e sono la meta' che
# conta quando si mappa un invio: senza, si sa dove premere e non cosa
# riempire. `accept` dice anche cosa accetta un caricamento di file, che qui
# serve — la campagna chiede uno screenshot delle statistiche.
SCAN_CAMPI = """() => {
  const out = [];
  for (const el of document.querySelectorAll('input,textarea,select')) {
    const r = el.getBoundingClientRect();
    const et = el.labels && el.labels[0] ? el.labels[0].innerText.trim() : '';
    out.push({tag: el.tagName, tipo: el.getAttribute('type') || '',
              nome: el.getAttribute('name') || el.id || '',
              posto: el.getAttribute('placeholder') || '',
              etichetta: et.slice(0, 40),
              accetta: el.getAttribute('accept') || '',
              visibile: r.width > 2 && r.height > 2});
  }
  return out;
}"""


# Il pannello che conta — "Content Rewards" — non e' la pagina di Whop: e'
# un'applicazione servita da un altro dominio dentro un iframe. Guardando la
# pagina si vede solo la cornice (barra di ricerca, saldo, icone) e si conclude
# che non c'e' niente. Ogni lettura e ogni clic vanno fatti nel riquadro giusto.
DOMINIO_APP = "apps.whop.com"
FRAME_RUMORE = (
    "sardine.ai",
    "snapchat.com",
    "googletagmanager",
    "doubleclick",
    "facebook.",
    "about:blank",
    "__whop_pixels",
)


def riquadri(page) -> list:
    """I riquadri della pagina, senza quelli di tracciamento."""
    return [f for f in page.frames if not any(r in (f.url or "") for r in FRAME_RUMORE)]


def riquadro(page):
    """
    Il riquadro dove sta il contenuto: quello dell'applicazione se c'e',
    altrimenti il piu' ricco di testo. Mai la cornice per partito preso.
    """
    for f in riquadri(page):
        if DOMINIO_APP in (f.url or ""):
            return f
    migliore, quanto = page.main_frame, -1
    for f in riquadri(page):
        try:
            n = len(f.inner_text("body"))
        except Exception:
            n = 0
        if n > quanto:
            migliore, quanto = f, n
    return migliore


def aspetta_app(page, secondi: int = 40):
    """
    Aspetta che il riquadro dell'applicazione **esista**, e non solo che la
    pagina si sia calmata.

    Sono due condizioni diverse: la cornice di Whop si ferma quasi subito, e chi
    si accontenta di quella clicca su un riquadro dove i collegamenti
    dell'applicazione non ci sono ancora. L'errore che ne esce — «non trovo
    l'elemento» — sembra un selettore sbagliato ed e' solo fretta.
    """
    for _ in range(max(1, secondi)):
        for f in page.frames:
            if DOMINIO_APP in (f.url or ""):
                try:
                    if f.locator("a[href*='/c/']").first.count():
                        return f
                except Exception:
                    pass
        page.wait_for_timeout(1000)
    return riquadro(page)


def vai_a(page, rotta: str, attesa_max: int = 25) -> bool:
    """
    Apre una rotta dell'applicazione cliccando **dentro** il riquadro.

    Chiesta di prima mano, la rotta rimbalza nel guscio di Whop (`/app/`): la
    navigazione esiste solo come clic. Per legarsi al percorso invece che
    all'etichetta — tradotta, e a volte con una pastiglia accanto — si punta
    all'href.

    Il ritorno dice se ci si e' arrivati **davvero**, e la differenza non e'
    teorica: un clic andato a buon fine su `discover` ha lasciato il riquadro
    sulla home, e la versione precedente scriveva «navigato» lo stesso. Una
    ricognizione che dichiara riuscita una navigazione mai avvenuta fa mappare i
    selettori della pagina sbagliata.
    """
    # Tre tentativi, ognuno su un riquadro **riletto**. Il 29 settembre 2026 il
    # secondo invio, partito quattro secondi dopo il primo, ha preso `Error` al
    # clic nello stesso secondo — il riquadro dell'applicazione si stava
    # ricaricando, e l'oggetto in mano era gia' staccato. Un clic fallito cosi'
    # e' fretta, non un selettore rotto.
    for tentativo in range(3):
        dentro = aspetta_app(page, attesa_max)
        partenza = dentro.url or ""
        try:
            dentro.locator(f'a[href$="/{rotta}"]').first.click(timeout=12000, force=True)
            break
        except Exception as exc:
            log(f"non riesco a cliccare «{rotta}» (tentativo {tentativo + 1}): {type(exc).__name__}")
            page.wait_for_timeout(3000)
    else:
        return False
    for _ in range(max(1, attesa_max)):
        page.wait_for_timeout(1000)
        ora = riquadro(page).url or ""
        if ora != partenza:
            attesa(page, 15)
            arrivo = riquadro(page).url or ora
            # Arrivare **altrove** e' un esito, non un errore: con zero campagne
            # iscritte `campaigns` rimbalza su `discover`, e chi pretende
            # l'uguaglianza esatta si ferma su una pagina che va benissimo.
            log(
                f"aperta «{rotta}»"
                if arrivo.rstrip("/").endswith("/" + rotta)
                else f"«{rotta}» ha portato a {arrivo.split('/c/')[-1] or arrivo}"
            )
            return True
    log(
        f"il clic su «{rotta}» non ha cambiato rotta: il riquadro e' fermo su "
        f"{partenza.split('/c/')[-1] or partenza}"
    )
    return False


def apri_scheda(page, titolo: str, uuid: str, attesa_max: int = 25) -> bool:
    """Apre la scheda di una campagna dall'elenco, agganciandola per **titolo**.

    Per uuid non si puo': nell'elenco di Clipping Culture l'uuid non compare
    affatto — le schede sono JavaScript, senza ancora — quindi `a[href$=...]`
    cerca una cosa che non esiste. Su Clip Farm esisteva, ed e' la solita cosa
    vera per un provider e falsa per l'altro.

    Il titolo nell'elenco e' **troncato** («…September Episode Clippin»), quindi
    si cerca per pezzo iniziale e per contenuto, non per uguaglianza. L'uuid
    resta la **verifica**: una scheda aperta che non lo mostra nell'indirizzo si
    dichiara, perche' inviare nella campagna sbagliata non torna indietro.
    """
    chiave = re.sub(r"\s+", " ", (titolo or "")).strip()[:40]
    if not chiave:
        log("non so il titolo della campagna: senza, la scheda non si aggancia")
        return False

    dentro = aspetta_app(page, attesa_max)
    partenza = dentro.url or ""

    # Non si passa da `cliccabili()`: la scheda **e'** un <button>, ma il suo
    # testo comprende provider, eta', titolo, budget e tariffa, e la scansione
    # scarta tutto cio' che supera i 60 caratteri. L'etichetta di accessibilita'
    # invece e' pulita: `aria-label="Preview <titolo>"`.
    #
    # E l'elenco si **aspetta**: l'intestazione «49 campaigns» arriva prima
    # delle schede, e cercarle in quell'istante trova zero — successo il 29
    # settembre 2026, con la clip gia' online e la finestra dei 30 minuti
    # aperta. Ogni giro rilegge il riquadro, che nel frattempo puo' essersi
    # ricaricato.
    card = None
    for _ in range(max(1, attesa_max)):
        dentro = riquadro(page)
        try:
            c = dentro.locator(f'button[aria-label*="{chiave}"]').first
            if not c.count():
                c = dentro.get_by_text(chiave, exact=False).first
            if c.count():
                card = c
                break
        except Exception:
            pass
        page.wait_for_timeout(1000)
    if card is None:
        log(f"nessuna scheda contiene «{chiave}» nell'elenco dopo {attesa_max} s")
        return False
    partenza = dentro.url or partenza
    try:
        card.click(timeout=12000)
    except Exception as exc:
        log(f"non riesco a cliccare la scheda «{chiave}»: {type(exc).__name__}")
        return False

    # La riuscita si misura su «Submit clip», non sul cambio di indirizzo:
    # l'etichetta dice «Preview», quindi la scheda puo' aprirsi come pannello
    # sopra l'elenco, con la rotta ferma. Cio' che conta e' che ci sia da
    # premere il pulsante per cui siamo venuti.
    for _ in range(max(1, attesa_max)):
        page.wait_for_timeout(1000)
        dentro = riquadro(page)
        arrivo = dentro.url or ""
        try:
            pronta = dentro.locator('button:has-text("Submit clip")').count() > 0
        except Exception:
            pronta = False
        if pronta or arrivo != partenza:
            attesa(page, 10)
            arrivo = riquadro(page).url or arrivo
            if uuid and uuid not in arrivo:
                # Il titolo e' un aggancio, non una prova: due campagne dello
                # stesso committente cominciano uguali. L'uuid nell'indirizzo
                # e' la prova, e senza non si prosegue — un invio nella
                # campagna sbagliata non torna indietro.
                log(
                    f"la scheda aperta da «{chiave}» non porta l'uuid atteso "
                    f"({uuid}): indirizzo {arrivo.split('/c/')[-1] or arrivo}. Non invio."
                )
                return False
            log(f"aperta la scheda di «{chiave}» ({uuid})")
            return True
    log(f"la scheda «{chiave}» non si e' aperta")
    return False


def testo_totale(page) -> int:
    tot = 0
    for f in riquadri(page):
        try:
            tot += len(f.inner_text("body"))
        except Exception:
            pass
    return tot


def attesa(page, secondi: int = 20) -> None:
    """
    Aspetta che la pagina smetta di riempirsi.

    Non un tempo fisso: si guarda quanto testo c'e' e si esce quando smette di
    crescere per due giri di fila. Un'attesa fissa e' sempre o troppo corta
    (pagina vuota) o troppo lunga (minuti buttati a ogni comando).
    """
    ultimo, fermo = -1, 0
    for _ in range(max(1, secondi)):
        page.wait_for_timeout(1000)
        ora = testo_totale(page)
        if ora == ultimo and ora > 200:
            fermo += 1
            if fermo >= 2:
                return
        else:
            fermo = 0
        ultimo = ora


def cliccabili(dove) -> list[dict]:
    """`dove` e' una pagina o un riquadro: entrambi sanno valutare JavaScript."""
    try:
        return dove.evaluate(SCAN_CLICKABILI)
    except Exception:
        return []


def trova(dove, etichette: list[str]) -> dict | None:
    """Il candidato piu' piccolo il cui testo comincia con una delle etichette."""
    voci = cliccabili(dove)
    for et in etichette:  # l'ordine delle etichette e' la priorita'
        eq = [v for v in voci if v["testo"].lower().startswith(et.lower())]
        if eq:
            return sorted(eq, key=lambda v: v["area"])[0]
    return None


def clicca(dove, voce: dict, cosa: str = "elemento") -> bool:
    """
    Clic per etichetta, non per coordinate: dentro un riquadro le coordinate
    sono relative al riquadro e il mouse della pagina le sbaglia di tutto
    l'ingombro della cornice.
    """
    testo = voce["testo"].split("\n")[0][:40]
    for sel in (
        f'button:has-text("{testo}")',
        f'a:has-text("{testo}")',
        f'[role="button"]:has-text("{testo}")',
    ):
        try:
            loc = dove.locator(sel)
            quanti = loc.count()
        except Exception:
            continue
        # Non `.first`: la stessa etichetta compare piu' volte nella stessa
        # pagina. Su Whop «Submit clip» sta anche in un'intestazione fuori
        # schermo, ed e' quella che vince: il clic scade su un elemento che
        # nessuno vedrebbe mai, e il pulsante vero resta li' a un dito.
        for i in range(min(quanti, 8)):
            candidato = loc.nth(i)
            try:
                if not candidato.is_visible():
                    continue
                candidato.click(timeout=10000)
                return True
            except Exception:
                continue
    log(f"  non riesco a cliccare {cosa} («{testo}»)")
    return False


def sessione(headless: bool):
    import registro

    PROFILE_DIR.mkdir(exist_ok=True)
    return DynamicSession(
        headless=headless,
        user_data_dir=str(PROFILE_DIR),
        timeout=60000,
        **registro.ambiente(),
        network_idle=False,
        google_search=False,
    )


def chi_sono(page, naviga: bool = True) -> str:
    """
    Lo @handle dell'account Whop, se la sessione e' aperta.
    Sulla pagina pubblica il bottone 'Accedi' e' la prova del contrario: piu'
    affidabile di cercare l'handle, che a volte sta dietro un menu.
    """
    try:
        testo = page.inner_text("body")
    except Exception:
        return ""
    if re.search(r"^\s*(Accedi|Log in|Sign up|Registrati)\s*$", testo, re.M):
        return ""
    # Prima il menu dell'utente, che parla **di chi guarda**; l'ancora `/@...`
    # per ultima, e solo se in pagina ce n'e' una sola.
    #
    # Costato un handle sbagliato scritto nel registro: su `whop.com/home` di un
    # account nuovo la prima ancora `/@...` e' un profilo **suggerito**, cioe' di
    # un altro utente. L'agente ha risposto `@altro_utente59` con la stessa
    # sicurezza con cui avrebbe detto quello giusto, e nessuno se ne sarebbe
    # accorto finche' la guardia non avesse bloccato ogni invio del gruppo
    # accusando il profilo di essere sbagliato. Se i candidati sono piu' d'uno
    # non si indovina: si torna "" — e su Whop l'incognita non ferma un invio.
    for sel in ('[data-testid="user-menu"]', 'img[alt^="@"]'):
        try:
            loc = page.locator(sel).first
            if loc.count():
                val = loc.get_attribute("href") or loc.get_attribute("alt") or ""
                m = re.search(r"@([\w.\-]+)", val)
                if m:
                    return m.group(1)
        except Exception:
            continue
    try:
        ancore = page.locator('a[href^="/@"]')
        trovati = set()
        for i in range(min(ancore.count(), 12)):
            m = re.search(r"@([\w.\-]+)", ancore.nth(i).get_attribute("href") or "")
            if m:
                trovati.add(m.group(1))
        if len(trovati) == 1:
            return trovati.pop()
    except Exception:
        pass

    # L'handle non e' su ogni pagina: dentro una comunita' la barra in alto
    # mostra il saldo e l'avatar, non il nome. La pagina del profilo ce l'ha
    # sempre, ma visitarla **ricarica la pagina che si stava guardando**: usato
    # dentro una ricognizione, fa ripartire da zero l'applicazione e la si
    # fotografa mentre gira la rotella. Percio' chi ispeziona chiede
    # `naviga=False` e si tiene il "?".
    if not naviga:
        return "?"
    try:
        prima = page.url
        page.goto("https://whop.com/@me/", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        m = re.search(r"@([\w.\-]{2,})", page.inner_text("body"))
        if prima and not prima.startswith("about:"):
            page.goto(prima, wait_until="domcontentloaded")
        if m:
            return m.group(1)
    except Exception:
        pass
    return "?"


# --------------------------------------------------------------------- comandi


def cmd_login(args) -> None:
    def azione(page):
        page.wait_for_timeout(4000)
        print("\n1) Accedi a Whop (email + codice a sei cifre).")
        print("2) Resta sulla pagina finche' vedi il tuo profilo in alto a destra.")
        input("\nQuando hai finito, torna qui e premi INVIO... ")
        page.goto(WHOP_HOME, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        chi = chi_sono(page)
        print(f"Sessione Whop: {'aperta come @' + chi if chi else 'NON aperta'}")
        return page

    with sessione(headless=False) as s:
        s.fetch(WHOP_LOGIN, page_action=azione, load_dom=True, wait=2000)


RADAR = HERE.parent / "radar"
DETTAGLI = RADAR / "radar_dettagli.json"
PROVIDER = RADAR / "provider.json"


def segna_iscritto(url_join: str) -> str:
    """
    Segna in provider.json che siamo entrati nella comunita' di quella campagna.

    Serve a `dettagli.py` e alla dashboard per non riproporre un'iscrizione gia'
    fatta. L'organizzazione si ricava dalla scansione: e' l'unita' di
    iscrizione, mentre l'`exp_...` dell'indirizzo cambia nel tempo.
    """
    if not (DETTAGLI.exists() and PROVIDER.exists()):
        return ""
    try:
        schede = json.loads(DETTAGLI.read_text(encoding="utf-8"))
        dati = json.loads(PROVIDER.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    scheda = next((s for s in schede if s.get("join") and s["join"] in url_join), None)
    org = (scheda or {}).get("organizzazione") or ""
    if not org:
        return ""
    for v in dati.get("provider", []):
        if v.get("organizzazione") == org:
            v["iscritto"] = True
            break
    else:
        # Un organizzatore che l'elenco non ha ancora: prima si scartava in
        # silenzio, e l'iscrizione appena fatta non risultava da nessuna parte.
        # `provider.py` conserva `iscritto` per organizzazione alla prossima
        # rigenerazione.
        esperienza = (scheda or {}).get("provider") or ""
        dati.setdefault("provider", []).append(
            {
                "provider": esperienza or org,
                "esperienze": [esperienza] if esperienza else [],
                "organizzazione": org,
                "join": url_join,
                "iscritto": True,
                "attrito_invio": "ignoto",
                "note": "",
            }
        )
    PROVIDER.write_text(json.dumps(dati, ensure_ascii=False, indent=1), encoding="utf-8")
    return org


API_LOG = "whop-login-api.log"


def registra_rete(page) -> None:
    """
    Registra le risposte dell'applicazione durante il login.

    La riga rossa a schermo — «Whop account already registered» — e' il
    riassunto che l'interfaccia sceglie di mostrare. Il corpo della risposta
    dice **quale** vincolo e' stato violato, ed e' l'unica cosa con cui si puo'
    decidere se il problema si risolve da qui o va chiesto all'assistenza.
    """
    LOG_DIR.mkdir(exist_ok=True)
    dove = LOG_DIR / API_LOG

    def annota(risposta):
        url = risposta.url or ""
        # Il primo filtro guardava solo `apps.whop.com` e si e' perso l'errore
        # che contava: superato il login, l'applicazione serve le sue rotte
        # sotto `whop.com/<comunita>/<exp>/app/`, e il guasto e' arrivato da li'.
        # Si tiene tutto cio' che fallisce, ovunque, piu' le chiamate del login.
        if risposta.status >= 400:
            pass
        elif DOMINIO_APP in url and re.search(r"/(api|auth|login|verify|session)", url):
            pass
        else:
            return
        corpo = ""
        try:
            corpo = risposta.text()[:1500]
        except Exception as exc:
            corpo = f"(corpo non leggibile: {type(exc).__name__})"
        with dove.open("a", encoding="utf-8") as fh:
            fh.write(
                f"\n[{datetime.now():%H:%M:%S}] {risposta.status} "
                f"{risposta.request.method} {url}\n{corpo}\n"
            )

    page.on("response", annota)
    log(f"registro le risposte dell'applicazione in logs/{API_LOG}")


def cmd_login_app(args) -> None:
    """
    Il secondo login, quello che non ci si aspetta.

    Entrare in Whop non basta: il pannello "Content Rewards" e' un'applicazione
    di un altro dominio, con una sua autenticazione a email + codice a sei
    cifre. Finche' non e' aperta, la campagna mostra un modulo di accesso al
    posto del contenuto — e dall'esterno somiglia a una pagina vuota.
    """

    def azione(page):
        registra_rete(page)
        attesa(page, 25)
        dentro = riquadro(page)
        log(f"riquadro dell'applicazione: {dentro.url[:100]}")
        print("\n1) Nel riquadro «Content Rewards» inserisci la tua email e premi «Send code».")
        print("2) Se compare una riga rossa, IGNORALA e premi «Resend code»: va distinto")
        print("   un avviso del passaggio email da un rifiuto del codice.")
        print("3) Incolla il codice a sei cifre e CONFERMA, anche se la riga rossa e' li'.")
        print("4) Guarda cosa succede DOPO aver confermato: e' quello che conta.")
        input("\nQuando hai confermato il codice, torna qui e premi INVIO... ")
        attesa(page, 20)
        dentro = riquadro(page)
        aperta = "/login" not in (dentro.url or "")
        log(f"pannello Content Rewards: {'aperto' if aperta else 'ANCORA al login'}")
        if aperta:
            try:
                log("prime righe: " + re.sub(r"\s+", " ", dentro.inner_text("body"))[:200])
            except Exception:
                pass
        dump(page, "login-app")
        return page

    with sessione(headless=False) as s:
        s.fetch(args.url, page_action=azione, load_dom=True, wait=3000)


def cmd_stato(args) -> None:
    def azione(page):
        page.wait_for_timeout(5000)
        chi = chi_sono(page)
        log(f"sessione Whop: {'aperta come @' + chi if chi else 'NON aperta — esegui `login`'}")
        return page

    with sessione(headless=args.headless) as s:
        s.fetch(WHOP_HOME, page_action=azione, load_dom=True, wait=2000)


def cmd_whops(args) -> None:
    """
    Elenca i whop di cui l'account e' membro.

    Non li tocca: uscire da una comunita' e' un'azione sull'account e va decisa
    guardando l'elenco, non indovinata da un'icona nella barra laterale.
    """

    def azione(page):
        attesa(page, 25)
        js = """() => {
          const visti = new Set(), out = [];
          for (const a of document.querySelectorAll('a[href^="/"]')) {
            const href = a.getAttribute('href') || '';
            const m = href.match(/^\\/([a-z0-9][a-z0-9\\-]{2,})\\/?$/i);
            if (!m) continue;
            // La barra laterale mescola le comunita' con le voci di Whop
            // stesso: senza questo elenco «Messaggi» e «Partner» risultano
            // whop di cui sei membro, e uno si mette a cercare come uscirne.
            const nav = ['home','discover','settings','login','api','messages',
                         'townhall','partners','affiliates','new-business',
                         'communities','checkout','orders','help'];
            if (nav.includes(m[1])) continue;
            if (visti.has(m[1])) continue;
            visti.add(m[1]);
            const img = a.querySelector('img');
            out.push({slug: m[1],
                      nome: (a.innerText || '').trim().split('\\n')[0]
                            || (img && img.getAttribute('alt')) || ''});
          }
          return out;
        }"""
        voci = page.evaluate(js) or []
        log(f"{len(voci)} whop nella barra laterale")
        for v in voci:
            print(f"  {v['slug']:<34} {v['nome'][:40]}")
        dump(page, "whops")
        return page

    with sessione(headless=args.headless) as s:
        s.fetch("https://whop.com/home/", page_action=azione, load_dom=True, wait=3000)


def cmd_apri(args) -> None:
    """Ricognizione. Serve a mappare un flusso nuovo senza indovinare i selettori."""

    def azione(page):
        # Whop e' un'applicazione lato client: il guscio arriva subito, il
        # contenuto molto dopo. Guardare troppo presto fa vedere una pagina
        # vuota e concludere che non c'e' niente — che e' il modo peggiore di
        # sbagliarsi, perche' non somiglia a un errore.
        attesa(page, args.attesa)
        # Le rotte dell'applicazione (`/c/<exp>/submissions` e simili) non si
        # raggiungono dall'indirizzo esterno: chiesta di prima mano, l'app
        # rimbalza dentro il guscio di Whop. Si naviga cliccando **dentro** il
        # riquadro, e per legarsi al percorso invece che all'etichetta —
        # tradotta e a volte con una pastiglia accanto — si punta all'href.
        # Le rotte si concatenano perche' non tutte partono da casa: la scheda di
        # una campagna e' un collegamento che esiste **solo** sull'elenco delle
        # campagne, quindi ci si arriva in due clic e non con un indirizzo.
        for rotta in [r for r in args.vai.split(",") if r.strip()]:
            if not vai_a(page, rotta.strip(), args.attesa):
                break
        # Un solo clic, sull'etichetta che gli si dice: serve ad aprire i moduli
        # (il «Submit clip» della campagna) e a vedere cosa chiedono. Non
        # conferma niente — la ricognizione guarda, l'invio e' un altro comando.
        if args.clicca:
            dentro = riquadro(page)
            voce = trova(dentro, [args.clicca])
            if voce is None:
                log(f"nessun elemento cliccabile che cominci con «{args.clicca}»")
            elif clicca(dentro, voce, f"«{args.clicca}»"):
                attesa(page, 15)
                log(f"cliccato «{voce['testo'][:40]}»")
        # L'indirizzo della pagina resta `.../app/` qualunque rotta interna si
        # apra: la navigazione avviene dentro il riquadro e la barra di Whop non
        # la registra. Guardando solo `page.url` si conclude che il clic non ha
        # fatto niente, mentre il contenuto e' cambiato del tutto.
        log(f"URL finale: {page.url}")
        log(f"URL dell'applicazione: {riquadro(page).url}")
        log(f"titolo: {page.title()}")
        log(f"account Whop: {chi_sono(page, naviga=False) or '(non loggato)'}")

        print("\n--- riquadri ---")
        for f in riquadri(page):
            try:
                n = len(f.inner_text("body"))
            except Exception:
                n = 0
            print(f"  {n:>7} car.  {f.url[:96]}")

        dentro = riquadro(page)
        print(f"\n--- elementi cliccabili (riquadro: {dentro.url[:70]}) ---")
        for v in cliccabili(dentro)[:60]:
            print(f"  {v['tag']:<7} {v['testo'][:44]:<46} {v['href'][:50]}")
        try:
            campi = dentro.evaluate(SCAN_CAMPI) or []
        except Exception:
            campi = []
        if campi:
            print("\n--- campi del modulo ---")
            for c in campi:
                stato = "" if c["visibile"] else "  (nascosto)"
                print(
                    f"  {c['tag'].lower():<9} {c['tipo']:<8} {c['nome'][:24]:<26}"
                    f"{(c['etichetta'] or c['posto'])[:34]:<36}{c['accetta'][:24]}{stato}"
                )

        print("\n--- testo del riquadro ---")
        try:
            print(dentro.inner_text("body")[: args.testo])
        except Exception:
            pass
        dump(page, "apri")
        return page

    with sessione(headless=args.headless) as s:
        s.fetch(args.url, page_action=azione, load_dom=True, wait=3000)


APP_ESEMPIO = "https://whop.com/clip-farm-official/exp_sKCcnfigfcLoSb/app/"


def apri_collegamenti(page) -> object | None:
    """Porta il riquadro su Settings -> Connected accounts. Torna il riquadro."""
    attesa(page, 25)
    vai_a(page, "settings", 25)
    page.wait_for_timeout(4000)
    dentro = riquadro(page)
    voce = trova(dentro, ["Connected accounts", "Account collegati"])
    if voce is None:
        return None
    clicca(dentro, voce, "«Connected accounts»")
    page.wait_for_timeout(4000)
    return riquadro(page)


def leggi_collegati(dentro) -> list[str]:
    """
    Le righe della scheda, come le scrive Whop.

    Non si interpreta: si riporta. La frase «No connected accounts» e' la sola
    cosa di cui conosciamo il significato con certezza, e finche' un account
    collegato non l'abbiamo mai visto, inventarsi come riconoscerlo sarebbe
    inventarsi anche il verdetto.
    """
    try:
        testo = dentro.locator("main, [role=main], body").first.inner_text(timeout=10000)
    except Exception:
        return []
    fuori, dentro_scheda = [], False
    for riga in (r.strip() for r in testo.split("\n")):
        if not riga:
            continue
        if riga.startswith("Connected accounts"):
            dentro_scheda = True
            continue
        if dentro_scheda:
            if riga.startswith("Help & Support"):
                break
            fuori.append(riga)
    return fuori


def cmd_collegati(args) -> None:
    """Dice quali account social risultano collegati. Non tocca niente."""

    def azione(page):
        dentro = apri_collegamenti(page)
        if dentro is None:
            dump(page, "collegati-senza-scheda")
            log("non trovo «Connected accounts»: il pannello e' aperto? (`login-app`)")
            return page
        righe = leggi_collegati(dentro)
        vuoto = any("No connected accounts" in r for r in righe)
        log("account collegati: NESSUNO" if vuoto else f"scheda: {len(righe)} righe")
        for r in righe:
            print("   ", r)
        dump(page, "collegati")
        return page

    with sessione(headless=args.headless) as s:
        s.fetch(APP_ESEMPIO, page_action=azione, load_dom=True, wait=3000)


def cmd_collega(args) -> None:
    """
    Apre la scheda dei collegamenti e aspetta che l'autorizzazione la faccia
    una persona.

    Il collegamento e' un OAuth: la fa il browser con le credenziali di chi
    guarda, e da qui non passa niente. Quello che questo comando aggiunge e'
    aprire **il profilo giusto** — le autorizzazioni fatte nella finestra
    sbagliata collegano l'account sbagliato, ed e' un errore che si scopre solo
    quando una campagna rifiuta un invio — e **rileggere la scheda dopo**,
    perche' «l'ho fatto» non e' una verifica.

    Nota: in questo profilo le sessioni social non ci sono. Ogni piattaforma
    chiedera' di nuovo l'accesso. Non e' un guasto, e' la separazione dei
    profili che funziona.
    """

    def azione(page):
        dentro = apri_collegamenti(page)
        if dentro is None:
            dump(page, "collega-senza-scheda")
            print(
                "Non trovo «Connected accounts». Il pannello Content Rewards e' "
                "aperto? Esegui prima `login-app`."
            )
            return page
        prima = leggi_collegati(dentro)
        print("\nAdesso la scheda dice:")
        for r in prima:
            print("   ", r)
        print("\n1) Premi «Connect account» e scegli la piattaforma.")
        print("2) Accedi con l'account di QUESTO gruppo e autorizza.")
        print("   Le sessioni social non ci sono in questo profilo: te le richiede.")
        print("3) Ripeti per le altre piattaforme.")
        input("\nQuando hai finito, torna qui e premi INVIO... ")
        dentro = apri_collegamenti(page) or riquadro(page)
        dopo = leggi_collegati(dentro)
        print("\nAdesso la scheda dice:")
        for r in dopo:
            print("   ", r)
        if any("No connected accounts" in r for r in dopo):
            log("NESSUN account collegato: l'autorizzazione non e' andata a buon fine")
        dump(page, "collega-dopo")
        return page

    with sessione(headless=False) as s:
        s.fetch(APP_ESEMPIO, page_action=azione, load_dom=True, wait=3000)


def cmd_join(args) -> None:
    """
    Entra in una campagna. `joinHref` arriva dal radar: e' il campo `join` di
    `radar_dettagli.json`, non un URL da comporre a mano.
    """

    def esito(quale: str) -> None:
        # Una riga sola su stdout, per chi lancia l'iscrizione e non guarda il
        # browser: `orchestrator.py iscrivi`, cioe' il pulsante «scegli».
        print(f"iscrizione: {quale}", flush=True)

    def azione(page):
        attesa(page, 25)
        chi = chi_sono(page)
        if not chi:
            dump(page, "join-non-loggato")
            log("NON sei loggato su Whop: esegui prima `whop_agent.py login`")
            esito("non loggato")
            return page
        log(f"account Whop: @{chi}")
        log(f"pagina: {page.url}")

        # La cornice di Whop si ferma subito, il contenuto del whop no: il 7
        # ottobre 2026 il pulsante si cercava sotto la rotellina, e «nessun
        # pulsante» era solo fretta. Chi non e' iscritto puo' non avere mai il
        # riquadro dell'applicazione: dopo l'attesa si guarda comunque.
        aspetta_app(page, 30)
        attesa(page, 15)
        dentro = riquadro(page)
        log(f"riquadro: {dentro.url[:90]}")
        if "/login" in (dentro.url or ""):
            dump(page, "join-app-non-loggata")
            log(
                "Il pannello Content Rewards ha un login suo e non e' aperto: "
                "esegui `whop_agent.py login-app`"
            )
            esito("non loggato")
            return page

        btn = trova(dentro, JOIN_LABELS)
        if btn is None and args.titolo:
            # Il collegamento profondo porta all'elenco del provider, non alla
            # campagna (7 ottobre 2026, On Successful DNA): l'iscrizione, se
            # c'e', sta dentro la scheda. Si apre come per l'invio — per titolo,
            # con l'uuid come prova — e si guarda li'.
            uuid = (re.search(r"campaigns/([0-9a-f-]{36})", args.url) or [None, ""])[1]
            if apri_scheda(page, args.titolo, uuid):
                dentro = riquadro(page)
                try:
                    if dentro.locator('button:has-text("Submit clip")').count():
                        log("la scheda ha gia' «Submit clip»: nessuna iscrizione da fare")
                        org = None if args.prova else segna_iscritto(args.url)
                        if org:
                            log(f"segnato «{org}» come iscritto in radar/provider.json")
                        dump(page, "join-gia-aperta")
                        esito("gia' dentro")
                        return page
                except Exception:
                    pass
                btn = trova(dentro, JOIN_LABELS)

        gia = trova(dentro, JOINED_LABELS)
        if gia and not btn:
            log(f"sembra gia' fatta: trovato «{gia['testo']}», nessun pulsante di iscrizione")
            esito("gia' dentro")
            return page

        if btn is None:
            dump(page, "join-senza-pulsante")
            log(
                "nessun pulsante di iscrizione riconosciuto. Guarda la diagnostica: "
                "se l'etichetta e' cambiata, aggiungila a JOIN_LABELS."
            )
            esito("da guardare")
            return page

        log(f"pulsante trovato: «{btn['testo']}»")
        if args.prova:
            dump(page, "join-prova")
            log("PROVA: non clicco. Togli --prova per entrare davvero.")
            esito("prova")
            return page

        if not clicca(dentro, btn, "il pulsante di iscrizione"):
            dump(page, "join-clic-fallito")
            esito("da guardare")
            return page
        page.wait_for_timeout(6000)
        dump(page, "join-dopo-clic")

        # Whop puo' aprire un passaggio di conferma (accettazione, checkout a
        # zero, verifica del pubblico): si guarda cosa c'e' e si riferisce, non
        # si tira a indovinare cliccando il primo pulsante che capita.
        dentro = riquadro(page)
        resto = trova(dentro, JOIN_LABELS)
        try:
            testo = dentro.inner_text("body")[:600]
        except Exception:
            testo = ""
        log(f"dopo il clic — URL: {page.url}")
        log(f"dopo il clic — testo: {re.sub(chr(10), ' | ', testo[:300])}")
        if resto:
            log(
                f"c'e' ancora «{resto['testo']}»: il flusso ha un passaggio in piu'. "
                "Guarda lo screenshot e dimmi cosa chiede."
            )
            esito("da guardare")
        else:
            log(
                "nessun pulsante di iscrizione residuo: iscrizione probabilmente andata. "
                "Verificala con `apri --url` sulla stessa pagina."
            )
            org = segna_iscritto(args.url)
            if org:
                log(f"segnato «{org}» come iscritto in radar/provider.json")
            esito("fatta")
        return page

    with sessione(headless=args.headless) as s:
        s.fetch(args.url, page_action=azione, load_dom=True, wait=3000)


# Dal titolo «Reference materials» si sale fino al primo contenitore che ha un
# link o la scritta del blocco: e' la sezione, e la sezione dopo («Content
# requirements») e' una sorella, quindi resta fuori.
SEZIONE_MATERIALI = """() => {
  const tit = [...document.querySelectorAll('*')].find(
    e => e.children.length === 0 && e.textContent.trim() === 'Reference materials');
  if (!tit) return null;
  let box = tit;
  for (let i = 0; i < 8 && box.parentElement; i++) {
    box = box.parentElement;
    if (box.querySelector('a[href]') || box.innerText.includes('Visible once you join')) break;
  }
  return {testo: box.innerText,
          link: [...box.querySelectorAll('a[href]')].map(a => [a.innerText.trim(), a.href])};
}"""


def materiali_da_sezione(sezione: dict | None) -> tuple[str, list[dict]]:
    """
    I materiali come li mostra la scheda: l'esito e le voci per l'archivio.

    Si legge solo cio' che la pagina mostra a noi. L'elenco `discover` del
    pannello porta i materiali di tutte le campagne, anche dove la scheda dice
    «Visible once you join»: e' un dato che il sito nasconde apposta, e leggerlo
    con l'account che incassa e' un rischio scelto di non correre (31 ottobre
    2026). Le voci hanno la forma di `materiali_dett` del radar; `tipo` e
    `mezzo` la pagina non li dice, l'etichetta si' — «Example clips» e' proprio
    l'indizio che il risolutore cerca.
    """
    if not sezione:
        return "senza sezione", []
    if "Visible once you join" in (sezione.get("testo") or ""):
        return "chiusi", []
    voci, visti = [], set()
    for etichetta, url in sezione.get("link") or []:
        if url.startswith("http") and url not in visti:
            visti.add(url)
            voci.append({"url": url, "tipo": None, "mezzo": None, "etichetta": etichetta})
    return ("letti" if voci else "vuota"), voci


def cmd_materiali(args) -> None:
    """
    Legge i materiali dalla scheda di una campagna a cui siamo iscritti. Non
    tocca niente: apre la scheda come `invii`.

    Le ultime righe sono `materiali: <esito>` e, se letti, `materiali_json:`
    con le voci, per `orchestrator.py`, che le scrive nell'archivio del radar.
    """

    def azione(page):
        attesa(page, 25)
        if "/login" in (riquadro(page).url or ""):
            dump(page, "materiali-app-non-loggata")
            log("Il pannello Content Rewards non e' aperto: esegui `login-app`")
            print("materiali: non leggibile", flush=True)
            return page
        if not vai_alla_scheda(page, args.titolo, args.campagna):
            dump(page, "materiali-campagna-non-aperta")
            print("materiali: non leggibile", flush=True)
            return page
        page.wait_for_timeout(5000)
        try:
            sezione = riquadro(page).evaluate(SEZIONE_MATERIALI)
        except Exception as exc:
            log(f"sezione non letta: {exc}")
            sezione = None
        esito, voci = materiali_da_sezione(sezione)
        if esito != "letti":
            dump(page, f"materiali-{esito.replace(' ', '-')}")
        for v in voci:
            log(f"  {v['etichetta'][:50]} -> {v['url'][:100]}")
        print(f"materiali: {esito}", flush=True)
        if voci:
            print("materiali_json: " + json.dumps(voci, ensure_ascii=False), flush=True)
        return page

    with sessione(headless=args.headless) as s:
        s.fetch(radice_app(args.url), page_action=azione, load_dom=True, wait=3000)


INVII = HERE / "whop_invii.json"


def registra_invio(campagna: str, post: str, esito: str) -> None:
    """
    Il registro degli invii: quale clip, in quale campagna, quando, com'e'
    andata. E' il sostituto di `altrofornitore_add()`, e serve a due cose che nessuna
    pagina sa dire dopo: non riproporre due volte lo stesso post — la campagna
    lo rifiuta — e sapere a distanza di settimane quali invii hanno pagato.
    """
    voci = []
    if INVII.exists():
        try:
            voci = json.loads(INVII.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            voci = []
    voci.append(
        {
            "quando": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
            "campagna": campagna,
            "post": post,
            "esito": esito,
        }
    )
    INVII.write_text(json.dumps(voci, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------- invii aperti?

INTAKE = "/api/submission/submissions/intake?campaignIds="


def ascolta_invii(page) -> dict[str, dict]:
    """
    Raccoglie le risposte sullo stato degli invii che il pannello chiede da solo.

    Aprendo una scheda, il pannello chiede `INTAKE<uuid>` per decidere se
    accendere «Submit clip». Ascoltarle costa niente e non fa richieste in piu'.
    """
    catturati: dict[str, dict] = {}

    def su_risposta(risposta):
        if INTAKE.split("?")[0] not in risposta.url:
            return
        try:
            dati = risposta.json()
        except Exception:
            return
        for voce in (dati or {}).get("data") or []:
            if isinstance(voce, dict) and voce.get("campaignId"):
                catturati[voce["campaignId"]] = voce

    page.on("response", su_risposta)
    return catturati


def stato_invii(dentro, uuid: str, catturati: dict[str, dict]) -> dict | None:
    """
    Se la campagna accetta invii, come lo dice il pannello: la voce di `INTAKE`.

    Prima la risposta gia' ricevuta dal pannello; se manca, la si richiede
    **dall'interno del riquadro**, dove la sessione vale — da fuori l'endpoint
    risponde `NOT_AUTHORIZED`. None se non si riesce a sapere.
    """
    if uuid in catturati:
        return catturati[uuid]
    try:
        testo = dentro.evaluate(
            """async (url) => {
                 const r = await fetch(url, {credentials: 'include'});
                 return await r.text();
               }""",
            INTAKE + uuid,
        )
        dati = json.loads(testo)
    except Exception:
        return None
    return next(
        (
            v
            for v in (dati or {}).get("data") or []
            if isinstance(v, dict) and v.get("campaignId") == uuid
        ),
        None,
    )


def descrivi_invii(stato: dict | None) -> tuple[str, str] | None:
    """
    L'esito e la frase per chi legge, quando la campagna non accetta invii.

    Il 16 settembre 2026 «Submit clip» era `disabled` senza una parola in pagina,
    e l'agente scriveva «non trovo il pulsante»: sembrava un selettore rotto, ed
    era Contoso che aveva sospeso gli invii (`intake: closed`, motivo `hold`).
    **In pausa** e **chiusi** si distinguono perche' portano a decisioni diverse:
    una pausa riapre, e le clip si tengono ferme; una chiusura no.
    None quando gli invii risultano aperti o lo stato non si sa: li' il guasto,
    se c'e', e' nostro.
    """
    if not stato or stato.get("intake") != "closed":
        return None
    motivo = str(stato.get("intakeClosedReason") or "").strip()
    if motivo == "hold":
        return (
            "invii in pausa",
            "la campagna ha messo in pausa gli invii (intake closed, motivo «hold»): "
            "il pulsante di invio e' spento finche' il committente non riapre",
        )
    return (
        "invii chiusi",
        f"la campagna non accetta piu' invii (intake closed, motivo "
        f"«{motivo or 'non dichiarato'}»)",
    )


def esito_invii(stato: dict | None) -> str:
    """
    Lo stato degli invii in una parola sola, per chi decide se pubblicare.

    **Aperti vuol dire `intake: open`, e nient'altro.** `descrivi_invii` tace
    su tutto cio' che non e' `closed`, ed e' giusto per spiegare un pulsante
    spento; qui e' il verso opposto. Un valore che non conosciamo — l'API non
    ne documenta l'elenco, e finora se ne sono visti due — non e' un permesso:
    davanti c'e' una clip che, uscita a campagna chiusa, non si invia piu'.
    """
    if not stato:
        return "non leggibile"
    spiegato = descrivi_invii(stato)
    if spiegato:
        return spiegato[0].removeprefix("invii ")
    if stato.get("intake") == "open":
        return "aperti"
    return f"sconosciuto ({stato.get('intake')})"


def vai_alla_scheda(page, titolo: str, uuid: str) -> bool:
    """
    Dal pannello alla scheda della campagna.

    `campaigns` sono **le mie** campagne, e con zero iscrizioni rimbalza su
    `discover` — che e' l'elenco di tutte, cioe' dove la scheda si trova
    davvero. Quindi qui non si pretende la rotta: si pretende la scheda.
    """
    vai_a(page, "campaigns", 25)
    if apri_scheda(page, titolo, uuid):
        return True
    vai_a(page, "discover", 25)
    return apri_scheda(page, titolo, uuid)


def gia_inviato(post: str) -> dict | None:
    if not INVII.exists():
        return None
    try:
        voci = json.loads(INVII.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return next(
        (v for v in voci if v.get("post") == post and v.get("esito", "").startswith("inviato")),
        None,
    )


def aspetta_conferma(page, secondi: int = 90) -> bool:
    """
    Aspetta che il pulsante esca da «Submitting…», cioe' che l'invio sia finito.

    Senza, tutto quello che viene dopo la conferma leggeva una pagina a meta':
    16 dump «invia-dopo» su 17 mostrano ancora «Submitting…». Il 14 settembre
    l'elenco degli invii e' stato letto 17 secondi dopo, prima che Whop finisse
    di verificare un post TikTok, e un invio arrivato e' stato registrato come
    «non registrato». E ricaricare la pagina mentre la richiesta e' in volo puo'
    anche interromperla, che e' peggio del falso negativo.
    """
    page.wait_for_timeout(1500)
    for _ in range(max(1, secondi)):
        try:
            in_corso = riquadro(page).locator('button:has-text("Submitting")').count() > 0
        except Exception:
            in_corso = False
        if not in_corso:
            return True
        page.wait_for_timeout(1000)
    return False


def id_post(post: str) -> str:
    """L'identificatore del video dentro un indirizzo: e' quello che si confronta.

    Le due parti scrivono lo stesso post in forme diverse — con o senza www,
    shorts o watch — quindi l'indirizzo intero non e' una chiave.
    """
    chiave = re.search(r"(?:shorts/|watch\?v=|youtu\.be/|/video/|/reel/)([\w\-]{6,})", post)
    return chiave.group(1) if chiave else post


def risulta_inviato(
    page, post: str, app: str = "", attese: tuple[int, ...] = (0, 15, 30, 45)
) -> bool | None:
    """
    Cerca il post nell'elenco «Submissions» della campagna.

    E' l'unica prova che l'invio esista: la pagina dopo la conferma non dice
    «rifiutato» nemmeno quando non registra niente, e cosi' due invii dichiarati
    riusciti non erano mai arrivati — i video erano bozze, e il pannello mostrava
    un solo invio su tre. Assenza di parole d'errore non e' presenza dell'invio.

    **Ma assenza dall'elenco al primo sguardo non e' assenza dell'invio.** Whop
    verifica il post prima di elencarlo, e su TikTok ci mette di piu': una
    lettura sola, fatta presto, ha dato un falso «non registrato». Quindi si
    rilegge, con attese crescenti — la finestra dei 30 minuti riguarda l'invio,
    che a questo punto e' gia' partito, non la sua verifica.

    Tre esiti, non due: `True` il post c'e', `False` l'elenco e' stato letto e il
    post non c'e', `None` l'elenco non si e' potuto leggere. «Non l'ho trovato»
    e «non ho potuto guardare» non possono dare lo stesso esito.
    """
    ago = id_post(post)
    letto = False
    for n, pausa in enumerate(attese, 1):
        if pausa:
            page.wait_for_timeout(pausa * 1000)
        try:
            # Il modulo appena confermato resta aperto sopra la scheda della
            # campagna e il clic sulla voce di menu non cambia rotta: si ricarica
            # il guscio, che e' il modo piu' semplice di chiudere qualunque cosa
            # ci sia davanti senza dover indovinare quale.
            if app:
                page.goto(app, wait_until="domcontentloaded")
                aspetta_app(page, 25)
            if not vai_a(page, "submissions", 25):
                log(f"  lettura {n}/{len(attese)}: non riesco ad aprire l'elenco degli invii")
                continue
            dentro = aspetta_app(page, 20)
            attesa(page, 10)
            indirizzi = (
                dentro.eval_on_selector_all(
                    "a[href]", "els => els.map(e => e.getAttribute('href'))"
                )
                or []
            )
        except Exception as exc:
            log(
                f"  lettura {n}/{len(attese)}: elenco degli invii non leggibile "
                f"({type(exc).__name__})"
            )
            continue
        letto = True
        if any(ago in (h or "") for h in indirizzi):
            if n > 1:
                log(f"  il post compare fra gli invii alla lettura {n}")
            return True
        log(f"  lettura {n}/{len(attese)}: il post non compare ancora fra gli invii")
    if letto:
        dump(page, "invia-non-in-elenco")
        return False
    return None


def registro_whop(args) -> str | None:
    """L'handle Whop atteso per il gruppo chiesto, se il registro lo sa."""
    gruppo = getattr(args, "gruppo", "") or None
    if not gruppo:
        return None
    try:
        import registro
    except ImportError:
        return None
    return registro.whop_handle(gruppo)


def cmd_invii(args) -> None:
    """
    Dice se una campagna accetta invii adesso. Non tocca niente.

    Serve a sapere quando riprendere le clip tenute ferme mentre la campagna era
    in pausa: il pannello non lo scrive da nessuna parte, spegne solo il
    pulsante. E lo chiede `orchestrator.py invii` prima di ogni pubblicazione.

    L'ultima riga e' `esito invii: <esito>` (`esito_invii`) ed e' quella che si
    legge da fuori, **su ogni strada**: un'uscita senza quella riga vuol dire
    che non si e' arrivati a guardare, e chi legge la tratta cosi'.
    """

    def azione(page):
        catturati = ascolta_invii(page)
        attesa(page, 25)
        if "/login" in (riquadro(page).url or ""):
            dump(page, "invii-app-non-loggata")
            log("Il pannello Content Rewards non e' aperto: esegui `login-app`")
            log("esito invii: non leggibile")
            return page
        if not vai_alla_scheda(page, args.titolo, args.campagna):
            dump(page, "invii-campagna-non-aperta")
            log("non sono arrivato alla scheda della campagna")
            log("esito invii: non leggibile")
            return page
        page.wait_for_timeout(3000)
        stato = stato_invii(riquadro(page), args.campagna, catturati)
        spiegato = descrivi_invii(stato)
        if stato is None:
            log("stato degli invii: non leggibile")
        elif spiegato:
            log(f"stato degli invii: {spiegato[0]} — {spiegato[1]}")
        else:
            log(
                f"stato degli invii: intake «{stato.get('intake')}»"
                + (", quasi pieno" if stato.get("almostFull") else "")
            )
        log(f"esito invii: {esito_invii(stato)}")
        return page

    with sessione(headless=args.headless) as s:
        s.fetch(radice_app(args.app), page_action=azione, load_dom=True, wait=3000)


def cmd_invia(args) -> None:
    """
    Registra un post gia' pubblicato nella campagna. E' il pezzo che mancava.

    Il vincolo che decide tutto sta scritto nel modulo, non nel brief: **il
    video va inviato entro 30 minuti dalla pubblicazione**, altrimenti viene
    respinto in automatico. Quindi pubblicare e inviare non sono due fasi che si
    possono staccare: `publish_agent.py` deve chiamare questo comando appena ha
    l'URL del post, non la sera dopo.

    Gli altri due controlli che la campagna dichiara di fare: il post deve
    venire da un account collegato, e non essere gia' stato inviato qui.
    """
    if not args.davvero:
        log(
            "PROVA: riempio il modulo e mi fermo prima di confermare. "
            "Aggiungi --davvero per inviare sul serio."
        )
    doppione = gia_inviato(args.post)
    if doppione:
        log(
            f"questo post e' gia' stato inviato il {doppione['quando']} "
            f"({doppione['campagna']}): la campagna lo rifiuterebbe. Mi fermo."
        )
        return

    def azione(page):
        catturati = ascolta_invii(page)
        attesa(page, 25)
        dentro = riquadro(page)
        if "/login" in (dentro.url or ""):
            dump(page, "invia-app-non-loggata")
            log("Il pannello Content Rewards non e' aperto: esegui `login-app`")
            return page

        # Da quale conto si sta inviando. I Whop sono uno per nicchia, e un
        # invio nel pannello sbagliato non paga: la campagna vuole che l'account
        # che ha pubblicato sia collegato **a quel** Whop.
        #
        # Qui «non si sa» non blocca, al contrario che su TikTok, e la ragione
        # e' che significa un'altra cosa: l'handle Whop non compare su tutte le
        # pagine — dentro una comunita' la barra mostra il saldo, non il nome —
        # mentre la sessione l'ha gia' dimostrata il controllo su /login qui
        # sopra. Fermarsi qui vorrebbe dire buttare la finestra dei 30 minuti
        # su un video gia' pubblicato.
        atteso = registro_whop(args)
        if atteso:
            trovato = (chi_sono(page, naviga=False) or "").lstrip("@").lower()
            if trovato == "?":
                # naviga=False fa tornare "?" quando l'handle non e' sulla
                # pagina campagna: e' lo stesso "non si sa" del caso vuoto
                # qui sopra, non un handle da confrontare. Confrontarlo come
                # se fosse reale blocca ogni invio da questa pagina con un
                # falso "profilo sbagliato" anche a sessione giusta.
                trovato = ""
            if trovato and trovato != atteso:
                dump(page, "invia-conto-sbagliato")
                log(
                    f"NON invio: il pannello e' aperto come @{trovato}, ma questo "
                    f"gruppo incassa su @{atteso}. E' il profilo Whop sbagliato."
                )
                return page
            log(
                f"  conto Whop: @{trovato or 'non leggibile qui'}"
                + ("" if trovato else f" (atteso @{atteso})")
            )

        titolo = getattr(args, "titolo", "") or ""
        aperta = vai_alla_scheda(page, titolo, args.campagna)
        if not aperta:
            dump(page, "invia-campagna-non-aperta")
            log("non sono arrivato alla scheda della campagna")
            return page

        dentro = riquadro(page)
        voce = trova(dentro, ["Submit clip", "Invia clip"])
        if voce is None or not clicca(dentro, voce, "il pulsante di invio"):
            dump(page, "invia-senza-pulsante")
            stato = stato_invii(dentro, args.campagna, catturati)
            spiegato = descrivi_invii(stato)
            if spiegato:
                # Non e' un guasto nostro e non si riprova: si dice cosa ha
                # deciso la campagna, con un esito che il pannello mostra.
                esito, frase = spiegato
                log(f"esito: {esito} — {frase}")
                registra_invio(args.campagna, args.post, esito)
                azione.esito = esito
                return page
            log(
                "non trovo il pulsante di invio sulla scheda"
                + (
                    " (e la campagna risulta aperta agli invii)"
                    if stato
                    else " (e non sono riuscito a leggere se la campagna accetta invii)"
                )
            )
            return page
        attesa(page, 15)

        dentro = riquadro(page)
        try:
            campo = dentro.locator('input[type="url"]').first
            campo.fill(args.post, timeout=10000)
            # Rileggere e' l'unica prova che il campo abbia preso: gli editor
            # controllati da React accettano il testo e lo riscrivono da soli.
            scritto = campo.input_value(timeout=5000)
        except Exception as exc:
            dump(page, "invia-campo-assente")
            log(f"non riesco a scrivere il link: {type(exc).__name__}")
            return page
        if scritto.strip() != args.post.strip():
            dump(page, "invia-campo-diverso")
            log(f"il campo contiene «{scritto[:80]}» invece del link dato: non confermo")
            return page
        log(f"link scritto nel modulo: {scritto[:80]}")

        try:
            dentro.evaluate("""() => {
                const inp = document.querySelector('input[type="checkbox"]');
                if (inp) {
                    inp.click();
                }
            }""")
            page.wait_for_timeout(2500)
            log("accettazione delle regole spuntata")
        except Exception as exc:
            dump(page, "invia-spunta-assente")
            log(f"non riesco a spuntare l'accettazione: {type(exc).__name__}")
            return page

        dump(page, "invia-modulo-pieno")
        if not args.davvero:
            log(
                "PROVA finita: modulo pieno, niente inviato. "
                "Guarda lo screenshot, poi rilancia con --davvero."
            )
            return page

        # Il pulsante di conferma ha la **stessa etichetta** di quello che ha
        # aperto il modulo, e la finestra non e' un `role="dialog"`: non c'e'
        # niente da cui distinguerli se non l'ordine nel documento — il modulo
        # e' aggiunto in fondo, quindi la conferma e' l'ultimo. Resta abilitato
        # solo a modulo valido, ed e' la verifica che tiene: se e' spento,
        # qualcosa non e' andato e non si insiste.
        conferma = dentro.locator('button:has-text("Submit clip")').last
        try:
            if not conferma.count():
                raise RuntimeError("nessun pulsante di conferma")
            if not conferma.is_enabled(timeout=5000):
                dump(page, "invia-conferma-spenta")
                log(
                    "il pulsante di conferma e' disabilitato: il modulo non e' "
                    "valido (link o accettazione). Non invio."
                )
                return page
            conferma.click(timeout=10000)
        except Exception as exc:
            dump(page, "invia-conferma-non-trovata")
            log(f"non riesco a confermare: {type(exc).__name__}")
            return page

        if not aspetta_conferma(page):
            log(
                "  il pulsante e' ancora su «Submitting…» dopo 90 secondi: "
                "verifico lo stesso nell'elenco degli invii"
            )
        attesa(page, 20)
        dentro = riquadro(page)
        try:
            testo = re.sub(r"\s+", " ", dentro.inner_text("body"))[:400]
        except Exception:
            testo = ""
        dump(page, "invia-dopo")
        rifiutato = re.search(r"reject|error|not eligible|failed|non idone", testo, re.I)
        trovato = None if rifiutato else risulta_inviato(page, args.post, args.app)
        if rifiutato:
            esito = "rifiutato"
        elif trovato:
            esito = "inviato"
        elif trovato is None:
            esito = "non verificato"
            log(
                "non sono riuscito a leggere l'elenco degli invii: l'invio potrebbe "
                "esserci. Controllalo nel pannello prima di rifarlo."
            )
        else:
            esito = "non registrato"
            log(
                "il post NON compare fra gli invii della campagna dopo quattro letture: "
                "l'invio non e' andato a segno, qualunque cosa dica la pagina dopo la conferma."
            )
        log(f"esito: {esito} — {testo[:200]}")
        registra_invio(args.campagna, args.post, esito)
        log(f"registrato in {INVII.name}")
        azione.esito = esito
        return page

    with sessione(headless=args.headless) as s:
        s.fetch(radice_app(args.app), page_action=azione, load_dom=True, wait=3000)
    return getattr(azione, "esito", None)


# ------------------------------------------------------------------ guadagni

# Dove il pannello tiene i pagamenti: la pagina «earnings» la chiede da sola, e
# risponde soltanto dall'interno del riquadro, come `INTAKE`.
PAYOUTS = "/api/submission/payouts"

# I contatori in cima a «Submissions», nell'ordine in cui la pagina li scrive.
CONTATORI = ("Clips", "Retainer", "Approved", "Pending", "Rejected", "History")


def contatori_invii(testo: str) -> dict[str, int] | None:
    """
    I contatori di «Submissions»: quante clip, quante approvate, in attesa, respinte.

    Sono lo stato **vero** degli invii, quello che `stato.pagamento()` finora
    poteva solo dedurre da views e soglia. La pagina li scrive come etichetta e
    numero su due righe («Pending» / «12»). None se non ci sono almeno «Clips» e
    «Pending»: una pagina cambiata non deve diventare «zero approvate».
    """
    righe = [r.strip() for r in (testo or "").splitlines() if r.strip()]
    trovati: dict[str, int] = {}
    for etichetta, valore in itertools.pairwise(righe):
        if etichetta in CONTATORI and etichetta not in trovati and re.fullmatch(r"[\d.,]+", valore):
            trovati[etichetta] = int(re.sub(r"[.,]", "", valore))
    if not {"Clips", "Pending"} <= trovati.keys():
        return None
    return {k.lower(): v for k, v in trovati.items()}


def _numero(v) -> float | None:
    """Gli importi arrivano come stringhe («0», «125»): un numero, o None."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def somma_pagamenti(dati) -> dict:
    """
    Quanti pagamenti, di che stato, e quanti dollari **netti** sono arrivati.

    La prima voce vista, il 28 settembre 2026 sul conto del laboratorio, era
    `amount: "0"`, `netAmount: "0"`, `status: "reversed"` — e i paletti dentro
    la stessa voce sono dichiarati in centesimi (`maxPayoutCents`), l'importo
    no. Da qui tre regole:

    - **un campo `...Cents` e' in centesimi**, e si preferisce il netto;
    - **zero e' zero in qualunque unita'**, quindi un importo nullo si somma
      anche senza sapere se `amount` e' in dollari o in centesimi;
    - **un importo diverso da zero senza unita' dichiarata non si indovina**:
      `totale: None` con le chiavi viste, perche' scommettere vuol dire scrivere
      un incasso cento volte sbagliato, e «non so leggerlo» non e' «zero».

    Un pagamento stornato (`status: reversed`) non e' un incasso: si conta fra
    gli stati e resta fuori dal totale.
    """
    voci = dati.get("data") if isinstance(dati, dict) else None
    if not isinstance(voci, list):
        return {"n": None, "totale": None, "forma": "risposta senza elenco"}
    totale = 0.0
    stati: dict[str, int] = {}
    for voce in voci:
        if not isinstance(voce, dict):
            return {"n": len(voci), "totale": None, "forma": "voce non leggibile"}
        stato_voce = str(voce.get("status") or "?")
        stati[stato_voce] = stati.get(stato_voce, 0) + 1
        if stato_voce == "reversed" or voce.get("reversedAt"):
            continue
        centesimi = next(
            (
                _numero(voce[k])
                for k in ("netAmountCents", "amountCents")
                if _numero(voce.get(k)) is not None
            ),
            None,
        )
        if centesimi is not None:
            totale += centesimi / 100
            continue
        nudo = next(
            (_numero(voce[k]) for k in ("netAmount", "amount") if _numero(voce.get(k)) is not None),
            None,
        )
        if nudo == 0:
            continue
        return {
            "n": len(voci),
            "totale": None,
            "stati": stati,
            "forma": "importo da riconoscere",
            "chiavi": sorted(voce)[:25],
        }
    return {"n": len(voci), "totale": round(totale, 2), "stati": stati}


def cmd_guadagni(args) -> None:
    """
    Legge dal pannello Content Rewards lo stato degli invii e i pagamenti. Non tocca niente.

    Due pagine: «Submissions», che scrive i contatori (clip, approvate, in
    attesa, respinte), ed «earnings», che chiede `PAYOUTS`. L'ultima riga e'
    `guadagni: <json>` ed e' quella che legge `metriche.py --whop`; una parte
    che non si e' potuta leggere resta `null`, mai zero.
    """
    risultato: dict = {
        "letto_il": datetime.now().isoformat(timespec="seconds"),
        "account": None,
        "invii": None,
        "pagamenti": None,
    }

    def azione(page):
        catturati: dict = {}

        def su_risposta(risposta):
            if PAYOUTS in risposta.url:
                try:
                    catturati["payouts"] = risposta.json()
                except Exception:
                    pass

        page.on("response", su_risposta)
        attesa(page, 25)
        if "/login" in (riquadro(page).url or ""):
            dump(page, "guadagni-app-non-loggata")
            log("Il pannello Content Rewards non e' aperto: esegui `login-app`")
            return page
        risultato["account"] = (chi_sono(page, naviga=False) or "").strip("?") or None
        if vai_a(page, "submissions", 25):
            page.wait_for_timeout(4000)
            try:
                risultato["invii"] = contatori_invii(riquadro(page).inner_text("body"))
            except Exception as exc:
                log(f"contatori degli invii non leggibili ({type(exc).__name__})")
            if risultato["invii"] is None:
                dump(page, "guadagni-contatori")
        page.goto(radice_app(args.app), wait_until="domcontentloaded")
        aspetta_app(page, 25)
        if "payouts" not in catturati and vai_a(page, "earnings", 25):
            page.wait_for_timeout(5000)
        if "payouts" in catturati:
            risultato["pagamenti"] = somma_pagamenti(catturati["payouts"])
        else:
            log("la risposta dei pagamenti non e' arrivata")
        return page

    with sessione(headless=args.headless) as s:
        s.fetch(radice_app(args.app), page_action=azione, load_dom=True, wait=3000)
    print("guadagni: " + json.dumps(risultato, ensure_ascii=False), flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Agente Whop: iscrizione alle campagne")
    ap.add_argument(
        "--headless",
        action="store_true",
        help="senza finestra (sconsigliato per join: i passaggi di conferma vanno visti)",
    )
    ap.add_argument(
        "--gruppo",
        default="",
        help="il gruppo di dashboard/identita.json: un account Whop per nicchia, "
        "perche' Whop ne regge uno per piattaforma",
    )
    ap.add_argument(
        "--profilo", default="", help="cartella del profilo, se non la si vuole dal registro"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("login").set_defaults(func=cmd_login)
    sub.add_parser("stato").set_defaults(func=cmd_stato)

    la = sub.add_parser("login-app", help="il secondo login: il pannello Content Rewards")
    la.add_argument(
        "--url",
        default=CAMPAGNA_ESEMPIO,
        help="una qualunque campagna: serve solo ad aprire il pannello",
    )
    la.set_defaults(func=cmd_login_app)

    sub.add_parser("whops", help="elenca i whop di cui sei membro").set_defaults(func=cmd_whops)

    sub.add_parser("collega", help="apre Settings -> Connected accounts e aspetta te").set_defaults(
        func=cmd_collega
    )
    sub.add_parser("collegati", help="dice quali account social risultano collegati").set_defaults(
        func=cmd_collegati
    )

    a = sub.add_parser("apri")
    a.add_argument("--url", required=True)
    a.add_argument("--testo", type=int, default=2500, help="quanti caratteri di testo stampare")
    a.add_argument("--attesa", type=int, default=25, help="secondi massimi di attesa del contenuto")
    a.add_argument(
        "--vai",
        default="",
        help="rotte interne da aprire in sequenza, separate da virgola: "
        "submissions, campaigns, earnings, settings, "
        "oppure «campaigns,campaigns/<uuid>» per una scheda",
    )
    a.add_argument(
        "--clicca",
        default="",
        help="etichetta da cliccare dopo le rotte, per aprire un modulo "
        "(es. «Submit clip»). Un clic solo, e non conferma niente",
    )
    a.set_defaults(func=cmd_apri)

    i = sub.add_parser("invia", help="registra un post pubblicato in una campagna")
    i.add_argument("--campagna", required=True, help="uuid della campagna (dalla sua scheda)")
    i.add_argument(
        "--titolo",
        default="",
        help="il nome della campagna: e' con quello che si aggancia la sua "
        "scheda nell'elenco, perche' l'uuid li' non compare",
    )
    i.add_argument("--post", required=True, help="URL del post gia' pubblicato")
    i.add_argument(
        "--app", default=APP_CLIP_FARM, help="indirizzo del pannello Content Rewards del provider"
    )
    i.add_argument(
        "--davvero",
        action="store_true",
        help="conferma l'invio: senza, riempie il modulo e si ferma",
    )
    i.set_defaults(func=cmd_invia)

    v = sub.add_parser("invii", help="dice se una campagna accetta invii adesso (sola lettura)")
    v.add_argument("--campagna", required=True, help="uuid della campagna")
    v.add_argument("--titolo", default="", help="il nome della campagna, per agganciarne la scheda")
    v.add_argument(
        "--app", default=APP_CLIP_FARM, help="indirizzo del pannello Content Rewards del provider"
    )
    v.set_defaults(func=cmd_invii)

    gd = sub.add_parser(
        "guadagni", help="stato degli invii e pagamenti dal pannello (sola lettura)"
    )
    gd.add_argument(
        "--app", default=APP_CLIP_FARM, help="indirizzo del pannello Content Rewards del provider"
    )
    gd.set_defaults(func=cmd_guadagni)

    j = sub.add_parser("join")
    j.add_argument("--url", required=True, help="il joinHref della campagna")
    j.add_argument("--prova", action="store_true", help="mostra cosa farebbe senza cliccare")
    j.add_argument("--titolo", default="", help="il titolo della campagna, per aprirne la scheda")
    j.set_defaults(func=cmd_join)

    m = sub.add_parser("materiali", help="i materiali di una campagna iscritta (sola lettura)")
    m.add_argument("--url", required=True, help="il joinHref della campagna")
    m.add_argument("--titolo", required=True)
    m.add_argument("--campagna", required=True, help="uuid, la prova che la scheda e' quella")
    m.set_defaults(func=cmd_materiali)

    args = ap.parse_args()
    if args.profilo:
        scelto = Path(args.profilo)
        usa_profilo(scelto if scelto.is_absolute() else HERE / scelto)
    elif args.gruppo:
        # Whop non e' una piattaforma di pubblicazione, quindi non ha una
        # identita' nel registro: il profilo si ricava dal nome del gruppo, che
        # e' l'unica cosa che serve a tenerli separati.
        usa_profilo(HERE / f".whop-{args.gruppo}")
    if args.gruppo or args.profilo:
        log(f"profilo Whop: {PROFILE_DIR.name}")
    args.func(args)


if __name__ == "__main__":
    main()
