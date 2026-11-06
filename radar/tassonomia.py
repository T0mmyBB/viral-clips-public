#!/usr/bin/env python
"""
tassonomia.py — la parte meccanica della classificazione per nicchia.

La divisione del lavoro, qui come nella pipeline video
-------------------------------------------------------
`nicchie.py` classifica col regex sul titolo: e' una prima passata onesta e
sbaglia in modo prevedibile — 379 campagne su 521 finivano in "da verificare"
perche' il titolo era un nome proprio. Il giudizio vero — *di cosa parla* una
campagna, e in che nicchia va messo l'account che la lavora — non e' un regex:
lo fa Claude leggendo, esattamente come sceglie i momenti dentro un video.

Quindi questo file non decide niente. Fa due cose meccaniche:

  --digest   impagina ogni campagna in poche righe leggibili (titolo,
             descrizione, segnali del brief, numeri) perche' Claude possa
             leggerne 528 senza saturare il contesto. E' l'equivalente della
             vista di `windows.py`: il canale e' stdout, il lettore e' Claude.
  --report   rilegge le assegnazioni scritte da Claude in `assegnazioni.json`
             e le impagina con i numeri misurati di `radar_dettagli.json`.

`assegnazioni.json` sta a questo file come `decisions.json` sta al render: e'
prodotto da Claude, non da uno script, ed e' l'unica cosa qui dentro che
nessun programma sa rigenerare.

Chi somma, e chi impagina
-------------------------
`quadro()` fa i conti una volta sola e li restituisce come dati. Da li'
`righe_report()` ricava il Markdown e la plancia ricava le sue tabelle: due
lettori, una somma. Se ognuno rifacesse la propria, divergerebbero al primo
cambio di soglia e lo farebbero in silenzio — lo stesso motivo per cui
`stato.py` non tiene una seconda copia dello stato di pubblicazione.

`rigenera_report()` e' chiamata anche da `dettagli.py`, appena le schede sono
state riscritte: il report non e' un passo che si puo' dimenticare, e' la
conseguenza dell'aggiornamento del catalogo.

Uso
---
    python tassonomia.py --digest              # tutte le campagne
    python tassonomia.py --digest --da 0 --a 60
    python tassonomia.py --digest --senza-assegnazione
    python tassonomia.py --report              # scrive nicchie_report.md
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

for _flusso in (sys.stdout, sys.stderr):
    try:
        _flusso.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent
DETTAGLI = HERE / "radar_dettagli.json"
BRIEFS = HERE / "briefs"
ASSEGNAZIONI = HERE / "assegnazioni.json"
TASSONOMIA = HERE / "tassonomia.json"
REPORT = HERE / "nicchie_report.md"

# Le righe del brief che dicono **da dove viene il materiale**: e' la sola
# domanda che decide se la pipeline puo' lavorare quella campagna. Tutto il
# resto del documento (regole di hashtag, scadenze di pagamento, elenco delle
# parole vietate) e' identico ovunque e non aiuta a distinguere una campagna
# dall'altra.
SEGNALI = re.compile(
    r"\b(podcast|interview|episode|long[- ]form|stream|vod|twitch|kick\.com|livestream|"
    r"live show|talking[- ]head|voice ?over|faceless|film yourself|record yourself|"
    r"your own (?:face|content|video)|ugc|repost|re-post|download the|provided (?:asset|clip|"
    r"footage|content)|content bank|raw footage|gameplay|slideshow|photo (?:carousel|"
    r"slideshow)|audio only|song|track|snippet|trailer|documentary|youtube channel|"
    r"google drive folder|dropbox|clip from|clips from|source (?:video|material)|"
    r"b[- ]roll|face cam|facecam|reaction|edit(?:s|ing)? (?:style|the)|sermon|webinar|"
    r"course|coaching|breaks?|unboxing|opening packs?)\b",
    re.I,
)

RUMORE = re.compile(r"^\s*(?:[-*•]|\d+[.)])?\s*(?:https?://\S+\s*)+$")


def carica(percorso: Path, difetto):
    if not percorso.exists():
        return difetto
    try:
        return json.loads(percorso.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return difetto


def righe_utili(testo: str, quante: int = 6) -> list[str]:
    """Le righe del brief che parlano del sorgente, ripulite e accorciate."""
    fuori: list[str] = []
    visti: set[str] = set()
    for riga in testo.splitlines():
        riga = riga.strip()
        if len(riga) < 25 or RUMORE.match(riga):
            continue
        if not SEGNALI.search(riga):
            continue
        riga = re.sub(r"\s+", " ", riga)[:220]
        chiave = riga[:60].lower()
        if chiave in visti:
            continue
        visti.add(chiave)
        fuori.append(riga)
        if len(fuori) >= quante:
            break
    return fuori


def ospiti(campagna: dict) -> str:
    """I domini dei materiali: dicono la forma dell'allegato, non il suo contenuto."""
    conto: dict[str, int] = defaultdict(int)
    for url in campagna.get("materiali") or []:
        dominio = urlparse(str(url)).netloc.replace("www.", "")
        conto[dominio] += 1
    return " ".join(f"{d}x{n}" if n > 1 else d for d, n in sorted(conto.items()))


