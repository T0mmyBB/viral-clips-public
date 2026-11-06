"""Verifica che i sottotitoli di una clip renderizzata siano in sincrono.

    python check_sync.py work/<slug> --slug 01-il-rospo
    python check_sync.py work/<slug> --slug 01-il-rospo --samples 5

**Perche esiste.** Un fotogramma a risoluzione piena dice se l'inquadratura e
buona e se il testo sta nei bordi, ma **non** dice se i sottotitoli sono
sfasati: da un singolo istante quella cosa e invisibile, e sfasati di una parola
sono il difetto piu probabile di tutta la pipeline. Finche il controllo era
"guarda e giudica", non era un controllo.

Come si automatizza. Ogni parola ha il suo istante, quindi il momento in cui una
data parola *deve* essere accesa e noto al centesimo. Il controllo prende tre
istanti al centro esatto di tre parole e verifica tre cose in fila:

1. che l'ASS accenda quella parola li — deterministico, si legge dal file;
2. che quella parola sia davvero cio che si sta dicendo in quel punto del
   video, confrontandola con `transcript.json` ai tempi assoluti;
3. che nel fotogramma **renderizzato** l'evidenziazione gialla cada dove sta
   quella parola nella riga, e non su quella prima o dopo.

Il terzo punto e l'unico che guarda i pixel, ed e l'unico che puo accorgersi di
uno sfasamento introdotto dal render invece che dai sottotitoli. Non legge il
testo: misura *dove* sta il giallo e lo confronta con la posizione di ogni parola
della riga, che l'ASS dichiara al pixel — una parola sbagliata sposta il giallo di
una parola intera.

Con i tagli dei silenzi un istante della clip montata non e piu `start + t` nel
sorgente: il piano dei tagli che il render scrive accanto ai sottotitoli dice dove
cade davvero.

I fotogrammi restano in `work/<slug>/sync_*.png`, con il nome della parola
attesa: se il controllo fallisce si guarda quello e in due secondi si capisce.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from common import OUTPUT_DIR, emit, fmt_timecode, log, read_json, tool, video_info
from make_subs import STYLES, salda_elisioni

# La parola attiva e l'unica marcata con questo scatto di scala: gli altri tag
# (ingresso del gruppo, ritorno dopo l'accensione) usano durate diverse, quindi
# il segno e univoco.
ACTIVE_RE = re.compile(r"\{[^}]*\\t\(0,90,[^}]*\}([^{]+)\{\\r\}")
DIALOGUE_RE = re.compile(r"^Dialogue:\s*\d+,([\d:.]+),([\d:.]+),(\w+),(.*)$", re.M)
POS_RE = re.compile(r"\\pos\(([-\d.]+),([-\d.]+)\)")
PLAIN_RE = re.compile(r"\{[^}]*\}")

# Colore dell'evidenziazione dello stile predefinito (FFE500) in BGR, come lo
# legge OpenCV. Gli altri stili ne hanno uno loro: vedi `colore_attivo()`.
ACTIVE_BGR = (0, 229, 255)
COLOUR_TOLERANCE = 70


def colore_attivo(stile: dict) -> tuple[tuple[int, int, int] | None, str]:
    """Il colore da cercare nei pixel per trovare la parola accesa, in BGR.

    **Lo dice lo stile con cui la clip e stata scritta, non una costante.** Il
    controllo cercava sempre il giallo di `bold`: su `punch` la parola accesa e
    rossa e il giallo e delle keyword e delle cifre, quindi avrebbe misurato la
    keyword e dichiarato sfasata una clip in sincrono — lo stesso falso allarme
    della keyword ciano, spostato su un altro stile.

    E ci sono stili in cui la posizione **non si puo misurare**: se il colore
    acceso e quello del testo normale (`minimal` accende solo con la scala) o
    coincide con quello delle keyword o delle cifre, nessun pixel distingue la
    parola accesa. Allora si torna `None` con la ragione, e il campione e «non
    misurabile» invece di un verdetto inventato.
    """
    acceso = (stile.get("active") or "").upper()
    if len(acceso) != 6:
        return None, "lo stile non dichiara il colore della parola accesa"
    uguali = [
        nome
        for nome in ("primary", "keyword", "number")
        if (stile.get(nome) or "").upper() == acceso
    ]
    if uguali:
        return None, (
            f"nello stile la parola accesa ha lo stesso colore di "
            f"{', '.join(uguali)}: la sua posizione non si misura sui pixel"
        )
    r, g, b = (int(acceso[i : i + 2], 16) for i in (0, 2, 4))
    return (b, g, r), ""


# **Il giallo del sottotitolo si distingue dal giallo della scena per il
# contorno.** Un pixel acceso con del quasi nero a pochi pixel di distanza e un
# glifo; una maglietta gialla no. E la stessa firma che serviva prima a isolare
# la riga bianca, applicata al solo colore acceso.
GLYPH_DARK = 60  # il contorno nero
GLYPH_RADIUS = 7  # quanto lontano cercare il contorno, in pixel


def parse_time(raw: str) -> float:
    h, m, s = raw.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def read_events(subs: Path) -> list[dict]:
    """Gli eventi dei sottotitoli: una parola ciascuno, con la sua posizione.

    Da quando ogni parola ha il suo `\\pos`, il file dice esattamente **dove**
    deve cadere il giallo: niente piu stima della posizione dal numero di
    caratteri.
    """
    events = []
    for start, end, style, rest in DIALOGUE_RE.findall(subs.read_text(encoding="utf-8")):
        if style != "Main":
            continue  # l'hook non e un sottotitolo
        text = rest.split(",", 5)[-1] if rest.count(",") >= 5 else rest
        pos = POS_RE.search(text)
        if not pos:
            continue
        active = ACTIVE_RE.search(text)
        events.append(
            {
                "start": parse_time(start),
                "end": parse_time(end),
                "x": float(pos.group(1)),
                "y": float(pos.group(2)),
                "word": PLAIN_RE.sub("", text).strip(),
                "active": bool(active),
            }
        )
    return events


def normalise(word: str) -> str:
    return re.sub(r"[^\w']", "", word).lower()


def visible_line(events: list[dict], lit: dict, instant: float) -> tuple[list[dict], int]:
    """Le parole a schermo nella riga della parola accesa, da sinistra a destra."""
    riga = sorted(
        (e for e in events if e["y"] == lit["y"] and e["start"] <= instant < e["end"]),
        key=lambda e: e["x"],
    )
    xs = [e["x"] for e in riga]
    indice = xs.index(lit["x"]) if lit["x"] in xs else -1
    return riga, indice


def read_image(path: Path):
    """Un fotogramma su disco, anche se il percorso ha lettere accentate.

    `cv2.imread` su Windows non apre i percorsi fuori ASCII e torna `None` senza
    errore: il controllo lo leggeva come «nessun giallo nella striscia», cioe come
    uno sfasamento, su ogni parola come PERCHE o COSI. Leggere i byte con numpy e
    decodificarli in memoria non passa dal percorso.
    """
    import cv2
    import numpy as np

    try:
        return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    except OSError:
        return None


def grab_frame(clip: Path, instant: float, dest: Path) -> Path:
    subprocess.run(
        [
            tool("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{instant:.3f}",
            "-i",
            str(clip),
            "-frames:v",
            "1",
            str(dest),
        ],
        check=True,
    )
    return dest


def highlight_x(
    frame: Path,
    band: tuple[int, int],
    span: tuple[float, float],
    bgr: tuple[int, int, int] = ACTIVE_BGR,
) -> tuple[float, int] | None:
    """Dove sta, in pixel, il giallo del sottotitolo dentro la riga.

    Si guarda solo la striscia della riga e solo fra la prima e l'ultima parola
    (con margine), e si contano solo i pixel gialli che hanno un contorno scuro
    vicino: cosi un oggetto giallo nella scena non sposta il baricentro.
    """
    import cv2
    import numpy as np

    img = read_image(frame)
    if img is None:
        return None
    x0 = max(0, int(span[0]))
    x1 = min(img.shape[1], int(span[1]))
    strip = img[band[0] : band[1], x0:x1, :]
    if strip.size == 0:
        return None
    grigio = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
    nucleo = np.ones((GLYPH_RADIUS, GLYPH_RADIUS), np.uint8)
    scuro_vicino = cv2.dilate((grigio < GLYPH_DARK).astype(np.uint8), nucleo) > 0
    acceso = np.abs(strip.astype(np.int16) - np.array(bgr)).max(axis=2) < COLOUR_TOLERANCE
    glifo = acceso & scuro_vicino
    if glifo.sum() < 40:
        return None
    colonne = np.where(glifo)[1]
    return x0 + float(colonne.mean()), int(glifo.sum())


def parola_vicina(words: list[dict], istante: float) -> str | None:
    """La parola detta in quell'istante, o la piu vicina se cade in una pausa."""
    dentro = next((w for w in words if w["start"] <= istante <= w["end"]), None)
    if dentro:
        return dentro["word"]
    if not words:
        return None
    vicina = min(words, key=lambda w: min(abs(w["start"] - istante), abs(w["end"] - istante)))
    return vicina["word"]


