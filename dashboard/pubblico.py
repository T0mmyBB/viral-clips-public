#!/usr/bin/env python
"""
pubblico.py — da quale paese arrivano le views. Scrive `storico.sqlite`,
tabella `pubblico`, e non tocca nient'altro.

    python pubblico.py --gruppo business              # tutte le piattaforme del gruppo
    python pubblico.py --gruppo business --piattaforma tiktok
    python pubblico.py --leggi                        # cosa c'e' gia' nel database

Perche' esiste
--------------
Le tre nicchie sono anglofone e americane, gli account sono stati registrati
dall'Italia, e su TikTok la distribuzione iniziale e' **locale**: un account
di regione Italia che pubblica business in inglese viene mostrato prima a
italiani, che non ingaggiano, e il segnale debole affossa la clip. E' il
meccanismo per cui si finisce a parlare di VPN e proxy.

Quella discussione pero' non si puo' fare: **non sappiamo dove vanno le
views**. Al 13 settembre 2026 questo progetto ha cinque post misurati, tutti
YouTube, e zero righe sul paese di chi guarda. Comprare proxy adesso sarebbe
aprire una nicchia senza aver misurato quanto costa lavorarla — l'errore che
sta scritto in CLAUDE.md e che il progetto ha gia' pagato due volte.

Quindi prima si misura. Se il pubblico business e' per meta' italiano il
problema e' reale e si discute la cura con un numero in mano; se e' americano,
si e' risparmiato un abbonamento e tre account in rodaggio.

Perche' per account e non per post
-----------------------------------
La domanda e' «dove vanno le nostre views», non «dove sono andate quelle della
clip 03»: la risposta sta su **una pagina sola** per account invece di una per
post, e con cinque post misurati la media per post sarebbe comunque rumore. Il
per-post si aggiunge quando i post saranno abbastanza da farne una media.

Perche' si scrive uno storico e non un'istantanea
--------------------------------------------------
Stessa ragione di `metriche.py`: la geografia di un account **cambia mentre
l'account matura**, ed e' esattamente la curva che dice se le contromisure
gratuite (lingua dichiarata, didascalie inglesi, orari americani) stanno
funzionando. Se si tiene solo l'ultimo valore, quella curva non esiste piu'.

La guardia
----------
Prima di leggere si confronta l'account collegato con quello che il registro
si aspetta, esattamente come prima di pubblicare. Qui non si carica niente, ma
leggere l'audience dell'account sbagliato produce **un numero plausibile e
falso** — e su quel numero si deciderebbe se comprare dei proxy.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PUBLISH_DIR = ROOT / "publish"
DB = HERE / "storico.sqlite"
DUMP_DIR = HERE / "dump-pubblico"

sys.path.insert(0, str(PUBLISH_DIR))

PIATTAFORME = ("tiktok", "youtube")

# Gli indirizzi delle analytics, e sotto le etichette con cui le due pagine
# chiamano la stessa cosa. Non si indovina: se nessuna aggancia si salva la
# pagina e ci si ferma.
ANALYTICS = {
    "tiktok": "https://www.tiktok.com/tiktokstudio/analytics/viewers",
    "youtube": "https://studio.youtube.com/",
}


def adesso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS pubblico (
            gruppo TEXT NOT NULL,
            identita TEXT,               -- lo @handle da cui si e' letto
            piattaforma TEXT NOT NULL,   -- 'tiktok' | 'youtube' | 'instagram'
            paese TEXT NOT NULL,         -- come lo scrive la piattaforma
            quota REAL NOT NULL,         -- percentuale, 0-100
            quando TEXT NOT NULL,        -- ISO, il momento della lettura
            fonte TEXT NOT NULL          -- quale pagina l'ha detto
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_pubblico ON pubblico(gruppo, piattaforma, quando)")
    con.commit()
    return con


def salva(righe: list[dict]) -> int:
    if not righe:
        return 0
    con = db()
    con.executemany(
        "INSERT INTO pubblico (gruppo, identita, piattaforma, paese, quota, quando, fonte)"
        " VALUES (:gruppo, :identita, :piattaforma, :paese, :quota, :quando, :fonte)",
        righe,
    )
    con.commit()
    con.close()
    return len(righe)


def ultime(gruppo: str | None = None) -> list[dict]:
    """L'ultima lettura per ogni coppia (gruppo, piattaforma)."""
    con = db()
    con.row_factory = sqlite3.Row
    dove = "WHERE gruppo = ?" if gruppo else ""
    righe = con.execute(
        f"""
        SELECT p.* FROM pubblico p
        JOIN (SELECT gruppo, piattaforma, MAX(quando) AS ultimo
              FROM pubblico {dove} GROUP BY gruppo, piattaforma) u
          ON p.gruppo = u.gruppo AND p.piattaforma = u.piattaforma AND p.quando = u.ultimo
        ORDER BY p.gruppo, p.piattaforma, p.quota DESC
    """,
        (gruppo,) if gruppo else (),
    ).fetchall()
    con.close()
    return [dict(r) for r in righe]