def scheda_digest(campagna: dict, brief: str | None, assegnata: dict | None) -> str:
    testa = (
        f"### {campagna['titolo']}\n"
        f"id {campagna['id'][:8]} | {campagna.get('organizzazione', '')} | "
        f"${campagna['cpm']:.2f} CPM | {campagna['clipper']} clipper | "
        f"${campagna['per_clipper']:,.0f}/clipper/mese | "
        f"${campagna['residuo']:,.0f} residui | {campagna['mesi']:.1f} mesi | "
        f"{'campione ok' if campagna['campione_ok'] else 'campione scarso'} | "
        f"{'/'.join(campagna.get('piattaforme') or [])}"
    )
    corpo = [testa]
    if assegnata:
        corpo.append(
            f"GIA' ASSEGNATA: {assegnata.get('nicchia')} / {assegnata.get('sottonicchia')}"
        )
    descrizione = re.sub(r"\s+", " ", campagna.get("descrizione") or "").strip()
    corpo.append(f"desc: {descrizione[:600] or '(nessuna)'}")
    materiali = ospiti(campagna)
    corpo.append(f"materiali: {materiali or '(nessuno)'}")
    if brief:
        righe = righe_utili(brief)
        if righe:
            corpo.append("brief: " + " // ".join(righe))
        else:
            corpo.append("brief: (scaricato, nessuna riga sul sorgente)")
    else:
        corpo.append("brief: (non disponibile)")
    return "\n".join(corpo)


def digest(args) -> None:
    schede = carica(Path(args.dettagli), [])
    assegnate = {a["id"]: a for a in carica(ASSEGNAZIONI, {}).get("assegnazioni", [])}
    schede = [s for s in schede if not s.get("errore")]
    schede.sort(key=lambda s: -s.get("per_clipper", 0))
    if args.senza_assegnazione:
        schede = [s for s in schede if s["id"] not in assegnate]
    fetta = schede[args.da : args.a if args.a else None]
    print(f"# {len(fetta)} campagne (di {len(schede)}), ordinate per $/clipper/mese\n")
    for campagna in fetta:
        percorso = Path(args.briefs) / f"{campagna['id']}.txt"
        brief = (
            percorso.read_text(encoding="utf-8", errors="replace") if percorso.exists() else None
        )
        print(scheda_digest(campagna, brief, assegnate.get(campagna["id"])))
        print()


# ------------------------------------------------------------------- quadro


def soldi(v: float) -> str:
    return f"${v:,.0f}"


MESI_CHE_REGGE = 1.5

# Sotto questa soglia di post negli ultimi 30 giorni, views/post e' l'aneddoto
# di due clipper fortunati, non una media della campagna.
POST_MINIMI = 20

# Le regole di pagina che rendono una campagna inservibile a un account che
# deve servirne molte: `marca` vuole una pagina intestata a quel creator,
# `esclusiva` vieta di tenere altre campagne sulla stessa pagina.
PAGINA_ESCLUSA = ("marca", "esclusiva")

# Sopra questo valore per post la campagna vale il lavoro di prepararla.
BUONA = 20.0

# Le piattaforme su cui pubblichiamo davvero. Serve a `cpm_nostro()`: una
# campagna puo' pagare su X o su Facebook, e quel che paga li' non arriva qui.
NOSTRE_PIATTAFORME = ("youtube", "tiktok", "instagram")


def _resa(voci: list[dict], mesi_min: float = 0.0) -> float:
    return sum(
        v["m"]["per_clipper"] for v in voci if v["m"]["campione_ok"] and v["m"]["mesi"] >= mesi_min
    )


