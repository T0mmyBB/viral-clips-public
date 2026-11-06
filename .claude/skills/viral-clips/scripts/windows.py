"""La vista su cui Claude sceglie i momenti.

    python windows.py <work_dir>                        # vista a segmenti + segnali
    python windows.py <work_dir> --words --around 1120  # parole attorno a un istante

La vista esce **su stdout**: e quella l'uscita utile, non un file. Con `--save` ne resta
anche una copia in `work/<slug>/windows.json`, per rileggerla senza ricalcolarla.

**Perche esiste.** `transcript.json` con i timestamp per parola e quasi due ordini di
grandezza piu grande della stessa ora vista a segmenti: leggerlo intero satura il
contesto e rende impossibile la parte editoriale. Le cifre misurate stanno in
PROGRESS.md, dove restano vere anche quando cambia il video.

I timestamp per parola non spariscono: servono al render, e si consultano con `--words`
solo sulle candidate scelte, per agganciare i bordi alla parola giusta.
"""

from __future__ import annotations

import argparse
import bisect
from pathlib import Path

import campagna
from check_material import as_text
from check_material import check as check_material
from common import fmt_timecode, log, read_json, write_json

# Un marcatore per tipo di segnale, messo accanto al timestamp. Compatto di proposito:
# ogni carattere in piu per riga si moltiplica per qualche centinaio di righe.
MARK_PEAK = "!"  # picco di enfasi
MARK_LAUGH = "R"  # risata (euristica, vedi PROGRESS.md: molti falsi positivi)
MARK_PAUSE = "_"  # pausa lunga: buon punto di taglio
MARK_SCENE = "|"  # cambio scena

# Frasi che segnalano un segmento sponsorizzato. Non decidono da sole: marcano il
# tratto perche venga guardato prima di proporlo.
SPONSOR_HINTS = (
    "sponsor",
    "codice sconto",
    "discount code",
    "link in descrizione",
    "link in the description",
    "qr code",
    "in due minuti",
    "account gratis",
    "free account",
    "check out",
    "promo",
)


def collect_marks(signals: dict, tolerance: float = 1.5) -> dict:
    """Indicizza i segnali per istante, arrotondati al secondo."""

    def at(items, key):
        return {round(i[key]) for i in items}

    return {
        MARK_PEAK: at(signals.get("energy_peaks", []), "t"),
        MARK_LAUGH: at(signals.get("laughter", []), "start"),
        MARK_PAUSE: at(signals.get("pauses", []), "start"),
        MARK_SCENE: at(signals.get("scenes", [])[1:], "start"),
    }


def marks_for(t: float, marks: dict, tolerance: float = 2.0) -> str:
    out = ""
    for symbol, times in marks.items():
        if any(abs(t - x) <= tolerance for x in times):
            out += symbol
    return out


def face_layout_at(layout: list[dict], t: float) -> str:
    """Composizione dell'inquadratura in quell'istante: una faccia, due, nessuna.

    Serve durante la selezione, non dopo: sapere che un tratto e schermo condiviso o
    b-roll cambia se ha senso proporlo come clip parlata.
    """
    for entry in layout:
        if entry["start"] <= t < entry["end"]:
            return entry["layout"]
    return "?"


def looks_sponsored(text: str) -> bool:
    low = text.lower()
    return any(h in low for h in SPONSOR_HINTS)


def build_view(work: Path) -> tuple[str, dict]:
    transcript = read_json(work / "transcript.json")
    meta = read_json(work / "meta.json")

    signals = {}
    if (work / "signals.json").exists():
        signals = read_json(work / "signals.json")
    marks = collect_marks(signals)

    layout = []
    if (work / "faces.json").exists():
        layout = read_json(work / "faces.json").get("layout", [])

    lines: list[str] = []
    sponsor_zones: list[dict] = []
    prev_layout = None

    for seg in transcript["segments"]:
        t = seg["start"]
        text = seg["text"].strip()
        if not text:
            continue

        m = marks_for(t, marks)

        # La composizione si annota solo quando cambia: ripeterla su ogni riga
        # sarebbe rumore che si paga 400 volte.
        cur_layout = face_layout_at(layout, t) if layout else None
        layout_tag = ""
        if cur_layout and cur_layout != prev_layout:
            layout_tag = f" <{cur_layout}>"
            prev_layout = cur_layout

        flag = ""
        if looks_sponsored(text):
            flag = "  [SPONSOR?]"
            sponsor_zones.append({"start": t, "text": text[:80]})

        lines.append(f"{fmt_timecode(t)}{m:<4}{layout_tag} {text}{flag}")

    header = [
        f"# {meta.get('title', work.name)}",
        f"durata {fmt_timecode(meta['duration'])} · lingua {transcript.get('language')} "
        f"· {transcript.get('word_count')} parole · {len(transcript['segments'])} segmenti",
        "",
        f"marcatori: {MARK_PEAK} enfasi · {MARK_LAUGH} risata · {MARK_PAUSE} pausa "
        f"· {MARK_SCENE} cambio scena · <one/two/none> composizione",
    ]

    # Le anomalie vanno **qui dentro**, non in un log che scorre via: questa vista
    # e l'unica cosa che si legge per intero prima di scegliere, quindi e l'unico
    # posto dove un avviso arriva ancora in tempo per cambiare una decisione.
    note = check_material(work)
    if note:
        header += ["", as_text(note)]

    # I paletti della campagna stanno **qui**, per la stessa ragione delle
    # anomalie e subito dopo: le anomalie dicono se c'e qualcosa da consegnare,
    # i paletti dicono a quali condizioni. Prima stavano solo nel brief, cioe'
    # in un documento che chi sceglieva doveva ricordarsi di riaprire — e una
    # clip giudicata senza le regole del committente esce identica a una
    # giudicata con quelle.
    paletti = campagna.as_text(work)
    if paletti:
        header += ["", paletti]

    if sponsor_zones:
        header.append(
            f"ATTENZIONE: {len(sponsor_zones)} tratti sembrano sponsorizzati, "
            f"marcati [SPONSOR?] - da escludere dalla selezione"
        )
    header.append("")

    view = "\n".join(header + lines)
    stats = {
        "segments": len(lines),
        "chars": len(view),
        "approx_tokens": len(view) // 4,
        "sponsor_suspects": len(sponsor_zones),
        "anomalie": [n["codice"] for n in note],
        "campagna": (campagna.load(work) or {}).get("titolo"),
    }
    return view, stats


