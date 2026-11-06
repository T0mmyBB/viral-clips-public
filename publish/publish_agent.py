#!/usr/bin/env python
"""
publish_agent.py — agente di pubblicazione clip verticali.

Per ogni clip di una cartella:
  1. la carica su TikTok con la didascalia prevista (menzione @brandly.ai inclusa),
  2. recupera l'URL del post pubblicato,
  3. scrive un commento sotto al video,
  4. aggiunge l'URL alla campagna AltroFornitore.

Tutto in locale, con un profilo browser persistente: nessuna API, nessuna
credenziale salvata dallo script (il login lo fai a mano una volta sola).

Comandi
-------
  python publish_agent.py login                  # login TikTok + AltroFornitore (una tantum)
  python publish_agent.py plan                   # cosa manca e a che ora andrebbe
  python publish_agent.py run --limit 10         # pubblica un lotto e esce
  python publish_agent.py run --limit 1 --dry-run  # prova tutto SENZA pubblicare
  python publish_agent.py status                 # stato e URL raccolti
  python publish_agent.py reset --id 07          # rimette in coda una clip

Ogni passo è annotato in publish_state.json: se qualcosa si rompe a metà,
un nuovo `run` riprende esattamente da dove si era fermato.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import registro
import ritmo

try:
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sys.exit(
        "Playwright non è installato. Esegui prima setup.ps1 (o: pip install playwright && playwright install chromium)"
    )

HERE = Path(__file__).resolve().parent
DEFAULT_FOLDER = HERE.parent / "output" / "esempio-video"
PROFILE_DIR = HERE / ".browser-profile"
LOG_DIR = HERE / "logs"

TIKTOK_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# etichette del pulsante di pubblicazione nelle lingue che TikTok può servire
POST_LABELS = ["Pubblica", "Post", "Publier", "Publicar", "Posten"]


# --------------------------------------------------------------------------- utils


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(exist_ok=True)
    with (LOG_DIR / "agent.log").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def load_json(path: Path, default=None):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def first_visible(page, selectors, timeout=8000):
    """Ritorna il primo locator visibile tra quelli proposti, altrimenti None."""
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible():
                    return loc
            except Exception:
                continue
        page.wait_for_timeout(300)
    return None


JOYRIDE_SKIP = [
    '[data-test-id="button-skip"]',
    '[data-test-id="button-close"]',
    'button[aria-label="Close"]',
    'button:has-text("Salta")',
    'button:has-text("Ignora")',
    'button:has-text("Ho capito")',
    'button:has-text("Chiudi")',
    'button:has-text("Skip")',
]

# Nota: NON si rimuovono i nodi del tour dal DOM. React li possiede ancora e,
# quando prova a smontarli, va in errore e TikTok mostra "Si è verificato un
# problema" al posto della pagina. Si nascondono con un foglio di stile: React
# non se ne accorge e l'overlay smette di intercettare i clic.
REMOVE_OVERLAYS_JS = """() => {
  const id = '__agent_overlay_killer__';
  if (document.getElementById(id)) return 0;
  const st = document.createElement('style');
  st.id = id;
  st.textContent = '#react-joyride-portal,.react-joyride__overlay,'
    + '.react-joyride__spotlight,[data-test-id="overlay"]'
    + '{display:none !important;pointer-events:none !important;visibility:hidden !important;}';
  document.head.appendChild(st);
  return 1;
}"""


# Il banner dei cookie è un custom element con il suo shadow root: i pulsanti non
# compaiono in `page.content()`, quindi un dump della pagina non li mostra e il
# problema sembra un altro. Si scende nello shadow root e si rifiutano i cookie
# opzionali, che è la scelta che chiude il banner senza concedere niente.
COOKIE_BANNER_JS = """() => {
  const host = document.querySelector('tiktok-cookie-banner');
  if (!host) return 0;
  const root = host.shadowRoot;
  if (root) {
    for (const b of root.querySelectorAll('button')) {
      const t = (b.textContent || '').toLowerCase();
      if (t.includes('rifiuta') || t.includes('decline')) { b.click(); return 1; }
    }
  }
  // Se il pulsante non si trova, il banner va almeno tolto di mezzo: è fisso in
  // fondo alla pagina e copre il pulsante di pubblicazione.
  host.style.setProperty('display', 'none', 'important');
  return 2;
}"""


def dismiss_cookie_banner(page) -> None:
    """
    Il banner dei cookie è fisso in fondo alla finestra e copre il pulsante
    Pubblica. Il clic normale fallisce per intercettazione, il ripiego con
    `force=True` centra le coordinate del pulsante — cioè il banner — e la
    pubblicazione non parte senza che nessun errore lo dica.
    """
    try:
        esito = page.evaluate(COOKIE_BANNER_JS)
    except Exception:
        return
    if esito == 1:
        log("  banner dei cookie: rifiutati quelli opzionali")
        page.wait_for_timeout(800)
    elif esito == 2:
        log("  banner dei cookie: pulsante non trovato, banner nascosto")


def dismiss_overlays(page) -> None:
    """
    TikTok Studio propone un tour guidato: il suo overlay copre tutta la pagina
    e mangia i clic. Prima si prova a chiuderlo come farebbe una persona, poi si
    tolgono di mezzo i nodi rimasti. Stessa sorte per il banner dei cookie.
    """
    dismiss_cookie_banner(page)
    for sel in JOYRIDE_SKIP:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=2500, force=True)
                page.wait_for_timeout(600)
        except Exception:
            pass
    try:
        if page.evaluate(REMOVE_OVERLAYS_JS):
            log("  tour guidato neutralizzato")
    except Exception:
        pass


def page_broken(page) -> bool:
    """TikTok sostituisce la pagina con una schermata di errore quando il suo JS crolla."""
    try:
        txt = page.inner_text("body")[:600]
    except Exception:
        return False
    return (
        "Si è verificato un problema" in txt
        or "Something went wrong" in txt
        or "Qualcosa è andato storto" in txt
    )


def safe_click(page, loc, what: str = "elemento") -> None:
    """Clic che non si lascia bloccare da un overlay di passaggio."""
    for attempt in range(3):
        try:
            loc.click(timeout=10000)
            return
        except Exception:
            dismiss_overlays(page)
            try:
                loc.click(timeout=8000, force=True)
                return
            except Exception:
                if attempt == 2:
                    try:
                        loc.evaluate("el => { el.focus && el.focus(); el.click && el.click(); }")
                        return
                    except Exception as exc:
                        raise RuntimeError(f"non riesco a cliccare {what}: {exc}")
            page.wait_for_timeout(1000)


def dump(page, tag: str) -> None:
    """Salva screenshot + HTML: serve per capire cosa è cambiato quando un selettore salta."""
    LOG_DIR.mkdir(exist_ok=True)
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}-{tag}"
    try:
        page.screenshot(path=str(LOG_DIR / f"{stamp}.png"), full_page=True)
        (LOG_DIR / f"{stamp}.html").write_text(page.content(), encoding="utf-8")
        log(f"  diagnostica salvata in logs/{stamp}.png|.html")
    except Exception as exc:
        log(f"  impossibile salvare la diagnostica: {exc}")


# --------------------------------------------------------------------------- browser

PROFILE_BUSY = (
    "Il browser dell'agente non è partito.\n"
    "  Causa quasi sempre questa: un'altra finestra dell'agente è ancora aperta\n"
    "  (tipicamente quella lasciata aperta da `login`) e tiene occupato il profilo\n"
    "  in .browser-profile. Chiudi quella finestra — e chiudi anche il comando\n"
    "  `login` con INVIO o Ctrl+C — poi rilancia questo comando."
)


def usa_profilo(percorso) -> None:
    """
    Cambia la cartella del profilo per tutto il processo.

    E' una variabile di modulo e non un parametro perche' `browser()` e
    `_launch()` sono chiamate da mezzo file e da `orchestrator.py`: passarla di
    mano in mano vorrebbe dire toccare ogni firma, e una firma dimenticata
    tornerebbe al difetto — cioe' all'account sbagliato. Si imposta una volta
    all'avvio, prima che parta qualunque browser.
    """
    global PROFILE_DIR
    PROFILE_DIR = Path(percorso)


def browser(headless: bool = False):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    pw = sync_playwright().start()
    try:
        ctx = _launch(pw, headless)
    except Exception as exc:
        pw.stop()
        raise RuntimeError(f"{PROFILE_BUSY}\n  (dettaglio: {exc})") from exc
    try:  # se il profilo è occupato il contesto muore subito
        ctx.set_default_timeout(30000)
        (ctx.pages[0] if ctx.pages else ctx.new_page()).goto("about:blank")
    except Exception as exc:
        try:
            ctx.close()
        finally:
            pw.stop()
        raise RuntimeError(f"{PROFILE_BUSY}\n  (dettaglio: {exc})") from exc
    return pw, ctx


def _launch(pw, headless: bool):
    return pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=headless,
        viewport={"width": 1440, "height": 900},
        user_agent=UA,
        **registro.ambiente(),
        args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
    )


# --------------------------------------------------------------------------- TikTok


def detect_handle(page) -> str:
    """
    Ricava lo @handle dell'account loggato.
    TikTok Studio impiega diversi secondi a popolare la lista dei post, quindi
    non basta un'attesa fissa: si controlla a ripetizione finché non compare un
    link del tipo /@nome/... .
    """
    urls = (
        "https://www.tiktok.com/tiktokstudio/content",
        "https://www.tiktok.com/tiktokstudio/upload",
        "https://www.tiktok.com/foryou",
    )
    for url in urls:
        try:
            page.goto(url, wait_until="domcontentloaded")
        except Exception:
            continue
        for _ in range(15):  # fino a ~45 secondi per pagina
            page.wait_for_timeout(3000)
            try:
                hrefs = page.eval_on_selector_all(
                    "a[href*='/@']", "els => els.map(e => e.getAttribute('href'))"
                )
            except Exception:
                hrefs = []
            for h in hrefs or []:
                m = re.search(r"/@([A-Za-z0-9._]{2,})", h or "")
                if m:
                    return m.group(1)
    return ""


def _ids_in_page(page) -> set[str]:
    try:
        hrefs = page.eval_on_selector_all('a[href*="/video/"]', "els => els.map(e => e.href)")
    except Exception:
        hrefs = []
    return {m.group(1) for m in (re.search(r"/video/(\d+)", h or "") for h in hrefs or []) if m}


def video_ids(page, handle: str) -> set[str]:
    page.goto(f"https://www.tiktok.com/@{handle}", wait_until="domcontentloaded")
    page.wait_for_timeout(5000)
    return _ids_in_page(page)


def all_video_ids(page, handle: str) -> set[str]:
    """
    Gli id dei post visti da entrambe le parti: il profilo pubblico e TikTok Studio.

    Il profilo pubblico mostra il post con parecchi minuti di ritardo — misurato:
    dopo sette minuti e dodici tentativi non c'era ancora, mentre in Studio
    compariva già. È un ritardo che costa: la campagna accetta l'invio solo entro
    trenta minuti dalla pubblicazione, quindi un URL ripescato tardi è un invio
    perso. Le due liste non coincidono in nessuna delle due direzioni, per questo
    il confronto "prima e dopo" va fatto sull'unione.
    """
    ids = set()
    try:
        page.goto("https://www.tiktok.com/tiktokstudio/content", wait_until="domcontentloaded")
        page.wait_for_timeout(9000)
        ids |= _ids_in_page(page)
    except Exception:
        pass
    try:
        ids |= video_ids(page, handle)
    except Exception:
        pass
    return ids


# La voce del suggeritore si sceglie col mouse, e il clic porta via il fuoco
# dall'editor: da lì in poi tutto quello che si digita finisce nel nulla. Non
# solleva niente — la didascalia esce semplicemente troncata alla menzione, e
# siccome gli hashtag stanno in coda il confronto indulgente li perdonava.
FOCUS_EDITOR_END_JS = """() => {
  const e = document.querySelector('.public-DraftEditor-content');
  if (!e) return 0;
  e.focus();
  const r = document.createRange();
  r.selectNodeContents(e);
  r.collapse(false);
  const s = window.getSelection();
  s.removeAllRanges();
  s.addRange(r);
  return 1;
}"""


def focus_editor_end(page) -> None:
    """Rimette il cursore in fondo alla didascalia dopo un clic fuori dall'editor."""
    try:
        page.evaluate(FOCUS_EDITOR_END_JS)
        page.wait_for_timeout(250)
    except Exception:
        pass


