"""Applica al transcript le parole che Whisper ha sentito male.

    python apply_corrections.py <work_dir>

**Perche esiste.** I sottotitoli vengono *impressi* nel file: un errore li non si
corregge dopo, resta nella clip che pubblichi. Whisper sbaglia in modo
riconoscibile — `seppi qr` per SPQR, `Adalesia` per Alesia, `carta pesta` per
cartapesta — e riconoscerlo e lavoro di lingua, quindi di Claude.

**Cosa questo NON e.** Non e trascrivere: l'audio lo sente solo Whisper, e i
tempi delle parole li allinea solo lui. Qui si cambiano parole gia riconosciute,
mai i loro istanti. Ne segue il limite da tenere a mente: dove Whisper produce
una frase plausibile ma sbagliata, nessuna correzione dall'esterno puo
accorgersene.

**Perche un file e non una modifica al volo.** Correggere `transcript.json` sul
posto significa perdere tutto al primo `transcribe.py --force`. Le correzioni
sono un giudizio, e i giudizi si persistono — come `decisions.json`. Qui il file
e `corrections.json`, scritto da Claude e applicato da questo script.

Applicarlo due volte non fa danni: ogni correzione, una volta fatta, non trova
piu niente da correggere.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from common import emit, log, read_json, write_json


def scomponi(token: str) -> tuple[str, str, str]:
    """Separa la punteggiatura in testa e in coda dal corpo della parola.

    L'apostrofo resta attaccato al corpo: Whisper spezza `l'audacia` in `l` e
    `'audacia`, e la seconda meta va trattata come una parola sua.
    """
    inizio = 0
    while inizio < len(token) and not token[inizio].isalnum() and token[inizio] != "'":
        inizio += 1
    fine = len(token)
    while fine > inizio and not token[fine - 1].isalnum():
        fine -= 1
    return token[:inizio], token[inizio:fine], token[fine:]


def applica_globali(parole: list[dict], mappa: dict[str, str]) -> list[str]:
    """Sostituzioni valide ovunque: parole che sono sbagliate in se."""
    fatte = []
    for w in parole:
        testa, corpo, coda = scomponi(w["word"])
        if corpo in mappa:
            nuovo = testa + mappa[corpo] + coda
            fatte.append(f"{w['start']:7.2f}  {w['word']!r} -> {nuovo!r}")
            w["word"] = nuovo
    return fatte


def applica_puntuali(parole: list[dict], regole: list[dict]) -> tuple[list[str], list[str]]:
    """Correzioni ancorate a un istante.

    Servono per le parole comuni: 'si', 'che', 'alla' sono sbagliate in *quel*
    punto e giuste in tutti gli altri, quindi una sostituzione globale
    romperebbe il resto del testo.
    """
    fatte, mancate = [], []
    for regola in regole:
        istante, atteso = float(regola["t"]), regola["parola"]
        finestra = float(regola.get("finestra", 2.5))
        candidati = [
            w
            for w in parole
            if abs(w["start"] - istante) < finestra and scomponi(w["word"])[1] == atteso
        ]
        if not candidati:
            mancate.append(f"{atteso!r} attorno a {istante}s: non trovata")
            continue

        w = min(candidati, key=lambda x: abs(x["start"] - istante))
        if regola.get("togli"):
            fatte.append(f"{w['start']:7.2f}  {w['word']!r} tolta")
            w["_togli"] = True
        else:
            testa, _, coda = scomponi(w["word"])
            nuovo = testa + regola["in"] + coda
            fatte.append(f"{w['start']:7.2f}  {w['word']!r} -> {nuovo!r}")
            w["word"] = nuovo
    return fatte, mancate


def togli_marcate(parole: list[dict]) -> list[dict]:
    """Chi viene tolto cede il proprio tempo alla parola precedente, cosi la
    linea temporale non resta con un buco in mezzo."""
    ripulite: list[dict] = []
    for w in parole:
        if w.pop("_togli", False):
            if ripulite:
                ripulite[-1]["end"] = w["end"]
            continue
        ripulite.append(w)
    return ripulite


def main() -> None:
    ap = argparse.ArgumentParser(description="Corregge le parole sentite male da Whisper")
    ap.add_argument("work_dir")
    ap.add_argument("--corrections", default="corrections.json")
    args = ap.parse_args()

    work = Path(args.work_dir)
    src = work / args.corrections
    if not src.exists():
        raise SystemExit(
            f"{src} non trovato. Lo scrive Claude leggendo la vista di windows.py: "
            f"vedi SKILL.md, passo 4."
        )

    transcript_path = work / "transcript.json"
    transcript = read_json(transcript_path)
    correzioni = read_json(src)

    parole = transcript["words"]
    prima = len(parole)

    fatte = applica_globali(parole, correzioni.get("globali", {}))
    puntuali, mancate = applica_puntuali(parole, correzioni.get("puntuali", []))
    transcript["words"] = togli_marcate(parole)
    transcript["word_count"] = len(transcript["words"])

    for riga in fatte + puntuali:
        log("  " + riga)
    for m in mancate:
        # Non e un errore: se la correzione e gia stata applicata, la parola
        # sbagliata non c'e piu. Lo si dice e basta.
        log(f"  (niente da fare) {m}")

    write_json(transcript_path, transcript)
    log(
        f"\n{len(fatte) + len(puntuali)} correzioni applicate, "
        f"{prima - len(transcript['words'])} parole unite."
    )

    emit(
        {
            "corrections": len(fatte) + len(puntuali),
            "words_before": prima,
            "words_after": len(transcript["words"]),
            "not_found": mancate,
        }
    )


if __name__ == "__main__":
    main()
