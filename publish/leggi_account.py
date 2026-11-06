#!/usr/bin/env python
"""
leggi_account.py — views e segnali di tutti i post di un account, dalla pagina del proprietario.

    <interprete> leggi_account.py --gruppo business --piattaforma tiktok
    <interprete> leggi_account.py --gruppo business --piattaforma instagram --vedi

Stampa un JSON su stdout e non scrive niente: lo storico lo tiene
`dashboard/metriche.py --account`, che lancia questo file con l'interprete giusto
per ogni piattaforma (`INTERPRETE`). Esce con 3 se il profilo e' occupato da una
pubblicazione, con 1 su ogni altro guasto.

Perche' esiste
--------------
Fino al 17 settembre 2026 le views si leggevano solo da YouTube, in HTTP. Su TikTok
e Instagram non le leggeva nessuno, e la prima lettura ha detto la cosa piu'
importante dell'intera consegna Contoso: **i quattro post TikTok erano a 0 views
dopo cinque giorni**, con la pagina di Studio che si dichiara «aggiornata in tempo
reale». Il limite dei due mesi successivi e' portare la mediana sopra le 1.500
views, e su due piattaforme su tre non si vedeva nemmeno lo zero.

Una pagina per account, non una per post
----------------------------------------
Ogni piattaforma ha una pagina del proprietario che porta i numeri di tutti i post
in una risposta sola, e ognuna si e' trovata guardando la pagina salvata:

- **TikTok** — Studio, `tiktokstudio/content`: lo script `__Creator_Center_Context__`
  contiene l'utente collegato e `item_list` con `play_count`, like, commenti,
  condivisioni, `in_review` e `visibility`. Il profilo pubblico invece chiede un
  captcha: non e' una fonte.
- **Instagram** — la griglia dei reel, `/<handle>/reels/`: una risposta graphql con
  `play_count`, like e commenti di ogni reel. La pagina «stato dell'account» sul web
  non esiste.
- **YouTube** — Studio, `list_creator_videos`: le views ci sono ma arrivano gia' da
  `metriche.py --youtube` senza browser; da qui si prendono le **restrizioni**
  (`allRestrictions`) e l'esito del controllo copyright, che l'HTTP non vede.

Tutte e tre leggono la **prima pagina** di post: dodici su Instagram, il primo lotto
su TikTok. Un post piu' vecchio resta con la sua ultima misura, e non diventa zero.

Il profilo e' condiviso con chi pubblica
----------------------------------------
Sono gli stessi profili browser degli agenti, e due Chromium sulla stessa cartella
non convivono. Qui si prende la serratura del profilo (`serratura.nome_profilo`, la
stessa che prende `orchestrator.py` mentre carica) e **si aspetta poco**: se c'e'
una pubblicazione in corso si salta, perche' una lettura rimandata non costa niente
e una pubblicazione rimandata si'.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import registro  # noqa: E402
import serratura  # noqa: E402

# Ogni piattaforma si legge con l'interprete del suo agente, perche' il profilo lo
# ha creato quel Chromium: TikTok e YouTube col venv dello scraping, Instagram col
# venv di `publish/` (Playwright piu' recente). Aprire un profilo con un Chromium
# piu' vecchio di chi l'ha creato e' il modo di rovinare la sessione da cui dipende
# la pubblicazione, per leggere un numero.
from interpreti import PYTHON_PUBLISH, PYTHON_SCRAPING  # noqa: E402

INTERPRETE = {"tiktok": PYTHON_SCRAPING, "youtube": PYTHON_SCRAPING, "instagram": PYTHON_PUBLISH}

# Chi legge aspetta poco, chi pubblica aspetta di piu' (`orchestrator.ATTESA_PROFILO`).
ATTESA_PROFILO, SCADUTA_PROFILO = 20.0, 3600.0
PROFILO_OCCUPATO = 3

STUDIO_TIKTOK = "https://www.tiktok.com/tiktokstudio/content"
LOG_DIR = HERE / "logs"

# Senza restrizioni YouTube scrive questo, non un campo vuoto.
NESSUNA_RESTRIZIONE = "VIDEO_RESTRICTIONS_SEVERITY_UNSPECIFIED"


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", file=sys.stderr, flush=True)


# ------------------------------------------------------------ lettura pura


def id_da_url(piattaforma: str, url: str | None) -> str | None:
    """L'identificatore di un post nel suo URL: e' cosi' che un post letto trova la sua clip."""
    schemi = {
        "tiktok": r"/video/(\d+)",
        "instagram": r"/(?:reel|reels|p)/([\w-]+)",
        "youtube": r"(?:shorts/|watch\?v=|youtu\.be/)([\w-]{6,})",
    }
    m = re.search(schemi.get(piattaforma, r"$^"), url or "")
    return m.group(1) if m else None


