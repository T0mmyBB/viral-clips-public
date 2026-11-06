"""Crop dinamico 9:16 che insegue il soggetto.

Produce un file di comandi `sendcmd` per ffmpeg che muove la finestra di ritaglio
fotogramma per fotogramma, cosi il soggetto resta inquadrato invece di uscire dal
bordo come farebbe un crop centrale fisso.

    python track_subject.py <work_dir> --start 120 --end 165

Tre cose lo distinguono da un inseguimento ingenuo:

1. **Gli stacchi non si attraversano.** Se tra due posizioni c'e un cambio scena,
   la finestra salta invece di fare una panoramica. Una panoramica che attraversa
   un taglio e il difetto che rende una clip riconoscibilmente automatica.
2. **La ricucitura delle tracce guarda le scene, non solo il tempo.** Sul materiale
   di test 5 rotture di traccia su 9 coincidevano con tagli di camera veri: unirle
   per sola vicinanza temporale avrebbe creato proprio l'artefatto del punto 1.
3. **Il movimento e voluto, non solo reattivo.** Uno scatto di zoom sulle parole
   che Claude ha scelto, e un cambio di scala a ogni taglio dei silenzi: senza,
   anche un tracking perfetto sembra una webcam.

Questo file decide **chi** inquadrare e **dove si trova**; come ci arriva la
camera — pose, movimenti, zoom, il crop fotogramma per fotogramma — lo decide
`camera.py`, che e solo aritmetica e ha i suoi test.
"""

from __future__ import annotations

import argparse
import math
import statistics
from bisect import bisect_right
from pathlib import Path

import cv2
import numpy as np

import camera
import tagli as tagli_mod
from common import YUNET_MODEL, analysis_source, emit, log, read_json, write_json
from faces import sample_frames

# Campionamento denso: gira solo sull'intervallo della clip, non sull'ora intera.
DENSE_FPS = 10.0

# Rilevamento. La soglia e volutamente permissiva: un falso positivo isolato viene
# scartato dalla continuita temporale, un buco no.
SCORE_THRESHOLD = 0.6
NMS_THRESHOLD = 0.3
DETECT_WIDTH = 640

# Ricucitura delle tracce.
STITCH_MAX_GAP = 1.2  # secondi
STITCH_MIN_IOU = 0.20

# **La camera sta ferma finche non ha una ragione per muoversi.**
#
# Misurato su un verticale di riferimento gia montato: dentro una singola
# inquadratura il volto si sposta del 15% della larghezza e il rapporto tra
# percorso e spostamento netto e 2.9. Le nostre clip, prima della zona morta,
# stavano a 63 — con punte oltre 400: la camera inseguiva ogni sbandata del
# rilevamento. La cura non e filtrare di piu, e **decidere di meno**. Zona morta,
# posa minima e stacco al 30% stanno in `camera.py`, **come frazione del
# ritaglio** e mai in pixel: da un 4K la finestra 9:16 e larga il doppio che da
# un 1080p.

# **Sotto questa durata di inquadratura la camera non si muove piu dentro le
# inquadrature** — si riposiziona solo sugli stacchi del girato, vedi plan_crop.
#
# Se il girato stacca ogni due secondi, il montaggio la varieta la fa gia da
# solo, e ogni nostro riposizionamento si somma al suo: da fuori non si legge
# come regia, si legge come tremolio. Misurato sul materiale che lo ha rivelato
# — un 4K girato in mezzo alla folla, 568 stacchi in 33 minuti — la durata
# mediana delle nostre inquadrature era 2.2 s contro i 3.4 s del riferimento,
# con punte a 0.8 s.
#
# C'e anche una ragione che riguarda *cosa* si insegue: in un materiale cosi i
# volti rilevati sono in larga parte passanti, quindi il soggetto "scelto" non e
# nemmeno la persona del video. Restare fermi non e una rinuncia, e la risposta
# corretta quando non ci sono le condizioni per decidere.
# Il valore non e tarato su questo video: e la durata di inquadratura del
# **verticale di riferimento montato a mano** (3.4 s, la stessa misura che regge
# i tre parametri qui sopra). La regola che ne discende si dice in una riga: se
# il girato stacca piu spesso di quanto stacchi un montatore umano, la regia
# c'e gia e la nostra si somma soltanto.
INQUADRATURA_TROPPO_BREVE = 3.4