class EditorInstabile(RuntimeError):
    """L'editor di TikTok sta perdendo caratteri: riscrivere adesso non serve."""


def type_plain(page, text: str, chunk: int = 40) -> None:
    """
    Digita testo semplice a pezzi, verificando che ogni pezzo sia entrato davvero.

    L'editor perde caratteri quando la pagina ha da fare — tipicamente mentre il
    video è ancora in elaborazione: sono spariti ventidue caratteri in mezzo a una
    frase e lo spazio fra due hashtag, in tutti e tre i tentativi. Non è un
    carattere sbagliato, è un carattere assente, quindi non si corregge
    riscrivendo tutto più volte: si scrive un pezzo per volta e si guarda se c'è.

    Il pezzo si verifica guardando **la coda** dell'editor, e quel che manca si
    aggiunge: non si cancella mai niente. Contare i caratteri entrati per poi
    toglierli sembra più preciso e non lo è — un a capo vale un carattere che
    `innerText` mostra o no a seconda di dove sta, e con quel conto sbagliato la
    riparazione mangiava il punto della frase precedente insieme al paragrafo.

    **Arrendersi in silenzio era il difetto peggiore dei due.** Con `range(3)` e
    nessun segnale d'uscita, un pezzo che non entrava lasciava il testo monco e
    la funzione tornava come se avesse scritto: la clip 02 e' finita con
    «Clo@acmeshowtv» al posto di «Clouted @acmeshowtv», e a fermarla e'
    stato il confronto finale, tre tentativi piu' tardi. Ora chi non converge lo
    dice, e chi chiama puo' aspettare che la pagina si calmi invece di ribattere
    lo stesso testo sulla stessa pagina occupata.

    Il ribattuto **rallenta a ogni giro** (60, 120, 240 ms per tasto): la perdita
    viene dalla pagina che non sta dietro, quindi insistere alla stessa velocita'
    e' il modo di riprodurla identica.
    """
    coda = lambda: (editor_text(page) or "").replace("​", "").rstrip("\n")
    for i in range(0, len(text), chunk):
        pezzo = text[i : i + chunk]
        page.keyboard.type(pezzo, delay=30)
        page.wait_for_timeout(200)
        lento, entrato = 60, 0
        for tentativo in range(6):
            attuale = coda()
            if attuale.endswith(pezzo):
                break
            # Quanto del pezzo è arrivato davvero: si riparte da lì.
            entrato = next(
                (j for j in range(len(pezzo) - 1, 0, -1) if attuale.endswith(pezzo[:j])), 0
            )
            log(f"  editor: persi {len(pezzo) - entrato} caratteri, li riscrivo")
            page.keyboard.type(pezzo[entrato:], delay=lento)
            lento = min(lento * 2, 240)
            page.wait_for_timeout(400 + 300 * tentativo)
        else:
            raise EditorInstabile(
                f"l'editor ha perso {len(pezzo) - entrato} caratteri su {len(pezzo)} "
                f"dopo sei tentativi"
            )


