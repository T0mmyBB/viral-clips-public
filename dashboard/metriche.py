#!/usr/bin/env python
"""
metriche.py — il raccoglitore delle views nel tempo. Scrive `storico.sqlite`
e non tocca nessun altro file: e' l'unico dato della dashboard che non ha gia'
un proprietario altrove, quindi e' l'unico che questo progetto scrive.

    python metriche.py --youtube          # legge tutte le clip YouTube note, in HTTP
    python metriche.py --account          # TikTok e Instagram, e i segnali di tutte e tre
    python metriche.py --whop             # stato degli invii e pagamenti, dal pannello Whop

Perche' comincia da YouTube
----------------------------
Le views di YouTube si leggono in HTTP puro, senza browser: la pagina del
video contiene `"viewCount":"24"`. TikTok e Whop hanno lo stesso bisogno ma
costano un browser vero (vedi `verifica.py`), quindi entrano dopo, nella
stessa tabella — lo schema e' gia' pensato per tre fonti, non una.

TikTok e Instagram: una pagina per account
-------------------------------------------
`--account` apre, per ogni identita' attiva del registro, la pagina del
proprietario che porta i numeri di tutti i post (`publish/leggi_account.py`,
lanciato con l'interprete del suo agente). Views e like vanno in `misura`, come
YouTube; i **segnali** — in revisione, non pubblico, restrizioni — vanno in
`salute`, per tutte e tre, YouTube compreso: le sue views restano all'HTTP, che
non ha bisogno di un browser, ma le restrizioni le vede solo Studio. Un post si
aggancia alla sua clip per l'id nell'URL, e un post che non appartiene a
nessuna clip si registra lo stesso: la misura presa e' un fatto.

Perche' esiste da subito, anche vuoto
--------------------------------------
Lo storico e' l'unica cosa che non si puo' recuperare a posteriori: se la
clip 01 e' a 24 views adesso e nessuno lo scrive, fra una settimana non si
sapra' mai quanto ha fatto nelle prime 24 ore. Il raccoglitore nasce con la
base della dashboard apposta per questo.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / "storico.sqlite"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "publish"))
import instagram_api  # noqa: E402
import leggi_account  # noqa: E402
import stato  # noqa: E402

# Un lettore che non torna in dieci minuti e' appeso. Ucciderlo lascia la sua
# serratura di profilo sul disco fino alla scadenza (un'ora): una pubblicazione
# che cade li' dentro si ferma e riprova al giro dopo, e lo dice.
ATTESA_LETTORE = 600


def db() -> sqlite3.Connection:
    nuovo = not DB.exists()
    con = sqlite3.connect(DB)
    if nuovo:
        con.execute("""
            CREATE TABLE misura (
                post_id TEXT NOT NULL,       -- l'URL del post: e' l'identificatore stabile
                cartella TEXT, clip_id TEXT, identita TEXT,
                fonte TEXT NOT NULL,         -- 'youtube' | 'tiktok' | 'whop'
                quando TEXT NOT NULL,        -- ISO, il momento della lettura
                views INTEGER, like INTEGER, guadagno REAL
            )
        """)
        con.execute("CREATE INDEX idx_misura_post ON misura(post_id, quando)")
        con.commit()
    con.execute("""
        CREATE TABLE IF NOT EXISTS salute (
            post_id TEXT NOT NULL,
            cartella TEXT, clip_id TEXT, identita TEXT,
            piattaforma TEXT NOT NULL,
            quando TEXT NOT NULL,
            avvisi TEXT NOT NULL,        -- JSON: le frasi, vuoto se niente da dire
            grezzo TEXT                  -- JSON: i campi della piattaforma, anche quelli non letti
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_salute_post ON salute(post_id, quando)")
    # Le analitiche per post (`dashboard/analitiche.py`): una riga per lettura e per
    # post, coi campi comuni alle tre piattaforme in JSON. `dati` NULL e' «non letto»
    # e `guasto` dice perche'; il grezzo dell'ultima lettura sta su disco.
    con.execute("""
        CREATE TABLE IF NOT EXISTS analitiche (
            post_id TEXT NOT NULL,
            cartella TEXT, clip_id TEXT, identita TEXT,
            piattaforma TEXT NOT NULL,
            quando TEXT NOT NULL,
            dati TEXT,
            guasto TEXT
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_analitiche_post ON analitiche(post_id, quando)")
    # Lo stato degli invii e i pagamenti, per gruppo: una riga per lettura.
    # `NULL` e' «non letto», mai zero — vedi `whop_agent.somma_pagamenti`.
    con.execute("""
        CREATE TABLE IF NOT EXISTS whop (
            gruppo TEXT NOT NULL,
            quando TEXT NOT NULL,
            account TEXT,
            clip INTEGER, approvate INTEGER, in_attesa INTEGER, respinte INTEGER,
            pagamenti INTEGER,
            pagato REAL,
            grezzo TEXT                  -- JSON: la riga `guadagni:` intera
        )
    """)
    return con


def registra(
    con: sqlite3.Connection,
    *,
    post_id: str,
    cartella: str,
    clip_id: str,
    identita: str | None,
    fonte: str,
    views: int | None = None,
    like: int | None = None,
    guadagno: float | None = None,
) -> None:
    con.execute(
        "INSERT INTO misura (post_id, cartella, clip_id, identita, fonte, quando, views, like, guadagno) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            post_id,
            cartella,
            clip_id,
            identita,
            fonte,
            datetime.now().isoformat(timespec="seconds"),
            views,
            like,
            guadagno,
        ),
    )
    con.commit()


def views_youtube(url: str, timeout: float = 10.0) -> int | None:
    """
    Il conteggio sta nell'HTML della pagina pubblica, non nell'oembed (che non
    lo porta): `"viewCount":"24"` compare piu' volte, si prende la prima.
    Nessun browser — e' lo stesso motivo per cui questa fonte parte per prima.
    """
    m_id = re.search(r"(?:shorts/|watch\?v=|youtu\.be/)([\w\-]{6,})", url or "")
    if not m_id:
        return None
    req = urllib.request.Request(
        f"https://www.youtube.com/watch?v={m_id.group(1)}", headers={"User-Agent": UA}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            html = r.read().decode("utf-8", errors="replace")
    except OSError:
        # HTTPError, URLError, timeout, connessione chiusa: tutti «non ho letto»,
        # e un solo post che non risponde non deve fermare il giro delle metriche.
        return None
    m = re.search(r'"viewCount":"(\d+)"', html)
    return int(m.group(1)) if m else None


def raccogli_youtube() -> list[dict]:
    con = db()
    letture = []
    for camp in stato.campagne_output():
        for c in camp["clip"]:
            if c["piattaforma"] != "youtube" or not c["url"]:
                continue
            if c["stato_pubblicazione"] not in ("pubblicato", "done"):
                continue
            v = views_youtube(c["url"])
            registra(
                con,
                post_id=c["url"],
                cartella=camp["cartella"],
                clip_id=c["id"],
                identita=c["identita"],
                fonte="youtube",
                views=v,
            )
            letture.append(
                {"cartella": camp["cartella"], "clip": c["id"], "url": c["url"], "views": v}
            )
    con.close()
    return letture


def abbinamenti() -> dict[tuple[str, str], dict]:
    """Le clip pubblicate, per (piattaforma, id del post): e' cosi' che un post letto trova la sua."""
    fuori = {}
    for camp in stato.campagne_output():
        for c in camp["clip"]:
            pid = leggi_account.id_da_url(c["piattaforma"], c["url"])
            if pid:
                fuori[(c["piattaforma"], pid)] = {
                    "cartella": camp["cartella"],
                    "clip_id": c["id"],
                    "url": c["url"],
                }
    return fuori


def da_leggere() -> list[dict]:
    """
    Le identita' attive con un gruppo, una piattaforma leggibile e un profilo su disco.

    Instagram col token dell'API di Meta passa anche senza profilo: i lettori
    leggono dall'API e il browser non si apre. Sul server la cartella non
    esiste, e senza questa eccezione Instagram restava fermo all'ultima misura
    senza che nessuno se ne accorgesse (dal 4 al 31 ottobre 2026).
    """
    fuori = []
    for i in stato.identita_attive():
        if i.get("piattaforma") not in leggi_account.INTERPRETE or not i.get("gruppo"):
            continue
        if i["piattaforma"] == "instagram" and instagram_api.ha_token(i["gruppo"]):
            fuori.append(i)
            continue
        if not i.get("profilo_browser"):
            continue
        profilo = Path(i["profilo_browser"])
        if not profilo.is_absolute():
            profilo = HERE.parent / profilo
        if profilo.is_dir():
            fuori.append(i)
    return fuori


def raccogli_account() -> tuple[list[dict], list[str]]:
    """Legge ogni account e scrive `misura` (TikTok, Instagram) e `salute` (tutte). Torna letture e guasti."""
    abbinati = abbinamenti()
    lettore = Path(leggi_account.__file__)
    letture, guasti = [], []
    con = db()
    for ident in da_leggere():
        piattaforma = ident["piattaforma"]
        interprete = leggi_account.INTERPRETE[piattaforma]
        nome = f"{ident['id']} ({piattaforma})"
        if not interprete.exists():
            guasti.append(f"{nome}: manca l'interprete {interprete}")
            continue
        print(f"  leggo {nome}...", file=sys.stderr, flush=True)
        try:
            esito = subprocess.run(
                [
                    str(interprete),
                    str(lettore),
                    "--gruppo",
                    ident["gruppo"],
                    "--piattaforma",
                    piattaforma,
                ],
                cwd=str(lettore.parent),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=ATTESA_LETTORE,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            guasti.append(f"{nome}: nessuna risposta in {ATTESA_LETTORE} s")
            continue
        coda = (esito.stderr or "").strip().splitlines()
        if esito.returncode == leggi_account.PROFILO_OCCUPATO:
            # Una pubblicazione in corso: la lettura si rifa' al prossimo giro.
            print(f"  {nome}: saltato, profilo in uso", file=sys.stderr)
            continue
        if esito.returncode != 0:
            guasti.append(f"{nome}: {coda[-1] if coda else 'codice ' + str(esito.returncode)}")
            continue
        dati = json.loads(esito.stdout)
        quando = dati["letto_il"]
        for post in dati["post"]:
            clip = abbinati.get((piattaforma, post["id"])) or {}
            post_id = clip.get("url") or post["url"]
            if piattaforma != "youtube":
                # Le views di YouTube restano a `--youtube`: due fonti per lo
                # stesso numero farebbero una serie a zig-zag.
                con.execute(
                    "INSERT INTO misura (post_id, cartella, clip_id, identita, fonte, quando, "
                    "views, like, guadagno) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        post_id,
                        clip.get("cartella"),
                        clip.get("clip_id"),
                        ident["id"],
                        piattaforma,
                        quando,
                        post["views"],
                        post["like"],
                        None,
                    ),
                )
            grezzo = {
                **post["grezzo"],
                "views": post["views"],
                "commenti": post["commenti"],
                "condivisioni": post["condivisioni"],
            }
            con.execute(
                "INSERT INTO salute (post_id, cartella, clip_id, identita, piattaforma, quando, "
                "avvisi, grezzo) VALUES (?,?,?,?,?,?,?,?)",
                (
                    post_id,
                    clip.get("cartella"),
                    clip.get("clip_id"),
                    ident["id"],
                    piattaforma,
                    quando,
                    json.dumps(post["avvisi"], ensure_ascii=False),
                    json.dumps(grezzo, ensure_ascii=False),
                ),
            )
            letture.append(
                {
                    "cartella": clip.get("cartella"),
                    "clip": clip.get("clip_id"),
                    "piattaforma": piattaforma,
                    "url": post_id,
                    "views": post["views"],
                    "avvisi": post["avvisi"],
                }
            )
        con.commit()
    con.close()
    return letture, guasti


def ultima_salute() -> dict[str, dict]:
    """L'ultima lettura dei segnali di ogni post, per URL."""
    con = db()
    righe = con.execute("""
        SELECT s.post_id, s.piattaforma, s.quando, s.avvisi, s.grezzo
        FROM salute s
        JOIN (SELECT post_id, MAX(quando) AS ultimo FROM salute GROUP BY post_id) u
          ON s.post_id = u.post_id AND s.quando = u.ultimo
    """).fetchall()
    con.close()
    return {
        pid: {
            "piattaforma": p,
            "quando": q,
            "avvisi": json.loads(a or "[]"),
            "grezzo": json.loads(g) if g else None,
        }
        for pid, p, q, a, g in righe
    }


def storico(post_id: str) -> list[dict]:
    con = db()
    righe = con.execute(
        "SELECT quando, views, like, guadagno FROM misura WHERE post_id=? ORDER BY quando",
        (post_id,),
    ).fetchall()
    con.close()
    return [{"quando": q, "views": v, "like": l, "guadagno": g} for q, v, l, g in righe]


# ------------------------------------------------------------------ whop


def conti_whop() -> dict[str, str]:
    """
    Per ogni gruppo con una consegna passata da Whop, il pannello da cui leggerlo.

    L'indirizzo si prende dalla consegna **piu' recente** del gruppo
    (`publish.json`, campo `whop_app`): e' l'unico posto dove sta scritto, e il
    pannello del provider e' quello a cui quel conto e' iscritto davvero.
    """
    per_gruppo: dict[str, str] = {}
    cartelle = sorted(stato.OUTPUT_DIR.glob("*/publish.json"), key=lambda p: p.stat().st_mtime)
    for percorso in cartelle:
        cfg = stato.leggi(percorso, {}) or {}
        if cfg.get("gruppo") and cfg.get("whop_app"):
            per_gruppo[cfg["gruppo"]] = cfg["whop_app"]
    return per_gruppo


def leggi_riga_guadagni(uscita: str) -> dict | None:
    """L'ultima riga `guadagni: <json>` dell'agente, o None se non c'e'."""
    for riga in reversed((uscita or "").splitlines()):
        if riga.startswith("guadagni: "):
            try:
                return json.loads(riga[len("guadagni: ") :])
            except json.JSONDecodeError:
                return None
    return None


def raccogli_whop() -> tuple[list[dict], list[str]]:
    """
    Legge dal pannello Whop di ogni gruppo lo stato degli invii e i pagamenti.

    Stessa regola del lettore degli account: il profilo Whop e' quello con cui
    l'esecutore invia, quindi si prende la sua serratura e si aspetta poco — una
    lettura rimandata non costa niente, un invio rimandato si'.
    """
    import orchestrator
    import serratura

    letture, guasti = [], []
    con = db()
    for gruppo, app in conti_whop().items():
        cmd, profilo, nome = orchestrator.comando_whop(gruppo)
        if profilo is not None and not profilo.is_dir():
            guasti.append(f"whop {gruppo}: manca il profilo {profilo.name}")
            continue
        print(f"  leggo Whop di {gruppo}...", file=sys.stderr, flush=True)
        try:
            with serratura.presa(nome, attesa=20, scaduta=1800):
                esito = subprocess.run(
                    [*cmd, "--headless", "guadagni", "--app", app],
                    cwd=str(HERE.parent / "publish"),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=ATTESA_LETTORE,
                    stdin=subprocess.DEVNULL,
                )
        except serratura.Occupata:
            print(f"  whop {gruppo}: saltato, profilo in uso", file=sys.stderr)
            continue
        except subprocess.TimeoutExpired:
            guasti.append(f"whop {gruppo}: nessuna risposta in {ATTESA_LETTORE} s")
            continue
        dati = leggi_riga_guadagni(esito.stdout)
        if dati is None:
            coda = (esito.stderr or esito.stdout or "").strip().splitlines()
            guasti.append(f"whop {gruppo}: {coda[-1] if coda else 'nessuna uscita'}")
            continue
        invii = dati.get("invii") or {}
        pagamenti = dati.get("pagamenti") or {}
        con.execute(
            "INSERT INTO whop (gruppo, quando, account, clip, approvate, in_attesa, respinte, "
            "pagamenti, pagato, grezzo) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                gruppo,
                dati.get("letto_il") or datetime.now().isoformat(timespec="seconds"),
                dati.get("account"),
                invii.get("clips"),
                invii.get("approved"),
                invii.get("pending"),
                invii.get("rejected"),
                pagamenti.get("n"),
                pagamenti.get("totale"),
                json.dumps(dati, ensure_ascii=False),
            ),
        )
        con.commit()
        if dati.get("invii") is None or pagamenti.get("totale") is None:
            guasti.append(f"whop {gruppo}: letto solo in parte — {json.dumps(dati)[:200]}")
        letture.append({"gruppo": gruppo, **dati})
    con.close()
    return letture, guasti


def ultimi_whop() -> list[dict]:
    """L'ultima lettura del pannello Whop per ogni gruppo."""
    con = db()
    righe = con.execute("""
        SELECT w.gruppo, w.quando, w.account, w.clip, w.approvate, w.in_attesa, w.respinte,
               w.pagamenti, w.pagato
        FROM whop w
        JOIN (SELECT gruppo, MAX(quando) AS ultimo FROM whop GROUP BY gruppo) u
          ON w.gruppo = u.gruppo AND w.quando = u.ultimo
    """).fetchall()
    con.close()
    cols = [
        "gruppo",
        "quando",
        "account",
        "clip",
        "approvate",
        "in_attesa",
        "respinte",
        "pagamenti",
        "pagato",
    ]
    return [dict(zip(cols, r)) for r in righe]


def andamento(giorni: int = 30) -> dict[str, list[dict]]:
    """
    Le views totali per piattaforma, giorno per giorno: l'ultima misura di ogni post
    entro quel giorno, sommata. E' la curva che dice se la distribuzione si muove.
    """
    from datetime import date, timedelta

    attive = {i["id"] for i in stato.identita_attive()}
    con = db()
    righe = con.execute(
        "SELECT post_id, fonte, identita, cartella, substr(quando,1,10), views FROM misura "
        "WHERE views IS NOT NULL ORDER BY quando"
    ).fetchall()
    con.close()
    per_post: dict[tuple[str, str], dict[str, int]] = {}
    # Una volta per cartella, non per misura: `cartella_esclusa` legge la lingua
    # da `transcript.json`, che pesa megabyte — per riga costava 2,4 s a giro.
    esclusa: dict[str, bool] = {}
    for post_id, fonte, identita, cartella, giorno, views in righe:
        if (cartella or "") not in esclusa:
            esclusa[cartella or ""] = stato.cartella_esclusa(cartella or "")
        if esclusa[cartella or ""] or (identita and identita not in attive):
            continue
        per_post.setdefault((fonte, post_id), {})[giorno] = views
    oggi = date.today()
    giorni_elenco = [(oggi - timedelta(days=g)).isoformat() for g in range(giorni - 1, -1, -1)]
    serie: dict[str, list[dict]] = {}
    for (fonte, _post), misure in per_post.items():
        voce = serie.setdefault(
            fonte, [{"giorno": d, "views": 0, "post": 0} for d in giorni_elenco]
        )
        for punto in voce:
            prima = [v for d, v in sorted(misure.items()) if d <= punto["giorno"]]
            if prima:
                punto["views"] += prima[-1]
                punto["post"] += 1
    return serie


def ultime() -> list[dict]:
    """L'ultima misura per ciascun post: quello che la dashboard mostra di default.

    Le cartelle fuori dalle lingue della plancia si **filtrano in lettura**, non
    si cancellano dal database: una misura presa e' un fatto, e lo storico e'
    l'unica cosa qui dentro che non si puo' rifare a posteriori. Se un domani
    l'italiano rientra, i suoi numeri sono ancora li'.
    """
    con = db()
    righe = con.execute("""
        SELECT m.post_id, m.cartella, m.clip_id, m.identita, m.fonte, m.quando, m.views, m.like, m.guadagno
        FROM misura m
        JOIN (SELECT post_id, MAX(quando) AS ultimo FROM misura GROUP BY post_id) u
          ON m.post_id = u.post_id AND m.quando = u.ultimo
    """).fetchall()
    con.close()
    cols = [
        "post_id",
        "cartella",
        "clip_id",
        "identita",
        "fonte",
        "quando",
        "views",
        "like",
        "guadagno",
    ]
    attive = {i["id"] for i in stato.identita_attive()}
    return [
        r
        for r in (dict(zip(cols, x)) for x in righe)
        if not stato.cartella_esclusa(r["cartella"] or "")
        and (r["identita"] is None or r["identita"] in attive)
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description="Raccoglitore delle metriche nel tempo")
    ap.add_argument(
        "--youtube", action="store_true", help="legge le views di tutte le clip YouTube note"
    )
    ap.add_argument(
        "--account",
        action="store_true",
        help="apre ogni account del registro: views di TikTok e Instagram, segnali di tutte e tre",
    )
    ap.add_argument(
        "--whop",
        action="store_true",
        help="apre il pannello Whop di ogni gruppo: stato degli invii e pagamenti",
    )
    ap.add_argument(
        "--json", action="store_true", help="stampa il risultato in JSON invece che a righe"
    )
    args = ap.parse_args()

    if not (args.youtube or args.account or args.whop):
        ap.error("indica almeno una fonte: --youtube, --account, --whop")

    letture, guasti = [], []
    if args.youtube:
        letture += raccogli_youtube()
    if args.account:
        dagli_account, guasti = raccogli_account()
        letture += dagli_account
    if args.whop:
        da_whop, guasti_whop = raccogli_whop()
        guasti += guasti_whop
        for r in da_whop:
            print(f"whop {r['gruppo']}: invii {r.get('invii')} · pagamenti {r.get('pagamenti')}")
    if args.json:
        json.dump(letture, sys.stdout, ensure_ascii=False, indent=2)
    else:
        for r in letture:
            avvisi = f"  [{', '.join(r['avvisi'])}]" if r.get("avvisi") else ""
            print(
                f"{r['cartella']}/{r['clip']}  {r.get('piattaforma', 'youtube')}  "
                f"{r['views']}  {r['url']}{avvisi}"
            )
    for g in guasti:
        print(f"GUASTO {g}", file=sys.stderr)
    # Un account non letto deve tingere il giro: «Aggiorna» mostra l'esito dei passi.
    sys.exit(1 if guasti else 0)


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
