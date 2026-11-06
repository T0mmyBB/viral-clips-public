"""Cosa c'e di anomalo nel materiale, prima che diventi un problema.

    python check_material.py <work_dir>

**Perche esiste.** SKILL.md dice di fermarsi quando c'e "qualcosa di anomalo che
cambia il senso della consegna: sorgente gia verticale, audio coperto per larghi
tratti". Quell'istruzione era irraggiungibile: nessuno rilevava niente, quindi la
pipeline attraversava i casi limite **in silenzio**. Non crashava — taceva, che e
peggio, perche una consegna sbagliata sembra una consegna riuscita.

Tre casi misurati su materiale costruito apposta:

- **audio senza parlato**: la pipeline arrivava in fondo e diceva "pronto",
  dopo aver speso trascrizione, segnali e volti per consegnare una vista vuota
- **nessun volto**: renderizza correttamente, ma il ritaglio 9:16 su una
  condivisione schermo tiene una striscia centrale e butta il resto
- **sorgente gia verticale**: nessuno lo diceva, e il crop dinamico ci
  ingrandiva dentro un sorgente che era gia nel formato giusto

**Questo script non decide niente.** Constata e riferisce; se fermarsi, se
consegnare lo stesso, se scegliere altri momenti lo decide chi legge. E la
stessa divisione del lavoro del resto della skill.

Le note escono dove servono: in JSON su stdout, in chiaro su stderr, e — quella
che conta — in cima alla vista di `windows.py`, che e l'unico posto dove Claude
guarda davvero al momento di scegliere.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from common import emit, log, read_json
from transcribe import DEFAULT_MODEL

# Sotto questa quota di fotogrammi con un volto il video non e materiale parlato
# inquadrato: e slide, condivisione schermo o b-roll. Sul materiale reale la
# quota sta fra l'87% e il 94%, sul caso limite costruito apposta e 0%: in mezzo
# non c'e niente da distinguere finemente, serve solo separare i due mondi.
FACE_RATIO_MIN = 0.20

# Parole al minuto sotto cui il video non e parlato continuo. Il parlato normale
# sta fra 120 e 180; 40 lascia passare un'intervista lenta e prende il video
# musicale con qualche frase sopra.
WORDS_PER_MINUTE_MIN = 40.0


def _crop_9_16(width: int, height: int) -> tuple[int, int]:
    """La finestra 9:16 piu grande dentro il fotogramma.

    Stessa geometria di `render_clip.crop_geometry`, ridotta a cio che serve
    qui. Duplicarla sarebbe un rischio se fosse un criterio; e invece aritmetica
    di ritaglio, e riusare quella funzione trascinerebbe dentro tutto il render.
    """
    crop_w = int(round(height * 9 / 16))
    crop_w -= crop_w % 2
    if crop_w <= width:
        return crop_w, height - (height % 2)
    crop_w = width - (width % 2)
    crop_h = min(int(round(width * 16 / 9)), height)
    return crop_w, crop_h - (crop_h % 2)


def check(work: Path) -> list[dict]:
    """Le anomalie del materiale, in ordine di gravita.

    `ferma` vuol dire che consegnare non ha senso: manca la materia prima.
    `avvisa` vuol dire che la consegna e possibile ma cambia di significato, e
    chi legge deve saperlo *prima* di scegliere i momenti, non dopo il render.
    """
    note: list[dict] = []
    meta = read_json(work / "meta.json") if (work / "meta.json").exists() else {}

    width = int(meta.get("width") or 0)
    height = int(meta.get("height") or 0)

    # --- parlato ---------------------------------------------------------
    tr_path = work / "transcript.json"
    if tr_path.exists():
        tr = read_json(tr_path)
        parole = int(tr.get("word_count") or 0)
        durata = float(meta.get("duration") or tr.get("duration") or 0.0)
        al_minuto = parole / (durata / 60.0) if durata > 0 else 0.0

        if parole == 0:
            note.append(
                {
                    "codice": "niente-parlato",
                    "gravita": "ferma",
                    "titolo": "nessuna parola trascritta",
                    "dettaglio": "l'audio non contiene parlato riconoscibile. Non c'e testo su cui "
                    "giudicare un momento, quindi non c'e nessuna clip da scegliere: "
                    "questa skill sceglie leggendo.",
                }
            )
        elif al_minuto < WORDS_PER_MINUTE_MIN:
            note.append(
                {
                    "codice": "parlato-scarso",
                    "gravita": "avvisa",
                    "titolo": f"parlato rado: {al_minuto:.0f} parole al minuto",
                    "dettaglio": f"{parole} parole in {durata / 60:.0f} minuti. Il parlato continuo "
                    f"sta fra 120 e 180 al minuto: qui il video e in larga parte "
                    f"musica, silenzio o rumore, e i tratti parlati vanno cercati "
                    f"invece che scelti fra molti.",
                }
            )

        # Una cartella preparata prima di un cambio di default resta in cache e non
        # si ritrascrive da sola: il testo bucato lo legge chi sceglie, senza saperlo.
        # Un transcript che non dichiara il modello non accusa nessuno.
        modello = tr.get("model")
        if modello and modello != DEFAULT_MODEL:
            note.append(
                {
                    "codice": "modello-vecchio",
                    "gravita": "avvisa",
                    "titolo": f"trascritto con «{modello}», il default e' «{DEFAULT_MODEL}»",
                    "dettaglio": "i modelli piu piccoli perdono frasi intere, e una frase assente "
                    "non ha un istante a cui agganciarsi: la vista che stai per leggere "
                    "puo avere buchi che non si vedono. Ritrascrivi prima di scegliere "
                    "(transcribe.py <work_dir> --force).",
                }
            )

    # --- formato ---------------------------------------------------------
    if width and height:
        crop_w, crop_h = _crop_9_16(width, height)
        quota = crop_w / width

        if height > width:
            gia_nel_formato = crop_w >= width and crop_h >= height
            note.append(
                {
                    "codice": "sorgente-verticale",
                    "gravita": "avvisa",
                    "titolo": f"il sorgente e gia verticale ({width}x{height})",
                    "dettaglio": (
                        "e gia nel formato di uscita: il ritaglio 9:16 non toglie niente "
                        "e non c'e nessuna reinquadratura da fare. Quasi sempre vuol dire "
                        "che il video **e gia una clip** — verifica che tagliarlo ancora "
                        "abbia senso prima di consegnare."
                        if gia_nel_formato
                        else f"piu alto del 9:16: il ritaglio tiene il {quota:.0%} della "
                        f"larghezza e si muove in verticale, non in orizzontale."
                    ),
                }
            )

    # --- volti -----------------------------------------------------------
    faces_path = work / "faces.json"
    if faces_path.exists() and width:
        faces = read_json(faces_path)
        ratio = float(faces.get("face_ratio") or 0.0)
        if ratio < FACE_RATIO_MIN:
            crop_w, _ = _crop_9_16(width, height) if height else (width, 0)
            note.append(
                {
                    "codice": "niente-volti",
                    "gravita": "avvisa",
                    "titolo": f"volto presente solo nel {ratio:.0%} dei fotogrammi",
                    "dettaglio": f"slide, condivisione schermo o b-roll. Non c'e niente da "
                    f"inseguire, quindi il ritaglio resta al centro e tiene "
                    f"{crop_w} px sui {width} del sorgente ({crop_w / width:.0%} "
                    f"della larghezza): quello che sta ai lati sparisce dalla clip. "
                    f"Scegli i momenti in cui conta la voce, non l'immagine.",
                }
            )

    ordine = {"ferma": 0, "avvisa": 1}
    return sorted(note, key=lambda n: ordine.get(n["gravita"], 9))


def as_text(note: list[dict]) -> str:
    """Le note in chiaro, per la testa della vista e per il riepilogo di run.py."""
    if not note:
        return ""
    righe = ["ANOMALIE DEL MATERIALE — da tenere presenti scegliendo:"]
    for n in note:
        marca = "FERMA" if n["gravita"] == "ferma" else "nota"
        righe.append(f"  [{marca}] {n['titolo']}")
        righe.append(f"         {n['dettaglio']}")
    return "\n".join(righe)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Anomalie del materiale che cambiano il senso della consegna"
    )
    ap.add_argument("work_dir")
    args = ap.parse_args()

    work = Path(args.work_dir)
    if not (work / "meta.json").exists():
        raise SystemExit(f"{work} non e una cartella di lavoro: manca meta.json")

    note = check(work)
    if note:
        log(as_text(note))
    else:
        log("materiale ordinario: niente da segnalare")

    emit({"note": note, "ferma": any(n["gravita"] == "ferma" for n in note)})


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