def type_rich(page, text: str, mention_strict: bool = True) -> None:
    """
    Scrive nell'editor DraftJS di TikTok gestendo menzioni e hashtag:
    dopo @nome o #tag TikTok apre un suggeritore che ruberebbe Invio e spazio.
    """
    parts = re.split(r"(@[\w.]+|#[\wÀ-ÿ]+|\n)", text)
    for part in parts:
        if not part:
            continue
        if part == "\n":
            page.keyboard.press("Escape")
            page.wait_for_timeout(150)
            page.keyboard.press("Enter")
            continue
        if part[0] == "@":
            handle = part[1:]
            # Si digita prima la radice (@brandly): l'editor di TikTok chiude il
            # token sul punto, quindi scrivendo tutto d'un fiato la menzione
            # resterebbe spezzata in "@brandly" + ".ai". La voce completa va
            # scelta dal suggeritore.
            insert_mention(page, handle, strict=mention_strict)
            focus_editor_end(page)
            type_plain(page, " ")
            continue
        if part[0] == "#":
            page.keyboard.type(part, delay=50)
            page.wait_for_timeout(900)
            page.keyboard.press("Escape")
            type_plain(page, " ")
            continue
        type_plain(page, part)


# Cerca nel documento le voci del suggeritore: elementi visibili, piccoli, che
# contengono l'handle, fuori dall'editor, e che non hanno figli che a loro volta
# lo contengono (cioè la foglia dell'albero: la riga cliccabile).
SCAN_SUGGESTIONS_JS = """(h) => {
  const out = [];
  for (const el of document.querySelectorAll('div,li,span,a,p')) {
    const t = (el.innerText || '').trim();
    if (!t || t.length > 140) continue;
    if (!t.toLowerCase().includes(h)) continue;
    if (el.closest('.public-DraftEditor-content')) continue;
    if (el.closest('[contenteditable="true"]')) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) continue;
    if (r.bottom < 0 || r.top > innerHeight) continue;
    let inner = false;
    for (const c of el.children)
      if ((c.innerText || '').toLowerCase().includes(h)) { inner = true; break; }
    if (inner) continue;
    out.push({x: r.x + r.width / 2, y: r.y + r.height / 2,
              area: r.width * r.height, text: t});
  }
  return out.sort((a, b) => a.area - b.area);
}"""


def pick_suggestion(page, handle: str, wait_ms: int = 9000) -> bool:
    """
    Sceglie la voce del suggeritore delle menzioni.
    Niente selettori di classe (TikTok li rigenera a ogni rilascio): si scandaglia
    la pagina cercando la riga più piccola che contiene l'handle e ci si clicca
    sopra alle coordinate, come farebbe una persona.
    """
    # L'handle cercato è contenuto anche in quello di un omonimo più lungo:
    # cercando "acmeshowtv" il suggeritore offre pure "acmeshowtv2", che
    # è un altro account — e taggare l'account sbagliato invalida la clip per la
    # campagna. Va prima chi porta l'handle esatto, cioè non seguito da altre
    # lettere o cifre; l'area resta il criterio di spareggio.
    esatto = re.compile(re.escape(handle.lower()) + r"(?![\w.])")

    deadline = time.time() + wait_ms / 1000
    while time.time() < deadline:
        try:
            cands = page.evaluate(SCAN_SUGGESTIONS_JS, handle.lower())
        except Exception:
            cands = []
        cands.sort(key=lambda c: (0 if esatto.search(c["text"].lower()) else 1, c["area"]))
        if cands:
            c = cands[0]
            log(f"  suggeritore: clicco «{c['text'][:40]}»")
            page.mouse.click(c["x"], c["y"])
            page.wait_for_timeout(900)
            if mention_ok(page, handle):
                return True
        page.wait_for_timeout(600)
    return False


