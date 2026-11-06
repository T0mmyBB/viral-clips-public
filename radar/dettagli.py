#!/usr/bin/env python
"""
dettagli.py — la seconda passata del radar: legge la scheda di ogni campagna e
ne ricava i **numeri misurati**, non le promesse.

Dal 16 settembre 2026 le schede arrivano dall'API del catalogo (`catalogo.voci()`,
13 richieste per tutto il sito) e non piu' aprendo una pagina per campagna, che
restano il ripiego e la prova (`--pagine`). Le chiavi sono le stesse; i conti
stanno in un posto solo, `componi()`.

Perche' esiste
--------------
`radar.py` ordina con quello che la pagina elenco espone: tariffa, budget,
concorrenti. Manca il fatto che decide tutto, cioe' se in quella campagna le
clip le guarda qualcuno. Il campo `category` — su cui poggiava il peso del
bacino tematico — e' **vuoto su tutte le campagne**, quindi quel fattore era
una costante travestita da giudizio.

La scheda di ogni campagna, pubblica e senza login, porta invece:

  metrics.chartPoints      views e post approvati, giorno per giorno (30 giorni)
  metrics.budgetSpentCents quanto ha gia' pagato
  topEarners               quanto ha incassato chi sta davanti
  payouts                  per piattaforma: tariffa, **tetto per clip** e
                           **minimo per essere pagati**
  referenceMaterials       i materiali forniti (spesso il girato da tagliare)
  description              la frase con cui il committente descrive la campagna
  platforms, payoutType    dove si pubblica e con che modello si viene pagati

Da li' si ricavano le due misure che contano:

  soldi al mese     = views degli ultimi 30 giorni x tariffa / 1000
  per clipper       = soldi al mese / numero di clipper
  mesi di autonomia = budget residuo / soldi al mese

`per clipper` e' quanto incassa in un mese chi sta li' dentro, in media. E'
sporcato verso l'alto dai clipper grossi — `topEarners` dice di quanto — ma
come **ordinamento** e' misurato, mentre il peso per parola chiave era
indovinato.

`mesi di autonomia` e' il filtro che serve a noi: un account nuovo non pubblica
per sette giorni e poi misura per quattro settimane. Una campagna che si
esaurisce in meno di due mesi non arriva viva in fondo alla prima misura,
qualunque sia la sua tariffa.

Uso
---
    python radar/dettagli.py                    # classifica sui numeri misurati
    python radar/dettagli.py --mesi 3           # solo quelle che reggono 3 mesi
    python radar/dettagli.py --json out.json    # tutto il grezzo
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import catalogo  # noqa: E402  (stessa cartella: le voci intere dell'API)
import radar  # noqa: E402  (stessa cartella: riusa scarica() e _payload_rsc())
import tassonomia  # noqa: E402  (per riscrivere il report appena il catalogo cambia)

DETTAGLI = HERE / "radar_dettagli.json"
CATALOGO = HERE / "catalogo.json"
SCHEDA_URL = "https://contentrewards.com/discover/"

# Dove stanno i file caricati sul sito. L'API scrive i materiali caricati come
# chiave del bucket (`organizations/<org>/campaigns/references/...`), la pagina
# come indirizzo intero: misurato su 69 materiali su 69, la differenza e' solo
# questo prefisso. Senza, il link non si apre e l'impronta di `classifica.py`
# cambierebbe, cioe' 29 campagne si ridecidevano per niente.
BUCKET = (
    "https://content-rewards-production-publicassetsbucket-oxvzxvnr.s3.us-east-1.amazonaws.com/"
)

# Sotto queste soglie `$/clipper/mese` non descrive una campagna, descrive una
# persona. Non e' un filtro di qualita': e' la differenza tra una media e un
# aneddoto.
MIN_CLIPPER = 5
MIN_POST = 10

# Intervallo minimo tra due richieste, su tutti i lavoratori messi insieme.
#
# Il 31 agosto 2026, dopo due passate da 512 schede in dieci minuti, la loro
# applicazione ha risposto `ReservedFunctionConcurrentInvocationLimitExceeded —
# Rate Exceeded`: e' il limite di esecuzioni contemporanee di una funzione
# AWS Lambda. Non e' dimostrato che fossimo noi — quel limite e' condiviso con
# tutto il traffico del servizio — ma un migliaio di richieste in pochi minuti
# e' la nostra parte, ed e' l'unica su cui possiamo intervenire. Leggere il
# catalogo e' una cosa che si fa una volta al giorno: non c'e' nessun motivo per
# cui debba costare loro una raffica.
PAUSA_MIN = 0.35
_ultimo = [0.0]
_semaforo = threading.Lock()


def _aspetta_il_turno() -> None:
    with _semaforo:
        manca = PAUSA_MIN - (time.monotonic() - _ultimo[0])
        if manca > 0:
            time.sleep(manca)
        _ultimo[0] = time.monotonic()


def catalogo_completo(percorso) -> list[dict] | None:
    """
    Le campagne da leggere, dal catalogo prodotto da `catalogo.py`.

    Torna None se il catalogo non c'e': in quel caso si ripiega sull'indice, che
    pero' contiene **50 campagne su 512** — un decimo del sito. Il ripiego deve
    dirlo, perche' una classifica costruita sul decimo sbagliato ha lo stesso
    aspetto di quella giusta.
    """
    p = Path(percorso)
    if not p.exists():
        return None
    try:
        dati = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    voci = dati.get("campagne") if isinstance(dati, dict) else dati
    return voci or None


# ------------------------------------------------------------------- lettura


def _oggetto(testo: str, start: int) -> str | None:
    """Come radar._blocco_bilanciato, ma per un oggetto invece che un array."""
    livello, in_stringa, escape = 0, False, False
    for i in range(start, len(testo)):
        c = testo[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_stringa = not in_stringa
            continue
        if in_stringa:
            continue
        if c == "{":
            livello += 1
        elif c == "}":
            livello -= 1
            if livello == 0:
                return testo[start : i + 1]
    return None


def _chiave(flight: str, nome: str, apertura: str):
    m = re.search(r'"%s"\s*:\s*\%s' % (nome, apertura), flight)
    if not m:
        return None
    inizio = flight.index(apertura, m.end() - 1)
    grezzo = (
        _oggetto(flight, inizio) if apertura == "{" else radar._blocco_bilanciato(flight, inizio)
    )
    try:
        return json.loads(grezzo)
    except (TypeError, json.JSONDecodeError):
        return None


def _scalare(flight: str, nome: str):
    """Il valore di una chiave semplice: numero, stringa o booleano."""
    m = re.search(r'"%s"\s*:\s*("(?:[^"\\]|\\.)*"|-?[\d.]+|true|false)' % nome, flight)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _uniforme(tariffe: list[dict], chiave: str):
    """
    Il valore di `chiave` in centesimi, ma solo se le piattaforme concordano.

    Con tre righe che dicono la stessa cifra il numero unico e' vero; con tre
    righe diverse non esiste un numero unico, e sceglierne uno — il primo, il
    massimo — vuol dire scrivere nella scheda una cifra che su due piattaforme
    su tre e' falsa. `None` costringe chi legge a mostrare il dettaglio.
    """
    valori = {radar.numero(t.get(chiave)) for t in tariffe}
    return (valori.pop() / 100) if len(valori) == 1 else None


def scheda(campagna: dict) -> dict:
    """
    Legge la scheda di una campagna dalla sua pagina e ne estrae i numeri misurati.

    Dal 16 settembre 2026 non e' piu' la strada normale: le stesse chiavi
    arrivano tutte insieme dall'API del catalogo (`scheda_da_api`), con 13
    richieste invece di 643. Resta per le campagne che il catalogo conosce e
    l'API non elenca, e per `--pagine`, cioe' per confrontare le due fonti.
    """
    base = {
        "id": str(campagna.get("id") or campagna.get("title", "")),
        "titolo": str(campagna.get("titolo") or campagna.get("title", "")),
    }
    _aspetta_il_turno()
    try:
        flight = radar._payload_rsc(radar.scarica(SCHEDA_URL + base["id"]))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {**base, "errore": str(exc)}

    join = re.search(r'"joinHref"\s*:\s*"([^"]+)"', flight)
    return componi(
        base,
        {
            "title": _scalare(flight, "title"),
            "primaryPayoutCents": _scalare(flight, "primaryPayoutCents"),
            "creatorCount": _scalare(flight, "creatorCount"),
            "budgetCents": _scalare(flight, "budgetCents"),
            "budgetSpentCents": _scalare(flight, "budgetSpentCents"),
            "metrics": _chiave(flight, "metrics", "{") or {},
            "topEarners": _chiave(flight, "topEarners", "[") or [],
            "referenceMaterials": _chiave(flight, "referenceMaterials", "[") or [],
            "payouts": _chiave(flight, "payouts", "[") or [],
            "platforms": _chiave(flight, "platforms", "[") or [],
            "joinHref": join.group(1) if join else "",
            "organizationExperienceId": _scalare(flight, "organizationExperienceId"),
            "organizationName": _scalare(flight, "organizationName"),
            "status": _scalare(flight, "status"),
            "requiresApplication": _scalare(flight, "requiresApplication"),
            "description": _scalare(flight, "description"),
            "payoutType": _scalare(flight, "payoutType"),
        },
    )


def scheda_da_api(voce: dict) -> dict:
    """
    La stessa scheda, da una voce dell'API del catalogo: nessuna richiesta in piu'.

    Le chiavi sono quelle della pagina, con tre spostamenti: `creatorCount`,
    `budgetSpentCents` e `topEarners` stanno dentro `metrics`, il titolo si
    chiama `name`, e i materiali caricati sul sito non hanno il prefisso del
    bucket. Il 16 settembre 2026 le due fonti sono state confrontate su 642
    campagne prima di cambiare strada: identiche su provider, tariffe, tetti,
    piattaforme e stato; i numeri differivano solo per i minuti fra le due
    letture; e la pagina sbagliava sei titoli, scrivendo `$$2,000` per `$2,000`.
    """
    metriche = voce.get("metrics") or {}
    base = {"id": str(voce.get("id") or ""), "titolo": str(voce.get("name") or "")}
    return componi(
        base,
        {
            "title": voce.get("name"),
            "primaryPayoutCents": voce.get("primaryPayoutCents"),
            "creatorCount": metriche.get("creatorCount"),
            "budgetCents": voce.get("budgetCents"),
            "budgetSpentCents": metriche.get("budgetSpentCents"),
            "metrics": metriche,
            "topEarners": metriche.get("topEarners") or [],
            "referenceMaterials": [
                {
                    **m,
                    "url": m["url"]
                    if not m.get("url") or "://" in m["url"]
                    else BUCKET + m["url"].lstrip("/"),
                }
                for m in (voce.get("referenceMaterials") or [])
                if isinstance(m, dict)
            ],
            "payouts": voce.get("payouts") or [],
            "briefCounts": voce.get("briefCounts"),
            "platforms": voce.get("platforms") or [],
            "joinHref": "",
            "organizationExperienceId": voce.get("organizationExperienceId"),
            "organizationName": voce.get("organizationName"),
            "status": voce.get("status"),
            "requiresApplication": voce.get("requiresApplication"),
            "description": voce.get("description"),
            "payoutType": voce.get("payoutType"),
        },
    )


def componi(base: dict, g: dict) -> dict:
    """
    La scheda con i numeri che ne derivano, da qualunque fonte vengano le chiavi.

    Un posto solo per i conti: con due fonti e due copie delle stesse formule,
    la prima correzione ne sistema una e l'altra resta indietro senza dirlo.
    """
    base = dict(base)
    speso_tot = radar.numero(g["budgetSpentCents"])
    base["titolo"] = str(g["title"] or base["titolo"])
    base["cpm"] = radar.numero(g["primaryPayoutCents"]) / 100
    base["clipper"] = int(radar.numero(g["creatorCount"]))
    base["residuo"] = max(0.0, (radar.numero(g["budgetCents"]) - speso_tot) / 100)

    metriche = g["metrics"] or {}
    punti = metriche.get("chartPoints") or []
    incassi = g["topEarners"] or []
    materiali = g["referenceMaterials"] or []
    tariffe = g["payouts"] or []
    piattaforme = g["platforms"] or []

    views = sum(radar.numero(p.get("totalViews")) for p in punti)
    post = sum(radar.numero(p.get("approvedSubmissionCount")) for p in punti)
    soldi_mese = views * base["cpm"] / 1000

    indirizzo_join = g["joinHref"] or f"/discover/{base['id']}/join"
    m_prov = re.search(r"/experiences/(exp_[A-Za-z0-9]+)/", indirizzo_join)
    # Dal ridisegno del 16 settembre 2026 `joinHref` e' interno al sito
    # (`/discover/<uuid>/join`) e non porta piu' l'esperienza Whop: il provider
    # usciva vuoto su tutte le campagne, senza errori, e con lui `iscritto` e il
    # collegamento per inviare. L'esperienza sta in `organizationExperienceId`,
    # e il collegamento si ricompone nella forma che `whop_agent.py` sa leggere.
    # Su una parte delle campagne quella chiave non c'e' affatto (vedi
    # `riporta_provider`): li' il collegamento resta quello del sito, ma
    # assoluto, perche' un percorso relativo non lo apre nessuno.
    esperienza = g["organizationExperienceId"] or ""
    if not m_prov and re.fullmatch(r"exp_[A-Za-z0-9]+", esperienza):
        indirizzo_join = f"https://whop.com/experiences/{esperienza}/campaigns/{base['id']}"
        m_prov = re.search(r"/experiences/(exp_[A-Za-z0-9]+)/", indirizzo_join)
    elif indirizzo_join.startswith("/"):
        indirizzo_join = "https://contentrewards.com" + indirizzo_join

    return {
        **base,
        "provider": m_prov.group(1) if m_prov else "",
        "organizzazione": g["organizationName"] or "",
        "stato": g["status"] or "",
        "candidatura": bool(g["requiresApplication"]),
        "views_30g": int(views),
        "post_30g": int(post),
        "giorni_serie": len(punti),
        "soldi_mese": round(soldi_mese, 2),
        "per_clipper": round(soldi_mese / base["clipper"], 2) if base["clipper"] else 0.0,
        # Con uno o due clipper `per_clipper` non e' una media, e' il risultato
        # di una persona sola: basta che sia brava perche' la campagna sembri la
        # migliore del sito. E' la stessa trappola del campione troppo piccolo
        # gia' pagata altrove nel progetto — un numero calcolato su un ottavo
        # del lavoro vero diceva il contrario di quello vero.
        "campione_ok": base["clipper"] >= MIN_CLIPPER and post >= MIN_POST,
        "mesi": round(base["residuo"] / soldi_mese, 1) if soldi_mese else 99.0,
        "post_totali": metriche.get("approvedSubmissionCount"),
        "speso": round(radar.numero(metriche.get("budgetSpentCents")) / 100, 2),
        # `payouts` porta una riga **per piattaforma**, e le tre non sono
        # obbligate a coincidere: leggerne solo la prima significa dichiarare
        # per YouTube il tetto di TikTok senza accorgersene. `tetto_clip` e
        # `minimo_clip` restano il caso normale — le tre righe uguali — e
        # valgono `None` quando divergono, cosi' chi li legge non ha un numero
        # falso ma nessun numero; il dettaglio vero sta in `tariffe`.
        "tetto_clip": _uniforme(tariffe, "maxPayoutCents"),
        "minimo_clip": _uniforme(tariffe, "minPayoutCents"),
        "tariffe": [
            {
                "piattaforma": t.get("platform"),
                "cpm": radar.numero(t.get("rateCents")) / 100,
                "tetto": radar.numero(t.get("maxPayoutCents")) / 100,
                "minimo": radar.numero(t.get("minPayoutCents")) / 100,
            }
            for t in tariffe
        ],
        "primo_incasso": (radar.numero(incassi[0].get("earnedCents")) / 100) if incassi else None,
        "materiali": [m.get("url") for m in materiali if m.get("url")],
        # La descrizione e' l'unica frase che il committente scrive di suo
        # pugno sulla scheda, ed e' il solo appiglio testuale per capire *di
        # cosa parla* una campagna: `categories` e' vuoto su tutte, e il titolo
        # spesso e' un nome proprio ("Whoods's Campaign") che non dice niente.
        # Senza questo campo la classificazione per argomento resta un regex
        # sul titolo, cioe' un indovinello ben scritto.
        "descrizione": (g["description"] or "").strip(),
        "piattaforme": [p for p in piattaforme if isinstance(p, str)],
        "tipo_pagamento": g["payoutType"] or "",
        # Gli stessi materiali con il loro tipo (`brandAsset`, `example`, ...):
        # l'elenco di soli URL sopra resta per chi lo legge gia', ma il tipo
        # dice se un link e' il girato da tagliare o un esempio di clip altrui.
        "materiali_dett": [
            {"url": m.get("url"), "tipo": m.get("type"), "mezzo": m.get("mediaType")}
            for m in materiali
            if m.get("url")
        ],
        # Quanti file ha la campagna anche quando non si vedono: dal sito nuovo
        # (fra il 5 e il 27 ottobre 2026) i materiali sono «Visible once you
        # join», e l'API pubblica li manda vuoti contando pero' i riferimenti.
        "riferimenti": (g.get("briefCounts") or {}).get("references"),
        "join": indirizzo_join,
        # I due indirizzi non sono intercambiabili e servono a due persone
        # diverse. `scheda` e' la pagina pubblica del catalogo: si apre senza
        # login e senza iscrizione al provider, e porta tetto, minimo,
        # piattaforme e brief — e' quella da dare a chi vuole *guardare* la
        # campagna. `join` e' il collegamento profondo dentro l'applicazione
        # Whop del provider: serve per *inviare*, ma chi non e' iscritto a quel
        # whop ci rimbalza sulla home (gia' visto su Clipping Culture) senza un
        # errore che lo dica. Darne uno solo vuol dire o togliere l'invio o
        # mandare a sbattere chi voleva solo leggere.
        "scheda": SCHEDA_URL + base["id"],
    }


def riporta_provider(schede: list[dict], precedenti: list[dict]) -> int:
    """
    Rimette l'esperienza Whop gia' letta sulle schede che oggi non la portano.

    Dal 16 settembre 2026 36 campagne su 643 — tutte di organizzazioni con una
    campagna sola — non espongono piu' `organizationExperienceId`, ne' nella
    scheda ne' nell'API. L'esperienza di una campagna non cambia da un giorno
    all'altro, e senza provider la campagna risulta «non iscritto» anche se lo
    siamo: meglio il valore letto ieri che nessun valore. Vince sempre quello
    letto adesso, quando c'e'. Torna quante schede ha completato.
    """
    noti = {
        p["id"]: p for p in precedenti if p.get("id") and p.get("provider") and not p.get("errore")
    }
    riportati = 0
    for s in schede:
        p = noti.get(s.get("id"))
        if s.get("errore") or s.get("provider") or not p:
            continue
        s["provider"], s["join"] = p["provider"], p.get("join") or s.get("join", "")
        riportati += 1
    return riportati


MATERIALI_NOTI = HERE / "materiali_noti.json"


def archivia_materiali(schede: list[dict], archivio: dict, letto_il: str) -> int:
    """Mette nell'archivio i materiali visibili oggi. Torna quante campagne ha aggiornato."""
    aggiornate = 0
    for s in schede:
        if s.get("id") and not s.get("errore") and s.get("materiali_dett"):
            archivio[s["id"]] = {
                "materiali": s.get("materiali") or [],
                "materiali_dett": s["materiali_dett"],
                "letto_il": letto_il,
            }
            aggiornate += 1
    return aggiornate