def cpm_nostro(scheda: dict) -> float | None:
    """
    Il CPM medio sulle piattaforme **dove pubblichiamo**, o `None` se non si sa.

    Il campo `cpm` della scheda e' `primaryPayoutCents`, e non e' «il CPM»: e'
    il **massimo** fra le piattaforme. Misurato sul catalogo del 13 settembre
    2026, coincide col massimo su 617 campagne su 618, e su **101** le
    piattaforme pagano diverso — errore mediano del 33%, massimo 94%. Sette
    volte quel massimo sta su una piattaforma dove non pubblichiamo: Fabrikam
    dichiara $2,00, che e' il prezzo di X, mentre su YouTube, TikTok e Instagram
    paga $0,12. Preso per buono, `$/post` di quella campagna e' sedici volte
    quello vero.

    La media e' la cosa onesta da fare qui: la stessa clip esce su tutte e tre,
    e `views_30g / post_30g` e' gia' un post medio sommato fra le piattaforme —
    non si sa come si dividono, quindi non si puo' fare di meglio che pesarle
    uguali. `None` quando la scheda non porta `tariffe` (9 campagne su 627) o
    quando nessuna delle nostre e' fra quelle pagate: li' chi chiama decide,
    invece di ricevere uno zero che ordinerebbe.
    """
    tariffe = [
        t
        for t in (scheda.get("tariffe") or [])
        if str(t.get("piattaforma", "")).lower() in NOSTRE_PIATTAFORME
    ]
    if not tariffe:
        return None
    return sum(float(t.get("cpm") or 0) for t in tariffe) / len(tariffe)


def dollari_post(scheda: dict) -> float | None:
    """
    Quanto vale **un nostro post** in questa campagna: views per post x CPM.

    E' la misura giusta perche' il nostro limite sono i post — due al giorno
    per piattaforma — non il budget. `per_clipper` risponde a un'altra domanda,
    «quanto guadagna un clipper qui dentro», e quindi divide anche per la
    concorrenza: una campagna deserta sembra ricca e una affollata sembra
    povera, mentre a noi cambia solo quante views prende un post.

    Il CPM e' quello delle **nostre** piattaforme (`cpm_nostro()`), non il
    `cpm` della scheda, che e' il massimo dichiarato e su una campagna su sei
    non e' quello che prenderemmo noi. Quando le tariffe per piattaforma non
    ci sono si ripiega sul campo vecchio: e' l'unica cosa che la scheda dice,
    e un ripiego dichiarato con un commento non e' il difetto — quello e' il
    ripiego che nessuno sa di avere.

    Torna None quando non si puo' misurare: campagna finita, o troppo pochi
    post per dire qualcosa.
    """
    post = scheda.get("post_30g") or 0
    if post < POST_MINIMI or scheda.get("stato") != "active":
        return None
    cpm = cpm_nostro(scheda)
    if cpm is None:
        cpm = scheda.get("cpm") or 0
    return (scheda.get("views_30g") or 0) / post / 1000 * cpm


def break_even(scheda: dict) -> dict:
    """
    Quante views deve fare un post perche' paghi **anche solo un centesimo**.

    E' `minimo / cpm x 1000`, per piattaforma. Non e' una raffinatezza: sotto
    quella soglia una clip vale **zero**, non poco, e il minimo e' il vincolo
    che morde davvero durante la rampa di un account nuovo. Misurato sul
    catalogo del 13 settembre 2026, su 377 coppie (campagna lavorabile,
    piattaforma): mediana **1.500 views**, primo quarto 1.000, terzo quarto
    5.000, peggiore 128.543. I primi cinque post misurati di questo progetto
    hanno fatto 1.278, 956, 26, 4 e 1 views — uno solo avrebbe pagato qualcosa.

    Da qui il suo uso: si legge **accanto** a `dollari_post`, non al posto suo.
    Una campagna da $36 a post che pretende centomila views vale meno, per un
    account giovane, di una da $8 che paga da mille — e `$/post` da solo mette
    la prima davanti.

    Il numero unico e' la **mediana fra le nostre piattaforme**, ed e' una
    sintesi dichiarata per ordinare, non un'affermazione su una piattaforma:
    il dettaglio vero e' in `per_piattaforma`, perche' la stessa clip esce su
    tre account diversi e ognuno incassa per conto suo. `None` quando non si
    puo' sapere — nessuna tariffa, o CPM a zero — perche' uno zero direbbe
    «paga da subito», che e' il contrario.
    """
    per = {}
    for t in scheda.get("tariffe") or []:
        piattaforma = str(t.get("piattaforma", "")).lower()
        cpm = float(t.get("cpm") or 0)
        if piattaforma not in NOSTRE_PIATTAFORME or cpm <= 0:
            continue
        per[piattaforma] = float(t.get("minimo") or 0) / cpm * 1000
    if not per:
        return {"break_even": None, "break_even_per_piattaforma": {}}
    return {"break_even": statistics.median(per.values()), "break_even_per_piattaforma": per}