def dump(page, tag: str) -> Path:
    """
    Salva schermata **e HTML**. L'HTML e' la meta' che conta: il 12 settembre
    2026 un selettore sbagliato e' stato curato due volte a tentativi, sei
    minuti, mentre aprire la pagina salvata mostrava i candidati uno sotto
    l'altro in trenta secondi.
    """
    DUMP_DIR.mkdir(exist_ok=True)
    base = DUMP_DIR / f"{tag}-{datetime.now():%Y%m%d-%H%M%S}"
    try:
        page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
        base.with_suffix(".html").write_text(page.content(), encoding="utf-8")
    except Exception as exc:  # una pagina morta non deve nascondere l'errore vero
        print(f"  (dump non riuscito: {exc})", file=sys.stderr)
    return base


def _numero(testo: str) -> float | None:
    """«12,3%» → 12.3. Le due pagine scrivono la percentuale in modi diversi."""
    ripulito = "".join(c for c in (testo or "") if c.isdigit() or c in ",.").replace(",", ".")
    if ripulito.count(".") > 1:  # «1.234.5» non e' un numero
        ripulito = ripulito.replace(".", "", ripulito.count(".") - 1)
    try:
        return float(ripulito)
    except ValueError:
        return None


def paesi_tiktok(page) -> tuple[list[tuple[str, float]], str | None]:
    """
    Il riquadro **«Locations»** della pagina Viewers. Torna le coppie lette e,
    quando non ce ne sono, il **motivo** — che e' la meta' importante.

    Ci sono due modi di non leggere niente, e confonderli e' il difetto che
    questo progetto insegue ovunque:

    - **TikTok non ha ancora abbastanza dati.** Lo dice con
      `components_DataUnavailableBanner` e la frase «You'll be able to see this
      information once there's enough data for analysis», e sul nostro account
      business — aperto il 12 settembre 2026 — e' quello che succede: la pagina
      si apre, il riquadro «Locations» c'e', e dentro c'e' il banner. **E' un
      fatto sull'account, non un guasto**, e va registrato come tale: dice che
      la domanda sulla geolocalizzazione non si puo' ancora porre.
    - **Il selettore non aggancia piu'.** Quello e' un guasto, e si cura
      guardando la pagina salvata — mai a tentativi.

    Se le due cose danno lo stesso errore, il primo giorno in cui TikTok
    cambiasse struttura lo leggeremmo come «account troppo giovane» e
    aspetteremmo dei dati che non arriverebbero mai.
    """
    page.goto(ANALYTICS["tiktok"], wait_until="domcontentloaded")
    page.wait_for_timeout(6000)

    # L'aggancio viene dall'HTML salvato, non da un'ipotesi: la pagina ha sette
    # `components_AnalyticsCard_CardWrapper` e quello con dentro «Locations» e'
    # uno solo. **Si controlla che sia uno solo**, perche' `:has-text` e'
    # sottostringa — la stessa proprieta' per cui «Pubblica» prendeva la voce
    # «Posts» della barra laterale e ogni pubblicazione era una navigazione.
    riquadri = page.query_selector_all(
        '[data-tt="components_AnalyticsCard_CardWrapper"]:has-text("Locations")'
    )
    if len(riquadri) != 1:
        return [], None  # zero o ambiguo: e' un guasto, si guarda il dump
    riquadro = riquadri[0]

    if riquadro.query_selector('[data-tt*="DataUnavailableBanner"]'):
        return (
            [],
            "non ancora disponibile: TikTok mostra le aree geografiche solo "
            "quando l'account ha abbastanza dati",
        )

    trovati: list[tuple[str, float]] = []
    for nodo in riquadro.query_selector_all("li, tr, div[class*='Item'], div[class*='Row']"):
        testo = (nodo.inner_text() or "").strip()
        if not testo or "%" not in testo:
            continue
        pezzi = [p.strip() for p in testo.replace("\n", "|").split("|") if p.strip()]
        nome = next((p for p in pezzi if "%" not in p), "")
        quota = next((_numero(p) for p in pezzi if "%" in p), None)
        if nome and quota is not None and (nome, quota) not in trovati:
            trovati.append((nome, quota))
    return trovati, None if trovati else None


def paesi_youtube(page, canale: str | None) -> tuple[list[tuple[str, float]], str | None]:
    """
    YouTube Studio, Analytics → Pubblico → principali aree geografiche.
    L'indirizzo diretto esiste e salta la navigazione a clic, che e' la parte
    che si rompe.
    """
    if not canale:
        return [], (
            "nessun `canale_id` nel registro per questo gruppo: senza, "
            "Studio non si apre sul canale giusto e leggerlo a caso "
            "darebbe il pubblico di un altro"
        )
    page.goto(
        f"https://studio.youtube.com/channel/{canale}/analytics/tab-build_audience"
        f"/period-default/explore",
        wait_until="domcontentloaded",
    )
    page.wait_for_timeout(8000)
    trovati: list[tuple[str, float]] = []
    for riga in page.query_selector_all('ytcp-video-row, #rows tr, tr[class*="row"]'):
        testo = (riga.inner_text() or "").strip()
        if "%" not in testo:
            continue
        pezzi = [p.strip() for p in testo.split("\n") if p.strip()]
        nome = next((p for p in pezzi if "%" not in p and not p.replace(".", "").isdigit()), "")
        quota = next((_numero(p) for p in pezzi if "%" in p), None)
        if nome and quota is not None:
            trovati.append((nome, quota))
    return trovati, None