# **Niente spinta lenta lungo tutta la clip** (14 settembre 2026). Un 5,5% di
# zoom distribuito su 45 secondi, con un crop a pixel interi, non e un movimento
# continuo: e un pixel ogni mezzo secondo, cioe un tic periodico — misurato, su
# ogni clip. Con proporzioni bloccate e senza allineamento a pixel pari resta
# comunque uno scatto di 1-2 px ogni secondo e mezzo. Il movimento voluto lo
# fanno adesso gli scatti d'enfasi e i cambi di scala ai tagli, che sono veloci
# abbastanza da non avere gradini.


# --------------------------------------------------------------------------
# Rilevamento
# --------------------------------------------------------------------------


def make_detector(width: int, height: int) -> cv2.FaceDetectorYN:
    if not YUNET_MODEL.exists():
        raise SystemExit(f"modello YuNet mancante: {YUNET_MODEL}. Lancia setup.ps1.")
    return cv2.FaceDetectorYN.create(
        str(YUNET_MODEL),
        "",
        (width, height),
        score_threshold=SCORE_THRESHOLD,
        nms_threshold=NMS_THRESHOLD,
        top_k=20,
    )


def mouth_motion(prev_gray, gray, box, landmarks) -> float:
    """Quanto si muove la bocca rispetto al resto del volto.

    I 5 punti di YuNet danno gli *angoli* della bocca, non la sua apertura: come
    segnale per "chi sta parlando" sarebbero inutili. Si guarda invece quanto
    cambiano i pixel nella zona della bocca tra due fotogrammi, normalizzato sul
    cambiamento dell'intero volto - cosi un movimento di testa o un cambio di luce,
    che muovono tutto insieme, non vengono scambiati per parlato.
    """
    if prev_gray is None:
        return 0.0

    x, y, w, h = box
    face = (int(x), int(y), int(w), int(h))
    if face[2] < 12 or face[3] < 12:
        return 0.0

    # Punto medio tra i due angoli della bocca.
    mx = (landmarks[3][0] + landmarks[4][0]) / 2.0
    my = (landmarks[3][1] + landmarks[4][1]) / 2.0
    mw = max(8.0, abs(landmarks[4][0] - landmarks[3][0]) * 1.6)
    mh = max(6.0, mw * 0.75)

    H, W = gray.shape

    def crop(img, cx, cy, cw, ch):
        x0 = max(0, int(cx - cw / 2))
        x1 = min(W, int(cx + cw / 2))
        y0 = max(0, int(cy - ch / 2))
        y1 = min(H, int(cy + ch / 2))
        if x1 - x0 < 4 or y1 - y0 < 4:
            return None
        return img[y0:y1, x0:x1]

    m_now, m_prev = crop(gray, mx, my, mw, mh), crop(prev_gray, mx, my, mw, mh)
    f_now, f_prev = (
        crop(gray, face[0] + face[2] / 2, face[1] + face[3] / 2, face[2], face[3]),
        crop(prev_gray, face[0] + face[2] / 2, face[1] + face[3] / 2, face[2], face[3]),
    )
    if m_now is None or m_prev is None or f_now is None or f_prev is None:
        return 0.0
    if m_now.shape != m_prev.shape or f_now.shape != f_prev.shape:
        return 0.0

    mouth_d = float(np.mean(cv2.absdiff(m_now, m_prev)))
    face_d = float(np.mean(cv2.absdiff(f_now, f_prev)))
    return mouth_d / (face_d + 1.0)