def paletti(scheda: dict) -> dict:
    """
    I quattro paletti di una campagna, nella forma in cui li legge una pagina.

    Non sono numeri da ordinare come `dollari_post`: sono i confini dentro cui
    il lavoro vale qualcosa, e servono **prima** di scegliere, non dopo.

      link_scheda   la pagina pubblica del catalogo, per andare a guardare
      link_whop     il collegamento dentro l'applicazione del provider
      tetto_clip    oltre quel guadagno una clip non paga di piu'
      minimo_clip   sotto quel guadagno una clip non paga affatto
      piattaforme   dove la campagna accetta di essere pubblicata

    Sta qui e non nella pagina perche' i lettori sono tre — le proposte, le
    campagne attive e il catalogo per nicchia — e tre letture scritte tre volte
    divergono al primo campo che cambia nome, in silenzio. `tetto_clip` e
    `minimo_clip` possono valere `None` quando le piattaforme non concordano:
    in quel caso il dettaglio e' in `tariffe`, e un numero solo sarebbe falso.
    """
    return {
        "link_scheda": scheda.get("scheda") or "",
        "link_whop": scheda.get("join") or "",
        "tetto_clip": scheda.get("tetto_clip"),
        "minimo_clip": scheda.get("minimo_clip"),
        "tariffe": scheda.get("tariffe") or [],
        "piattaforme_campagna": [str(p).lower() for p in (scheda.get("piattaforme") or [])],
        # Il quinto paletto, e durante la rampa e' il primo che morde: sotto il
        # minimo una clip vale zero, non poco.
        **break_even(scheda),
    }


def minimo_piu_basso(scheda: dict) -> float | None:
    """Il minimo per clip piu' basso fra le nostre piattaforme, o `None` se non e' dichiarato."""
    minimi = [
        float(t["minimo"])
        for t in scheda.get("tariffe") or []
        if str(t.get("piattaforma", "")).lower() in NOSTRE_PIATTAFORME
        and isinstance(t.get("minimo"), (int, float))
    ]
    if not minimi and isinstance(scheda.get("minimo_clip"), (int, float)):
        minimi = [float(scheda["minimo_clip"])]
    return min(minimi) if minimi else None


def fine_campagna(scheda: dict | None, schede_lette: bool) -> str | None:
    """
    Perche' una campagna e' finita, dai soli fatti del catalogo, o `None` se paga ancora.

    Tre modi di finire. **Fuori dall'elenco** `discover` (se il radar ha girato:
    senza schede l'assenza non dice niente). **Stato non attivo.** **Budget
    esaurito**, che non vuol dire residuo a zero: vuol dire che il residuo non
    paga piu' nemmeno il minimo di una clip. Contoso, a fine settembre 2026,
    restava `active` con 1,01 $ su 10.500 contro un minimo di 2,30 $: con la
    regola «residuo <= 0» le sue clip restavano in coda da pubblicare e
    l'esecutore apriva Whop tre volte al giorno per sentirsi dire «in pausa».

    Un residuo che non si legge **non** e' un budget esaurito: senza il numero
    non si chiude niente, perche' una campagna chiusa per sbaglio smette di
    pubblicare senza che nessuno se ne accorga.
    """
    if scheda is None:
        return (
            "la campagna non è più nell'elenco di Whop, cioè di norma è finita"
            if schede_lette
            else None
        )
    if scheda.get("stato") and scheda["stato"] != "active":
        return f"la campagna non è più attiva ({scheda['stato']})"
    residuo = scheda.get("residuo")
    if not isinstance(residuo, (int, float)):
        return None
    if residuo <= 0:
        return "il budget della campagna è esaurito"
    minimo = minimo_piu_basso(scheda)
    if minimo is not None and residuo < minimo:
        return (
            f"il budget della campagna è esaurito: restano {residuo:.2f} $, "
            f"meno del minimo di una clip ({minimo:.2f} $)"
        )
    return None


def campagna_finita(
    uuid: str | None, scheda: dict | None, schede_lette: bool, scelta: dict | None
) -> str | None:
    """
    Perche' non si pubblica piu' niente per questa campagna, o `None`.

    E' `fine_campagna` piu' la chiusura a mano (`stato: chiusa` in
    `dashboard/scelte.json`), che viene prima: e' un giudizio, e un giudizio
    batte una deduzione. La leggono il pannello, per togliere la campagna dalla
    coda delle pubblicazioni, e `publish/esegui.py`, per non aprire nemmeno
    Whop: **una regola sola, due lettori**, se no il pannello dice «finita» e
    l'esecutore continua a provarci.
    """
    if scelta and scelta.get("stato") == "chiusa":
        return "chiusa a mano"
    if not uuid:
        return None
    return fine_campagna(scheda, schede_lette)


def _lavorabili(voci: list[dict]) -> list[dict]:
    return [v for v in voci if v.get("lavorabile") == "si"]


def _riusabili(voci: list[dict]) -> list[dict]:
    """Lavorabili, attive, e che non pretendono una pagina tutta per se'."""
    return [
        v
        for v in _lavorabili(voci)
        if v.get("pagina") not in PAGINA_ESCLUSA and v["m"].get("stato") == "active"
    ]


def _valori_post(voci: list[dict]) -> list[float]:
    return [d for v in voci if (d := dollari_post(v["m"])) is not None]