def riporta_materiali(schede: list[dict], archivio: dict) -> int:
    """
    Rimette i materiali gia' letti sulle schede che oggi li hanno chiusi.

    Dal sito nuovo i materiali si vedono solo dopo l'iscrizione alla campagna:
    l'API pubblica li manda vuoti. Il girato di una campagna non cambia da un
    giorno all'altro, e senza materiali il risolutore delle sorgenti perde i
    link al video da tagliare: come per `riporta_provider`, meglio la lettura
    vecchia che niente, con `materiali_letti_il` che dice di quando e'. Vince
    sempre quello letto adesso, quando c'e'. Torna quante schede ha completato.
    """
    riportati = 0
    for s in schede:
        noto = archivio.get(s.get("id"))
        if s.get("errore") or s.get("materiali_dett") or not noto:
            continue
        s["materiali"] = noto["materiali"]
        s["materiali_dett"] = noto["materiali_dett"]
        s["materiali_letti_il"] = noto["letto_il"]
        riportati += 1
    return riportati


# -------------------------------------------------------------------- output


def tabella(righe: list[dict]) -> str:
    testa = (
        f"{'$/clipper':>9}  {'$/mese':>8}  {'mesi':>5}  {'cpm':>5}  "
        f"{'clip':>5}  {'residuo':>9}  {'tetto':>6}  {'min':>6}  {'mat':>3}  "
        f"{'provider':<20}  campagna"
    )
    fuori = [testa, "-" * len(testa)]
    for r in righe:
        fuori.append(
            f"{r['per_clipper']:>9,.0f}  {r['soldi_mese']:>8,.0f}  {min(r['mesi'], 99):>5.1f}  "
            f"{r['cpm']:>5.2f}  {r['clipper']:>5}  {r['residuo']:>9,.0f}  "
            f"{(r['tetto_clip'] or 0):>6,.0f}  {(r.get('minimo_clip') or 0):>6,.2f}  "
            f"{len(r['materiali']):>3}  "
            f"{str(r.get('organizzazione', ''))[:20]:<20}  {r['titolo'][:40]}"
        )
    return "\n".join(fuori)