def words_around(work: Path, t: float, span: float) -> str:
    """Le parole attorno a un istante, per agganciare un bordo.

    Sono decine di righe, non decine di migliaia: si consulta solo sulle candidate
    gia scelte, quando serve decidere se il taglio cade prima o dopo una parola.

    **Ogni segmento ha la sua intestazione, con inizio e fine.** Fra due segmenti
    c'e un buco — la fine del primo non e l'inizio del secondo — e `validate.py`
    vuole `start` su un inizio e `end` su una fine. Senza queste righe l'unico
    numero a portata di mano per chiudere era l'inizio del segmento dopo, cioe
    quello sbagliato.
    """
    transcript = read_json(work / "transcript.json")
    picked = [w for w in transcript["words"] if t - span <= w["start"] <= t + span]
    if not picked:
        return f"nessuna parola tra {t - span:.1f}s e {t + span:.1f}s"

    segmenti = sorted(transcript.get("segments") or [], key=lambda s: float(s["start"]))
    inizi = [float(s["start"]) for s in segmenti]

    out = [
        f"parole tra {t - span:.2f}s e {t + span:.2f}s (istante richiesto: {t:.2f}s)",
        "bordi: start = inizio di un segmento, end = FINE di un segmento (non l'inizio del successivo)",
        "",
    ]
    ultimo = None
    for w in picked:
        # Il segmento di una parola e l'ultimo che comincia non dopo di lei.
        i = bisect.bisect_right(inizi, float(w["start"]) + 0.01) - 1
        if segmenti and i >= 0 and i != ultimo:
            s = segmenti[i]
            out.append(
                f"  -- segmento  start {float(s['start']):.2f}   end {float(s['end']):.2f} --"
            )
            ultimo = i
        marker = "  <-- " if abs(w["start"] - t) < 0.25 else "      "
        out.append(f"  [{w['start']:8.2f} -> {w['end']:8.2f}]{marker}{w['word']}")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Vista a segmenti per la selezione dei momenti")
    ap.add_argument("work_dir")
    ap.add_argument(
        "--words", action="store_true", help="mostra i timestamp per parola attorno a un istante"
    )
    ap.add_argument(
        "--around", type=float, default=None, help="istante in secondi (richiesto con --words)"
    )
    ap.add_argument(
        "--span", type=float, default=4.0, help="quanti secondi prima e dopo (default 4)"
    )
    ap.add_argument(
        "--save", action="store_true", help="scrive anche windows.json nella cartella di lavoro"
    )
    args = ap.parse_args()

    work = Path(args.work_dir)
    if not (work / "transcript.json").exists():
        raise SystemExit(f"transcript.json non trovato in {work} - esegui prima transcribe.py")

    if args.words:
        if args.around is None:
            raise SystemExit("--words richiede --around <secondi>")
        print(words_around(work, args.around, args.span))
        return

    view, stats = build_view(work)
    print(view)

    log(f"{stats['segments']} segmenti, ~{stats['approx_tokens']:,} token")
    if stats["campagna"]:
        log(f"paletti di «{stats['campagna']}» in cima alla vista")
    else:
        log(
            "nessuna campagna agganciata: la vista non porta nessun paletto "
            "(python campagna.py <work_dir> --id <uuid>)"
        )
    if stats["anomalie"]:
        log(f"anomalie del materiale in cima alla vista: {', '.join(stats['anomalie'])}")
    if stats["sponsor_suspects"]:
        log(f"{stats['sponsor_suspects']} tratti sospetti di sponsorizzazione")

    if args.save:
        write_json(work / "windows.json", {"view": view, **stats})
        log(f"salvato in {work / 'windows.json'}")


if __name__ == "__main__":
    main()