def dense_scan(
    video: Path, start: float, end: float, fps: float, coord_scale: float = 1.0
) -> list[dict]:
    """Campiona la clip e rileva i volti, misurando anche il movimento della bocca.

    I fotogrammi arrivano da `faces.sample_frames`, che li estrae con una pipe
    ffmpeg: il seek per timestamp di OpenCV su molti codec non riavvolge affatto
    e restituisce zero fotogrammi.
    """
    frames: list[dict] = []
    detector = None
    prev_gray = None

    for t, frame, scale in sample_frames(video, fps, DETECT_WIDTH, start, end):
        if detector is None:
            detector = make_detector(frame.shape[1], frame.shape[0])

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        _, dets = detector.detect(frame)

        # `scale` riporta dal fotogramma ridotto al video letto; `coord_scale`
        # da quel video al sorgente, quando si analizza il proxy.
        s = scale * coord_scale
        faces = []
        if dets is not None:
            for d in dets:
                x, y, w, h = float(d[0]), float(d[1]), float(d[2]), float(d[3])
                lm = [(float(d[4 + 2 * k]), float(d[5 + 2 * k])) for k in range(5)]
                faces.append(
                    {
                        "box": [x * s, y * s, w * s, h * s],
                        "center": [(x + w / 2) * s, (y + h / 2) * s],
                        "area": (w * h) * s * s,
                        "score": float(d[14]),
                        "mouth": mouth_motion(prev_gray, gray, (x, y, w, h), lm),
                    }
                )
        prev_gray = gray
        frames.append({"t": round(t - start, 3), "faces": faces})

    return frames


# --------------------------------------------------------------------------
# Tracce
# --------------------------------------------------------------------------


