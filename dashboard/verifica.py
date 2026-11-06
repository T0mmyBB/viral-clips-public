#!/usr/bin/env python
"""
verifica.py — le prove che vengono da fuori, non dalla pagina che ha appena
compiuto l'azione.

    python verifica.py youtube --url https://youtube.com/shorts/3-gEBJW5524
    python verifica.py tiktok --handle canale_lab
    python verifica.py whop --post <url> --campagna <uuid>

Perche' esiste separato da stato.py
------------------------------------
`stato.py` incrocia file che abbiamo gia' e non tocca la rete: e' veloce e
offline. Qui invece si chiede al mondo esterno se una cosa e' vera, ed e'
costoso in due modi diversi — tempo (un browser vero, per TikTok e Whop) e
rischio (un profilo occupato che rompe un run in corso). La distinzione non e'
estetica: la notte del 31 agosto tre azioni erano "riuscite" secondo le pagine
che le avevano compiute, e nessuna lo era davvero. La prova stava sempre fuori:

  YouTube  oembed pubblico            200 = pubblico,   403 = privato/bozza
  TikTok   elenco degli id del profilo  presenza dell'id = pubblicato
  Whop     elenco «Submissions»         presenza dell'URL = registrato

Tre interpreti diversi
-----------------------
Questo file gira con qualunque Python (la parte YouTube usa solo la libreria
standard), ma le funzioni TikTok e Whop hanno bisogno delle dipendenze che
vivono altrove — Playwright in `publish/.venv`, Scrapling nel venv condiviso.
Import differiti e guardati: manca un pacchetto, la funzione lo dice invece di
far cadere l'intero modulo, cosi' la verifica leggera (YouTube) resta
disponibile anche da un interprete che non ha nient'altro.
"""

from __future__ import annotations

import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
PUBLISH_DIR = HERE.parent / "publish"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def _adesso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def video_id_youtube(url: str) -> str:
    m = re.search(r"(?:shorts/|watch\?v=|youtu\.be/)([\w\-]{6,})", url or "")
    return m.group(1) if m else ""


def verifica_youtube(url: str, timeout: float = 10.0) -> dict:
    """
    200 dall'oembed pubblico = chiunque puo' vederlo. 403 = privato o bozza,
    esattamente il caso che ha reso invisibili due clip senza che nessun log
    lo dicesse. Nessun browser: e' una singola richiesta HTTP.
    """
    vid = video_id_youtube(url)
    if not vid:
        return {"esito": "url_non_riconosciuto", "controllato_il": _adesso(), "fonte": "oembed"}
    chiesto = urllib.parse.quote(f"https://www.youtube.com/watch?v={vid}", safe="")
    req = urllib.request.Request(
        f"https://www.youtube.com/oembed?format=json&url={chiesto}", headers={"User-Agent": UA}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            stato = r.status
    except urllib.error.HTTPError as exc:
        stato = exc.code
    except Exception as exc:
        return {
            "esito": "errore",
            "dettaglio": str(exc),
            "controllato_il": _adesso(),
            "fonte": "oembed",
        }
    return {
        "esito": "pubblico" if stato == 200 else "non_pubblico",
        "codice": stato,
        "controllato_il": _adesso(),
        "fonte": "oembed",
    }


def verifica_tiktok(handle: str, id_atteso: str | None = None, headless: bool = True) -> dict:
    """
    Richiede Playwright (`publish/.venv`). Apre TikTok Studio e il profilo
    pubblico e ne unisce gli id — lo stesso motivo per cui esiste
    `all_video_ids()`: il profilo pubblico mostra un post appena uscito con
    minuti di ritardo, Studio no.
    """
    sys.path.insert(0, str(PUBLISH_DIR))
    try:
        from playwright.sync_api import sync_playwright

        import publish_agent
    except ImportError as exc:
        return {
            "esito": "richiede_interprete",
            "dettaglio": str(exc),
            "suggerimento": str(PUBLISH_DIR / ".venv" / "Scripts" / "python.exe"),
            "controllato_il": _adesso(),
        }

    profilo = PUBLISH_DIR / ".tiktok-canale_lab"
    if not profilo.exists() or handle not in str(profilo):
        alt = PUBLISH_DIR / ".browser-profile"
        profilo = alt if alt.exists() else profilo

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(profilo),
            headless=headless,
            user_agent=publish_agent.UA,
            viewport={"width": 1280, "height": 850},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            ids = publish_agent.all_video_ids(page, handle)
        finally:
            ctx.close()
    return {
        "esito": "trovato"
        if (id_atteso and id_atteso in ids)
        else ("presente" if not id_atteso else "assente"),
        "totale_post": len(ids),
        "controllato_il": _adesso(),
        "fonte": "tiktok studio + profilo",
    }


def verifica_whop(
    post: str,
    campagna: str,
    app: str = "https://whop.com/clip-farm-official/exp_sKCcnfigfcLoSb/app/",
    headless: bool = True,
) -> dict:
    """
    Richiede Scrapling (venv condiviso). Legge l'elenco Submissions della
    campagna e cerca l'identificatore del post — la stessa `risulta_inviato()`
    che ha sostituito "nessun errore" come prova di invio.
    """
    sys.path.insert(0, str(PUBLISH_DIR))
    try:
        import whop_agent
    except ImportError as exc:
        return {
            "esito": "richiede_interprete",
            "dettaglio": str(exc),
            "suggerimento": str(Path.home() / ".venvs" / "scraping" / "Scripts" / "python.exe"),
            "controllato_il": _adesso(),
        }

    trovato = {}

    def azione(page):
        whop_agent.attesa(page, 25)
        trovato["ok"] = whop_agent.risulta_inviato(page, post, app)
        return page

    with whop_agent.sessione(headless=headless) as s:
        s.fetch(app, page_action=azione, load_dom=True, wait=3000)
    return {
        "esito": "registrato" if trovato.get("ok") else "assente",
        "controllato_il": _adesso(),
        "fonte": "whop submissions",
    }


def main() -> None:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Verifica esterna di un esito dichiarato")
    sub = ap.add_subparsers(dest="cmd", required=True)

    y = sub.add_parser("youtube")
    y.add_argument("--url", required=True)

    t = sub.add_parser("tiktok")
    t.add_argument("--handle", required=True)
    t.add_argument("--id-atteso", default=None)
    t.add_argument("--headless", action="store_true")

    w = sub.add_parser("whop")
    w.add_argument("--post", required=True)
    w.add_argument("--campagna", required=True)
    w.add_argument("--app", default="https://whop.com/clip-farm-official/exp_sKCcnfigfcLoSb/app/")
    w.add_argument("--headless", action="store_true")

    args = ap.parse_args()
    if args.cmd == "youtube":
        r = verifica_youtube(args.url)
    elif args.cmd == "tiktok":
        r = verifica_tiktok(args.handle, args.id_atteso, headless=args.headless)
    else:
        r = verifica_whop(args.post, args.campagna, args.app, headless=args.headless)
    json.dump(r, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
