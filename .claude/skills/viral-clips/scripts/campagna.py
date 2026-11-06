"""I paletti della campagna, portati dove si sceglie.

    python campagna.py <work_dir> --id <uuid>   # aggancia la cartella alla campagna
    python campagna.py <work_dir>               # ristampa i paletti gia agganciati

**Perche esiste.** Il brief di una campagna veniva letto in un posto solo e per
una cosa sola: estrarne il link del video (`dashboard/stato.py`,
`sorgente_campagna()`). Tutto il resto — quanto deve durare una clip, che il
watermark non si puo tagliare, che si puo usare solo quell'episodio — restava
nel documento, e la cartella di lavoro non sapeva nemmeno di che campagna
fosse. Chi sceglieva i momenti doveva riaprire il brief a mano, che e
esattamente il lavoro che il pannello di controllo esiste per non far fare. E
il difetto non si vedeva: la consegna riusciva lo stesso, solo giudicata senza
le regole del committente.

**Il brief si copia per intero, non si riassume.** Un estratto e una decisione
su cosa conta presa da una regex, e la riga che conta e spesso una sola in
mezzo a cento: nel primo brief guardato, «Watermark must be visible throughout
the entire clip, not cropped or hidden by UI» sta alla riga 32 di 122 e
contraddice il ritaglio dinamico. Le righe evidenziate sopra il testo sono un
*marcatore*, come `[SPONSOR?]` in `windows.py`: segnalano dove guardare, non
decidono niente, e il testo integrale resta sotto perche nessuna parola vada
persa in silenzio.

**Questo script non decide niente**, come `check_material.py`. Constata,
riferisce, e mette il risultato dove Claude guarda davvero: in cima alla vista
di `windows.py`.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

from common import PROJECT_ROOT, emit, log, read_json, write_json

RADAR_DIR = PROJECT_ROOT / "radar"
DETTAGLI = RADAR_DIR / "radar_dettagli.json"
ASSEGNAZIONI = RADAR_DIR / "assegnazioni.json"
BRIEFS = RADAR_DIR / "briefs"

# I nomi dei file dentro la cartella di lavoro. Solo nomi, mai percorsi
# assoluti: le cartelle di lavoro viaggiano fra macchine (vedi CLAUDE.md).
SCHEDA_FILE = "campagna.json"
BRIEF_FILE = "brief.txt"

# Cosa cerca il marcatore. Ogni voce e (bucket, regex, perche): il `perche` non
# e decorazione, e la ragione per cui quella riga cambia una decisione — senza,
# la riga evidenziata sembra arbitraria e viene saltata come tutte le altre.
#
# I due secchi sono separati perche rispondono a due domande diverse e in due
# momenti diversi: uno cambia *come si taglia e si renderizza*, l'altro cambia
# *cosa si scrive quando si pubblica*. Mescolarli vorrebbe dire far leggere le
# regole sulle didascalie a chi sta scegliendo i bordi di una clip.
REGOLE = (
    (
        "render",
        r"\bwatermark\b|\blogo\b|\bbranding\b",
        "il ritaglio 9:16 e il crop dinamico possono tagliarlo fuori",
    ),
    (
        "render",
        r"\b(?:min|max|minimum|maximum|at least|at most|under|over|"
        r"no longer than|no shorter than)\b[^.]{0,40}"
        r"\b(?:second|seconds|sec|minute|minutes)\b|"
        r"\b\d+\s*(?:-|to|and)\s*\d+\s*(?:second|seconds|sec|minute|minutes)\b",
        "vincolo di durata: cambia dove si mettono i bordi",
    ),
    # «caption» in questi brief vuol dire quasi sempre il **testo del post**, non
    # i sottotitoli: cercarlo nudo mandava nel secchio sbagliato la riga «your
    # caption must name Acme Show». I sottotitoli impressi si chiamano
    # on-screen, burned-in o subtitle, e solo quelli toccano il render.
    (
        "render",
        r"\bon-?screen\b|\bburn(?:ed|t)[- ]?in\b|\bsubtitle|\bhard-?sub",
        "testo impresso nel fotogramma: la skill lo fa gia, verifica stile e obbligo",
    ),
    (
        "render",
        r"\bb-?roll\b|\bsfx\b|\bsound effect",
        "materiale aggiunto: la skill non ne mette, controlla se e richiesto",
    ),
    # «only» sta prima o dopo il sostantivo, e in inglese scritto in fretta piu
    # spesso dopo: «must come from Episode 25 only». Cercando solo la prima
    # forma, la riga che dice da quale video si taglia finiva fra le regole
    # sulla didascalia perche' li' capitava anche la parola «caption».
    (
        "render",
        r"\b(?:use |from )?only\b[^.]{0,60}\b(?:link|video|episode|footage|clip)\b|"
        r"\b(?:link|video|episode|footage|clip)\b[^.]{0,40}\bonly\b|"
        r"\bfrom outside\b|\bofficial link\b",
        "vincolo sul sorgente: da quale video si puo tagliare",
    ),
    (
        "render",
        r"\baspect ratio\b|\b9:16\b|\bvertical(?:ly)?\b|\bfull ?screen\b",
        "formato di uscita",
    ),
    # Le regole sulla pubblicazione si prendono solo quando **obbligano**: senza
    # il verbo modale entravano anche i tre esempi di didascalia, che sono
    # ispirazione e non vincolo, e un marcatore che prende un quinto del
    # documento ha smesso di marcare.
    (
        "pubblicazione",
        r"@[A-Za-z0-9._]{3,}|"
        r"\b(?:must|need|needs|required|have to|has to|should)\b[^.]{0,50}"
        r"\b(?:tag|mention|name|credit|caption)\b|"
        r"\b(?:caption|hashtag|tag)\w*\b[^.]{0,50}"
        r"\b(?:must|required|needs?)\b",
        "cosa deve dire il testo del post",
    ),
    (
        "pubblicazione",
        r"\b\d+\s*%\+?\s*(?:of )?(?:your )?(?:views|audience)|\bdemographic",
        "da dove devono arrivare le views",
    ),
    (
        "pubblicazione",
        r"\bkeep your post up\b|\b\d+\s*days\b|\bengagement\b|"
        r"\bhidden likes\b|\bwill not get paid\b",
        "condizioni per essere pagati",
    ),
)

RIGA_VUOTA = re.compile(r"^[\s•\*\-✔❌⚠]*$")

# Le intestazioni di tabella («Watermark», «Caption», «Audience») ripetono da
# sole la parola che il marcatore cerca, e il vincolo vero sta nella riga dopo.
# Tenerle vuol dire evidenziare due righe per dirne una. Restano quelle corte
# che portano un dato — un handle o un numero — che vincolo lo sono davvero.
DATO = re.compile(r"@\w|\d")


def _e_intestazione(testo: str) -> bool:
    return len(testo.split()) < 3 and not DATO.search(testo)


# **I numeri di riga stampati sopra devono valere aprendo `brief.txt`.** Un
# riferimento che punta al posto sbagliato e' peggio che nessun riferimento,
# perche' si segue: si apre la riga 58 e si legge un'altra cosa. Percio' il
# brief si copia normalizzando i separatori, e da li' in poi si taglia sempre e
# solo su `\n` — `splitlines()` spezza anche su tabulazione verticale, form
# feed e i due separatori Unicode, che nessun editor tratta da fine riga.
#
# Sul brief guardato non ce n'era nessuno: 237 a capo veri e niente altro. La
# normalizzazione non e' quindi una correzione misurata, e' il vincolo tenuto
# esplicito — i brief li scrive un'altra persona esportando da Google Docs, e
# il primo che arriva con un `\r\n` non deve spostare tutti i riferimenti di
# una riga senza che nessuno se ne accorga.
A_CAPO = re.compile("\\r\\n|[\\r\\v\\f\\u2028\\u2029]")


def _normalizza(testo: str) -> str:
    return A_CAPO.sub("\n", testo).replace("﻿", "")


def _tutte(percorso: Path) -> list[dict]:
    dati = read_json(percorso) if percorso.exists() else []
    if isinstance(dati, dict):  # assegnazioni.json e un oggetto
        dati = dati.get("assegnazioni", [])
    return [d for d in dati if isinstance(d, dict)]


def scheda(campagna_id: str) -> dict | None:
    """La riga del catalogo per questa campagna, o None se il radar non la conosce."""
    return next((c for c in _tutte(DETTAGLI) if c.get("id") == campagna_id), None)


def assegnazione(campagna_id: str) -> dict:
    """Il giudizio scritto leggendo il brief: nicchia, forma, lingua, regola di pagina."""
    return next((a for a in _tutte(ASSEGNAZIONI) if a.get("id") == campagna_id), {})


def righe_da_leggere(brief: str) -> list[dict]:
    """Le righe del brief che toccano una decisione, col numero di riga e il perche.

    Marca, non decide: il brief integrale resta sotto. Una riga che soddisfa piu
    regole esce una volta sola, con la prima ragione che l'ha presa — ripeterla
    tre volte la renderebbe rumore invece che segnale.
    """
    viste: set[str] = set()
    fuori: list[dict] = []
    # `split("\n")` e non `splitlines()`: il brief e' gia' normalizzato, e
    # `splitlines()` ricomincerebbe a spezzare su caratteri che l'editor di chi
    # apre `brief.txt` non spezza — cioe' rimetterebbe in circolo i numeri di
    # riga sbagliati che la normalizzazione serve a togliere.
    for n, riga in enumerate(brief.split("\n"), 1):
        testo = riga.strip()
        if not testo or RIGA_VUOTA.match(testo) or testo.startswith("#") or _e_intestazione(testo):
            continue
        chiave = " ".join(testo.lower().split())
        if chiave in viste:
            continue
        for bucket, pattern, perche in REGOLE:
            if re.search(pattern, testo, re.I):
                viste.add(chiave)
                fuori.append({"riga": n, "dove": bucket, "testo": testo, "perche": perche})
                break
    return fuori


# Le regole che questa campagna aggiunge a quelle base. **Vuote di proposito**:
# le riempie chi legge il brief, non una regex. E lo stesso schema di
# `assegnazioni.json` e `decisions.json` — scritto leggendo, non calcolato —
# perche' tradurre «watermark must be visible throughout» in un parametro e' un
# giudizio, e un giudizio sbagliato qui non fallisce: consegna.
#
# `null` non vuol dire «nessuna regola», vuol dire **«nessuno ha ancora
# guardato»**: e' `letto_il` a separare i due casi, ed e' la differenza fra una
# campagna senza vincoli di durata e una il cui brief non e' stato aperto.
REGOLE_VUOTE = {
    "letto_il": None,
    "durata_min": None,
    "durata_max": None,
    "logo_richiesto": False,
    "logo": None,
    "cta_richiesta": False,
    "sottotitoli": None,
    "vincoli_render": [],
    "vincoli_pubblicazione": [],
}


def limiti(work: Path | str) -> tuple[float, float, list[str]]:
    """I limiti di durata veri per questa cartella: base **intersecati** con la campagna.

    **Una regola di campagna puo' solo stringere.** Se un brief accetta clip da
    dieci secondi, resta il minimo di venti: quello non e' un vincolo di formato
    che un committente possa allentare, e' la durata sotto cui un pensiero intero
    non ci sta (vedi `validate.MIN_DURATION`). Nell'altro verso invece si
    obbedisce: un tetto di trenta secondi diventa il tetto.

    Torna anche le note su cosa e' stato ristretto o ignorato, perche' un limite
    che cambia in silenzio e' un limite che nessuno sa di avere.
    """
    from validate import MAX_DURATION, MIN_DURATION

    dati = load(Path(work)) or {}
    regole = dati.get("regole") or {}
    note: list[str] = []
    minimo, massimo = MIN_DURATION, MAX_DURATION

    chiesto_min = regole.get("durata_min")
    if isinstance(chiesto_min, (int, float)):
        if chiesto_min > minimo:
            note.append(f"minimo alzato a {chiesto_min:.0f}s dalla campagna (base {minimo:.0f}s)")
            minimo = float(chiesto_min)
        elif chiesto_min < minimo:
            note.append(
                f"la campagna accetterebbe clip da {chiesto_min:.0f}s, ma il "
                f"minimo di {minimo:.0f}s resta: le regole base non si allentano"
            )

    chiesto_max = regole.get("durata_max")
    if isinstance(chiesto_max, (int, float)):
        if chiesto_max < massimo:
            note.append(
                f"tetto abbassato a {chiesto_max:.0f}s dalla campagna (base {massimo:.0f}s)"
            )
            massimo = float(chiesto_max)
        elif chiesto_max > massimo:
            note.append(
                f"la campagna arriverebbe a {chiesto_max:.0f}s, ma il tetto di {massimo:.0f}s resta"
            )

    return minimo, massimo, note


# I servizi di trasferimento: il committente carica il materiale e manda un
# link che **scade** — SwissTransfer e WeTransfer dopo pochi giorni — e che
# `ingest.py` non sa aprire. Nei brief del catalogo del 24 settembre 2026 ci
# sono 24 link `we.tl`. Dropbox no: yt-dlp lo scarica e il link non scade.
TRASFERIMENTI = re.compile(
    r"https?://(?:www\.)?(?:we\.tl|wetransfer\.com|swisstransfer\.com|transfernow\.net|"
    r"fromsmash\.com|filemail\.com)/[^\s\"'<>)\]]+",
    re.I,
)


def link_a_scadenza(brief: str, materiali: list[str]) -> list[str]:
    """I link di trasferimento fra brief e materiali, senza doppioni e in ordine."""
    visti: list[str] = []
    for url in TRASFERIMENTI.findall(brief) + [m for m in materiali if TRASFERIMENTI.match(m)]:
        url = url.rstrip(".,;")
        if url not in visti:
            visti.append(url)
    return visti


def stage(
    work: Path,
    campagna_id: str,
    brief: str | None = None,
    brief_da: str | None = None,
    fornitore: str | None = None,
    titolo: str | None = None,
) -> dict:
    """Aggancia la cartella di lavoro alla campagna e copia il brief accanto.

    Il brief si **copia**, non si linka: la cartella di lavoro deve restare
    leggibile su un'altra macchina e dopo che il radar ha riscritto il catalogo.
    Un brief cambia — e quando cambia, quello che ha guidato questa consegna e
    quello che sta qui dentro, non quello di oggi.

    **Le regole gia' scritte sopravvivono a un nuovo `stage` della stessa
    campagna**, e spariscono se la campagna cambia. Sono le due meta' della
    stessa richiesta: rilanciare `prepare` dopo un'interruzione non deve
    cancellare mezz'ora di lettura del brief, e una cartella riassegnata a un
    altro committente non deve portarsi dietro i vincoli del precedente.

    **Una campagna fuori dal catalogo entra dalla stessa porta** (24 settembre
    2026). Il radar legge un catalogo solo, e la consegna AltroFornitore del 17 aveva
    un `campagna.json` scritto a mano: titolo, CPM e piattaforme digitati, e
    indistinguibili da quelli presi da una scheda. Ora il brief arriva da
    `brief` (un file o del testo incollato), `origine` dice se c'era una
    scheda, e fuori catalogo i numeri restano vuoti: stanno nel brief copiato,
    che e' la fonte, e un numero ricopiato a mano e' un numero che nessuno
    rilegge. Il brief gia' copiato sopravvive a un nuovo `stage` come le regole.
    """
    s = scheda(campagna_id) or {}
    a = assegnazione(campagna_id)

    prima = load(work) or {}
    stessa = prima.get("id") == campagna_id
    regole = dict(REGOLE_VUOTE)
    if stessa and isinstance(prima.get("regole"), dict):
        regole.update(prima["regole"])

    sorgente_brief = BRIEFS / f"{campagna_id}.txt"
    gia_copiato = work / BRIEF_FILE
    testo, da = "", None
    if brief is not None:
        testo, da = _normalizza(brief), brief_da or "incollato"
    elif sorgente_brief.exists():
        testo, da = (
            _normalizza(sorgente_brief.read_text(encoding="utf-8", errors="replace")),
            "radar",
        )
    elif stessa and prima.get("brief") and gia_copiato.exists():
        testo = gia_copiato.read_text(encoding="utf-8", errors="replace")
        da = prima.get("brief_da") or "gia' nella cartella"
    if testo:
        gia_copiato.write_text(testo, encoding="utf-8", newline="\n")

    materiali = s.get("materiali") or []
    dati = {
        "id": campagna_id,
        "titolo": s.get("titolo") or titolo or (stessa and prima.get("titolo")) or campagna_id,
        "origine": "catalogo" if s else "fuori catalogo",
        "fornitore": (
            fornitore or s.get("provider") or (prima.get("fornitore") if stessa else None)
        ),
        "organizzazione": s.get("organizzazione"),
        "descrizione": s.get("descrizione"),
        "piattaforme": s.get("piattaforme") or [],
        "cpm": s.get("cpm"),
        "tetto_clip": s.get("tetto_clip"),
        "join": s.get("join"),
        "materiali": materiali,
        "materiali_dett": s.get("materiali_dett") or [],
        "da_scaricare_a_mano": link_a_scadenza(testo, materiali),
        # Dal giudizio gia scritto leggendo il brief, non ridedotto qui.
        "nicchia": a.get("nicchia"),
        "sottonicchia": a.get("sottonicchia"),
        "forma": a.get("forma"),
        "lingua": a.get("lingua"),
        "pagina": a.get("pagina"),
        "pagina_nota": a.get("pagina_nota"),
        # Solo il nome: dentro la cartella di lavoro non si scrivono percorsi
        # assoluti, perche la cartella viaggia e il percorso no.
        "brief": BRIEF_FILE if testo else None,
        "brief_da": da,
        "brief_righe": len(testo.split("\n")) if testo else 0,
        "da_leggere": righe_da_leggere(testo),
        "regole": regole,
        "agganciata_il": datetime.now().isoformat(timespec="seconds"),
        "radar_conosce": bool(s),
    }
    # Gli allegati grafici si scaricano **adesso**, non quando servono: se il
    # brief chiede un logo, chi legge deve poterlo nominare avendolo gia' sotto
    # gli occhi. Scaricarli al momento del render vorrebbe dire scoprire li' che
    # il link e' morto, dopo aver gia' scelto tutto.
    dati["allegati"] = scarica_materiali(work, dati)
    write_json(work / SCHEDA_FILE, dati)
    return dati


def load(work: Path) -> dict | None:
    """I paletti gia agganciati a questa cartella, o None se non ce ne sono."""
    percorso = Path(work) / SCHEDA_FILE
    return read_json(percorso) if percorso.exists() else None


IMMAGINE = re.compile(r"\.(png|jpe?g|webp)(\?|$)", re.I)


def scarica_materiali(work: Path, dati: dict) -> list[str]:
    """Gli allegati grafici della campagna, portati nella cartella di lavoro.

    Sono i candidati per il watermark: quando un brief chiede di imprimere il
    logo del committente, il file e' quasi sempre gia' allegato alla campagna.
    Scaricarli qui vuol dire che `regole.logo` puo' nominarne uno e
    `common.resolve_logo()` lo trova al primo posto in cui guarda.

    **Si scaricano solo quelli riconoscibili come immagine dal link.** Le
    campagne allegano anche documenti, cartelle Drive e trasferimenti WeTransfer:
    seguirli tutti vorrebbe dire scrivere un downloader per ogni servizio, e
    fallire in silenzio su quelli che non funzionano. Quello che non si riesce a
    prendere torna nella lista degli scartati, che il chiamante stampa — un
    allegato non scaricato deve restare visibile, non sparire.

    Non decide **se** usare un logo: quello sta in `regole`, e lo scrive chi
    legge il brief.
    """
    import urllib.error
    import urllib.request

    presi: list[str] = []
    for m in dati.get("materiali_dett") or []:
        url = m.get("url") or ""
        if m.get("tipo") != "image" and not IMMAGINE.search(url):
            continue
        nome = re.sub(r"[^A-Za-z0-9._-]", "_", url.rsplit("/", 1)[-1].split("?")[0])
        if not IMMAGINE.search(nome):
            nome += ".png"
        nome = f"campagna-{nome}"[:80]
        destinazione = work / nome
        if destinazione.exists():
            presi.append(nome)
            continue
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                dati_immagine = r.read(20 * 1024 * 1024)
            destinazione.write_bytes(dati_immagine)
            presi.append(nome)
        except (urllib.error.URLError, OSError, ValueError) as e:
            log(f"allegato non scaricato ({e}): {url[:90]}")
    return presi


def regole_in_chiaro(work: Path, dati: dict) -> list[str]:
    """Le regole in vigore per questa consegna, o l'avviso che nessuno le ha scritte.

    **L'assenza si stampa piu forte della presenza.** Una campagna con le regole
    compilate si riassume in quattro righe; una con `letto_il` a `null` e' una
    consegna che sta per partire con i vincoli del committente ancora chiusi nel
    documento, ed e' quello il momento in cui serve un avviso — non dopo.
    """
    regole = dati.get("regole") or {}
    if not regole.get("letto_il"):
        return [
            "",
            "  REGOLE DELLA CAMPAGNA: NON ANCORA SCRITTE.",
            "    Il brief e' qui sotto ma nessuno ne ha ancora ricavato i vincoli."
            " Leggilo e compila",
            f"    il blocco `regole` di {SCHEDA_FILE} — durata, logo, sottotitoli,"
            " vincoli in chiaro —",
            "    prima di scegliere: valgono solo per questa campagna e"
            " `validate.py` le fa rispettare.",
        ]

    minimo, massimo, note = limiti(work)
    righe = [
        "",
        f"  REGOLE DI QUESTA CAMPAGNA (scritte il {regole['letto_il']}) — valgono solo qui:",
        f"    durata: da {minimo:.0f}s a {massimo:.0f}s",
    ]
    for n in note:
        righe.append(f"      · {n}")
    if regole.get("logo_richiesto"):
        righe.append(
            "    logo impresso: richiesto"
            + (
                f", «{regole['logo']}»"
                if regole.get("logo")
                else " — MA NON E' STATO INDICATO QUALE FILE"
            )
        )
    if regole.get("cta_richiesta"):
        righe.append("    richiamo al video intero (cta): richiesto")
    if regole.get("sottotitoli"):
        righe.append(f"    sottotitoli: {regole['sottotitoli']}")
    for v in regole.get("vincoli_render") or []:
        righe.append(f"    vincolo sul taglio: {v}")
    for v in regole.get("vincoli_pubblicazione") or []:
        righe.append(f"    vincolo sulla pubblicazione: {v}")
    return righe


def as_text(work: Path) -> str:
    """I paletti in chiaro, per la testa della vista di `windows.py`.

    Stringa vuota quando la cartella non e agganciata a nessuna campagna: un
    video adottato a mano non ha un committente, e inventargli dei paletti
    sarebbe peggio che non averne.
    """
    dati = load(work)
    if not dati:
        return ""

    intestazione = [f"PALETTI DELLA CAMPAGNA — {dati['titolo']}"]
    riga = [
        x
        for x in (
            dati.get("organizzazione"),
            f"CPM ${dati['cpm']:g}" if dati.get("cpm") else None,
            f"tetto ${dati['tetto_clip']:g} a clip" if dati.get("tetto_clip") else None,
            ", ".join(dati.get("piattaforme") or []) or None,
        )
        if x
    ]
    if riga:
        intestazione.append("  " + " · ".join(riga))
    contesto = [
        x
        for x in (
            f"pagina: {dati['pagina']}" if dati.get("pagina") else None,
            f"lingua: {dati['lingua']}" if dati.get("lingua") else None,
            f"forma: {dati['forma']}" if dati.get("forma") else None,
        )
        if x
    ]
    if contesto:
        intestazione.append("  " + " · ".join(contesto))
    if dati.get("origine") == "fuori catalogo":
        intestazione.append(
            f"  FUORI CATALOGO{' (' + dati['fornitore'] + ')' if dati.get('fornitore') else ''}:"
            " nessuna scheda del radar, quindi niente CPM, tetto ne piattaforme qui sopra."
            " I numeri veri stanno nel brief."
        )
    if dati.get("da_scaricare_a_mano"):
        intestazione.append(
            "  MATERIALE DA SCARICARE A MANO, ADESSO: link di trasferimento,"
            " scadono in pochi giorni e ingest.py non li apre."
        )
        intestazione += [f"    {u}" for u in dati["da_scaricare_a_mano"]]

    intestazione += regole_in_chiaro(work, dati)

    if not dati.get("brief"):
        intestazione.append(
            "  ATTENZIONE: di questa campagna non c'e nessun brief, ne in radar/briefs/ ne"
            " passato con --brief. I vincoli del committente non sono qui: aprili prima"
            " di scegliere."
        )
        if dati.get("materiali"):
            intestazione.append("  materiali della campagna: " + "  ".join(dati["materiali"][:3]))
        return "\n".join(intestazione)

    corpo = (Path(work) / dati["brief"]).read_text(encoding="utf-8", errors="replace")

    for bucket, titolo in (
        ("render", "Righe che toccano il RENDER e i BORDI"),
        ("pubblicazione", "Righe che toccano la PUBBLICAZIONE"),
    ):
        prese = [r for r in dati.get("da_leggere", []) if r["dove"] == bucket]
        if not prese:
            continue
        intestazione += ["", f"  {titolo}:"]
        for r in prese:
            intestazione.append(f"    [riga {r['riga']:>3}] {r['testo']}")
            intestazione.append(f"               ^ {r['perche']}")

    intestazione += [
        "",
        f"  Il brief per intero ({dati['brief_righe']} righe"
        f"{', da: ' + dati['brief_da'] if dati.get('brief_da') else ''}), come l'ha scritto il "
        f"committente — le righe sopra sono solo un marcatore, non un riassunto:",
        "  " + "-" * 68,
    ]
    intestazione += ["  " + r for r in corpo.split("\n")]
    intestazione.append("  " + "-" * 68)
    return "\n".join(intestazione)


def aggiungi_argomenti_fuori_catalogo(ap: argparse.ArgumentParser) -> None:
    """Gli argomenti per una campagna che il radar non conosce, in un posto solo:
    li accettano sia questo script sia `run.py prepare`."""
    ap.add_argument(
        "--brief",
        default=None,
        help="il brief del committente: un file di testo, o '-' per incollarlo "
        "da standard input. Serve quando il radar non l'ha scaricato",
    )
    ap.add_argument(
        "--fornitore", default=None, help="chi gestisce la campagna fuori catalogo (es. altrofornitore)"
    )
    ap.add_argument(
        "--titolo",
        default=None,
        help="titolo della campagna fuori catalogo; nel catalogo vince la scheda",
    )


def da_argomenti(args: argparse.Namespace) -> dict:
    """Da `--brief`, `--fornitore`, `--titolo` agli argomenti di `stage`."""
    fuori = {"fornitore": args.fornitore, "titolo": args.titolo}
    if args.brief == "-":
        return fuori | {"brief": sys.stdin.read(), "brief_da": "incollato"}
    if args.brief:
        percorso = Path(args.brief)
        if not percorso.is_file():
            raise SystemExit(f"--brief: {percorso} non esiste")
        return fuori | {
            "brief": percorso.read_text(encoding="utf-8", errors="replace"),
            "brief_da": f"file {percorso.name}",
        }
    return fuori


def main() -> None:
    ap = argparse.ArgumentParser(
        description="I paletti della campagna, agganciati alla cartella di lavoro"
    )
    ap.add_argument("work_dir")
    ap.add_argument(
        "--id",
        help="id della campagna: l'uuid del catalogo del radar, o "
        "quello del fornitore per una campagna fuori catalogo",
    )
    aggiungi_argomenti_fuori_catalogo(ap)
    args = ap.parse_args()

    work = Path(args.work_dir)
    if not work.is_dir():
        raise SystemExit(f"{work} non esiste")

    if args.id:
        dati = stage(work, args.id, **da_argomenti(args))
        if not dati["radar_conosce"]:
            log(
                f"il radar non conosce {args.id}: aggancio fuori catalogo, "
                f"senza CPM ne piattaforme — stanno nel brief"
            )
        if not dati["brief"]:
            log(f"nessun brief: ne in {BRIEFS / (args.id + '.txt')} ne passato con --brief")
    else:
        dati = load(work)
        if not dati:
            raise SystemExit(f"{work} non e agganciata a nessuna campagna: passa --id <uuid>")

    testo = as_text(work)
    if testo:
        log(testo)
    emit(
        {k: v for k, v in dati.items() if k != "da_leggere"}
        | {"da_leggere": len(dati.get("da_leggere", []))}
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
