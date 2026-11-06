"""Rilevamento e tracciamento dei volti con YuNet.

Passata A (questo script, su tutto il video, ~3 fps): quante persone ci sono e
dove, momento per momento. Serve a Claude per capire di che tipo di contenuto si
tratta - un primo piano che parla, due interlocutori, una condivisione schermo -
e serve al render per decidere quando ha senso lo split screen.

    python faces.py work/<slug>

Passata B (in track_subject.py, solo sulle clip approvate, ~15 fps): tracciamento
denso, chi sta parlando, percorso di crop. Costosa, quindi la paghiamo solo sui
30-60 secondi che renderizziamo davvero, non su un'ora intera.

I 5 landmark di YuNet sono, in ordine: occhio destro, occhio sinistro, punta del
naso, angolo destro della bocca, angolo sinistro della bocca.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np

from common import YUNET_MODEL, analysis_source, emit, log, read_json, tool, write_json

# YuNet lavora bene su frame piccoli e ci fa risparmiare molto tempo di decodifica.
# 640 di larghezza tiene i volti sopra i ~40 px anche in campo medio.
SAMPLE_WIDTH = 640
SCAN_FPS = 3.0
SCORE_THRESHOLD = 0.7


def sample_frames(
    video: Path, fps: float, width: int, start: float | None = None, end: float | None = None
):
    """Frame campionati via pipe ffmpeg.

    Molto piu veloce di leggerli uno a uno con OpenCV: il campionamento e il
    ridimensionamento li fa ffmpeg in C, e noi riceviamo solo i frame che
    servono gia della dimensione giusta.
    """
    cmd = [tool("ffmpeg"), "-hide_banner", "-loglevel", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if end is not None and start is not None:
        cmd += ["-t", f"{max(0.0, end - start):.3f}"]
    cmd += [
        "-i",
        str(video),
        "-vf",
        f"fps={fps},scale={width}:-2",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-",
    ]

    probe = subprocess.run(
        [
            tool("ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(video),
        ],
        capture_output=True,
        text=True,
    )
    src_w, src_h = (int(x) for x in probe.stdout.strip().split(",")[:2])
    out_w = width if width % 2 == 0 else width + 1
    out_h = int(round(src_h * out_w / src_w))
    out_h += out_h % 2
    frame_bytes = out_w * out_h * 3

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    scale = src_w / out_w  # per riportare le coordinate all'originale
    idx = 0
    try:
        while True:
            raw = proc.stdout.read(frame_bytes)
            if len(raw) < frame_bytes:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(out_h, out_w, 3)
            t = (start or 0.0) + idx / fps
            yield t, frame, scale
            idx += 1
    finally:
        proc.stdout.close()
        proc.wait()


def make_detector(width: int, height: int):
    import cv2

    if not YUNET_MODEL.exists():
        raise SystemExit(f"modello YuNet mancante: {YUNET_MODEL} - lancia setup.ps1")
    return cv2.FaceDetectorYN.create(
        str(YUNET_MODEL),
        "",
        (width, height),
        score_threshold=SCORE_THRESHOLD,
        nms_threshold=0.3,
        top_k=20,
    )


def detect(detector, frame: np.ndarray, scale: float) -> list[dict]:
    """Volti di un frame, con coordinate riportate alla risoluzione originale."""
    h, w = frame.shape[:2]
    detector.setInputSize((w, h))
    _, faces = detector.detect(frame)
    if faces is None:
        return []

    out = []
    for f in faces:
        x, y, bw, bh = (float(v) * scale for v in f[:4])
        lm = [(float(f[4 + 2 * i]) * scale, float(f[5 + 2 * i]) * scale) for i in range(5)]
        out.append(
            {
                "box": [round(x, 1), round(y, 1), round(bw, 1), round(bh, 1)],
                "center": [round(x + bw / 2, 1), round(y + bh / 2, 1)],
                "landmarks": [[round(a, 1), round(b, 1)] for a, b in lm],
                "score": round(float(f[14]), 3),
                "area": round(bw * bh, 1),
            }
        )
    # Il piu grande per primo: nella stragrande maggioranza dei casi e il soggetto.
    out.sort(key=lambda d: -d["area"])
    return out


# --------------------------------------------------------------------------
# Tracciamento
# --------------------------------------------------------------------------


def iou(a: list[float], b: list[float]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    return inter / (aw * ah + bw * bh - inter)


def build_tracks(frames: list[dict], max_gap_s: float = 1.0, min_iou: float = 0.25) -> list[dict]:
    """Associa i volti tra frame consecutivi con un matching greedy sull'IoU.

    Il tracciamento serve alla continuita del reframe: senza, l'inquadratura
    salterebbe tra due persone frame per frame in base a chi appare piu grande.
    """
    tracks: list[dict] = []
    active: list[dict] = []

    for entry in frames:
        t = entry["t"]
        unmatched = list(entry["faces"])
        still_active = []

        for tr in active:
            if t - tr["last_t"] > max_gap_s:
                tracks.append(tr)
                continue
            best, best_iou = None, min_iou
            for face in unmatched:
                score = iou(tr["last_box"], face["box"])
                if score > best_iou:
                    best, best_iou = face, score
            if best is not None:
                unmatched.remove(best)
                tr["last_box"] = best["box"]
                tr["last_t"] = t
                tr["end"] = t
                tr["samples"].append(
                    {
                        "t": round(t, 2),
                        "center": best["center"],
                        "box": best["box"],
                        "area": best["area"],
                    }
                )
            still_active.append(tr)

        for face in unmatched:
            still_active.append(
                {
                    "id": len(tracks) + len(still_active),
                    "start": t,
                    "end": t,
                    "last_t": t,
                    "last_box": face["box"],
                    "samples": [
                        {
                            "t": round(t, 2),
                            "center": face["center"],
                            "box": face["box"],
                            "area": face["area"],
                        }
                    ],
                }
            )
        active = still_active

    tracks.extend(active)

    out = []
    for tr in tracks:
        if len(tr["samples"]) < 2:
            continue  # rilevamento isolato: quasi sempre spurio
        areas = [s["area"] for s in tr["samples"]]
        out.append(
            {
                "id": len(out),
                "start": round(tr["start"], 2),
                "end": round(tr["end"], 2),
                "duration": round(tr["end"] - tr["start"], 2),
                "n_samples": len(tr["samples"]),
                "mean_area": round(float(np.mean(areas)), 1),
                "samples": tr["samples"],
            }
        )
    return sorted(out, key=lambda t: t["start"])


def layout_timeline(frames: list[dict], window: float = 5.0) -> list[dict]:
    """Quante persone inquadrate, per finestre di 5 secondi.

    'none' non e un errore: e una condivisione schermo, delle slide o del
    b-roll, e va trattata diversamente sia in selezione che in render.
    """
    if not frames:
        return []
    end = frames[-1]["t"]
    out = []
    t = 0.0
    while t < end:
        counts = [len(f["faces"]) for f in frames if t <= f["t"] < t + window]
        if not counts:
            t += window
            continue
        median = int(np.median(counts))
        kind = {0: "none", 1: "single", 2: "two"}.get(median, "many")
        out.append(
            {
                "start": round(t, 1),
                "end": round(min(t + window, end), 1),
                "faces": median,
                "layout": kind,
            }
        )
        t += window
    return out


def summarize(layout: list[dict]) -> dict:
    """Composizione dominante del video: aiuta a scegliere i criteri editoriali."""
    if not layout:
        return {"dominant": "unknown", "shares": {}}
    total = sum(l["end"] - l["start"] for l in layout)
    shares: dict[str, float] = {}
    for l in layout:
        shares[l["layout"]] = shares.get(l["layout"], 0.0) + (l["end"] - l["start"])
    shares = {k: round(v / total, 3) for k, v in sorted(shares.items(), key=lambda kv: -kv[1])}
    return {"dominant": next(iter(shares)), "shares": shares}


def scan(video: Path, fps: float = SCAN_FPS, coord_scale: float = 1.0) -> dict:
    """`coord_scale` riporta le coordinate al sorgente quando si analizza il
    proxy: senza, i box sarebbero in pixel del 480p e il render li userebbe su
    un 4K, sbagliando l'inquadratura di un fattore quattro."""
    frames: list[dict] = []
    detector = None
    n_with_face = 0

    for t, frame, scale in sample_frames(video, fps, SAMPLE_WIDTH):
        if detector is None:
            detector = make_detector(frame.shape[1], frame.shape[0])
        faces = detect(detector, frame, scale * coord_scale)
        if faces:
            n_with_face += 1
        frames.append({"t": round(t, 2), "faces": faces})
        if len(frames) % 300 == 0:
            log(f"  ... {t / 60:.0f} min analizzati")

    tracks = build_tracks(frames)
    layout = layout_timeline(frames)
    return {
        "scan_fps": fps,
        "n_frames": len(frames),
        "frames_with_face": n_with_face,
        "face_ratio": round(n_with_face / len(frames), 3) if frames else 0.0,
        "tracks": tracks,
        "layout": layout,
        "summary": summarize(layout),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Scansione volti su tutto il video")
    ap.add_argument("work_dir")
    ap.add_argument("--fps", type=float, default=SCAN_FPS)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    work = Path(args.work_dir)
    meta = read_json(work / "meta.json")
    out_path = work / "faces.json"

    if out_path.exists() and not args.force:
        log("scansione volti gia in cache")
        emit({"path": str(out_path), "cached": True})
        return

    log(f"scansione volti a {args.fps} fps")
    # Prescansione sul proxy: e una passata leggera che serve solo a sapere
    # "una faccia / due facce / nessuna", non ha bisogno della risoluzione piena.
    analysis_video, coord_scale = analysis_source(meta, work)
    result = scan(analysis_video, args.fps, coord_scale)
    result["coord_space"] = "sorgente"
    result["slug"] = meta["slug"]
    write_json(out_path, result)

    s = result["summary"]
    log(
        f"volto presente nel {result['face_ratio'] * 100:.0f}% dei frame; "
        f"composizione dominante: {s['dominant']}"
    )
    emit(
        {
            "path": str(out_path),
            "cached": False,
            "face_ratio": result["face_ratio"],
            "n_tracks": len(result["tracks"]),
            "summary": s,
        }
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