def _mediana_post(voci: list[dict]) -> float:
    """
    La **mediana** fra le campagne, non la media: e' quella che descrive il
    caso normale.

    In questo catalogo la differenza non e' accademica. Le distribuzioni hanno
    una coda lunghissima — una campagna a $431 per post accanto a venti sotto i
    dieci — e la media di una nicchia la puo' decidere una campagna sola,
    magari con due post misurati. Le due cifre stanno affiancate in report e
    pannello apposta: quando divergono di molto, il numero grande e' una
    campagna, non la nicchia.
    """
    misurate = _valori_post(voci)
    return statistics.median(misurate) if misurate else 0.0


def _media_post(voci: list[dict]) -> float:
    misurate = _valori_post(voci)
    return statistics.fmean(misurate) if misurate else 0.0


def quadro(dettagli: Path | str = DETTAGLI) -> dict:
    """
    Il quadro per nicchia **come dati**, non come pagina.

    Ne esistono due lettori — `nicchie_report.md` da leggere in un editor e la
    plancia — e il modo sicuro di averne due e' che nessuno dei due ricalcoli
    niente: qui si somma una volta sola, `report()` impagina e la plancia
    disegna. Due somme scritte due volte divergono al primo cambio di soglia,
    e divergono in silenzio.

    Le campagne **senza nicchia** escono da qui insieme alle altre. Prima
    sparivano da ogni tabella e sopravvivevano come un numero nell'intestazione:
    le 87 arrivate col catalogo del 3 settembre erano invisibili in un report
    che si dichiarava completo.
    """
    percorso = Path(dettagli)
    schede = {s["id"]: s for s in carica(percorso, []) if not s.get("errore")}
    tass = carica(TASSONOMIA, {})
    assegnazioni = carica(ASSEGNAZIONI, {}).get("assegnazioni", [])
    definizioni = {n["nome"]: n for n in tass.get("nicchie", [])}

    per_nicchia: dict[str, list[dict]] = defaultdict(list)
    assegnati: set[str] = set()
    for a in assegnazioni:
        campagna = schede.get(a["id"])
        if not campagna:
            continue
        assegnati.add(a["id"])
        per_nicchia[a["nicchia"]].append({**a, "m": campagna})

    nicchie = []
    # L'ordine e' per **quanto vale un post** fra le campagne riusabili, non per
    # $/clipper: il 7 settembre quella misura ha proposto due volte di fila la
    # nicchia sbagliata — prima il collezionismo, che ha il budget piu' grosso
    # del catalogo e la resa per post piu' bassa, poi il gaming, che vinceva
    # grazie a una campagna sola in scadenza.
    for nome in sorted(per_nicchia, key=lambda n: -_mediana_post(_riusabili(per_nicchia[n]))):
        voci = per_nicchia[nome]
        ok = _lavorabili(voci)
        riusabili = _riusabili(voci)
        definizione = definizioni.get(nome, {})
        sottonicchie = []
        per_sotto: dict[str, list[dict]] = defaultdict(list)
        for v in voci:
            per_sotto[v.get("sottonicchia") or "(nessuna)"].append(v)
        for sotto in sorted(
            per_sotto, key=lambda s: (-len(_lavorabili(per_sotto[s])), -_resa(per_sotto[s]))
        ):
            righe = sorted(
                per_sotto[sotto], key=lambda v: (v["lavorabile"] != "si", -v["m"]["per_clipper"])
            )
            sottonicchie.append(
                {
                    "nome": sotto,
                    "campagne": [
                        {
                            "id": v["m"]["id"],
                            "titolo": v["m"]["titolo"],
                            # `si*` = lavorabile ma con campione troppo piccolo: il
                            # $/clipper/mese li' e' un aneddoto, non una media.
                            "segno": (
                                "si*"
                                if v["lavorabile"] == "si" and not v["m"]["campione_ok"]
                                else {"si": "si", "lingua": "lng", "no": "no"}[v["lavorabile"]]
                            ),
                            "lavorabile": v["lavorabile"],
                            "forma": v["forma"],
                            "lingua": v["lingua"],
                            "nota": v.get("nota") or "",
                            "per_clipper": v["m"]["per_clipper"],
                            "cpm": v["m"]["cpm"],
                            "mesi": v["m"]["mesi"],
                            "clipper": v["m"]["clipper"],
                            "residuo": v["m"]["residuo"],
                            "campione_ok": v["m"]["campione_ok"],
                            "dollari_post": dollari_post(v["m"]),
                            "pagina": v.get("pagina") or "",
                            "pagina_nota": v.get("pagina_nota") or "",
                            # I paletti economici viaggiano con la riga invece di
                            # essere ricalcolati da chi disegna: il tetto per clip e il
                            # minimo per essere pagati stanno nella scheda, e ricavarli
                            # una seconda volta altrove e' il modo in cui due parti
                            # dello stesso programma finiscono a dire numeri diversi.
                            **paletti(v["m"]),
                        }
                        for v in righe
                    ],
                }
            )
        nicchie.append(
            {
                "nome": nome,
                "definizione": definizione.get("definizione", ""),
                "pagina": definizione.get("pagina", ""),
                "campagne": len(voci),
                "ok": len(ok),
                "lng": sum(1 for v in voci if v.get("lavorabile") == "lingua"),
                "budget": sum(v["m"]["residuo"] for v in ok),
                "resa": _resa(ok),
                "regge": _resa(ok, MESI_CHE_REGGE),
                "riusabili": len(riusabili),
                "esclusa_pagina": sum(1 for v in ok if v.get("pagina") in PAGINA_ESCLUSA),
                "dollari_post": _mediana_post(riusabili),
                "dollari_post_medio": _media_post(riusabili),
                "misurate": len(_valori_post(riusabili)),
                "buone_durevoli": sum(
                    1
                    for v in riusabili
                    if (d := dollari_post(v["m"])) is not None
                    and d >= BUONA
                    and v["m"]["mesi"] >= 2
                ),
                "sottonicchie": sottonicchie,
            }
        )

    senza = sorted(
        (s for i, s in schede.items() if i not in assegnati),
        key=lambda s: -(s.get("per_clipper") or 0),
    )
    return {
        "campagne_lette": len(schede),
        "assegnate": len(assegnati),
        "lavorabili": sum(n["ok"] for n in nicchie),
        "bloccate_lingua": sum(n["lng"] for n in nicchie),
        "nicchie": nicchie,
        "senza_nicchia": [
            {
                "id": s["id"],
                "titolo": s["titolo"],
                "per_clipper": s.get("per_clipper", 0),
                "residuo": s.get("residuo", 0),
                "mesi": s.get("mesi", 0),
                **paletti(s),
            }
            for s in senza
        ],
        # Quando e' stata letta l'ultima volta la fonte da cui esce tutto
        # questo: e' l'unico modo che ha un lettore di sapere se sta guardando
        # il catalogo di stamattina o quello di tre giorni fa.
        "dettagli_letti_il": (
            datetime.fromtimestamp(percorso.stat().st_mtime).isoformat(timespec="seconds")
            if percorso.exists()
            else None
        ),
    }