def iou(a: list[float], b: list[float]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    return inter / (aw * ah + bw * bh - inter)


def build_tracks(frames: list[dict], cuts: list[float]) -> list[dict]:
    """Raggruppa i rilevamenti in tracce, poi le ricuce.

    La regola di ricucitura e il risultato di un'analisi sul materiale reale:
    unire due tracce solo perche il buco e breve spezza il caso in cui il buco *e*
    un taglio di camera. Si unisce quindi solo se il buco e breve **e** dentro quel
    buco non cade nessun cambio scena.
    """
    tracks: list[dict] = []
    active: list[dict] = []

    for fr in frames:
        t = fr["t"]
        unmatched = list(fr["faces"])
        still: list[dict] = []

        for tr in active:
            if t - tr["last_t"] > STITCH_MAX_GAP:
                tracks.append(tr)
                continue
            best, best_iou = None, STITCH_MIN_IOU
            for face in unmatched:
                score = iou(tr["last_box"], face["box"])
                if score > best_iou:
                    best, best_iou = face, score
            if best is not None:
                unmatched.remove(best)
                tr["last_t"] = t
                tr["last_box"] = best["box"]
                tr["end"] = t
                tr["samples"].append(
                    {"t": t, **{k: best[k] for k in ("center", "box", "area", "mouth")}}
                )
            still.append(tr)

        for face in unmatched:
            still.append(
                {
                    "start": t,
                    "end": t,
                    "last_t": t,
                    "last_box": face["box"],
                    "samples": [
                        {"t": t, **{k: face[k] for k in ("center", "box", "area", "mouth")}}
                    ],
                }
            )
        active = still

    tracks.extend(active)

    # Ricucitura, con i cambi scena come autorita.
    tracks.sort(key=lambda tr: tr["start"])
    merged: list[dict] = []
    for tr in tracks:
        if merged:
            prev = merged[-1]
            gap = tr["start"] - prev["end"]
            cut_inside = any(prev["end"] - 0.05 <= c <= tr["start"] + 0.05 for c in cuts)
            box_ok = iou(prev["samples"][-1]["box"], tr["samples"][0]["box"]) >= STITCH_MIN_IOU
            if 0 <= gap <= STITCH_MAX_GAP and not cut_inside and box_ok:
                prev["samples"].extend(tr["samples"])
                prev["end"] = tr["end"]
                continue
        merged.append(tr)

    for i, tr in enumerate(merged):
        tr["id"] = i
        tr["duration"] = round(tr["end"] - tr["start"], 2)
        tr.pop("last_t", None)
        tr.pop("last_box", None)
    return merged


# Sotto questo scarto tra il primo e il secondo candidato, "chi parla" non e
# distinguibile e non si sceglie nessuno.
SPEAKER_MARGIN = 0.18


def centroid(faces: list[dict]) -> list[float]:
    """Punto medio tra i volti: l'inquadratura larga quando non si sa chi parla."""
    return [
        sum(f["center"][0] for f in faces) / len(faces),
        sum(f["center"][1] for f in faces) / len(faces),
    ]


def indice_inquadratura(t: float, cuts: list[float]) -> int:
    """A quale inquadratura del girato appartiene questo istante."""
    return sum(1 for c in cuts if c <= t)


def punteggio(campione: dict) -> float:
    """Quanto un volto merita di essere il soggetto: parla, ed e vicino."""
    return campione["mouth"] * 2.2 + math.sqrt(campione["area"]) / 220.0


def scegli_per_inquadratura(
    frames: list[dict],
    tracks: list[dict],
    cuts: list[float],
    src_w: float,
) -> tuple[list[float | None], list[float | None], list[float | None], list[bool]]:
    """Un soggetto per **inquadratura**, non uno per fotogramma.

    **E la correzione del difetto piu grave visto finora sul crop.** Scegliendo a
    ogni fotogramma, su materiale con piu volti in campo il vincitore cambiava di
    continuo — e siccome oltre il 30% di spostamento la camera stacca invece di
    inseguire, ogni cambio di idea diventava un salto. Misurato su una clip di 47 s
    con 18 cambi scena: la finestra attraversava il fotogramma quattro volte in
    quattro secondi, e **la maggior parte dei salti non cadeva su un cambio
    scena**. Da fuori si vede come tremolio e come inquadratura sbagliata; e la
    stessa causa.

    Qui la decisione si prende una volta per inquadratura e non si rivede: dentro
    un'inquadratura la camera puo seguire il soggetto che si muove, ma non cambia
    idea su **chi** sia. I riposizionamenti tornano cosi a essere quanti sono gli
    stacchi del girato, che e esattamente quello che fa il montaggio di
    riferimento.

    La traccia vincente non e quella che parla di piu in un istante, ma quella con
    il punteggio migliore **pesato su quanto e presente**: un volto che attraversa
    l'inquadratura per due fotogrammi non porta via la camera a chi c'e sempre.
    """
    per_inquadratura: dict[int, list[dict]] = {}
    for fr in frames:
        per_inquadratura.setdefault(indice_inquadratura(fr["t"], cuts), []).append(fr)

    # Ogni traccia, indicizzata per istante: serve a sapere dov'era il soggetto
    # scelto in ciascun fotogramma dell'inquadratura.
    campioni_per_traccia = [{round(s["t"], 3): s for s in tr["samples"]} for tr in tracks]

    raw_x: list[float | None] = [None] * len(frames)
    raw_y: list[float | None] = [None] * len(frames)
    # L'altezza del volto scelto: serve a sapere quanto si puo stringere prima di
    # tagliare la testa.
    raw_h: list[float | None] = [None] * len(frames)
    wide: list[bool] = [False] * len(frames)
    posizione = {id(fr): i for i, fr in enumerate(frames)}

    for gruppo in per_inquadratura.values():
        istanti = {round(fr["t"], 3) for fr in gruppo}
        n = len(gruppo)

        valutate = []
        for campioni in campioni_per_traccia:
            dentro = [s for t, s in campioni.items() if t in istanti]
            if not dentro:
                continue
            copertura = len(dentro) / n
            medio = sum(punteggio(s) for s in dentro) / len(dentro)
            valutate.append((medio * copertura, copertura, dentro, campioni))

        if not valutate:
            continue  # nessun volto: restano None, si sta al centro

        valutate.sort(key=lambda v: -v[0])
        migliore = valutate[0]

        # Due soggetti quasi pari: non si sceglie nessuno per **tutta**
        # l'inquadratura. Alternare fra i due e il difetto che si sta togliendo,
        # e farlo a meta inquadratura non sarebbe meno peggio.
        ambigua = len(valutate) > 1 and (valutate[0][0] - valutate[1][0]) < SPEAKER_MARGIN

        for fr in gruppo:
            i = posizione[id(fr)]
            t = round(fr["t"], 3)
            if ambigua:
                if fr["faces"]:
                    c = centroid(fr["faces"])
                    raw_x[i], raw_y[i], wide[i] = c[0], c[1], True
                continue
            campione = migliore[3].get(t)
            if campione is not None:
                raw_x[i], raw_y[i] = campione["center"][0], campione["center"][1]
                raw_h[i] = campione["box"][3]

    return raw_x, raw_y, raw_h, wide


# --------------------------------------------------------------------------
# Geometria
# --------------------------------------------------------------------------


def base_window(src_w: int, src_h: int) -> tuple[int, int]:
    """La finestra 9:16 piu grande dentro il fotogramma, a zoom 1.

    Serve anche *prima* di analizzare: da quanto e grande questa finestra
    rispetto al fotogramma dipende se ci sia qualcosa da inseguire, e se uno
    zoom costi nitidezza invece di darne.
    """
    base_h = src_h - (src_h % 2)
    base_w = int(round(base_h * 9 / 16))
    base_w -= base_w % 2
    if base_w > src_w:  # sorgente gia verticale o quadrata
        base_w = src_w - (src_w % 2)
        base_h = int(round(base_w * 16 / 9))
        base_h -= base_h % 2
        base_h = min(base_h, src_h - (src_h % 2))
    return base_w, base_h


def scene_cuts(work: Path, video: Path, start: float, end: float) -> list[float]:
    """Gli stacchi del girato dentro la clip, relativi a `start`, al fotogramma esatto."""
    sig_path = work / "signals.json"
    if not sig_path.exists():
        return []
    from signals import refine_cuts

    sig = read_json(sig_path)
    grezzi = [s["start"] - start for s in sig.get("scenes", [])[1:] if start < s["start"] < end]
    return refine_cuts(video, start, end, grezzi)


def emphasis_times(words: list[dict], keywords: list[str], start: float, end: float) -> list[float]:
    """Dove cadono, nella clip, le parole che Claude ha scelto di evidenziare.

    Sono gli istanti candidati agli scatti di zoom: la stessa scelta editoriale
    che accende una parola nei sottotitoli decide quando la camera stringe. Il
    picco d'energia dell'audio, che si usava prima, cade sulla sillaba piu forte,
    non sulla parola che conta.
    """
    chiavi = {str(k).strip().lower() for k in keywords if str(k).strip()}
    if not chiavi:
        return []
    out = []
    for w in words:
        if not (start <= w["start"] < end):
            continue
        nuda = w["word"].strip(".,!?;:\"'()").lower()
        if nuda in chiavi:
            out.append(round(w["start"] - start, 3))
    return out


# --------------------------------------------------------------------------


def plan_crop(
    work: Path,
    start: float,
    end: float,
    fps: float = DENSE_FPS,
    motion: bool = True,
    name: str | None = None,
    tenuti: list[tuple[float, float]] | None = None,
    enfasi: list[float] | None = None,
    fps_out: float | None = None,
    stacchi: list[float] | None = None,
) -> dict:
    """Calcola il percorso del ritaglio e scrive il file di comandi.

    `tenuti` sono i tratti della clip che sopravvivono ai tagli dei silenzi, in
    secondi relativi a `start` (default: tutta la clip); il file di comandi e nel
    tempo della clip **montata**, cioe quello che vede il filtro crop dopo il
    concat. `enfasi` sono gli istanti relativi delle parole chiave. Ritorna la
    diagnostica; il percorso del file sta in `path`.
    """
    meta = read_json(work / "meta.json")
    src_w, src_h = int(meta["width"]), int(meta["height"])
    duration = end - start
    tenuti = [tuple(t) for t in tenuti] if tenuti else [(0.0, duration)]
    durata_uscita = sum(b - a for a, b in tenuti)
    fps_out = float(fps_out or meta.get("fps") or 30.0)

    # **Su un sorgente gia verticale non c'e niente da reinquadrare.**
    #
    # La finestra 9:16 coincide con il fotogramma: non puo spostarsi, e ogni
    # ingrandimento la stringe *sotto* la risoluzione nativa costringendo il
    # render a riportarla in su. Misurato sul caso limite: la spinta lenta
    # portava la finestra da 1080 a 954 px, cioe un ingrandimento di 1.13x su un
    # sorgente che non ne aveva bisogno — mentre il render annunciava "riduzione,
    # nessuna perdita di nitidezza". Il difetto non era il movimento: era che il
    # movimento **costava qualita** e nessuno lo diceva.
    #
    # Su un sorgente piu alto del 9:16 la finestra e comunque larga quanto il
    # fotogramma, quindi lo zoom costa allo stesso modo, ma in verticale ha
    # ancora dove scorrere: si toglie l'ingrandimento e si tiene l'inseguimento.
    base_w, base_h = base_window(src_w, src_h)
    fermo = base_w >= src_w and base_h >= src_h
    if base_w >= src_w and motion:
        motion = False
        log(
            "  sorgente gia verticale: niente scatti di zoom, "
            "stringerebbero sotto la risoluzione nativa"
        )
    if fermo:
        # La finestra e l'intero fotogramma e non ha dove muoversi: la scansione
        # densa leggerebbe centinaia di fotogrammi per non poter decidere niente.
        log(
            "  la finestra 9:16 e gia tutto il fotogramma: nessun crop dinamico, "
            "il sorgente passa intero"
        )
        diag = {
            "path": None,
            "start": start,
            "end": end,
            "static": True,
            "reason": "sorgente gia nel formato 9:16",
            "base_window": [base_w, base_h],
        }
        write_json(work / f"{name or f'crop_{int(start)}_{int(end)}'}.json", diag)
        return diag

    # Cambi scena, riportati a tempo relativo alla clip e al fotogramma esatto.
    analysis_video, coord_scale = analysis_source(meta, work)
    cuts = list(stacchi) if stacchi is not None else scene_cuts(work, analysis_video, start, end)
    log(f"{len(cuts)} cambi scena dentro la clip, {len(tenuti) - 1} tagli dei silenzi")

    log(f"scansione densa a {fps} fps su {duration:.1f}s")
    if coord_scale != 1.0:
        log(f"  analizzo il proxy, coordinate riscalate x{coord_scale:.2f}")
    frames = dense_scan(analysis_video, start, end, fps, coord_scale)
    if not frames:
        raise SystemExit(
            f"nessun fotogramma letto tra {start}s e {end}s: "
            f"l'intervallo e fuori dalla durata del video ({meta.get('duration', '?')}s)?"
        )
    tracks = build_tracks(frames, cuts)
    detected = sum(1 for f in frames if f["faces"])
    log(
        f"{len(frames)} fotogrammi, volto in {detected} ({detected / max(1, len(frames)):.0%}), "
        f"{len(tracks)} tracce dopo la ricucitura"
    )

    times = [f["t"] for f in frames]
    raw_x, raw_y, raw_h, wide = scegli_per_inquadratura(frames, tracks, cuts, src_w)

    # Il girato stacca troppo spesso perche abbia senso inseguire: si sceglie una
    # posizione sola per tutta la clip e non ci si muove piu. La posizione e la
    # **mediana** di dove sono stati i soggetti, non il centro del fotogramma:
    # ferma si, ma ferma dove succede qualcosa.
    durata_media_inquadratura = duration / max(1, len(cuts) + 1)
    noti = [v for v in raw_x if v is not None]
    if durata_media_inquadratura < INQUADRATURA_TROPPO_BREVE and noti:
        fisso_x = statistics.median(noti)
        noti_y = [v for v in raw_y if v is not None]
        fisso_y = statistics.median(noti_y) if noti_y else src_h / 2.0
        # **Ferma dentro ogni inquadratura, ma non ferma per tutta la clip**
        # (14 settembre 2026). Una posizione sola per la clip intera tagliava il
        # soggetto: su dieci clip di un vlog montato veloce, 46 inquadrature su 188
        # col volto dentro la finestra per meno dell'80%, e in una clip sei primi
        # piani col volto tagliato a meta. Adesso ogni inquadratura in cui il
        # soggetto e sicuro ha la sua posizione, e la finestra la cambia **solo
        # sullo stacco**, al fotogramma esatto: li l'immagine cambia comunque, e
        # un nostro movimento non si somma a nulla. Dove il soggetto non e sicuro
        # — inquadratura corta, volto piccolo, presente di rado, cioe il passante
        # in mezzo alla folla — resta la posizione di tutta la clip.
        raw_x, raw_y, riposizionate = camera.lock_per_shot(
            times, raw_x, raw_y, raw_h, cuts, duration, src_h, base_w, (fisso_x, fisso_y)
        )
        log(
            f"  il girato stacca ogni {durata_media_inquadratura:.1f}s: niente movimento dentro "
            f"le inquadrature; {riposizionate} su {len(cuts) + 1} con il soggetto "
            f"inquadrato, le altre ferme a x={fisso_x:.0f}"
        )
        wide = [True] * len(times)  # niente zoom: non si sa chi inquadrare

    n_wide = sum(wide)
    if n_wide:
        log(f"  {n_wide} fotogrammi con piu volti e segnale ambiguo: inquadratura larga")
    senza = sum(1 for v in raw_x if v is None)
    if senza:
        log(
            f"  {senza} fotogrammi senza soggetto: in quelle inquadrature la finestra "
            f"usa la posizione nota piu vicina, o il centro"
        )
    log(
        f"  {len(set(indice_inquadratura(t, cuts) for t in times))} inquadrature nel girato: "
        f"un soggetto ciascuna, deciso una volta sola"
    )

    # Il piano si fa sul tempo del sorgente. Anche i tagli dei silenzi sono
    # confini: li l'immagine salta comunque, e riposizionarsi dentro un salto non
    # si vede — mentre un movimento a cavallo di un taglio arriverebbe a meta.
    confini = sorted(set(cuts) | {a for a, _ in tenuti[1:]})
    dead_zone = camera.DEAD_ZONE_FRAC * base_w
    salto = camera.CUT_INSTEAD_OF_PAN_FRAC * base_w
    velocita = camera.MOVE_SPEED_FRAC * base_w
    piano_x = camera.plan_axis(
        times, raw_x, confini, src_w / 2.0, dead_zone, salto, velocita, duration
    )
    piano_y = camera.plan_axis(
        times, raw_y, confini, src_h / 2.0, dead_zone, salto, velocita, duration
    )

    # Quanto si puo stringere senza tagliare la testa: la mediana dell'altezza del
    # volto scelto, inquadratura per inquadratura.
    altezze: dict[int, list[float]] = {}
    for t, h in zip(times, raw_h):
        if h:
            altezze.setdefault(indice_inquadratura(t, cuts), []).append(h)
    volto = {k: statistics.median(v) for k, v in altezze.items()}

    # Scala di ogni tratto tenuto: fra due spezzoni della **stessa** inquadratura,
    # separati da un taglio dei silenzi, si alterna. Il salto del soggetto al
    # taglio diventa un cambio di camera voluto. A un cambio scena si riparte da 1.
    scala: list[float] = []
    for j, (a, b) in enumerate(tenuti):
        if not motion or j == 0:
            scala.append(1.0)
            continue
        stessa = indice_inquadratura(a, cuts) == indice_inquadratura(tenuti[j - 1][1] - 1e-3, cuts)
        scala.append((camera.ZOOM_CUT if scala[-1] == 1.0 else 1.0) if stessa else 1.0)

    candidati = []
    # Dove l'inquadratura e larga dappertutto nessuno scatto verrebbe applicato:
    # meglio non elencarne nella diagnostica.
    for t in (enfasi or []) if motion and not all(wide) else []:
        t_out = tagli_mod.to_output(tenuti, t)
        if t_out is not None:
            candidati.append(t_out)
    # I bordi che uno scatto non deve toccare, nel tempo della clip montata: gli
    # stacchi del girato sopravvissuti ai tagli, e i tagli stessi.
    bordi = [b for b in (tagli_mod.to_output(tenuti, c) for c in cuts) if b is not None]
    bordi += tagli_mod.offsets(tenuti)[1:]
    scatti = camera.pick_punches(candidati, durata_uscita, bordi)

    n_out = max(1, int(round(durata_uscita * fps_out)))
    finestre = []
    for k in range(n_out):
        t_out = k / fps_out
        t_src = tagli_mod.to_source(tenuti, t_out)
        i_camp = max(0, bisect_right(times, t_src) - 1)
        cx = camera.position(piano_x, t_src)
        cy = camera.position(piano_y, t_src)
        if wide[i_camp] or not motion:
            z = 1.0
        else:
            j = tagli_mod.segment_of(tenuti, t_out)
            tetto = camera.max_zoom_for_face(base_h, volto.get(indice_inquadratura(t_src, cuts)))
            z = min(scala[j] * camera.punch_envelope(t_out, scatti), tetto, camera.ZOOM_MAX)
        finestre.append(camera.window(cx, cy, z, base_w, base_h, src_w, src_h))

    righe = camera.sendcmd_lines(finestre, fps_out)
    name = name or f"crop_{int(start)}_{int(end)}"
    cmd_path = work / f"{name}.sendcmd"
    cmd_path.write_text("\n".join(righe) + "\n", encoding="utf-8")

    movimenti = sum(1 for p in (piano_x, piano_y) for tr in p if tr[2] != tr[3])
    xs = [f[2] for f in finestre]
    diag = {
        "path": str(cmd_path),
        "start": start,
        "end": end,
        "commands": len(righe),
        "fps_out": fps_out,
        "frames": len(frames),
        "face_ratio": round(detected / max(1, len(frames)), 3),
        "tracks": len(tracks),
        "scene_cuts": len(cuts),
        "silence_cuts": len(tenuti) - 1,
        "zoom_cuts": sum(1 for s in scala if s != 1.0),
        "punches": scatti,
        # Stacchi del girato e tagli dei silenzi nel tempo della clip montata:
        # li legge `check_camera.py`, per non contare come tic la finestra che
        # cambia apposta su uno stacco.
        "bordi": sorted(round(b, 3) for b in bordi),
        "camera_moves": movimenti,
        "pan_range_px": [min(xs), max(xs)] if xs else None,
        "base_window": [base_w, base_h],
    }
    write_json(work / f"{name}.json", diag)
    log(
        f"panoramica tra x={min(xs)} e x={max(xs)} px, {movimenti} movimenti, "
        f"{len(scatti)} scatti d'enfasi, {diag['zoom_cuts']} cambi di scala ai tagli, "
        f"{len(righe)} comandi"
    )
    return diag


def build_crop_path(work: Path, start: float, end: float, **kwargs) -> Path | None:
    """Comodita per render_clip.py: calcola il crop e ritorna solo il percorso.

    Ritorna `None` quando non c'e nessun crop da fare — sorgente gia 9:16 — e in
    quel caso il render usa la finestra statica, che li e il fotogramma intero.
    """
    diag = plan_crop(work, start, end, **kwargs)
    return Path(diag["path"]) if diag.get("path") else None


def main() -> None:
    ap = argparse.ArgumentParser(description="Crop dinamico 9:16 che insegue il soggetto")
    ap.add_argument("work_dir")
    ap.add_argument("--start", type=float, required=True)
    ap.add_argument("--end", type=float, required=True)
    ap.add_argument("--fps", type=float, default=DENSE_FPS)
    ap.add_argument("--name", default=None, help="nome del file di uscita")
    ap.add_argument(
        "--no-motion",
        action="store_true",
        help="disattiva scatti e cambi di scala, lascia solo l'inseguimento",
    )
    args = ap.parse_args()

    emit(
        plan_crop(
            Path(args.work_dir),
            args.start,
            args.end,
            fps=args.fps,
            motion=not args.no_motion,
            name=args.name,
        )
    )


if __name__ == "__main__":
    main()