def leggi_piattaforma(gruppo: str, piattaforma: str, headless: bool) -> list[dict]:
    from playwright.sync_api import sync_playwright

    import publish_agent
    import registro

    profilo = registro.profilo_obbligatorio(gruppo, piattaforma)
    atteso = registro.handle(gruppo, piattaforma)
    righe: list[dict] = []

    with sync_playwright() as p:
        # **Il profilo YouTube lo crea Chrome vero, non il Chromium di
        # Playwright**: sono formati diversi e mescolarli fa litigare il
        # browser sul suo stesso profilo. `youtube_agent` usa `real_chrome`
        # per questo, e un lettore di analytics che aprisse quella cartella
        # con Chromium rischierebbe di rovinare la sessione da cui dipende
        # tutta la pubblicazione — per leggere una percentuale.
        canale = {"channel": "chrome"} if piattaforma == "youtube" else {}
        ctx = p.chromium.launch_persistent_context(
            str(profilo),
            headless=headless,
            user_agent=publish_agent.UA,
            viewport={"width": 1440, "height": 900},
            **canale,
            **registro.ambiente(),
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            if piattaforma == "tiktok":
                # La stessa guardia di prima di pubblicare. Qui non si carica
                # niente, ma leggere l'audience dell'account sbagliato da' un
                # numero plausibile e falso — e su quel numero si deciderebbe
                # se comprare dei proxy.
                registro.controlla_handle(publish_agent.detect_handle(page), atteso, "tiktok")
                coppie, motivo = paesi_tiktok(page)
            else:
                coppie, motivo = paesi_youtube(page, registro.canale_id(gruppo))
            # «La piattaforma non ce l'ha ancora» e «il selettore non aggancia
            # piu'» non sono lo stesso esito e non possono dare lo stesso
            # errore: confonderli vuol dire, il giorno che TikTok cambia
            # struttura, aspettare per mesi dei dati che nessuno sta leggendo.
            if not coppie and motivo:
                print(f"  {piattaforma}: {motivo}", file=sys.stderr)
                return [
                    {
                        "gruppo": gruppo,
                        "identita": atteso,
                        "piattaforma": piattaforma,
                        "paese": "(non disponibile)",
                        "quota": 0.0,
                        "quando": adesso(),
                        "fonte": motivo,
                    }
                ]
            if not coppie:
                percorso = dump(page, f"{gruppo}-{piattaforma}")
                raise SystemExit(
                    f"{piattaforma}: la pagina si e' aperta ma non ho trovato il riquadro "
                    f"delle aree geografiche, e non e' la piattaforma a dire che mancano i "
                    f"dati. Pagina salvata in {percorso.with_suffix('.html')} — **si apre "
                    f"quella**, non si ritenta: i candidati stanno li' dentro, e i tentativi "
                    f"a vuoto sono gia' costati sei minuti una volta."
                )
            righe = [
                {
                    "gruppo": gruppo,
                    "identita": atteso,
                    "piattaforma": piattaforma,
                    "paese": nome,
                    "quota": quota,
                    "quando": adesso(),
                    "fonte": ANALYTICS[piattaforma],
                }
                for nome, quota in coppie
            ]
        finally:
            ctx.close()
    return righe


def main() -> None:
    ap = argparse.ArgumentParser(description="Da quale paese arrivano le views")
    ap.add_argument("--gruppo", default="", help="il gruppo di dashboard/identita.json")
    ap.add_argument("--piattaforma", default="", choices=("", *PIATTAFORME))
    ap.add_argument(
        "--headless",
        action="store_true",
        help="senza finestra. Su TikTok il rilevamento dell'handle "
        "e' meno affidabile cosi': la prima volta guardala",
    )
    ap.add_argument("--leggi", action="store_true", help="mostra l'ultima lettura salvata")
    args = ap.parse_args()

    if args.leggi or not args.gruppo:
        righe = ultime(args.gruppo or None)
        if not righe:
            print(
                "Nessuna lettura: il database e' vuoto.\n  python pubblico.py --gruppo business",
                file=sys.stderr,
            )
            return
        json.dump(righe, sys.stdout, ensure_ascii=False, indent=2)
        return

    quali = [args.piattaforma] if args.piattaforma else list(PIATTAFORME)
    tutte: list[dict] = []
    for piattaforma in quali:
        print(f"=== {args.gruppo} / {piattaforma} ===", file=sys.stderr)
        righe = leggi_piattaforma(args.gruppo, piattaforma, args.headless)
        for r in righe[:8]:
            print(f"  {r['paese']:<24} {r['quota']:>6.1f}%", file=sys.stderr)
        tutte += righe
    print(f"{salva(tutte)} righe salvate in {DB.name}", file=sys.stderr)
    json.dump(tutte, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