def acceso_presente(
    frame: Path, band: tuple[int, int], bgr: tuple[int, int, int] = ACTIVE_BGR
) -> bool:
    """C'e del giallo nella striscia, ovunque sia.

    Domanda piu debole di "dove sta", ma che non dipende dallo sfondo: serve a
    distinguere "non riesco a misurare" da "il sottotitolo non c'e proprio".
    """
    import numpy as np

    img = read_image(frame)
    if img is None:
        return False
    strip = img[band[0] : band[1], :, :].astype(np.int16)
    acceso = np.abs(strip - np.array(bgr)).max(axis=2) < COLOUR_TOLERANCE
    return bool(acceso.sum() > 40)


# Sotto questa durata un evento non si campiona: il suo punto medio cade sul
# confine fra due fotogrammi, e ffmpeg ne restituisce uno dei due a caso. L'ultima
# parola di un segmento seguito da una pausa dura quanto la parola (`CAMERAS.`:
# 80 ms, due fotogrammi a 23,976 fps), e il fotogramma preso era quello dopo la
# fine: «nessun giallo nella striscia», cioe il messaggio di uno sfasamento vero.
# Con tre fotogrammi il punto medio sta dentro quello centrale, con un margine
# pieno per lato.
FOTOGRAMMI_MINIMI = 3