# ------------------------------------------------------------------- report


def righe_report(q: dict) -> list[str]:
    """Il quadro impaginato in Markdown. Nessun conto qui dentro: solo forma."""
    manca = len(q["senza_nicchia"])
    fuori = [
        "# Nicchie e sottonicchie del catalogo\n",
        f"{q['campagne_lette']} campagne lette, {q['assegnate']} assegnate"
        + (f", **{manca} ancora senza nicchia**" if manca else "")
        + f". Lavorabili dalla pipeline: {q['lavorabili']}.\n",
        "\n`ok` = la pipeline puo' lavorarla (parlato lungo, stream commentato o "
        "evento con reazione, in italiano o inglese) · `lng` = forma buona ma il "
        "parlato non e' ne' italiano ne' inglese · `no` = forma incompatibile.\n",
    ]

    fuori.append("\n## Quadro per nicchia\n")
    fuori.append(
        "**L'ordine e' `$/post`**, cioe' quanto vale un nostro post: views per "
        "post per CPM, mediana delle campagne riusabili e attive con almeno "
        f"{POST_MINIMI} post negli ultimi 30 giorni. E' la misura giusta perche' "
        "il nostro limite sono i post, non il budget: `$/clipper/mese` divide "
        "anche per la concorrenza e fa sembrare ricca una campagna deserta.\n"
    )
    fuori.append(
        "\n`riusa` = campagne lavorabili che **non** pretendono una pagina tutta "
        "per se' (fuori `marca` ed `esclusiva`) · `buone` = fra quelle, quante "
        f"valgono almeno ${BUONA:.0f} per post **e** hanno budget per 2 mesi. "
        "Una nicchia con tante campagne riusabili tiene occupato lo stesso "
        "account per mesi; una con budget grande e una campagna sola, no.\n"
    )
    fuori.append(
        "\nLe due colonne di resa restano perche' descrivono un'altra cosa: "
        "`resa/mese` conta ogni campagna lavorabile con campione valido, "
        f"`che regge` solo quelle con budget per almeno {MESI_CHE_REGGE:g} mesi.\n"
    )
    fuori.append(
        "\n**Mediana e media stanno affiancate**: la mediana descrive il caso "
        "normale, la media la puo' decidere una campagna sola. Quando le due "
        "divergono di molto, il numero grande e' **una campagna**, non la "
        "nicchia — e `mis.` dice su quante campagne sono calcolate.\n"
    )
    fuori.append(
        "| nicchia | $/post mediano | $/post medio | mis. | riusa | buone | "
        "campagne | ok | lng | budget lavorabile | resa/mese | che regge |"
    )
    fuori.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for n in q["nicchie"]:
        fuori.append(
            f"| {n['nome']} | ${n['dollari_post']:.2f} | "
            f"${n['dollari_post_medio']:.2f} | {n['misurate']} | {n['riusabili']} | "
            f"{n['buone_durevoli']} | {n['campagne']} | {n['ok']} | {n['lng']} | "
            f"{soldi(n['budget'])} | {soldi(n['resa'])} | {soldi(n['regge'])} |"
        )

    if q["senza_nicchia"]:
        fuori.append(f"\n## Da classificare — {manca} campagne\n")
        fuori.append(
            "Arrivate col catalogo e mai lette: **non compaiono in nessuna "
            "tabella qui sotto**, quindi ogni somma di questo report le esclude. "
            "Il digest da leggere si stampa con "
            "`python tassonomia.py --digest --senza-assegnazione`.\n"
        )
        fuori.append("| $/clipper/mese | mesi | residuo | id | campagna |")
        fuori.append("|---|---|---|---|---|")
        for s in q["senza_nicchia"]:
            fuori.append(
                f"| {soldi(s['per_clipper'])} | {min(s['mesi'], 99):.1f} | "
                f"{soldi(s['residuo'])} | {s['id'][:8]} | {s['titolo'][:60]} |"
            )

    for n in q["nicchie"]:
        fuori.append(f"\n## {n['nome']} — {n['campagne']} campagne, {n['ok']} lavorabili")
        if n["definizione"]:
            fuori.append(f"\n{n['definizione']}\n")
        if n["pagina"]:
            fuori.append(f"\n**Che pagina serve:** {n['pagina']}\n")
        fuori.append(
            f"\nBudget residuo lavorabile {soldi(n['budget'])} · "
            f"resa stimata {soldi(n['resa'])}/mese, di cui {soldi(n['regge'])} "
            f"da campagne che reggono almeno {MESI_CHE_REGGE:g} mesi\n"
        )
        for sotto in n["sottonicchie"]:
            fuori.append(f"\n### {sotto['nome']} ({len(sotto['campagne'])})\n")
            fuori.append(
                "| ok | $/post | pagina | $/clipper/mese | CPM | mesi | clipper | "
                "residuo | forma | ling | campagna | nota |"
            )
            fuori.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
            for c in sotto["campagne"]:
                dp = f"${c['dollari_post']:.2f}" if c["dollari_post"] is not None else "-"
                fuori.append(
                    f"| {c['segno']} | {dp} | {c['pagina'] or '-'} | "
                    f"{soldi(c['per_clipper'])} | ${c['cpm']:.2f} | "
                    f"{min(c['mesi'], 99):.1f} | {c['clipper']} | {soldi(c['residuo'])} | "
                    f"{c['forma']} | {c['lingua']} | {c['titolo'][:46]} | {c['nota'][:90]} |"
                )

    fuori.append(
        "\n\nLegenda: `si*` = lavorabile ma con campione troppo piccolo "
        "(meno di 5 clipper o 10 post): il $/clipper/mese li' e' un aneddoto, "
        "non una media. `$/post` vuoto = campagna finita o sotto i "
        f"{POST_MINIMI} post nei 30 giorni.\n"
    )
    fuori.append(
        "\nColonna `pagina`, letta dal brief: `libera` nessuna richiesta esplicita · "
        "`tema` basta una pagina di nicchia, quindi riusabile · `marca` vuole una "
        "pagina intestata a quel creator · `esclusiva` vieta altre campagne sulla "
        "stessa pagina · `non_detta` nessuno la nomina, ne' in un senso ne' "
        "nell'altro · `da_verificare` le regole stanno dietro Notion o Discord e "
        "non le ha lette nessuno. **`marca` ed `esclusiva` sono fuori dal piano**: "
        "un account che serve una campagna sola non e' un account, e' un costo.\n"
    )
    return fuori