MENTION_BUTTON = [
    "#web-creation-caption-mention-button",
    'button[aria-label="@menzione"]',
    'button[aria-label="Mention"]',
]


def insert_mention(page, handle: str, strict: bool = True) -> bool:
    """
    Inserisce @handle come tag vero.

    Scrivere l'handle a mano non basta: l'editor di TikTok chiude il token sul
    punto e ne esce "@brandly" + ".ai", cioè il tag di un altro account. La via
    buona è il pulsante «@menzione» della barra, che apre la ricerca ufficiale;
    la digitazione diretta resta come ripiego.
    """
    root = handle.split(".")[0]

    def erase(n: int) -> None:
        for _ in range(n):
            page.keyboard.press("Backspace")
        page.wait_for_timeout(400)

    # 1) digitando la radice e scegliendo MENTRE la ricerca è ancora attiva.
    #    Scrivere anche il punto chiude il token: da lì in poi la scelta non
    #    sostituisce più il testo, lo affianca (e la menzione finisce doppia).
    page.keyboard.type("@" + root, delay=90)
    page.wait_for_timeout(2000)
    if pick_suggestion(page, handle, wait_ms=12000):
        log(f"  menzione @{handle} agganciata dal suggeritore")
        return True
    page.keyboard.press("Escape")
    erase(len(root) + 1)

    # 2) dal pulsante «@menzione» della barra, che apre la ricerca ufficiale
    btn = first_visible(page, MENTION_BUTTON, timeout=4000)
    if btn is None:
        log("  pulsante @menzione non trovato")
    else:
        try:
            safe_click(page, btn, "il pulsante @menzione")
            page.wait_for_timeout(1500)
            page.keyboard.type(root, delay=90)  # il pulsante inserisce già "@"
            page.wait_for_timeout(2000)
            if pick_suggestion(page, handle, wait_ms=12000):
                log(f"  menzione @{handle} agganciata dalla ricerca")
                return True
            page.keyboard.press("Escape")
            erase(len(root) + 1)
        except Exception as exc:
            log(f"  pulsante @menzione non utilizzabile: {exc}")

    if not strict:
        # nei commenti va bene anche scritta a mano: TikTok la trasforma in link
        # quando il commento viene pubblicato.
        page.keyboard.type("@" + handle, delay=60)
        return False

    log(f"  ATTENZIONE: non ho potuto agganciare la menzione @{handle}")
    dump(page, "suggeritore-menzione")
    return False


def editor_text(page) -> str:
    """Il testo effettivamente presente nell'editor della didascalia."""
    try:
        return (
            page.evaluate(
                """() => { const e = document.querySelector('.public-DraftEditor-content');
                       return e ? e.innerText : ''; }"""
            )
            or ""
        )
    except Exception:
        return ""


def squash(s: str) -> str:
    """Confronto indulgente su spazi e a capo, severo su tutto il resto."""
    return re.sub(r"\s+", " ", s or "").strip()


def mention_ok(page, handle: str) -> bool:
    """
    Verifica che la menzione sia una vera entità e non testo battuto a mano:
    nell'editor deve esistere un frammento il cui testo è esattamente @handle.
    """
    try:
        return bool(
            page.evaluate(
                """(h) => [...document.querySelectorAll('[data-text="true"]')]
                   .some(e => e.textContent.trim() === '@' + h)""",
                handle,
            )
        )
    except Exception:
        return False


def wait_upload_done(page, timeout_ms: int = 300000) -> bool:
    """
    Aspetta che il video risulti caricato prima di scrivere la didascalia.

    Finché il caricamento è in corso la pagina ha da fare e l'editor perde
    caratteri: si scrive dopo, non durante.

    **La parola da sola non basta.** Cercarla nel corpo intero della pagina la
    trovava in un'etichetta ferma dell'interfaccia, non nello stato del video:
    sulla clip 02 ha dichiarato «caricato» **tre secondi** dopo aver passato un
    file da 13 MB, e la scrittura e' partita su una pagina ancora occupata — da
    li' i 33 caratteri persi per pezzo. Ora il segnale deve reggere due letture
    a distanza di tre secondi, e dopo si aspetta comunque: una parola che
    compare e sparisce e' l'interfaccia che si sta ancora ridisegnando.
    """
    deadline = time.time() + timeout_ms / 1000
    visto = 0
    while time.time() < deadline:
        try:
            body = squash(page.inner_text("body")).lower()
        except Exception:
            body = ""
        if "caricato" in body or "uploaded" in body:
            visto += 1
            if visto >= 2:
                log("  TikTok: video caricato, scrivo la didascalia")
                page.wait_for_timeout(5000)
                return True
        else:
            visto = 0
        page.wait_for_timeout(3000)
    return False


CONTENT_CHECK_SWITCH = (
    'xpath=//span[contains(normalize-space(),"Content check lite")]'
    '/ancestor::div[contains(@class,"headline-wrapper")][1]'
    '//div[contains(@class,"Switch__content")]'
)


def spegni_content_check(page) -> None:
    """Spegne «Content check lite», che tiene il video in verifica e non lo fa uscire.

    E' acceso di suo sugli account nuovi, e il suo stesso pannello lo dichiara:
    «Checking in progress. This will take about 10 minutes». Finche' gira, il
    pulsante Post non porta a niente. Non e' una preferenza editoriale: e' la
    differenza fra una clip online e una clip ferma in bozza.
    """
    try:
        sw = page.locator(CONTENT_CHECK_SWITCH).first
        if not sw.count():
            return
        if (sw.get_attribute("aria-checked") or "").lower() != "true":
            log("  Content check lite: gia' spento")
            return
        safe_click(page, sw, "l'interruttore Content check lite")
        page.wait_for_timeout(1500)
        acceso = (sw.get_attribute("aria-checked") or "").lower() == "true"
        log(
            "  Content check lite: "
            + ("NON si spegne, il post puo' restare in verifica" if acceso else "spento")
        )
    except Exception as exc:
        log(f"  Content check lite: non riesco a toccarlo ({type(exc).__name__})")


