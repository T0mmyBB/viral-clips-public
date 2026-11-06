"""Controlla `decisions.json` prima che il render ci lavori sopra.

    python validate.py <work_dir>

**Perche esiste.** `decisions.json` lo scrive Claude, non uno script: e l'unico file
della pipeline prodotto da un modello invece che da codice deterministico. Un campo
mancante, un timestamp invertito o un punteggio inventato si scoprirebbero altrimenti a
meta di un render da diversi minuti. Questo e il cancello che li intercetta prima.

**Cosa NON fa: ordinare.** Le clip in `decisions.json` sono gia nell'ordine in cui sono
state scelte, e quello e l'ordine editoriale. Riordinarle con un punteggio pesato
sostituirebbe il giudizio con dell'aritmetica — il contrario dello scopo del progetto.
Per prendere le migliori N si prendono le prime N.

**Due livelli di regole, e non si mescolano.** Quelle di questo file valgono per ogni
clip che esce da qui. Quelle della campagna — durata, logo, sottotitoli — vivono in
`work/<slug>/campagna.json`, valgono **solo per quella consegna**, e spariscono con la
cartella: il committente successivo ha le sue. Il cancello le interseca, e
l'intersezione va in una direzione sola: una campagna puo stringere una regola base, mai
allentarla (`campagna.limiti()`).
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import campagna
from common import OUTPUT_DIR, emit, fmt_timecode, log, read_json, resolve_logo, video_info
from render_clip import TARGET_H, TARGET_W

EMPHASIS_RE = re.compile(r"\*([^*]+)\*")

# Le sei dimensioni della rubrica (references/viral-heuristics.md). I bordi non ci sono
# di proposito: non sono una dimensione, sono un controllo da fare prima del render.
DIMENSIONS = ("hook", "autosufficienza", "arco", "carica", "concretezza", "attrito")
LEVELS = ("debole", "solido", "forte")

# **Il minimo e una richiesta esplicita dell'utente, e ha una ragione editoriale.**
# Le clip da 10-15 secondi non risultavano autoconclusive: in quindici secondi
# quasi sempre non c'e una premessa, uno sviluppo e una chiusura, c'e un
# frammento che *sembra* completo a chi conosce il video attorno. Il minimo non
# e una preferenza di formato, e il tempo sotto il quale un pensiero intero non
# ci sta.
#
# Il tetto invece **non e una politica**: una clip puo superare i 40 secondi se
# il momento li chiede. Serve solo a intercettare l'errore grosso, il bordo
# sbagliato di un fattore dieci — allineato al limite reale di YouTube Shorts
# (video caricati dal 15/10/2024, fino a 3 minuti), non a un vecchio 60s.
MIN_DURATION = 20.0
MAX_DURATION = 180.0

# Sopra il minuto, una rivendicazione di copyright attiva su musica di terzi
# blocca lo Short a livello globale e lo demonetizza. Non riguarda la voce:
# riguarda solo un eventuale sottofondo musicale del sorgente. E un rischio,
# non un limite di formato — Shorts accetta comunque fino a 3 minuti.
COPYRIGHT_RISK_LIMIT = 60.0

OVERLAP_THRESHOLD = 0.35  # oltre questa sovrapposizione e lo stesso momento

# Il cancello della consegna guarda anche i file, non solo `clips.md` che li
# nomina. Misurato sulle 111 clip consegnate al 24 settembre 2026: la piu
# leggera pesa 8,7 MB, la piu corta dura 20,02 s, tutte 1080x1920 con audio.
# Un megabyte sta nove volte sotto la piu leggera: prende il file vuoto o
# troncato, non la clip corta. Il mezzo secondo sulla durata assorbe la griglia
# dei fotogrammi e i bordi dei tagli dei silenzi, che non scendono sotto il
# minimo ma non ci cadono al millesimo.
#
# Il troncamento non lo vede ne il peso ne la sonda: con `+faststart` l'indice
# sta in testa, e un file tagliato a 3 MB su 35 si sonda come intero. Lo vede il
# rapporto fra i byte che l'indice attribuisce alle tracce e il peso vero: sulle
# 111 clip fra 0,998 e 1,001, sulla copia tagliata di prova 14.
MIN_BYTES_CLIP = 1_000_000
TOLLERANZA_DURATA = 0.5
ECCESSO_DICHIARATO = 1.05

REQUIRED = ("slug", "start", "end", "hook", "why")


def overlap_ratio(a: dict, b: dict) -> float:
    """Quanto due clip si sovrappongono, sulla piu corta delle due."""
    lo = max(a["start"], b["start"])
    hi = min(a["end"], b["end"])
    if hi <= lo:
        return 0.0
    shorter = min(a["end"] - a["start"], b["end"] - b["start"])
    return (hi - lo) / shorter if shorter > 0 else 0.0


def duration_warnings(clip: dict) -> list[str]:
    """Avvisi sulla durata: legittima, ma da sapere.

    Separati dagli errori di proposito: una clip sopra il minuto non e
    sbagliata, ha solo un rischio da verificare prima di pubblicarla.
    """
    start, end = clip.get("start"), clip.get("end")
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return []
    length = end - start
    if length > COPYRIGHT_RISK_LIMIT:
        return [
            f"{clip.get('slug')}: dura {length:.1f}s, oltre i "
            f"{COPYRIGHT_RISK_LIMIT:.0f}s - verifica che non ci sia musica di terzi "
            f"nel sottofondo del sorgente, altrimenti rischia il blocco globale"
        ]
    return []


def check_clip(
    clip: dict,
    index: int,
    duration: float | None,
    minimo: float = MIN_DURATION,
    massimo: float = MAX_DURATION,
) -> list[str]:
    """Errori bloccanti di una singola clip.

    `minimo` e `massimo` arrivano gia intersecati con le regole della campagna
    (`campagna.limiti()`): qui non si sa e non serve sapere se un limite venga
    dalla politica di questo file o dal brief di un committente.
    """
    errors: list[str] = []
    label = clip.get("slug") or f"clip #{index + 1}"

    for field in REQUIRED:
        if field not in clip or clip[field] in (None, ""):
            errors.append(f"{label}: manca il campo '{field}'")

    start, end = clip.get("start"), clip.get("end")
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        errors.append(f"{label}: start ed end devono essere numeri")
        return errors

    if end <= start:
        errors.append(f"{label}: end ({end}) non e dopo start ({start})")
        return errors

    length = end - start
    if length < minimo:
        errors.append(
            f"{label}: dura {length:.1f}s, sotto il minimo di {minimo:.0f}s. "
            f"In meno di cosi non ci sta un pensiero intero: allarga i bordi fino a "
            f"comprendere premessa e chiusura, oppure scartala"
        )
    if length > massimo:
        errors.append(
            f"{label}: dura {length:.1f}s, oltre i {massimo:.0f}s. "
            + (
                "Non e un limite di stile: a quella lunghezza e quasi sempre un bordo "
                "sbagliato, non un momento lungo"
                if massimo == MAX_DURATION
                else "E il tetto che chiede questa campagna: sopra, la clip viene rifiutata"
            )
        )

    # `"tagli": "false"` e una stringa non vuota, cioe vera: i silenzi verrebbero
    # tolti lo stesso, e nessuno se ne accorgerebbe prima di guardare la clip.
    if "tagli" in clip and not isinstance(clip["tagli"], bool):
        errors.append(f"{label}: 'tagli' vale {clip['tagli']!r}, attesi true o false")

    # Le grafiche: modello, campi e lunghezze qui; istante e accavallamenti li
    # rivede `grafiche.piano` al render, quando conosce i tagli dei silenzi.
    if "grafiche" in clip:
        voci = clip["grafiche"]
        if not isinstance(voci, list):
            errors.append(f"{label}: 'grafiche' dev'essere un elenco")
        else:
            from grafiche import difetti as difetti_grafica

            for i, g in enumerate(voci):
                if not isinstance(g, dict):
                    errors.append(f"{label}: grafica {i + 1} non e un oggetto")
                    continue
                errors.extend(f"{label}: {e}" for e in difetti_grafica(g, i))
                at = g.get("at")
                if isinstance(at, (int, float)) and not start <= at < end:
                    errors.append(
                        f"{label}: grafica {i + 1} a {at:.1f}s, fuori dalla clip "
                        f"({start:.1f}-{end:.1f}s)"
                    )

    if duration and end > duration:
        errors.append(f"{label}: finisce a {end:.1f}s ma il video dura {duration:.1f}s")
    if start < 0:
        errors.append(f"{label}: start negativo ({start})")

    scores = clip.get("scores")
    if not isinstance(scores, dict):
        errors.append(f"{label}: manca 'scores' o non e un oggetto")
    else:
        for dim in DIMENSIONS:
            if dim not in scores:
                errors.append(f"{label}: punteggio mancante per '{dim}'")
            elif scores[dim] not in LEVELS:
                errors.append(f"{label}: '{dim}' vale '{scores[dim]}', attesi {' / '.join(LEVELS)}")
        for extra in set(scores) - set(DIMENSIONS):
            errors.append(
                f"{label}: dimensione sconosciuta '{extra}'. "
                f"La rubrica ne prevede sei: {', '.join(DIMENSIONS)}"
            )

    return errors


# Quanto un bordo puo distare da un confine di segmento per dirsi "posato li".
TOLLERANZA_BORDO = 0.12
# Quanto deve entrare dentro una parola perche sia davvero tagliata.
DENTRO_LA_PAROLA = 0.06


def check_bordi(clips: list[dict], transcript: dict) -> tuple[list[str], list[str]]:
    """I bordi cadono dove finisce un pensiero, o tagliano a meta?

    **Esiste perche e gia successo, su tutte e trentotto le clip di una
    consegna.** I bordi erano stati presi dai timestamp dei segmenti letti nella
    vista, arrotondati al secondo: sembravano giusti e invece amputavano
    l'ultima parola della frase — "gli sono salito totalmente sopra la", senza
    'testa'. Non se ne accorge nessuno finche non guarda le clip, e a quel punto
    sono gia state renderizzate tutte.

    Due controlli di natura diversa. Il bordo che cade **dentro una parola** e un
    errore e basta: nessuna scelta editoriale vuole meta parola. Il bordo che
    non posa su un **confine di segmento** e solo un avviso, perche a volte si
    taglia di proposito dove Whisper non ha segmentato.
    """
    errori: list[str] = []
    avvisi: list[str] = []

    parole = transcript.get("words") or []
    segmenti = transcript.get("segments") or []
    if not parole or not segmenti:
        return errori, avvisi

    inizi = sorted(float(s["start"]) for s in segmenti)
    fini = sorted(float(s["end"]) for s in segmenti)

    for clip in clips:
        inizio, fine = clip.get("start"), clip.get("end")
        if not isinstance(inizio, (int, float)) or not isinstance(fine, (int, float)):
            continue
        label = clip.get("slug", "?")

        for bordo, come in ((inizio, "inizia"), (fine, "finisce")):
            dentro = next(
                (
                    w
                    for w in parole
                    if w["start"] + DENTRO_LA_PAROLA < bordo < w["end"] - DENTRO_LA_PAROLA
                ),
                None,
            )
            if dentro:
                errori.append(
                    f"{label}: {come} a {bordo:.2f}s, in mezzo alla parola "
                    f"{dentro['word']!r} ({dentro['start']:.2f}-{dentro['end']:.2f}). "
                    f"Aggancia il bordo alla parola, non al secondo"
                )

        if not any(abs(fine - f) < TOLLERANZA_BORDO for f in fini):
            avvisi.append(
                f"{clip.get('slug')}: finisce a {fine:.2f}s, che non e la fine di "
                f"nessun segmento - rischia di chiudere mentre si sta gia dicendo altro"
            )
        if not any(abs(inizio - i) < TOLLERANZA_BORDO for i in inizi):
            avvisi.append(
                f"{clip.get('slug')}: inizia a {inizio:.2f}s, che non e l'inizio di nessun segmento"
            )

    return errori, avvisi


def check_all(
    decisions: dict,
    duration: float | None,
    minimo: float = MIN_DURATION,
    massimo: float = MAX_DURATION,
) -> tuple[list[str], list[str]]:
    """Ritorna (errori bloccanti, avvisi)."""
    clips = decisions.get("clips")
    if not isinstance(clips, list) or not clips:
        return ["decisions.json non contiene nessuna clip in 'clips'"], []

    errors: list[str] = []
    warnings: list[str] = []

    if "tagli" in decisions and not isinstance(decisions["tagli"], bool):
        errors.append(f"'tagli' in cima al file vale {decisions['tagli']!r}, attesi true o false")

    for i, clip in enumerate(clips):
        errors.extend(check_clip(clip, i, duration, minimo, massimo))
        warnings.extend(duration_warnings(clip))

    slugs = [c.get("slug") for c in clips if c.get("slug")]
    for slug in {s for s in slugs if slugs.count(s) > 1}:
        errors.append(f"slug duplicato: '{slug}' - i file di uscita si sovrascriverebbero")

    # Le sovrapposizioni sono un avviso, non un errore: a volte due tagli diversi
    # dello stesso tratto sono voluti. Ma quasi sempre e una svista.
    valid = [
        c
        for c in clips
        if isinstance(c.get("start"), (int, float))
        and isinstance(c.get("end"), (int, float))
        and c["end"] > c["start"]
    ]
    for i, a in enumerate(valid):
        for b in valid[i + 1 :]:
            ratio = overlap_ratio(a, b)
            if ratio > OVERLAP_THRESHOLD:
                warnings.append(
                    f"'{a.get('slug')}' e '{b.get('slug')}' si sovrappongono al "
                    f"{ratio:.0%}: e lo stesso momento tagliato due volte?"
                )

    for clip in clips:
        hook = clip.get("hook", "")
        if len(hook) > 60:
            warnings.append(
                f"{clip.get('slug')}: hook di {len(hook)} caratteri, oltre i ~55 "
                f"consigliati - rischia di occupare piu di tre righe"
            )
        accenti = hook.count("*")
        # Il controllo guardava solo che gli accenti non fossero troppi, quindi
        # **zero** passava in silenzio: un hook senza parola gialla non e una
        # scelta di stile, e una marcatura dimenticata, e si scopre guardando la
        # clip renderizzata. Sulle 152 decisioni gia scritte non e mai successo —
        # questo non ripara un difetto presente, impedisce che diventi possibile.
        if hook and accenti == 0:
            errors.append(
                f"{clip.get('slug')}: hook senza nessuna parola fra *asterischi*, "
                f"quindi senza accento giallo"
            )
        elif accenti % 2:
            # Un asterisco spaiato non apre niente: `make_subs.EMPHASIS_RE` cerca
            # coppie, quindi il carattere resta e finisce impresso nella clip.
            errors.append(
                f"{clip.get('slug')}: asterisco spaiato nell'hook, resterebbe "
                f"stampato a schermo com'e"
            )
        if accenti > 2:
            warnings.append(
                f"{clip.get('slug')}: piu di una parola accentata nell'hook, l'effetto si annulla"
            )

    return errors, warnings


# Le parole con cui un brief dice che qualcosa di *suo* sta dentro il fotogramma del
# sorgente e non va perso. Larghe apposta: e un avviso, e chi legge decide.
MARCHI_NEL_SORGENTE_RE = re.compile(r"watermark|filigran|logo|handle|banner", re.IGNORECASE)


def avviso_ritaglio(work: Path) -> str | None:
    """Il 9:16 di un sorgente orizzontale butta i lati, e il brief dice di non perderli.

    Nessuno script sa se il watermark sta al centro o ai bordi: **non e un errore**,
    e non finge di controllare. Ma senza questo avviso il vincolo restava una frase
    ristampata in fondo, e su una consegna da 17 clip il logo e la fascia con gli
    handle sono spariti da tutte e 17 senza che nulla lo dicesse (osservazione 131).
    Scatta solo se `vincoli_render` nomina un marchio e il ritaglio tiene meno della
    larghezza; su un sorgente gia verticale non c'e niente da perdere.
    """
    regole = (campagna.load(work) or {}).get("regole") or {}
    citati = [v for v in regole.get("vincoli_render") or [] if MARCHI_NEL_SORGENTE_RE.search(v)]
    meta = read_json(work / "meta.json") if (work / "meta.json").exists() else {}
    larghezza, altezza = int(meta.get("width") or 0), int(meta.get("height") or 0)
    if not citati or not larghezza or not altezza:
        return None
    quota = min(1.0, altezza * TARGET_W / TARGET_H / larghezza)
    if quota >= 1.0:
        return None
    return (
        f"il ritaglio 9:16 tiene il {quota:.0%} della larghezza del sorgente e il brief "
        f"chiede: «{citati[0]}». Guarda un fotogramma del sorgente e uno della clip "
        f"prima di renderizzare tutto: quello che sta ai lati non c'e piu."
    )


def regole_campagna(work: Path, decisions: dict) -> list[str]:
    """Quello che la campagna pretende e `decisions.json` deve gia contenere.

    Sono errori e non avvisi per la stessa ragione per cui lo e un logo
    dichiarato e assente: una consegna che viola una condizione del committente
    non e una consegna riuscita male, e' lavoro da rifare — e lo si scopre dopo
    aver renderizzato tutto, o peggio dopo aver pubblicato.

    Qui si controlla **solo cio' che ha una risposta meccanica**. «Il watermark
    non dev'essere tagliato» resta una frase in `vincoli_render`: nessun campo
    di `decisions.json` risponde a quella domanda, e fingere di controllarla
    sarebbe peggio che dichiarare di non farlo.
    """
    dati = campagna.load(work)
    if not dati:
        return []
    regole = dati.get("regole") or {}
    if not regole.get("letto_il"):
        # Non e' un errore: si consegna anche senza, come si e' sempre fatto. Ma
        # deve costare una riga di avviso ogni volta, perche' e' il caso in cui
        # si sta per renderizzare con i vincoli del committente mai aperti.
        log(
            f"  avviso: campagna «{dati['titolo']}» agganciata, ma il blocco `regole` "
            f"di campagna.json e' vuoto: nessuno ha ricavato i vincoli dal brief"
        )
        return []

    errori: list[str] = []
    if regole.get("logo_richiesto") and not decisions.get("logo"):
        errori.append(
            f"la campagna «{dati['titolo']}» pretende un logo impresso, ma "
            f"decisions.json non dichiara nessun campo 'logo'"
            + (f" (il brief indica «{regole['logo']}»)" if regole.get("logo") else "")
        )
    if regole.get("cta_richiesta") and not decisions.get("cta"):
        errori.append(
            f"la campagna «{dati['titolo']}» pretende un richiamo al video intero, "
            f"ma decisions.json non dichiara nessun campo 'cta'"
        )
    return errori


def difetto_clip(f: Path, sonda=video_info) -> str | None:
    """Perche questo mp4 non si puo caricare, o `None` se si puo.

    Il peso si guarda per primo e senza ffprobe: un file vuoto non ha niente da
    sondare. Poi la sonda, che fallisce da sola su un render interrotto: l'indice
    si scrive per ultimo, e senza indice il file non si apre. Un file troncato
    *dopo* — una copia a meta — si apre invece come intero, e lo tradiscono solo
    i byte che l'indice dichiara e il file non ha. Il resto sono le regole base:
    1080x1920, audio, almeno il minimo di durata. `sonda` si sostituisce nei test.
    """
    peso = f.stat().st_size
    if peso < MIN_BYTES_CLIP:
        return f"{f.name}: {peso} byte, vuoto o troncato"
    try:
        info = sonda(f)
    except Exception as e:  # qualunque modo di non leggerlo e lo stesso esito
        return f"{f.name}: illeggibile ({type(e).__name__}: {e})"
    dichiarati = info.get("stream_bytes")
    if dichiarati and dichiarati > peso * ECCESSO_DICHIARATO:
        return (
            f"{f.name}: troncato, l'indice dichiara {dichiarati / 1e6:.1f} MB "
            f"e il file ne ha {peso / 1e6:.1f}"
        )
    if (info["width"], info["height"]) != (TARGET_W, TARGET_H):
        return f"{f.name}: {info['width']}x{info['height']}, non {TARGET_W}x{TARGET_H}"
    if not info["has_audio"]:
        return f"{f.name}: nessuna traccia audio"
    if info["duration"] < MIN_DURATION - TOLLERANZA_DURATA:
        return f"{f.name}: dura {info['duration']:.2f} s, sotto il minimo di {MIN_DURATION:.0f}"
    return None


def check_consegna(cartella: Path, sonda=video_info) -> list[str]:
    """La consegna e completa: gli mp4, caricabili, **e** il `clips.md` che li spiega.

    **Esiste perche la meta scritta a mano non la pretendeva nessuno.** Tutti i
    controlli guardano cio che produce il codice — durate, bordi, sincronia — e
    il documento che dice cosa sono le clip, con punteggi, didascalie e perche,
    lo scrive Claude dopo il render. Alla prima consegna per una campagna vera e
    mancato, e l'unico avviso era l'ultima riga di un output lungo, in un
    processo in background: scoperto per caso, elencando la cartella per altro.

    **E i file si guardano, non solo i loro nomi** (24 settembre 2026). Il 22
    una cartella di sette mp4 da zero byte ha passato il cancello — «consegna
    completa: 7 clip» — perche `clips.md` li citava tutti: la lista era completa,
    la consegna no. Didascalie e calendario sarebbero stati scritti per file che
    non si caricano, e il guasto sarebbe arrivato dall'agente di pubblicazione,
    dentro la finestra d'invio di trenta minuti. Per ogni file, `difetto_clip`.

    Controlla solo cio che ha una risposta meccanica. Un file citato non
    garantisce che la sua scheda sia scritta bene; un file non citato garantisce
    che manca. Misurato sulle 111 clip consegnate: zero falsi allarmi.
    """
    mp4 = sorted(cartella.glob("*.mp4"))
    if not mp4:
        return [f"nessuna clip in {cartella}: niente da consegnare"]
    problemi = [d for f in mp4 if (d := difetto_clip(f, sonda))]
    doc = cartella / "clips.md"
    if not doc.exists():
        return problemi + [
            f"manca {doc.name} accanto ai {len(mp4)} mp4 di {cartella.name}: "
            f"lo scrive Claude, passo 8 di SKILL.md"
        ]
    testo = doc.read_text(encoding="utf-8")
    assenti = [f.name for f in mp4 if f.stem not in testo]
    if assenti:
        problemi.append(
            f"{doc.name} non cita {len(assenti)} clip su {len(mp4)}: {', '.join(assenti)}"
        )
    return problemi


def main() -> None:
    ap = argparse.ArgumentParser(description="Valida decisions.json prima del render")
    ap.add_argument("work_dir")
    ap.add_argument("--decisions", default="decisions.json")
    ap.add_argument(
        "--consegna",
        action="store_true",
        help="dopo il render: controlla che gli mp4 si possano caricare e che "
        "accanto ci sia clips.md a citarli tutti",
    )
    args = ap.parse_args()

    work = Path(args.work_dir)

    if args.consegna:
        meta = read_json(work / "meta.json") if (work / "meta.json").exists() else {}
        cartella = OUTPUT_DIR / meta.get("slug", work.name)
        problemi = check_consegna(cartella)
        for p in problemi:
            log(f"  ERRORE: {p}")
        if problemi:
            raise SystemExit("\nLa consegna non e completa.")
        log(
            f"consegna completa: {len(list(cartella.glob('*.mp4')))} clip, caricabili e tutte in clips.md"
        )
        emit({"ok": True, "cartella": str(cartella)})
        return

    src = work / args.decisions
    if not src.exists():
        raise SystemExit(
            f"{src} non trovato. Lo scrive Claude dopo aver letto la vista "
            f"prodotta da windows.py - vedi SKILL.md, passo 5."
        )

    duration = None
    if (work / "meta.json").exists():
        duration = read_json(work / "meta.json").get("duration")

    decisions = read_json(src)

    # Le regole della campagna, se questa cartella e agganciata a una. Si leggono
    # **qui e non dentro `check_all`** perche dipendono dalla cartella, non dalle
    # decisioni: `check_all` resta una funzione pura sui dati che riceve, e
    # continua a valere per chi la chiama senza una cartella sotto.
    minimo, massimo, note_limiti = campagna.limiti(work)
    errors, warnings = check_all(decisions, duration, minimo, massimo)
    errors.extend(regole_campagna(work, decisions))
    if ritaglio := avviso_ritaglio(work):
        warnings.append(ritaglio)
    for n in note_limiti:
        log(f"  regola di campagna: {n}")

    # I bordi si controllano sul transcript, se c'e: senza le parole non c'e
    # modo di sapere se un taglio cade in mezzo a una.
    transcript_path = work / "transcript.json"
    if transcript_path.exists() and isinstance(decisions.get("clips"), list):
        e, a = check_bordi(decisions["clips"], read_json(transcript_path))
        errors.extend(e)
        warnings.extend(a)

    # Il logo e facoltativo, ma se e dichiarato dev'essere li: senza questo
    # controllo il nome sbagliato si scopre a ogni singolo render, dopo aver
    # gia speso la scansione dei volti di quella clip.
    logo = decisions.get("logo")
    if logo and resolve_logo(work, logo) is None:
        errors.append(
            f"logo '{logo}' dichiarato ma non trovato: cercato in "
            f"{work}, in assets/, e come percorso"
        )

    # Il richiamo al video intero: facoltativo, ma se c'e sta a schermo per quasi
    # tutta la clip, quindi una riga di troppo la si paga trenta volte.
    cta = decisions.get("cta")
    if cta and len(EMPHASIS_RE.sub(r"\1", cta)) > 40:
        warnings.append(
            f"cta di {len(cta)} caratteri: oltre le due righe da 24 rischia di "
            f"coprire l'inquadratura per tutta la clip"
        )

    clips = decisions.get("clips", [])
    for w in warnings:
        log(f"  avviso: {w}")

    if errors:
        for e in errors:
            log(f"  ERRORE: {e}")
        raise SystemExit(f"\n{len(errors)} errori in {src.name}: correggili prima del render.")

    total = sum(c["end"] - c["start"] for c in clips)
    log(
        f"{len(clips)} clip valide, {total:.0f}s totali"
        + (f", {len(warnings)} avvisi" if warnings else "")
    )
    for c in clips:
        forti = sum(1 for v in c.get("scores", {}).values() if v == "forte")
        log(
            f"  {c['slug']:<24} {fmt_timecode(c['start'])}-{fmt_timecode(c['end'])}"
            f"  {c['end'] - c['start']:>5.1f}s  {forti}/6 forte"
        )

    # I vincoli in chiaro si ristampano **qui**, dopo il verdetto: e l'ultimo
    # momento in cui si legge qualcosa prima di spendere il render, e sono
    # esattamente le regole che nessun controllo automatico sa verificare.
    regole = (campagna.load(work) or {}).get("regole") or {}
    prosa = (regole.get("vincoli_render") or []) + (regole.get("vincoli_pubblicazione") or [])
    if prosa:
        log("\n  vincoli della campagna che nessuno puo controllare per te:")
        for v in prosa:
            log(f"    - {v}")

    emit(
        {
            "ok": True,
            "clips": len(clips),
            "total_seconds": round(total, 1),
            "durata_min": minimo,
            "durata_max": massimo,
            "warnings": warnings,
            # L'ordine e quello di decisions.json: e l'ordine editoriale, non si tocca.
            "slugs": [c["slug"] for c in clips],
        }
    )


if __name__ == "__main__":
    main()