def main() -> None:
    ap = argparse.ArgumentParser(description="Numeri misurati delle campagne")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument(
        "--mesi",
        type=float,
        default=0.0,
        help="scarta le campagne che si esauriscono prima di N mesi",
    )
    ap.add_argument("--json", metavar="FILE", help="scrive tutto il grezzo in un file")
    ap.add_argument(
        "--catalogo", default=str(CATALOGO), help="elenco completo prodotto da catalogo.py"
    )
    ap.add_argument(
        "--lavoratori",
        type=int,
        default=3,
        help="richieste in parallelo. Il ritmo lo impone comunque PAUSA_MIN: "
        "alzarlo non accorcia la scansione, aumenta solo la raffica",
    )
    ap.add_argument(
        "--tutte",
        action="store_true",
        help="mostra anche le campagne con troppo pochi clipper per fare media",
    )
    ap.add_argument(
        "--riusa",
        action="store_true",
        help="riordina l'ultima lettura senza riscaricare le 512 schede",
    )
    ap.add_argument("--url", default=radar.DISCOVER_URL)
    ap.add_argument(
        "--pagine",
        action="store_true",
        help="legge ogni scheda dalla sua pagina invece che dall'API: "
        "piu' lento, serve a confrontare le due fonti",
    )
    args = ap.parse_args()

    if args.riusa:
        # Per riordinare o cambiare vista non si ribussa a 512 pagine: i numeri
        # cambiano di ora in ora, non di minuto in minuto.
        if not DETTAGLI.exists():
            sys.exit(f"{DETTAGLI.name} non esiste ancora: la prima volta serve una lettura vera.")
        schede = json.loads(DETTAGLI.read_text(encoding="utf-8"))
        return mostra(schede, args)

    elenco = catalogo_completo(args.catalogo)
    if elenco is None:
        print(
            "catalogo.json assente: leggo le sole 50 campagne dell'indice, "
            "cioe' un decimo del sito. Genera il catalogo con catalogo.py.",
            file=sys.stderr,
        )
        try:
            elenco = radar.estrai_campagne(radar.scarica(args.url))
        except (urllib.error.URLError, TimeoutError) as exc:
            sys.exit(f"Non riesco a leggere {args.url}: {exc}")
    if not elenco:
        sys.exit("Nessuna campagna da leggere.")

    # Le schede vengono dall'API del catalogo: 13 richieste invece di una per
    # campagna. Le pagine restano per cio' che l'API non elenca — una campagna
    # comparsa o sparita fra il passo `catalogo` e questo — e per `--pagine`.
    voci_api: dict[str, dict] = {}
    if not args.pagine:
        try:
            voci_api = {v["id"]: v for v in catalogo.voci()}
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            print(
                f"API del catalogo non letta ({exc}): leggo le {len(elenco)} "
                f"schede dalle pagine, ci vuole qualche minuto",
                file=sys.stderr,
            )
    dalle_pagine = [c for c in elenco if str(c.get("id")) not in voci_api]
    print(
        f"{len(elenco)} campagne: {len(elenco) - len(dalle_pagine)} dall'API, "
        f"{len(dalle_pagine)} dalle pagine",
        file=sys.stderr,
    )
    with ThreadPoolExecutor(max_workers=args.lavoratori) as pool:
        lette = {s["id"]: s for s in pool.map(scheda, dalle_pagine)}
    schede = [
        scheda_da_api(voci_api[str(c.get("id"))])
        if str(c.get("id")) in voci_api
        else lette[str(c.get("id") or c.get("title", ""))]
        for c in elenco
    ]

    rotte = [s for s in schede if s.get("errore")]
    buone = [s for s in schede if not s.get("errore")]
    if rotte:
        print(f"{len(rotte)} schede non lette", file=sys.stderr)

    try:
        precedenti = json.loads(DETTAGLI.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        precedenti = []
    riportati = riporta_provider(schede, precedenti if isinstance(precedenti, list) else [])
    senza = sum(1 for s in buone if not s.get("provider"))
    if riportati or senza:
        print(
            f"provider: {riportati} ripresi dalla lettura precedente, "
            f"{senza} ancora senza (il sito non espone l'esperienza Whop)",
            file=sys.stderr,
        )

    try:
        archivio = json.loads(MATERIALI_NOTI.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        archivio = {}
    if archivia_materiali(schede, archivio, datetime.now().date().isoformat()):
        MATERIALI_NOTI.write_text(json.dumps(archivio, ensure_ascii=False), encoding="utf-8")
    ripresi = riporta_materiali(schede, archivio)
    chiusi = sum(1 for s in buone if not s.get("materiali_dett") and s.get("riferimenti"))
    if ripresi or chiusi:
        print(
            f"materiali: {ripresi} ripresi da una lettura precedente, {chiusi} chiusi "
            f"(visibili solo dopo l'iscrizione)",
            file=sys.stderr,
        )

    DETTAGLI.write_text(json.dumps(schede, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.json:
        Path(args.json).write_text(
            json.dumps(schede, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    aggiorna_report()
    mostra(schede, args)


def aggiorna_report() -> None:
    """
    Riscrive `nicchie_report.md` adesso che le schede sono cambiate.

    Sta qui e non solo in `giornaliero.py` perche' il report **descrive** il
    catalogo: se lo aggiorna solo il giro giornaliero, chiunque lanci
    `dettagli.py` a mano lascia dietro un report che parla del catalogo di
    ieri senza dirlo. Le campagne nuove sono la parte che si perde per prima —
    non hanno ancora una nicchia, quindi sparivano da ogni tabella.

    Un report che non si riesce a scrivere non ferma la lettura delle schede:
    quelle sono gia' su disco ed erano il lavoro caro.
    """
    try:
        q = tassonomia.rigenera_report(DETTAGLI)
    except (RuntimeError, OSError) as exc:
        print(f"report delle nicchie non aggiornato: {exc}", file=sys.stderr)
        return
    manca = len(q["senza_nicchia"])
    print(
        f"nicchie_report.md riscritto: {q['campagne_lette']} campagne, "
        f"{q['lavorabili']} lavorabili"
        + (
            f", {manca} SENZA NICCHIA — `python tassonomia.py --digest --senza-assegnazione`"
            if manca
            else ""
        ),
        file=sys.stderr,
    )


def mostra(schede: list[dict], args) -> None:
    buone = [s for s in schede if not s.get("errore")]
    tenute = [s for s in buone if s["mesi"] >= args.mesi]
    scarse = [s for s in tenute if not s["campione_ok"]]
    if not args.tutte:
        tenute = [s for s in tenute if s["campione_ok"]]
    tenute.sort(key=lambda s: -s["per_clipper"])
    if args.mesi:
        print(
            f"{len(tenute)} campagne su {len(buone)} reggono almeno {args.mesi:g} mesi"
            + (
                f" (escluse {len(scarse)} con meno di {MIN_CLIPPER} clipper "
                f"o {MIN_POST} post: li' la media e' una persona sola)"
                if not args.tutte
                else ""
            )
            + "\n"
        )
    print(tabella(tenute[: args.top]))
    print(
        f"\nGrezzo in {DETTAGLI.name} — il campo `join` e' l'URL da passare a "
        f"whop_agent.py join --url"
    )


if __name__ == "__main__":
    main()
