#!/usr/bin/env python
"""
instagram_agent.py — pubblica un Reel su Instagram dal browser, come gli altri.

Stessa forma degli altri due agenti: nessuna API, nessuna credenziale nel
codice, un profilo Chromium persistente in cui il login lo fai a mano una
volta sola. Instagram e' la terza piattaforma dei tre account per nicchia: la
stessa clip esce su YouTube, TikTok e qui.

Comandi
-------
    python instagram_agent.py login --profilo .instagram-business
    python instagram_agent.py pubblica --file clip.mp4 --didascalia "..." --prova
    python instagram_agent.py pubblica --file clip.mp4 --didascalia "..." --davvero

`--prova` percorre tutto il caricamento e si ferma **prima** dell'ultimo clic:
serve a vedere se i selettori reggono senza mettere in pubblico niente.
Instagram non ha bozze per i Reel dal web, quindi questa e' l'unica prova
possibile — e va fatta ogni volta che l'interfaccia cambia.

Cosa aspettarsi
---------------
**Questo agente non e' ancora stato eseguito su un account vero**: e' scritto
sui passaggi noti dell'uploader web, con piu' selettori per ogni passo e in
piu' lingue, ma il primo `--prova` va guardato con gli occhi. Se un passo non
trova il suo elemento, l'agente scrive uno screenshot in `logs/` e si ferma
li' invece di tirare a indovinare col clic successivo: su una pagina che
pubblica in pubblico, indovinare e' la cosa che costa.

L'uploader di Instagram e' una sequenza di tre schermate dentro una finestra
sola — ritaglio, modifica, didascalia — e ognuna ha il suo "Avanti" in alto a
destra. Il passo che cambia piu' spesso e' il primo: caricando un video
Instagram interpone un avviso ("i post video sono condivisi come reel") che a
volte c'e' e a volte no. Per questo l'attesa non e' a tempo ma sull'elemento
successivo.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sys.exit(
        "Playwright non e' installato. Esegui prima setup.ps1 "
        "(o: pip install playwright && playwright install chromium)"
    )

HERE = Path(__file__).resolve().parent
LOG_DIR = HERE / "logs"
PROFILO_DEFAULT = HERE / ".instagram-profile"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Le etichette dei pulsanti nelle lingue che Instagram puo' servire: l'account
# nuovo e' spesso in inglese anche su un Windows italiano, e viceversa dopo il
# primo accesso da telefono. Cercarle tutte costa niente, sbagliare lingua
# costa una pubblicazione mancata a meta' strada.
AVANTI = ["Avanti", "Next", "Siguiente", "Weiter", "Suivant"]
CONDIVIDI = ["Condividi", "Share", "Compartir", "Teilen", "Partager"]
OK = ["OK", "Ok", "Va bene"]

PROFILO_OCCUPATO = (
    "Il browser dell'agente non e' partito.\n"
    "  Causa quasi sempre questa: un'altra finestra sullo stesso profilo e'\n"
    "  ancora aperta (tipicamente quella lasciata da `login`). Chiudila, e\n"
    "  chiudi anche il comando `login` con INVIO, poi rilancia."
)


def log(msg: str) -> None:
    riga = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(riga, flush=True)
    LOG_DIR.mkdir(exist_ok=True)
    with (LOG_DIR / "instagram.log").open("a", encoding="utf-8") as fh:
        fh.write(riga + "\n")


def foto(page, tag: str) -> None:
    """
    Screenshot **e HTML** quando un passo non trova quello che cerca.

    Lo screenshot dice che qualcosa copre la pagina, l'HTML dice cosa e con quali
    pulsanti: il 16 settembre 2026 c'era solo il primo, e il popup andava
    riconosciuto a occhio.
    """
    LOG_DIR.mkdir(exist_ok=True)
    percorso = LOG_DIR / f"ig-{tag}-{datetime.now():%Y%m%d-%H%M%S}.png"
    try:
        page.screenshot(path=str(percorso), full_page=True)
        log(f"  schermata salvata: {percorso.name}")
    except Exception:
        pass
    try:
        percorso.with_suffix(".html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass


# I pulsanti con cui un popup si **rifiuta**: notifiche, «salva le informazioni di
# accesso», cookie facoltativi. Sono gli unici che si cliccano sempre, anche a
# compositore aperto, perche' non annullano niente.
RIFIUTA = [
    "Not Now",
    "Not now",
    "Non ora",
    "Adesso no",
    "Maybe later",
    "Più tardi",
    "Dismiss",
    "Ignora",
    "Decline optional cookies",
    "Rifiuta cookie facoltativi",
    "Only allow essential cookies",
    "Consenti solo i cookie essenziali",
]
# La X di un dialogo invece si clicca **solo fuori dal compositore**: il
# compositore stesso e' un `role="dialog"` con la sua X, e cliccarla apre
# «Scartare il post?», cioe' butta via il caricamento.
CHIUDI = ["Close", "Chiudi"]


def chiudi_popup(page, fuori_dal_compositore: bool) -> list[str]:
    """
    Chiude i popup che coprono la pagina e torna i titoli di quelli chiusi.

    Il 16 settembre 2026 un «Turn on Notifications» copriva la home: il «+» era
    visibile ma non riceveva il clic, Playwright aspettava trenta secondi a
    selettore e il Reel non e' uscito. Un popup non e' un guasto da segnalare,
    e' una cosa da togliere di mezzo — ma solo con un gesto che non annulla
    niente (vedi `RIFIUTA` e `CHIUDI`).
    """
    chiusi: list[str] = []
    for _ in range(4):  # i popup arrivano anche impilati
        trovato = False
        dialoghi = page.locator('[role="dialog"]')
        try:
            quanti = dialoghi.count()
        except Exception:
            return chiusi
        for i in range(quanti):
            dialogo = dialoghi.nth(i)
            try:
                if not dialogo.is_visible():
                    continue
                righe = dialogo.inner_text(timeout=2000).strip().splitlines()
                titolo = (righe[0] if righe else "?")[:60]
            except Exception:
                continue
            candidati = [dialogo.get_by_role("button", name=t, exact=True) for t in RIFIUTA]
            candidati += [dialogo.locator(f'div[role="button"]:text-is("{t}")') for t in RIFIUTA]
            if fuori_dal_compositore:
                candidati += [dialogo.locator(f'[aria-label="{t}"]') for t in CHIUDI]
            for loc in candidati:
                try:
                    if loc.count() and loc.first.is_visible():
                        loc.first.click(timeout=5000)
                        log(f"  Instagram: chiuso il popup «{titolo}»")
                        chiusi.append(titolo)
                        page.wait_for_timeout(1000)
                        trovato = True
                        break
                except Exception:
                    continue
            if trovato:
                break
        if not trovato:
            return chiusi
    return chiusi


def browser(profilo: Path, headless: bool = False):
    import registro

    profilo.mkdir(parents=True, exist_ok=True)
    pw = sync_playwright().start()
    try:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(profilo),
            headless=headless,
            viewport={"width": 1440, "height": 900},
            user_agent=UA,
            **registro.ambiente(),
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
        )
        ctx.set_default_timeout(30000)
        (ctx.pages[0] if ctx.pages else ctx.new_page()).goto("about:blank")
    except Exception as exc:
        try:
            pw.stop()
        finally:
            pass
        raise RuntimeError(f"{PROFILO_OCCUPATO}\n  (dettaglio: {exc})") from exc
    return pw, ctx


def per_etichetta(page, etichette: list[str], timeout: int = 15000):
    """Il primo pulsante visibile fra quelli con una di queste etichette."""
    scadenza = time.monotonic() + timeout / 1000
    while time.monotonic() < scadenza:
        for testo in etichette:
            for loc in (
                page.get_by_role("button", name=testo, exact=True),
                page.locator(f'div[role="button"]:text-is("{testo}")'),
                page.locator(f'button:has-text("{testo}")'),
            ):
                try:
                    if loc.count() and loc.first.is_visible():
                        return loc.first
                except Exception:
                    continue
        page.wait_for_timeout(400)
    return None


def clicca(
    page, etichette: list[str], cosa: str, timeout: int = 15000, obbligatorio: bool = True
) -> bool:
    """
    Clicca il primo pulsante con una di queste etichette.

    Si usa solo a compositore aperto, quindi un popup si chiude con `RIFIUTA` e
    mai con la X. Se il clic non passa si toglie il popup e si riprova una volta:
    un pulsante visibile ma coperto e' il caso del 16 settembre 2026.
    """
    bottone = per_etichetta(page, etichette, timeout)
    if bottone is None and obbligatorio and chiudi_popup(page, fuori_dal_compositore=False):
        bottone = per_etichetta(page, etichette, 5000)
    if bottone is None:
        if obbligatorio:
            foto(page, f"manca-{cosa}")
            raise RuntimeError(f"non trovo il pulsante «{cosa}» ({'/'.join(etichette)})")
        return False
    try:
        bottone.click(timeout=10000)
    except Exception:
        # Senza popup, spesso e' il passaggio fra due schermate del
        # compositore: il 29 settembre 2026 «Next» di Edit ha rifiutato il clic
        # con la pagina pulita e un «Loading...» nel dialogo, e il Reel non e'
        # uscito. Si aspetta, si ritrova il pulsante e si riprova una volta.
        if not chiudi_popup(page, fuori_dal_compositore=False):
            page.wait_for_timeout(3000)
            bottone = per_etichetta(page, etichette, 5000)
        try:
            if bottone is None:
                raise RuntimeError("sparito")
            bottone.click(timeout=10000)
        except Exception:
            foto(page, f"coperto-{cosa}")
            raise RuntimeError(
                f"il pulsante «{cosa}» c'e' ma non riceve il clic, "
                f"e non c'e' un popup che so chiudere"
            )
    log(f"  Instagram: cliccato «{cosa}»")
    page.wait_for_timeout(1200)
    return True


def handle_attuale(page) -> str:
    """Lo @handle dell'account collegato, letto dal collegamento al profilo."""
    page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    for selettore in (
        'a[href^="/"][role="link"] img[alt*="profile picture" i]',
        'a[href^="/"]:has(img[alt*="profilo" i])',
    ):
        try:
            loc = page.locator(selettore).first
            if loc.count():
                href = loc.locator("xpath=ancestor::a[1]").get_attribute("href") or ""
                nome = href.strip("/").split("/")[0]
                if nome and "." not in nome[:1]:
                    return nome
        except Exception:
            continue
    m = re.search(r'"username":"([\w.]+)"', page.content())
    return m.group(1) if m else ""