def _intero(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _sembra_vero(v) -> bool:
    return v is True or str(v).lower() == "true"


def avvisi_tiktok(item: dict) -> list[str]:
    """
    I segnali che TikTok scrive su un post, in parole.

    `status` resta fuori di proposito: tutti i post letti finora dicono 102, compresi
    quelli a zero views, e non esiste un elenco pubblico dei valori. Si conserva
    grezzo, e diventa un avviso il giorno in cui un valore diverso si vede accanto a
    una causa nota — non prima, perche' un avviso inventato insegna a ignorarli.
    """
    avvisi = []
    if _sembra_vero(item.get("in_review")):
        avvisi.append("in revisione")
    visibilita = item.get("visibility")
    if visibilita is not None and str(visibilita) != "1":
        avvisi.append(f"non pubblico (visibility {visibilita})")
    return avvisi


def post_tiktok(contesto: dict) -> tuple[str | None, list[dict]]:
    """L'handle collegato e i post, dal `__Creator_Center_Context__` di Studio."""
    utente = (contesto.get("uploadUserProfile") or {}).get("user") or {}
    handle = utente.get("unique_id")
    trovati: dict[str, dict] = {}

    def cammina(o):
        if isinstance(o, dict):
            if "item_id" in o and "play_count" in o:
                trovati.setdefault(str(o["item_id"]), o)
                return
            for v in o.values():
                cammina(v)
        elif isinstance(o, list):
            for v in o:
                cammina(v)

    cammina(contesto)
    post = [
        {
            "id": pid,
            "url": f"https://www.tiktok.com/@{handle}/video/{pid}",
            "views": _intero(it.get("play_count")),
            "like": _intero(it.get("like_count")),
            "commenti": _intero(it.get("comment_count")),
            "condivisioni": _intero(it.get("share_count")),
            "avvisi": avvisi_tiktok(it),
            "grezzo": {k: it.get(k) for k in ("status", "in_review", "visibility")},
        }
        for pid, it in trovati.items()
    ]
    return handle, post


def post_instagram(risposte: list) -> list[dict]:
    """I reel dalle risposte graphql della griglia: ogni dizionario con `code` e `play_count`."""
    trovati: dict[str, dict] = {}

    def cammina(o):
        if isinstance(o, dict):
            if "code" in o and "play_count" in o:
                trovati.setdefault(str(o["code"]), o)
                return
            for v in o.values():
                cammina(v)
        elif isinstance(o, list):
            for v in o:
                cammina(v)

    cammina(risposte)
    return [
        {
            "id": code,
            "url": f"https://www.instagram.com/reel/{code}/",
            "views": _intero(m.get("play_count")),
            "like": _intero(m.get("like_count")),
            "commenti": _intero(m.get("comment_count")),
            "condivisioni": None,
            "avvisi": [],
            "grezzo": {k: m.get(k) for k in ("product_type", "media_type")},
        }
        for code, m in trovati.items()
    ]


def post_instagram_api(righe: list[dict]) -> list[dict]:
    """I reel dall'API di Meta (`instagram_api.reel_con_numeri`), nella forma della griglia web.

    Quando il gruppo ha il token si legge da qui e il browser non si apre. Non e'
    una comodita': sul server il login web di Instagram non passa — reCAPTCHA
    Enterprise riconosce il browser comandato da Playwright, anche col Chrome
    vero (5 ottobre 2026) — e ogni tentativo rende l'account piu' sospetto. Un
    numero che Meta non da' resta vuoto: i like e i commenti ripiegano sui campi
    del media, le views no.
    """
    fuori = []
    for r in righe:
        valori, media = r.get("valori") or {}, r.get("media") or {}
        like = valori.get("likes")
        commenti = valori.get("comments")
        fuori.append(
            {
                "id": r["id"],
                "url": r["url"],
                "views": _intero(valori.get("views")),
                "like": _intero(like if like is not None else media.get("like_count")),
                "commenti": _intero(commenti if commenti is not None else media.get("comments_count")),
                "condivisioni": _intero(valori.get("shares")),
                "avvisi": [],
                "grezzo": {"media_type": media.get("media_type"), "fonte": "api"},
            }
        )
    return fuori


def avvisi_youtube(video: dict) -> list[str]:
    """Restrizioni e visibilita' di uno Short, come le scrive Studio."""
    avvisi = []
    gravita = ((video.get("allRestrictions") or {}).get("summary") or {}).get("severity")
    if gravita and gravita != NESSUNA_RESTRIZIONE:
        avvisi.append(
            "restrizioni: " + gravita.removeprefix("VIDEO_RESTRICTIONS_SEVERITY_").lower()
        )
    privacy = video.get("privacy")
    if privacy and privacy != "VIDEO_PRIVACY_PUBLIC":
        avvisi.append("non pubblico: " + privacy.removeprefix("VIDEO_PRIVACY_").lower())
    return avvisi


def post_youtube(risposte: list) -> tuple[set[str], list[dict]]:
    """I video da `list_creator_videos`, con i canali a cui appartengono (per la guardia)."""
    canali: set[str] = set()
    trovati: dict[str, dict] = {}
    for r in risposte:
        for v in (r or {}).get("videos") or []:
            if not isinstance(v, dict) or not v.get("videoId"):
                continue
            canali.add(v.get("channelId") or "")
            trovati.setdefault(v["videoId"], v)
    post = []
    for vid, v in trovati.items():
        pubbliche = v.get("publicMetrics") or {}
        copyright_ = ((v.get("videoPrechecks") or {}).get("videoUploadChecksMonetized") or {}).get(
            "copyrightCheck"
        ) or {}
        post.append(
            {
                "id": vid,
                "url": f"https://youtube.com/shorts/{vid}",
                "views": _intero(pubbliche.get("externalViewCount")),
                "like": _intero(pubbliche.get("likeCount")),
                "commenti": _intero(pubbliche.get("commentCount")),
                "condivisioni": None,
                "avvisi": avvisi_youtube(v),
                "grezzo": {
                    "restrizioni": v.get("allRestrictions"),
                    "privacy": v.get("privacy"),
                    "copyright": copyright_,
                },
            }
        )
    return canali, post


# ------------------------------------------------------------ browser


def dump(page, piattaforma: str) -> Path:
    """Schermata e HTML: e' la pagina salvata a dire perche' non si e' letto, non un tentativo in piu'."""
    LOG_DIR.mkdir(exist_ok=True)
    base = LOG_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-lettura-{piattaforma}"
    try:
        page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
        base.with_suffix(".html").write_text(page.content(), encoding="utf-8")
    except Exception as exc:
        log(f"(dump non riuscito: {exc})")
    return base.with_suffix(".html")


@contextmanager
def pagina_tiktok(profilo: Path, headless: bool):
    """Una pagina sul profilo TikTok, aperta come la apre l'agente che pubblica."""
    from playwright.sync_api import sync_playwright

    import publish_agent

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(profilo),
            headless=headless,
            user_agent=publish_agent.UA,
            viewport={"width": 1440, "height": 900},
            **registro.ambiente(),
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            yield ctx.pages[0] if ctx.pages else ctx.new_page()
        finally:
            ctx.close()


def post_da_studio(page, atteso: str | None) -> list[dict]:
    """I post dalla pagina dei contenuti di Studio, dopo aver controllato l'account."""
    page.goto(STUDIO_TIKTOK, wait_until="domcontentloaded")
    testo = None
    for _ in range(10):
        page.wait_for_timeout(3000)
        testo = page.evaluate(
            "() => { const s = document.getElementById("
            "'__Creator_Center_Context__'); "
            "return s ? s.textContent : null; }"
        )
        if testo:
            break
    if not testo:
        raise SystemExit(
            f"tiktok: Studio non porta il contesto dei post. "
            f"Pagina salvata in {dump(page, 'tiktok')}"
        )
    try:
        contesto = json.loads(testo)
    except ValueError:
        # Nell'HTML di Studio il JSON arriva con le virgolette come entita'.
        contesto = json.loads(html.unescape(testo))
    handle, post = post_tiktok(contesto)
    # Leggere l'account sbagliato non sbaglia nessuna clip — i post si
    # agganciano per id — ma le fa risultare «non lette» senza dire perche'.
    registro.controlla_handle(handle, atteso, "tiktok")
    return post


def leggi_tiktok(profilo: Path, atteso: str | None, headless: bool) -> list[dict]:
    with pagina_tiktok(profilo, headless) as page:
        return post_da_studio(page, atteso)


def leggi_instagram(profilo: Path, atteso: str | None, headless: bool) -> list[dict]:
    import instagram_agent

    if not atteso:
        raise SystemExit(
            "instagram: il registro non dice l'handle, e la griglia dei reel si apre per handle"
        )
    pw, ctx = instagram_agent.browser(profilo, headless=headless)
    risposte: list = []

    def su_risposta(r):
        if "graphql" not in r.url:
            return
        try:
            risposte.append(r.json())
        except Exception:
            pass

    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.on("response", su_risposta)
    try:
        # La pagina e' quella dell'handle, non «la mia»: i numeri sono di quell'account
        # qualunque sia la sessione, quindi la guardia qui e' l'indirizzo.
        page.goto(f"https://www.instagram.com/{atteso}/reels/", wait_until="domcontentloaded")
        post: list[dict] = []
        for _ in range(8):
            page.wait_for_timeout(2500)
            post = post_instagram(risposte)
            if post:
                break
        if not post:
            raise SystemExit(
                f"instagram: nessun reel nelle risposte della griglia (sessione "
                f"scaduta, o nessun reel). Pagina salvata in {dump(page, 'instagram')}"
            )
        return post
    finally:
        ctx.close()
        pw.stop()


def elenco_youtube(page, canale: str) -> list[dict]:
    """Gli Short del canale da Studio, dentro una sessione gia' aperta.

    Gira dentro la `page_action` della sessione, che non propaga le eccezioni in
    modo affidabile: i guasti tornano come `SystemExit` da rilanciare fuori.
    """
    risposte: list = []

    def su_risposta(r):
        if "list_creator_videos" not in r.url:
            return
        try:
            risposte.append(r.json())
        except Exception:
            pass

    page.on("response", su_risposta)
    page.goto(
        f"https://studio.youtube.com/channel/{canale}/videos/short",
        wait_until="domcontentloaded",
    )
    for _ in range(10):
        page.wait_for_timeout(3000)
        if risposte:
            break
    page.remove_listener("response", su_risposta)
    if not risposte:
        raise SystemExit(
            f"youtube: Studio non ha elencato i video. Pagina salvata in {dump(page, 'youtube')}"
        )
    canali, post = post_youtube(risposte)
    if canali - {canale}:
        raise SystemExit(
            f"youtube: Studio ha elencato video di {sorted(canali)}, il registro "
            f"si aspetta {canale}. Non leggo: sono i numeri di un altro canale."
        )
    return post


def sessione_youtube(profilo: Path, canale: str | None, headless: bool, lavoro) -> object:
    """Apre Studio sul profilo e fa girare `lavoro(page)`; rilancia fuori i suoi guasti."""
    import youtube_agent

    if not canale:
        raise SystemExit(
            "youtube: il registro non dice `canale_id`, e senza Studio non si "
            "apre sul canale giusto"
        )
    youtube_agent.usa_profilo(profilo)
    esito: dict = {}

    def azione(page):
        try:
            esito["valore"] = lavoro(page)
        except SystemExit as exc:
            esito["guasto"] = exc
        return page

    with youtube_agent.sessione(headless=headless) as s:
        s.fetch("https://studio.youtube.com/", page_action=azione, load_dom=True, wait=3000)
    if "guasto" in esito:
        raise esito["guasto"]
    if "valore" not in esito:
        raise SystemExit("youtube: la sessione di Studio si e' chiusa senza leggere niente")
    return esito["valore"]


def leggi_youtube(profilo: Path, canale: str | None, headless: bool) -> list[dict]:
    return sessione_youtube(profilo, canale, headless, lambda page: elenco_youtube(page, canale))


def main() -> None:
    ap = argparse.ArgumentParser(description="Views e segnali dei post di un account")
    ap.add_argument("--gruppo", required=True, help="il gruppo di dashboard/identita.json")
    ap.add_argument("--piattaforma", required=True, choices=sorted(INTERPRETE))
    ap.add_argument("--vedi", action="store_true", help="con la finestra, per guardare")
    args = ap.parse_args()

    voce = registro.identita(args.gruppo, args.piattaforma) or {}
    if args.piattaforma == "instagram" and not args.vedi:
        import instagram_api

        if instagram_api.ha_token(args.gruppo):
            try:
                post = post_instagram_api(instagram_api.reel_con_numeri(args.gruppo))
            except (registro.AccountSbagliato, RuntimeError) as exc:
                sys.exit(f"instagram (API): {exc}")
            scrivi_esito(args, voce, post)
            return

    profilo = registro.profilo_obbligatorio(args.gruppo, args.piattaforma)
    headless = not args.vedi
    try:
        with serratura.presa(
            serratura.nome_profilo(profilo), attesa=ATTESA_PROFILO, scaduta=SCADUTA_PROFILO
        ):
            if args.piattaforma == "tiktok":
                post = leggi_tiktok(profilo, registro.handle(args.gruppo, "tiktok"), headless)
            elif args.piattaforma == "instagram":
                post = leggi_instagram(profilo, registro.handle(args.gruppo, "instagram"), headless)
            else:
                post = leggi_youtube(profilo, registro.canale_id(args.gruppo), headless)
    except serratura.Occupata as exc:
        log(f"{args.piattaforma}: salto, profilo in uso — {exc}")
        sys.exit(PROFILO_OCCUPATO)
    except (registro.AccountSbagliato, RuntimeError) as exc:
        # RuntimeError e' il «profilo gia' aperto» degli agenti: una finestra di
        # `login` lasciata aperta, che la serratura non vede.
        sys.exit(f"{args.piattaforma}: {exc}")

    scrivi_esito(args, voce, post, profilo.name)


def scrivi_esito(args, voce: dict, post: list[dict], da: str = "API di Meta") -> None:
    json.dump(
        {
            "gruppo": args.gruppo,
            "piattaforma": args.piattaforma,
            "identita": voce.get("id"),
            "letto_il": datetime.now().isoformat(timespec="seconds"),
            "post": post,
        },
        sys.stdout,
        ensure_ascii=False,
    )
    log(f"{args.piattaforma}: {len(post)} post letti da {da}")


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