def campionabili(accese: list[dict], fps: float) -> tuple[list[dict], int]:
    """Gli eventi su cui un campione al punto medio vale qualcosa, e quanti ne restano fuori.

    «Troppo breve per essere guardato» non e «guardato e non c'era»: chi sta
    sotto soglia si scarta e si conta, non si dichiara sfasato. Fra quelli che
    restano si preferiscono le parole da almeno quattro lettere, perche su "I" o
    "CHE" il giallo e cosi poco che lo sposta un contorno qualunque; se non ce
    n'e nessuna si prendono le altre, purche abbastanza lunghe.
    """
    durata_minima = FOTOGRAMMI_MINIMI / fps
    lunghi = [e for e in accese if e["end"] - e["start"] >= durata_minima]
    parole_lunghe = [e for e in lunghi if len(normalise(e["word"])) >= 4]
    return parole_lunghe or lunghi, len(accese) - len(lunghi)


def main() -> None:
    ap = argparse.ArgumentParser(description="Controlla la sincronia dei sottotitoli")
    ap.add_argument("work_dir")
    ap.add_argument("--slug", required=True, help="quale clip di decisions.json")
    ap.add_argument("--decisions", default="decisions.json")
    ap.add_argument(
        "--clip", default=None, help="file renderizzato (default: output/<video>/<slug>.mp4)"
    )
    ap.add_argument(
        "--subs",
        default=None,
        help="file .ass (default: quello scritto dal render per questa clip)",
    )
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument(
        "--band",
        type=int,
        default=90,
        help="mezza altezza della striscia dei sottotitoli, in pixel",
    )
    args = ap.parse_args()

    work = Path(args.work_dir)
    meta = read_json(work / "meta.json")
    decisions = read_json(work / args.decisions)
    clip_data = next((c for c in decisions.get("clips", []) if c.get("slug") == args.slug), None)
    if clip_data is None:
        raise SystemExit(f"slug '{args.slug}' assente da {args.decisions}")

    start, end = float(clip_data["start"]), float(clip_data["end"])
    subs = Path(args.subs) if args.subs else work / f"subs_{start:.0f}_{end:.0f}.ass"
    clip = (
        Path(args.clip)
        if args.clip
        else (OUTPUT_DIR / meta.get("slug", work.name) / f"{args.slug}.mp4")
    )
    for path, cosa in ((subs, "i sottotitoli"), (clip, "la clip renderizzata")):
        if not path.exists():
            raise SystemExit(f"{cosa} non esistono: {path}\nRenderizza prima la clip.")

    events = read_events(subs)
    accese = [e for e in events if e["active"]]
    if not accese:
        raise SystemExit(f"{subs.name} non contiene eventi con una parola accesa")

    # Le stesse parole da cui sono stati costruiti i sottotitoli, elisioni saldate
    # comprese: confrontare l'ASS con una lista diversa da quella che l'ha generato
    # produce differenze che non esistono. Caso reale: `L'ARIA` nell'ASS contro `l`
    # nel transcript, subito dopo aver introdotto la saldatura in make_subs.
    words = salda_elisioni(read_json(work / "transcript.json")["words"])

    # Se il render ha tolto dei silenzi, un istante della clip non e piu
    # `start + istante` nel sorgente: il piano dei tagli sta accanto ai
    # sottotitoli, con lo stesso nome.
    tenuti = None
    piano = work / f"tagli_{start:.0f}_{end:.0f}.json"
    if piano.exists():
        tenuti = [tuple(t) for t in read_json(piano).get("tenuti") or []] or None

    def nel_sorgente(istante: float) -> float:
        if not tenuti:
            return start + istante
        import tagli

        return start + tagli.to_source(tenuti, istante)

    # La striscia da guardare la decide lo stile con cui la clip e stata scritta:
    # ogni preset mette i sottotitoli a un'altezza sua.
    stile = STYLES[clip_data.get("style") or "bold"]
    y = stile["y"]
    band = (y - args.band, y + args.band)
    bgr, perche_no = colore_attivo(stile)
    if bgr is None:
        log(f"  {perche_no}: controllo solo il transcript, i pixel restano «?»")

    # Campioni sparsi nel corpo della clip: i bordi sono i punti in cui una
    # parola puo essere tagliata a meta, e non dicono niente sulla sincronia.
    #
    # E si campiona solo su parole abbastanza lunghe: su "I" o "CHE" il giallo
    # e cosi poco che il baricentro lo sposta un contorno qualunque. Misurare
    # dove non si puo misurare non rende il controllo piu severo: lo rende
    # rumoroso, e un controllo rumoroso viene ignorato.
    fps = video_info(clip)["fps"]
    misurabili, troppo_brevi = campionabili(accese, fps)
    if troppo_brevi:
        log(
            f"  {troppo_brevi} eventi sotto {FOTOGRAMMI_MINIMI} fotogrammi non campionati "
            f"(troppo brevi per essere verificati, non sfasati)"
        )
    if not misurabili:
        raise SystemExit(
            f"non verificabile: nessuno dei {len(accese)} eventi dura "
            f"{FOTOGRAMMI_MINIMI} fotogrammi, non c'e un istante da campionare"
        )
    quanti = max(1, min(args.samples, len(misurabili)))
    scelti = [misurabili[int(len(misurabili) * (i + 1) / (quanti + 1))] for i in range(quanti)]

    esiti = []
    for n, ev in enumerate(scelti, 1):
        istante = (ev["start"] + ev["end"]) / 2
        attesa = ev["word"]
        assoluto = nel_sorgente(istante)

        # 1. cosa si sta dicendo davvero in quel punto del video sorgente.
        #    L'evento ASS di una parola dura fino all'inizio della successiva, quindi
        #    il suo punto medio puo cadere nel silenzio *dopo* la parola: li nessuna
        #    parola contiene l'istante, e chiedere "quale si dice esattamente adesso"
        #    non ha risposta. La domanda giusta e quale sia la piu vicina.
        detta = parola_vicina(words, assoluto)
        coerente = detta is not None and normalise(detta) == normalise(attesa)

        # 2. dove dovrebbe cadere il giallo, e dove cade davvero
        riga, atteso_i = visible_line(events, ev, istante)
        dest = work / f"sync_{n}_{normalise(attesa) or 'x'}.png"
        grab_frame(clip, istante, dest)
        misura = None
        if bgr is not None and riga and atteso_i >= 0:
            margine = 260.0
            misura = highlight_x(dest, band, (riga[0]["x"] - margine, riga[-1]["x"] + margine), bgr)

        esito = {
            "istante_clip": round(istante, 2),
            "istante_video": fmt_timecode(assoluto),
            "parola_attesa": attesa,
            "parola_detta": detta,
            "coerente_col_transcript": coerente,
            "fotogramma": str(dest),
        }

        if bgr is None:
            esito["in_sincrono"] = None
            esito["nota"] = perche_no
        elif misura and atteso_i >= 0:
            trovato, pixel = misura
            # Non "quanto dista", ma "a quale parola e piu vicino": e accesa
            # questa parola, o quella prima o quella dopo?
            centri = [e["x"] for e in riga]
            vicino_i = min(range(len(centri)), key=lambda k: abs(centri[k] - trovato))
            esito.update(
                {
                    "posizione_attesa_px": round(centri[atteso_i], 1),
                    "posizione_trovata_px": round(trovato, 1),
                    "parola_accesa_stimata": riga[vicino_i]["word"],
                    "pixel_accesi": pixel,
                    "in_sincrono": vicino_i == atteso_i,
                }
            )
        elif atteso_i >= 0 and not acceso_presente(dest, band, bgr):
            # A meta di un evento quella parola *deve* essere accesa: se nella
            # striscia non c'e proprio giallo, il sottotitolo non e li. Non e un
            # dubbio, e un difetto.
            esito["in_sincrono"] = False
            esito["nota"] = "nessun giallo nella striscia: il sottotitolo non e a schermo"
        elif atteso_i >= 0:
            esito["in_sincrono"] = None
            esito["nota"] = (
                "il giallo c'e ma non ha il contorno del sottotitolo: "
                "la sua posizione non e misurabile"
            )
        else:
            esito["in_sincrono"] = None
            esito["nota"] = "parola attiva non presente nella riga: non misurabile"

        esiti.append(esito)
        stato = {True: "ok", False: "SFASATO", None: "?"}[esito["in_sincrono"]]
        log(
            f"  {n}. {esito['istante_video']}  '{attesa}'  "
            f"transcript: {'ok' if coerente else 'DIVERSO (' + str(detta) + ')'}  "
            f"pixel: {stato}"
            + (
                f"  (acceso sembra '{esito['parola_accesa_stimata']}')"
                if esito.get("in_sincrono") is False and "parola_accesa_stimata" in esito
                else ""
            )
        )

    rotti = [e for e in esiti if e["in_sincrono"] is False or not e["coerente_col_transcript"]]
    dubbi = [e for e in esiti if e["in_sincrono"] is None]

    if rotti:
        log(
            f"\n{len(rotti)} campioni su {len(esiti)} non tornano. "
            f"Guarda i fotogrammi in {work}: il nome dice la parola attesa."
        )
    elif dubbi:
        log(
            f"\n{len(dubbi)} campioni non misurabili, gli altri sono in sincrono. "
            f"Guarda i fotogrammi."
        )
    else:
        log(f"\n{len(esiti)} campioni su {len(esiti)} in sincrono, sui pixel e sul transcript.")

    emit(
        {
            "slug": args.slug,
            "ok": not rotti,
            "samples": esiti,
            "troppo_brevi_non_campionati": troppo_brevi,
        }
    )
    if rotti:
        sys.exit(1)


if __name__ == "__main__":
    main()