def rigenera_report(dettagli: Path | str = DETTAGLI) -> dict:
    """
    Riscrive `nicchie_report.md` dal quadro. La chiama `dettagli.py` appena ha
    finito di leggere le schede: il report **non e' un passo separato che
    qualcuno puo' dimenticare**, e' la conseguenza dell'aggiornamento del
    catalogo. Torna il quadro, cosi' chi la chiama sa quante sono rimaste
    senza nicchia senza rileggere il file.
    """
    q = quadro(dettagli)
    if not q["assegnate"]:
        raise RuntimeError(
            f"{ASSEGNAZIONI.name} e' vuoto: le nicchie le scrive Claude, non questo script."
        )
    REPORT.write_text("\n".join(righe_report(q)), encoding="utf-8")
    return q


def report(args) -> None:
    try:
        q = rigenera_report(args.dettagli)
    except RuntimeError as exc:
        sys.exit(str(exc))
    manca = len(q["senza_nicchia"])
    print(
        f"{REPORT.name} riscritto: {q['campagne_lette']} campagne lette, "
        f"{q['assegnate']} in {len(q['nicchie'])} nicchie"
        + (f", {manca} SENZA NICCHIA" if manca else ""),
        file=sys.stderr,
    )


# ------------------------------------------------------------------ importa