def reel_del_profilo(page, handle: str) -> set[str]:
    """Gli id dei reel gia' pubblicati: servono a riconoscere quello nuovo."""
    if not handle:
        return set()
    page.goto(f"https://www.instagram.com/{handle}/reels/", wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    return set(re.findall(r"/reel/([\w-]+)/", page.content()))


RITAGLIO = ["Seleziona ritaglio", "Select crop", "Ritaglia", "Crop"]
# «9:16» per primo, esplicito: «Originale» sarebbe equivalente — il file e' gia'
# 1080x1920 — ma il 12 settembre 2026 il Reel e' uscito quadrato lo stesso, e
# quando una voce dichiara la forma che si vuole non si delega a una che la
# deduce.
VERTICALE = ["9:16", "Originale", "Original"]


def scegli_verticale(page) -> None:
    """Sceglie il ritaglio verticale nella prima schermata del compositore.

    **Il compositore parte su 1:1**, e chi preme «Avanti» senza toccarlo pubblica
    un quadrato: la clip e' 1080x1920, quindi il ritaglio ne butta via meta' e il
    Reel esce sbagliato. Non da' nessun errore — l'ha visto l'utente guardando il
    post, il 12 settembre 2026.

    «Originale» prima di «9:16» perche' il file **e' gia'** 9:16: chiedere
    l'originale non ricampiona niente.

    Se il controllo non si trova ci si ferma. Pubblicare comunque vorrebbe dire
    un post quadrato da cancellare a mano su un account vero, cioe' il modo di
    fallire che questo progetto insegue: una consegna che sembra riuscita.
    """
    bottone = per_etichetta(page, RITAGLIO, timeout=15000)
    if bottone is None:
        for selettore in [f'svg[aria-label="{e}"]' for e in RITAGLIO]:
            loc = page.locator(selettore).first
            try:
                if loc.count():
                    bottone = loc.locator("xpath=ancestor::button[1]")
                    break
            except Exception:
                continue
    if bottone is None:
        foto(page, "manca-ritaglio")
        raise RuntimeError(
            "non trovo il selettore del ritaglio: non pubblico, perche' il "
            "compositore parte su 1:1 e il Reel uscirebbe quadrato"
        )

    bottone.click()
    page.wait_for_timeout(1200)
    if clicca(page, VERTICALE, "ritaglio verticale", timeout=8000, obbligatorio=False):
        return

    # Le voci del menu sono `div` con dentro etichetta e icona: non sono
    # pulsanti per `get_by_role`, e `:text-is` sul contenitore non combacia.
    # Si aggancia il **nodo di testo** e si lascia salire il clic.
    for etichetta in VERTICALE:
        loc = page.get_by_text(etichetta, exact=True).first
        try:
            if loc.count() and loc.is_visible():
                loc.click()
                log(f"  Instagram: ritaglio «{etichetta}»")
                page.wait_for_timeout(1200)
                return
        except Exception:
            continue

    foto(page, "ritaglio-senza-verticale")
    raise RuntimeError(
        f"il menu del ritaglio non offre nessuna fra {'/'.join(VERTICALE)}: "
        f"non pubblico, il Reel uscirebbe col ritaglio di partenza"
    )


def pubblica(page, video: Path, didascalia: str, handle: str, prova: bool) -> str | None:
    prima = set() if prova else reel_del_profilo(page, handle)

    log("  Instagram: apro il compositore")
    page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    # Qui il compositore non e' ancora aperto: un popup si puo' chiudere anche
    # con la sua X.
    chiudi_popup(page, fuori_dal_compositore=True)

    aperto = False
    for _ in range(2):
        for selettore in (
            'svg[aria-label="Nuovo post"]',
            'svg[aria-label="New post"]',
            'svg[aria-label="Crea"]',
            'svg[aria-label="Create"]',
            'a[href="#"]:has-text("Crea")',
            'a[href="#"]:has-text("Create")',
        ):
            loc = page.locator(selettore).first
            try:
                if loc.count() and loc.is_visible():
                    # Dieci secondi, non i trenta di default: un «+» visibile
                    # che non riceve il clic e' coperto, e aspettare non lo
                    # scopre. Tre selettori a trenta secondi erano cento secondi
                    # buttati prima di arrendersi.
                    loc.click(timeout=10000)
                    aperto = True
                    break
            except Exception:
                continue
        if aperto or not chiudi_popup(page, fuori_dal_compositore=True):
            break
    if not aperto:
        foto(page, "no-crea")
        raise RuntimeError(
            "non trovo il pulsante per creare un post, o qualcosa lo copre "
            "(vedi la schermata): sei ancora collegato?"
        )
    page.wait_for_timeout(1500)

    # Il menu «Crea» a volte offre la scelta fra post e reel: se c'e', si
    # sceglie; se non c'e', il video diventa comunque un reel da solo.
    clicca(page, ["Reel", "Post"], "tipo di contenuto", timeout=4000, obbligatorio=False)

    campo = page.locator('input[type="file"]').first
    try:
        campo.wait_for(state="attached", timeout=30000)
    except PWTimeout:
        foto(page, "no-input-file")
        raise RuntimeError("campo di caricamento non trovato")
    campo.set_input_files(str(video))
    log(f"  Instagram: file passato ({video.stat().st_size / 1e6:.1f} MB)")
    page.wait_for_timeout(4000)

    # Avviso «i post video sono condivisi come reel»: c'e' o non c'e'.
    clicca(page, OK, "avviso sui reel", timeout=5000, obbligatorio=False)

    scegli_verticale(page)

    clicca(page, AVANTI, "avanti (ritaglio)", timeout=60000)
    clicca(page, AVANTI, "avanti (modifica)", timeout=60000)

    editor = None
    for selettore in (
        'textarea[aria-label*="didascalia" i]',
        'textarea[aria-label*="caption" i]',
        'div[contenteditable="true"][aria-label*="didascalia" i]',
        'div[contenteditable="true"][aria-label*="caption" i]',
        'div[contenteditable="true"]',
    ):
        loc = page.locator(selettore).first
        try:
            if loc.count() and loc.is_visible():
                editor = loc
                break
        except Exception:
            continue
    if editor is None:
        foto(page, "no-didascalia")
        raise RuntimeError("editor della didascalia non trovato")

    editor.click()
    # A caratteri, non con fill(): l'editor di Instagram e' un contenteditable
    # che perde il testo iniettato in blocco, esattamente come quello di TikTok.
    page.keyboard.type(didascalia, delay=12)
    page.wait_for_timeout(800)
    scritto = (
        editor.input_value()
        if editor.evaluate("e => e.tagName") == "TEXTAREA"
        else editor.inner_text()
    )
    if didascalia.strip()[:40] not in (scritto or ""):
        foto(page, "didascalia-parziale")
        log("  ATTENZIONE: la didascalia sullo schermo non coincide con quella prevista")

    if prova:
        log("  --prova: mi fermo qui, prima di «Condividi». Niente e' stato pubblicato.")
        foto(page, "prova-prima-di-condividere")
        return None

    clicca(page, CONDIVIDI, "condividi", timeout=30000)
    log("  Instagram: condivisione avviata, attendo la conferma")

    conferma = re.compile(
        r"(reel .*(condivis|shared)|post .*(condivis|shared)|"
        r"tuo reel|your reel)",
        re.I,
    )
    for _ in range(60):
        page.wait_for_timeout(2000)
        try:
            if conferma.search(page.inner_text("body")):
                break
        except Exception:
            continue
    else:
        foto(page, "nessuna-conferma")
        log("  nessun messaggio di conferma: cerco comunque il reel sul profilo")

    dopo = reel_del_profilo(page, handle)
    nuovi = dopo - prima
    if len(nuovi) == 1:
        url = f"https://www.instagram.com/reel/{nuovi.pop()}/"
        log(f"pubblicato: {url}")
        return url
    # Nessun URL certo: non se ne inventa uno. Un URL sbagliato registrato
    # come buono e' esattamente il difetto del 31 agosto — l'azione dichiarata
    # riuscita che nessuno aveva verificato da fuori.
    foto(page, "url-incerto")
    log(f"  ATTENZIONE: {len(nuovi)} reel nuovi sul profilo: URL non determinabile con certezza")
    return None


def cmd_login(args) -> None:
    profilo = Path(args.profilo) if args.profilo else PROFILO_DEFAULT
    if not profilo.is_absolute():
        profilo = HERE / profilo
    pw, ctx = browser(profilo, headless=False)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://www.instagram.com/accounts/login/")
    print(f"\nProfilo browser: {profilo}")
    print("1) Accedi con l'account Instagram che deve pubblicare.")
    print("2) Passa i controlli (verifica del telefono, «salva le informazioni»).")
    input("\nQuando hai finito, torna qui e premi INVIO... ")
    handle = handle_attuale(page)
    print(
        f"Account collegato: @{handle}"
        if handle
        else "Non sono riuscito a leggere lo @handle: passalo a mano con --handle."
    )
    ctx.close()
    pw.stop()


def cmd_pubblica(args) -> None:
    import registro

    video = Path(args.file).resolve()
    if not video.exists():
        sys.exit(f"file non trovato: {video}")
    if not args.davvero and not args.prova:
        sys.exit("serve --davvero per pubblicare, oppure --prova per fermarsi prima.")

    profilo = Path(args.profilo) if args.profilo else PROFILO_DEFAULT
    if not profilo.is_absolute():
        profilo = HERE / profilo

    pw, ctx = browser(profilo, headless=False)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        # **L'account collegato si legge sempre, e si confronta.** Prima
        # `handle_attuale()` veniva chiamato solo quando `--handle` mancava:
        # con il registro che diceva @X e il profilo browser collegato a @Y, il
        # Reel usciva da @Y e il suo URL veniva cercato fra i reel di @X. Nessun
        # errore da nessuna parte — «URL non determinabile», che sembra un
        # problema di lettura e invece e' un post sull'account sbagliato.
        # TikTok ha questa guardia dal 12 settembre e YouTube ha la sua
        # (`controlla_canale`): Instagram era l'unica delle tre senza, ed e'
        # quella dove un post non si ritira con un clic.
        collegato = handle_attuale(page)
        registro.controlla_handle(
            collegato, args.handle.lstrip("@").lower() if args.handle else None, "instagram"
        )
        handle = (collegato or args.handle).lstrip("@")
        log(
            f"=== Instagram [{video.name}] su @{handle or '?'} "
            f"({'PROVA' if args.prova else 'EFFETTIVA'}) ==="
        )
        url = pubblica(page, video, args.didascalia, handle, prova=args.prova)
        if args.prova:
            return
        if not url:
            sys.exit(3)
    finally:
        ctx.close()
        pw.stop()


def main() -> None:
    for flusso in (sys.stdout, sys.stderr):
        try:
            flusso.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="Pubblica un Reel su Instagram")
    ap.add_argument("--profilo", default="", help="cartella del profilo browser")
    ap.add_argument(
        "--gruppo",
        default="",
        help="il gruppo di dashboard/identita.json: decide profilo e handle. "
        "Con questo, --profilo non serve",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", help="accesso a mano, una volta sola")
    p.add_argument("--handle", default="")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("pubblica", help="carica un video come Reel")
    p.add_argument("--file", required=True)
    p.add_argument("--didascalia", default="")
    p.add_argument("--handle", default="", help="il nostro account, per riconoscere il reel nuovo")
    p.add_argument("--davvero", action="store_true", help="pubblica davvero")
    p.add_argument("--prova", action="store_true", help="tutto tranne l'ultimo clic")
    p.set_defaults(func=cmd_pubblica)

    args = ap.parse_args()
    # `--gruppo` riempie quello che `--profilo` e `--handle` direbbero a mano, e
    # lo fa **senza ripiego**: chiedere un gruppo non registrato si ferma invece
    # di aprire il browser sul profilo di un altro account.
    if args.gruppo:
        import registro

        args.profilo = str(registro.profilo_obbligatorio(args.gruppo, "instagram"))
        if not getattr(args, "handle", ""):
            args.handle = registro.handle(args.gruppo, "instagram") or ""
    args.func(args)


if __name__ == "__main__":
    main()