def upload_tiktok(page, video: Path, caption: str, dry_run: bool = False) -> bool:
    log("  TikTok: apro l'uploader")
    page.goto(TIKTOK_UPLOAD_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(6000)
    dismiss_overlays(page)

    file_input = page.locator('input[type="file"]').first
    try:
        file_input.wait_for(state="attached", timeout=45000)
    except PWTimeout:
        dump(page, "no-file-input")
        raise RuntimeError("campo di caricamento non trovato: sei ancora loggato su TikTok?")
    file_input.set_input_files(str(video))
    log(f"  TikTok: file passato ({video.stat().st_size / 1e6:.1f} MB), attendo l'elaborazione")

    editor = first_visible(
        page,
        [
            'div[contenteditable="true"][role="combobox"]',
            ".public-DraftEditor-content",
            'div[contenteditable="true"]',
        ],
        timeout=180000,
    )
    if editor is None:
        dump(page, "no-caption-editor")
        raise RuntimeError("editor della didascalia non trovato")

    if not wait_upload_done(page):
        log(
            "  il caricamento non risulta finito: scrivo lo stesso, ma la didascalia può perdere caratteri"
        )
    dismiss_overlays(page)
    m = re.search(r"@([\w.]+)", caption)

    for attempt in range(1, 4):
        safe_click(page, editor, "l'editor della didascalia")
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        page.wait_for_timeout(400)
        try:
            type_rich(page, caption)
        except EditorInstabile as exc:
            # Ribattere subito sulla stessa pagina occupata riproduce la stessa
            # perdita: si aspetta, e l'attesa cresce col tentativo.
            log(f"  {exc} — aspetto che la pagina si calmi")
            if attempt == 3:
                dump(page, "editor-instabile")
                raise
            page.wait_for_timeout(15000 * attempt)
            continue
        page.keyboard.press("Escape")

        written = editor_text(page)
        tag_ok = (m is None) or mention_ok(page, m.group(1))
        # TikTok spesso trasforma o inghiotte gli hashtag: confronto
        # il corpo del testo ignorando i token #xxx in coda.
        strip_tags = lambda s: re.sub(r"\s*(?:#[\w\u00C0-\u024F]+\s*)+$", "", s)
        text_ok = squash(strip_tags(written)) == squash(strip_tags(caption))
        if tag_ok and text_ok:
            log(
                "  TikTok: didascalia scritta"
                + (f", menzione @{m.group(1)} agganciata" if m else "")
            )
            # Il confronto sopra perdona gli hashtag mancanti di proposito, ma
            # perdonarli in silenzio significa pubblicare senza accorgersene:
            # sono stati ingoiati tutti per tre pubblicazioni di fila.
            tags = lambda s: set(re.findall(r"#[\wÀ-ɏ]+", s or ""))
            persi = tags(caption) - tags(written)
            if persi:
                log(
                    f"  ATTENZIONE: hashtag non entrati nella didascalia: {' '.join(sorted(persi))}"
                )
            break

        motivo = []
        if not tag_ok:
            motivo.append("menzione non agganciata")
        if not text_ok:
            motivo.append("testo diverso da quello previsto")
        log(f"  didascalia da rifare ({', '.join(motivo)}) — tentativo {attempt}/3")
        if attempt == 3:
            dump(page, "didascalia-sbagliata")
            log(f"    atteso : {squash(caption)}")
            log(f"    ottenuto: {squash(written)}")
            raise RuntimeError("didascalia non conforme dopo tre tentativi: non pubblico")
        # Stessa ragione dell'editor instabile: fra un tentativo e l'altro si
        # lascia respirare la pagina, invece di riscrivere immediatamente.
        page.wait_for_timeout(8000 * attempt)

    spegni_content_check(page)

    post = None
    deadline = time.time() + 900  # fino a 15 minuti di elaborazione
    while time.time() < deadline:
        if page_broken(page):
            dump(page, "pagina-in-errore")
            raise RuntimeError(
                "TikTok ha mostrato la schermata di errore: il caricamento va rifatto"
            )
        # Prima l'identificativo stabile, poi il testo — e il testo **esatto**.
        # Costato tre pubblicazioni non riuscite: `has-text` e' sottostringa, e
        # nella barra laterale del TikTok Studio nuovo c'e' una voce «Posts».
        # `button:has-text("Post")` la prendeva per prima, quindi ogni clic su
        # «Pubblica» era in realta' un clic su *naviga via dall'uploader*, e la
        # finestra «Are you sure you want to exit?» tornava identica a ogni
        # tentativo. Il pulsante vero e' `data-e2e="post_video_button"`.
        cand = first_visible(
            page,
            ['[data-e2e="post_video_button"]', ".btn-post button"]
            + [f'button:text-is("{lbl}")' for lbl in POST_LABELS],
            timeout=3000,
        )
        if cand is not None:
            try:
                disabled = cand.is_disabled() or (cand.get_attribute("aria-disabled") == "true")
            except Exception:
                disabled = False
            if not disabled:
                post = cand
                break
        page.wait_for_timeout(5000)
    if post is None:
        dump(page, "post-button-never-ready")
        raise RuntimeError("il pulsante di pubblicazione non è mai diventato attivo")

    if dry_run:
        dump(page, "dryrun-pronto-a-pubblicare")
        log("  DRY RUN: tutto pronto, NON pubblico.")
        return False

    dismiss_overlays(page)
    dump(page, "prima-di-pubblicare")
    try:
        post.scroll_into_view_if_needed(timeout=3000)
        page.wait_for_timeout(500)
    except Exception:
        pass
    safe_click(page, post, "il pulsante Pubblica")
    log("  TikTok: pulsante Pubblica premuto")
    page.wait_for_timeout(5000)
    if not confirm_publish(page, post):
        dump(page, "pubblicazione-non-confermata")
        raise RuntimeError("TikTok non ha confermato la pubblicazione: il video non è online")
    log("  TikTok: pubblicazione confermata")
    return True


PUBLISH_DONE = [
    "carica un altro",
    "gestisci i post",
    "visualizza profilo",
    "pubblicato",
    "in fase di elaborazione",
    "in elaborazione",
]

DIALOG_CONFIRM = [
    'div[role="dialog"] button:has-text("Pubblica")',
    'div[role="dialog"] button:has-text("Post")',
    'div[role="dialog"] button:has-text("Continua")',
    'div[role="dialog"] button:has-text("Conferma")',
    'div[role="dialog"] button:has-text("OK")',
]

# Non tutte le finestre che compaiono dopo Pubblica sono conferme. Quella che
# ha bloccato la clip 01 diceva «Are you sure you want to exit? Your progress
# and changes will not be saved», coi pulsanti **Exit** e **Cancel**: nessuna
# voce di DIALOG_CONFIRM combacia, quindi non veniva premuto niente e il ciclo
# ha ripetuto lo stesso avviso dodici volte prima di arrendersi — 79 secondi in
# cui il caricamento era vivo e nessuno lo portava a casa.
#
# La risposta giusta e' **Cancel**: si resta sulla pagina e si ripreme Pubblica.
# «Exit» fa esattamente quel che dice — butta via il caricamento — e per questo
# non sta in nessuna delle due liste: una finestra che offre solo di uscire e'
# un blocco da dichiarare, non da cliccare a caso.
DIALOG_DISMISS = [
    'div[role="dialog"] button:has-text("Annulla")',
    'div[role="dialog"] button:has-text("Cancel")',
    'div[role="dialog"] button:has-text("Continua a modificare")',
    'div[role="dialog"] button:has-text("Keep editing")',
]


def confirm_publish(page, post=None) -> bool:
    """
    Dopo il clic su Pubblica, TikTok può chiedere una conferma in una finestra,
    oppure lamentare qualcosa restando sulla pagina. Qui si distingue il caso
    "andata" da "ferma lì", invece di dare per scontato il successo.

    `post` e' il pulsante Pubblica: serve a ripremerlo dopo aver chiuso una
    finestra di uscita, che altrimenti lascerebbe la pagina intatta e il video
    mai pubblicato. Si ripete al massimo due volte — se non basta, il caso e'
    un altro e va guardato, non insistito.
    """
    ripetuti = 0
    for _ in range(12):  # fino a ~60 secondi
        try:
            body = squash(page.inner_text("body")).lower()
        except Exception:
            body = ""
        if "/upload" not in page.url or any(k in body for k in PUBLISH_DONE):
            return True

        dlg = first_visible(page, ['div[role="dialog"]', '[class*="TUXModal"]'], timeout=1200)
        if dlg is not None:
            try:
                log(f"  finestra dopo Pubblica: «{squash(dlg.inner_text())[:200]}»")
            except Exception:
                pass
            btn = first_visible(page, DIALOG_CONFIRM, timeout=2000)
            if btn is not None:
                safe_click(page, btn, "la conferma nella finestra")
                page.wait_for_timeout(3000)
                continue

            # Non e' una conferma: e' l'avviso di chi sta per lasciare la pagina
            # con un caricamento in corso. Si chiude restando qui, e si ripreme
            # Pubblica — senza, la finestra torna identica al giro dopo e il
            # ciclo si consuma senza che nessuno tocchi niente.
            stop = first_visible(page, DIALOG_DISMISS, timeout=2000)
            if stop is not None:
                safe_click(page, stop, "il rifiuto di uscire nella finestra")
                page.wait_for_timeout(2000)
                if post is not None and ripetuti < 4:
                    ripetuti += 1
                    try:
                        post.scroll_into_view_if_needed(timeout=3000)
                        page.wait_for_timeout(500)
                    except Exception:
                        pass
                    safe_click(page, post, "il pulsante Pubblica")
                    log(f"  TikTok: Pubblica ripremuto dopo la finestra di uscita ({ripetuti}/4)")
                    page.wait_for_timeout(5000)
                continue

            # Nessuno dei due: la finestra offre solo vie che perdono il lavoro
            # (tipicamente il solo «Exit»). Si dichiara invece di indovinare.
            log("  finestra senza conferma ne' rifiuto utilizzabili: non la tocco")
        page.wait_for_timeout(5000)
    return False


def fetch_new_url(page, handle: str, known: set[str]) -> str | None:
    """Ripesca l'URL del post appena pubblicato (l'id più alto non ancora visto)."""
    for attempt in range(12):  # ~6 minuti di attesa massima
        try:
            ids = all_video_ids(page, handle)
        except Exception as exc:
            log(f"  post non leggibili ({exc}), riprovo")
            ids = set()
        new = ids - known
        if new:
            vid = max(new, key=int)
            return f"https://www.tiktok.com/@{handle}/video/{vid}"
        log(f"  post non ancora online, attendo (tentativo {attempt + 1}/12)")
        page.wait_for_timeout(30000)
    return None


def comment_tiktok(page, url: str, comment: str) -> bool:
    log("  TikTok: scrivo il commento")
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(8000)
    dismiss_overlays(page)

    box_selectors = [
        '[data-e2e="comment-input"] div[contenteditable="true"]',
        'div[data-e2e="comment-input"] div[contenteditable="true"]',
        'div[contenteditable="true"][role="textbox"]',
        '[placeholder*="commento" i]',
        '[placeholder*="comment" i]',
        'div[contenteditable="true"]',
    ]
    box = None
    for attempt in range(4):
        box = first_visible(page, box_selectors, timeout=8000)
        if box is not None:
            break
        # il pannello dei commenti a volte va aperto o portato in vista
        icon = first_visible(
            page,
            [
                '[data-e2e="comment-icon"]',
                '[data-e2e="comment-count"]',
                'button[aria-label*="ommenti" i]',
                'button[aria-label*="omment" i]',
            ],
            timeout=3000,
        )
        if icon is not None:
            try:
                safe_click(page, icon, "l'icona dei commenti")
            except Exception:
                pass
        page.mouse.wheel(0, 700)
        page.wait_for_timeout(3000)
        log(f"  campo commento non ancora pronto (tentativo {attempt + 1}/4)")

    if box is None:
        dump(page, "no-comment-box")
        return False

    safe_click(page, box, "il campo del commento")
    page.wait_for_timeout(600)
    type_rich(page, comment, mention_strict=False)
    page.keyboard.press("Escape")
    page.wait_for_timeout(600)
    btn = first_visible(
        page,
        [
            '[data-e2e="comment-post"]',
            'div[data-e2e="comment-post"]',
            'button:has-text("Pubblica")',
            'button:has-text("Post")',
        ],
        timeout=8000,
    )
    if btn is None:
        page.keyboard.press("Enter")
    else:
        safe_click(page, btn, "il pulsante del commento")
    page.wait_for_timeout(5000)

    # verifica: il commento deve comparire davvero nella pagina
    probe = squash(re.sub(r"@[\w.]+", "", comment))[:35]
    try:
        posted = probe.lower() in squash(page.inner_text("body")).lower()
    except Exception:
        posted = False
    if not posted:
        dump(page, "commento-non-visibile")
        log("  commento inviato ma non trovato nella pagina")
    return posted


# --------------------------------------------------------------------------- AltroFornitore


def altrofornitore_add(page, campaign_url: str, post_url: str, platform: str = "TikTok") -> bool:
    log("  AltroFornitore: aggiungo l'URL alla campagna")
    page.goto(campaign_url, wait_until="domcontentloaded")
    page.wait_for_timeout(6000)
    dismiss_overlays(page)

    opener = first_visible(
        page,
        [
            'button:has-text("Carica contenuto")',
            'button:has-text("Upload content")',
            'button:has-text("Aggiungi contenuto")',
            'button:has-text("Carica")',
        ],
        timeout=20000,
    )
    if opener is None:
        dump(page, "altrofornitore-no-opener")
        return False
    safe_click(page, opener, "Carica contenuto")
    page.wait_for_timeout(1500)

    plat = first_visible(
        page,
        [
            'input[placeholder*="piattaforma" i]',
            'input[placeholder*="platform" i]',
            'input[placeholder*="linea" i]',
        ],
        timeout=10000,
    )
    if plat is None:
        dump(page, "altrofornitore-no-platform")
        return False
    safe_click(page, plat, "il campo piattaforma")
    page.wait_for_timeout(800)
    opt = first_visible(
        page,
        [
            f'li:has-text("{platform}")',
            f'div[role="option"]:has-text("{platform}")',
            f'*:text-is("{platform}")',
        ],
        timeout=6000,
    )
    if opt is None:
        dump(page, "altrofornitore-no-option")
        return False
    safe_click(page, opt, "l'opzione TikTok")
    page.wait_for_timeout(600)

    url_field = first_visible(
        page,
        [
            'input[placeholder*="URL" i]',
            'input[placeholder*="post" i]',
        ],
        timeout=8000,
    )
    if url_field is None:
        dump(page, "altrofornitore-no-url-field")
        return False
    safe_click(page, url_field, "il campo URL")
    url_field.fill(post_url)
    page.wait_for_timeout(400)

    submit = first_visible(
        page,
        [
            'button[type="submit"]',
            'button:has-text("Aggiungi")',
            'button:has-text("Add")',
        ],
        timeout=6000,
    )
    if submit is None:
        dump(page, "altrofornitore-no-submit")
        return False
    safe_click(page, submit, "il pulsante di conferma")
    page.wait_for_timeout(4000)
    return True


# --------------------------------------------------------------------------- comandi


def cmd_login(args) -> None:
    folder = Path(args.folder)
    cfg_path = folder / "publish.json"
    cfg = load_json(cfg_path)
    pw, ctx = browser(headless=False)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://www.tiktok.com/login")
    print("\n1) Accedi a TikTok con l'account che deve pubblicare.")
    # La seconda meta' e' della campagna AltroFornitore, che viveva in una cartella sola.
    # Su una macchina senza quella cartella (il server) il login di TikTok serve
    # comunque: al profilo, per leggere Studio. Prima moriva qui con un TypeError.
    if cfg:
        print("2) Poi apri", cfg["campaign_url"], "nella stessa finestra e accedi a AltroFornitore.")
    input("\nQuando hai finito, torna qui e premi INVIO... ")
    handle = args.handle or detect_handle(page)
    if handle and cfg is None:
        print(f"Account collegato: @{handle.lstrip('@')}")
    elif handle:
        cfg["tiktok_handle"] = handle.lstrip("@")
        save_json(cfg_path, cfg)
        print(f"Account: @{cfg['tiktok_handle']} (salvato in publish.json)")
    else:
        print("Non sono riuscito a rilevare lo @handle.")
        print("Scrivilo tu:  python publish_agent.py set-handle --handle nomeaccount")
    ctx.close()
    pw.stop()


def posts_today(state) -> int:
    oggi = datetime.now().date().isoformat()
    return sum(1 for r in state.values() if str(r.get("published_at", ""))[:10] == oggi)


def daily_cap(cfg) -> tuple[int, str]:
    """
    Quante clip si possono pubblicare oggi su questo account.

    La regola vera — rampa e tetto, misurate su @canale_lab — sta in
    `ritmo.py` e non qui: e' la stessa che usa l'orchestratore, cosi' i due
    punti d'ingresso non possono piu' divergere silenziosamente com'e' gia'
    successo una volta.
    """
    return ritmo.tetto_giornaliero(int(cfg.get("max_per_day", 4)), cfg.get("account_started"))


def pending(cfg, state):
    return [it for it in cfg["items"] if state.get(it["id"], {}).get("status") != "done"]


def cmd_plan(args) -> None:
    folder = Path(args.folder)
    cfg = load_json(folder / "publish.json")
    state = load_json(folder / "publish_state.json", {})
    todo = pending(cfg, state)
    print(f"Clip totali: {len(cfg['items'])} — da fare: {len(todo)}")
    cap, motivo = daily_cap(cfg)
    print(f"Tetto giornaliero: {cap} post ({motivo}) — oggi già {posts_today(state)}")
    slots = cfg["batch_times"]
    giorno, i = 1, 0
    while i < len(todo):
        quota = todo[i : i + cap] if cap else []
        if not quota:
            print(f"  giorno {giorno}: nessuna pubblicazione (rampa in corso)")
            giorno += 1
            if giorno > 30:
                break
            continue
        orari = ", ".join(slots[: len(quota)])
        print(f"  giorno {giorno} ({orari}): {', '.join(it['id'] for it in quota)}")
        i += cap
        giorno += 1


def cmd_status(args) -> None:
    folder = Path(args.folder)
    cfg = load_json(folder / "publish.json")
    state = load_json(folder / "publish_state.json", {})
    for it in cfg["items"]:
        s = state.get(it["id"], {})
        flag = {"done": "OK ", "partial": "!! ", None: "-- "}.get(s.get("status"), "-- ")
        print(f"{flag}{it['id']} {it['title'][:40]:<42} {s.get('url', '')}")


def cmd_set_handle(args) -> None:
    folder = Path(args.folder)
    cfg_path = folder / "publish.json"
    cfg = load_json(cfg_path)
    cfg["tiktok_handle"] = args.handle.lstrip("@")
    save_json(cfg_path, cfg)
    print(f"Account impostato: @{cfg['tiktok_handle']}")


def cmd_reset(args) -> None:
    folder = Path(args.folder)
    path = folder / "publish_state.json"
    state = load_json(path, {})
    state.pop(args.id, None)
    save_json(path, state)
    print(f"Clip {args.id} rimessa in coda.")


def cmd_run(args) -> None:
    folder = Path(args.folder)
    cfg_path, state_path = folder / "publish.json", folder / "publish_state.json"
    cfg = load_json(cfg_path)
    state = load_json(state_path, {})
    handle = cfg.get("tiktok_handle") or ""

    cap, motivo = daily_cap(cfg)
    fatti = posts_today(state)
    residuo = max(0, cap - fatti)
    log(f"Tetto di oggi: {cap} post ({motivo}) — già pubblicati {fatti}, restano {residuo}")
    if residuo == 0 and not args.dry_run:
        log("Tetto giornaliero raggiunto: non pubblico altro oggi.")
        return

    limite = args.limit if args.dry_run else min(args.limit, residuo)
    todo = pending(cfg, state)[:limite]
    if not todo:
        log("Niente da pubblicare: tutte le clip sono già online.")
        return

    pw, ctx = browser(headless=False)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        if not handle:
            handle = detect_handle(page)
            if not handle:
                raise RuntimeError("account TikTok non rilevato: esegui prima `login`")
            cfg["tiktok_handle"] = handle
            save_json(cfg_path, cfg)
        log(f"Account TikTok: @{handle} — lotto di {len(todo)} clip")

        for n, it in enumerate(todo, 1):
            rec = state.setdefault(it["id"], {"status": "partial"})
            video = folder / it["file"]
            log(f"[{n}/{len(todo)}] {it['title']}")
            if not video.exists():
                log(f"  file mancante: {video}")
                rec["error"] = "file mancante"
                save_json(state_path, state)
                continue
            try:
                if not rec.get("url"):
                    known = all_video_ids(page, handle)
                    published = upload_tiktok(page, video, it["caption"], dry_run=args.dry_run)
                    if not published:  # dry run
                        break
                    rec["published_at"] = datetime.now().isoformat(timespec="seconds")
                    save_json(state_path, state)
                    url = fetch_new_url(page, handle, known)
                    if not url:
                        rec["error"] = (
                            "URL non recuperato (il post potrebbe essere ancora in elaborazione)"
                        )
                        save_json(state_path, state)
                        log("  URL non trovato: riprova più tardi con un nuovo run")
                        continue
                    rec["url"] = url
                    rec.pop("error", None)
                    save_json(state_path, state)
                    log(f"  URL: {url}")

                want_comment = cfg.get("comment", True) and bool(it.get("comment"))
                if want_comment and not rec.get("comment_ok"):
                    rec["comment_ok"] = comment_tiktok(page, rec["url"], it["comment"])
                    save_json(state_path, state)

                if not rec.get("altrofornitore_ok"):
                    rec["altrofornitore_ok"] = altrofornitore_add(
                        page, cfg["campaign_url"], rec["url"], cfg.get("platform", "TikTok")
                    )
                    save_json(state_path, state)

                commento_a_posto = rec.get("comment_ok") or not want_comment
                rec["status"] = (
                    "done" if (commento_a_posto and rec.get("altrofornitore_ok")) else "partial"
                )
                save_json(state_path, state)
                log(f"  esito: {rec['status']}")
            except Exception as exc:
                rec["error"] = str(exc)
                save_json(state_path, state)
                log(f"  ERRORE: {exc}")
                if "has been closed" in str(exc) or "Target closed" in str(exc):
                    log("Il browser si è chiuso: interrompo il lotto.\n" + PROFILE_BUSY)
                    break

            if n < len(todo):
                gap = random.randint(
                    cfg.get("min_gap_seconds", 180), cfg.get("max_gap_seconds", 360)
                )
                log(f"  pausa di {gap // 60} min {gap % 60}s prima della prossima")
                page.wait_for_timeout(gap * 1000)
    finally:
        save_json(state_path, state)
        ctx.close()
        pw.stop()
        log("Lotto concluso.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Agente di pubblicazione TikTok + AltroFornitore")
    ap.add_argument(
        "--folder", default=str(DEFAULT_FOLDER), help="cartella con le clip e publish.json"
    )
    ap.add_argument(
        "--gruppo",
        default="",
        help="il gruppo di dashboard/identita.json da cui pubblicare: "
        "decide profilo browser e handle atteso",
    )
    ap.add_argument(
        "--profilo",
        default="",
        help="cartella del profilo browser, se non la si vuole dal registro",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    lg = sub.add_parser("login")
    lg.add_argument(
        "--handle", default="", help="imposta subito lo @handle senza attendere il rilevamento"
    )
    lg.set_defaults(func=cmd_login)

    sub.add_parser("plan").set_defaults(func=cmd_plan)
    sub.add_parser("status").set_defaults(func=cmd_status)

    sh = sub.add_parser("set-handle")
    sh.add_argument("--handle", required=True)
    sh.set_defaults(func=cmd_set_handle)

    r = sub.add_parser("run")
    r.add_argument("--limit", type=int, default=10)
    r.add_argument("--dry-run", action="store_true", help="fa tutto tranne premere Pubblica")
    r.set_defaults(func=cmd_run)

    x = sub.add_parser("reset")
    x.add_argument("--id", required=True)
    x.set_defaults(func=cmd_reset)

    args = ap.parse_args()

    # Il profilo si sceglie **prima** di qualunque browser: dopo sarebbe tardi,
    # e la finestra si sarebbe gia' aperta sull'account di qualcun altro.
    if args.profilo:
        scelto = Path(args.profilo)
        usa_profilo(scelto if scelto.is_absolute() else HERE / scelto)
    elif args.gruppo:
        usa_profilo(registro.profilo_obbligatorio(args.gruppo, "tiktok"))
    if args.gruppo or args.profilo:
        log(
            f"profilo browser: {PROFILE_DIR.name}"
            + (
                f"  (gruppo {args.gruppo}, atteso @{registro.handle(args.gruppo, 'tiktok')})"
                if args.gruppo
                else ""
            )
        )
    args.func(args)


if __name__ == "__main__":
    main()