CAMPI = ("id", "nicchia", "sottonicchia", "forma", "lingua", "lavorabile", "nota")
LAVORABILE = {"si", "no", "lingua"}


def importa(percorso: str, dettagli: str) -> None:
    """
    Trasforma il TSV scritto a mano in `assegnazioni.json`, risolvendo gli
    identificativi accorciati.

    Gli id delle campagne sono UUID di 36 caratteri: scriverli per intero 528
    volte e' un modo caro di sbagliarne uno. Qui si scrive il prefisso che il
    digest stampa (8 caratteri) e la risoluzione la fa il programma, che sa
    anche dire **quali campagne mancano** — la cosa che una lista scritta a
    mano perde per prima.
    """
    schede = [s for s in carica(Path(dettagli), []) if not s.get("errore")]
    per_prefisso: dict[str, list[dict]] = defaultdict(list)
    for s in schede:
        per_prefisso[s["id"][:8]].append(s)

    righe: list[dict] = []
    problemi: list[str] = []
    visti: set[str] = set()
    for n, riga in enumerate(Path(percorso).read_text(encoding="utf-8").splitlines(), 1):
        if not riga.strip() or riga.lstrip().startswith("#"):
            continue
        pezzi = riga.rstrip("\n").split("\t")
        if len(pezzi) < 6:
            problemi.append(f"riga {n}: solo {len(pezzi)} colonne")
            continue
        voce = dict(zip(CAMPI, pezzi + [""] * (len(CAMPI) - len(pezzi))))
        candidate = per_prefisso.get(voce["id"], [])
        if len(candidate) != 1:
            problemi.append(
                f"riga {n}: prefisso {voce['id']} corrisponde a {len(candidate)} campagne"
            )
            continue
        if voce["lavorabile"] not in LAVORABILE:
            problemi.append(
                f"riga {n}: lavorabile='{voce['lavorabile']}' non e' uno di {sorted(LAVORABILE)}"
            )
            continue
        campagna = candidate[0]
        if campagna["id"] in visti:
            problemi.append(f"riga {n}: {voce['id']} assegnata due volte")
            continue
        visti.add(campagna["id"])
        righe.append({**voce, "id": campagna["id"], "titolo": campagna["titolo"]})

    mancanti = [s for s in schede if s["id"] not in visti]
    for s in sorted(mancanti, key=lambda s: -s.get("per_clipper", 0)):
        problemi.append(f"senza nicchia: {s['id'][:8]}  {s['titolo'][:60]}")

    ASSEGNAZIONI.write_text(
        json.dumps(
            {
                "_leggimi": (
                    "Nicchia e sottonicchia di ogni campagna, decise leggendo "
                    "descrizione e brief. Le scrive Claude, non uno script: "
                    "`tassonomia.py --importa` le converte e basta. "
                    "lavorabile: si = la pipeline puo' lavorarla; lingua = la "
                    "forma va bene ma il parlato non e' ne' italiano ne' "
                    "inglese; no = forma incompatibile."
                ),
                "quante": len(righe),
                "assegnazioni": righe,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )

    print(f"{len(righe)} assegnazioni scritte in {ASSEGNAZIONI.name}", file=sys.stderr)
    for p in problemi:
        print(f"  ! {p}", file=sys.stderr)
    if problemi:
        print(f"{len(problemi)} problemi", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Nicchie: digest per Claude, report dalle assegnazioni"
    )
    ap.add_argument("--digest", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument(
        "--importa", metavar="TSV", help="converte il TSV delle assegnazioni in assegnazioni.json"
    )
    ap.add_argument("--da", type=int, default=0)
    ap.add_argument("--a", type=int, default=0)
    ap.add_argument(
        "--senza-assegnazione",
        action="store_true",
        help="solo le campagne che Claude non ha ancora messo in una nicchia",
    )
    ap.add_argument("--dettagli", default=str(DETTAGLI))
    ap.add_argument("--briefs", default=str(BRIEFS))
    args = ap.parse_args()

    if args.importa:
        return importa(args.importa, args.dettagli)
    if args.report:
        return report(args)
    if args.digest:
        return digest(args)
    ap.error("serve --digest, --importa o --report")


if __name__ == "__main__":
    main()
