"""Stage 2 - Segnali meccanici.

    python signals.py work/<slug>

Produce gli indizi oggettivi che Claude, leggendo solo la trascrizione, non puo
vedere: dove la voce si alza, dove si ride, dove si applaude, dove ci sono pause
sfruttabili come punti di taglio, dove stacca la camera.

Nessuno di questi segnali decide da solo se un momento e buono. Servono a
corroborare o smentire il giudizio sul testo: una battuta che il testo suggerisce
e che coincide con una risata vale piu di una che non la produce.

Sul rilevamento di risate e applausi: e un'euristica DSP, non un classificatore
addestrato. Riconosce bene i casi netti (una risata di gruppo, un applauso vero)
e sbaglia sui casi ambigui. Volutamente: un classificatore serio (YAMNet, PANNs)
costerebbe centinaia di MB di dipendenze per un segnale di contorno.

Le risate passano poi da un secondo filtro che **non costa niente**, perche usa
dati gia prodotti dalla trascrizione: dove ci sono parole riconosciute con
sicurezza non c'e una risata, c'e qualcuno che parla a ritmo sillabico. E il
rimedio al difetto peggiore dell'euristica — cercare modulazione fra 3 e 8 Hz
vuol dire cercare anche la frequenza delle sillabe.
"""

from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import numpy as np

from common import analysis_source, emit, log, read_json, require_audio, write_json

SR = 16000  # ingest.py produce sempre 16 kHz mono
FRAME = 512  # 32 ms
HOP = 256  # 16 ms
CHUNK_SECONDS = 300  # elabora 5 minuti alla volta: su un'ora l'FFT
# dell'intero file non entrerebbe in RAM


# --------------------------------------------------------------------------
# Lettura audio
# --------------------------------------------------------------------------


def read_wav_chunks(path: Path, chunk_seconds: int = CHUNK_SECONDS):
    """Legge il WAV a blocchi, come float32 in [-1, 1]."""
    with wave.open(str(path), "rb") as wf:
        if wf.getsampwidth() != 2 or wf.getnchannels() != 1:
            raise RuntimeError("atteso WAV PCM 16 bit mono (lo produce ingest.py)")
        rate = wf.getframerate()
        total = wf.getnframes()
        block = chunk_seconds * rate
        offset = 0
        while offset < total:
            raw = wf.readframes(min(block, total - offset))
            if not raw:
                break
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            yield offset / rate, samples, rate
            offset += len(samples)


def frame_signal(x: np.ndarray, frame: int, hop: int) -> np.ndarray:
    """Matrice (n_frame, frame) senza copiare i dati."""
    if len(x) < frame:
        return np.empty((0, frame), dtype=np.float32)
    n = 1 + (len(x) - frame) // hop
    return np.lib.stride_tricks.as_strided(
        x, shape=(n, frame), strides=(x.strides[0] * hop, x.strides[0])
    )


# --------------------------------------------------------------------------
# Caratteristiche per frame
# --------------------------------------------------------------------------


def frame_features(samples: np.ndarray, rate: int) -> dict[str, np.ndarray]:
    """RMS, zero-crossing rate e planarita spettrale, un valore per frame.

    - RMS: quanto e forte -> enfasi
    - ZCR: quanto e "rumoroso" rispetto a tonale -> applausi e consonanti
    - planarita spettrale: 1 = rumore bianco, 0 = tono puro. L'applauso e
      rumore a banda larga, la voce no.
    """
    frames = frame_signal(samples, FRAME, HOP)
    if frames.shape[0] == 0:
        empty = np.zeros(0, dtype=np.float32)
        return {"rms": empty, "zcr": empty, "flatness": empty}

    rms = np.sqrt(np.mean(frames.astype(np.float32) ** 2, axis=1) + 1e-12)

    signs = np.signbit(frames)
    zcr = np.mean(signs[:, 1:] != signs[:, :-1], axis=1).astype(np.float32)

    windowed = frames * np.hanning(FRAME).astype(np.float32)
    spec = np.abs(np.fft.rfft(windowed, axis=1)) + 1e-10
    # Media geometrica su media aritmetica.
    log_spec = np.log(spec)
    flatness = np.exp(np.mean(log_spec, axis=1)) / np.mean(spec, axis=1)

    return {"rms": rms.astype(np.float32), "zcr": zcr, "flatness": flatness.astype(np.float32)}


# --------------------------------------------------------------------------
# Risate e applausi
# --------------------------------------------------------------------------


def detect_laughter_applause(
    rms: np.ndarray, zcr: np.ndarray, flatness: np.ndarray, hop_rate: float
) -> tuple[list[dict], list[dict]]:
    """Euristica su finestre di 1 secondo.

    Risata: l'inviluppo di energia pulsa a 4-6 Hz (le singole sillabe "ah-ah-ah").
    E la firma piu affidabile che si possa estrarre senza un modello addestrato.

    Applauso: rumore a banda larga sostenuto -> planarita spettrale e ZCR alti
    insieme, per almeno un secondo.
    """
    win = int(round(hop_rate))  # frame in 1 secondo
    if win < 8 or len(rms) < win * 2:
        return [], []

    step = max(1, win // 2)  # finestre sovrapposte al 50%
    laughs: list[dict] = []
    claps: list[dict] = []

    # Soglie relative al materiale: un podcast sussurrato e un comizio hanno
    # livelli assoluti diversissimi, le soglie fisse non funzionerebbero.
    rms_med = float(np.median(rms))
    rms_loud = rms_med * 1.6
    flat_hi = float(np.percentile(flatness, 85))
    zcr_hi = float(np.percentile(zcr, 85))

    for start in range(0, len(rms) - win, step):
        seg_rms = rms[start : start + win]
        seg_zcr = zcr[start : start + win]
        seg_flat = flatness[start : start + win]
        t0 = start / hop_rate

        if float(np.mean(seg_rms)) < rms_loud:
            continue  # troppo silenzioso per essere l'uno o l'altro

        # --- risata: picco di modulazione dell'inviluppo tra 3 e 8 Hz ---
        env = seg_rms - float(np.mean(seg_rms))
        if len(env) >= 8:
            mod = np.abs(np.fft.rfft(env * np.hanning(len(env))))
            freqs = np.fft.rfftfreq(len(env), d=1.0 / hop_rate)
            band = (freqs >= 3.0) & (freqs <= 8.0)
            if band.any() and mod[1:].sum() > 0:
                ratio = float(mod[band].sum() / mod[1:].sum())
                if ratio > 0.45:
                    laughs.append(
                        {
                            "start": round(t0, 2),
                            "end": round(t0 + 1.0, 2),
                            "confidence": round(min(1.0, ratio), 2),
                        }
                    )

        # --- applauso: rumore largo e costante ---
        if float(np.mean(seg_flat)) > flat_hi and float(np.mean(seg_zcr)) > zcr_hi:
            steadiness = 1.0 - min(1.0, float(np.std(seg_rms)) / (float(np.mean(seg_rms)) + 1e-9))
            if steadiness > 0.55:
                claps.append(
                    {
                        "start": round(t0, 2),
                        "end": round(t0 + 1.0, 2),
                        "confidence": round(steadiness, 2),
                    }
                )

    return merge_spans(laughs), merge_spans(claps)


# Parole "sicure" al secondo sopra cui dentro la finestra c'e parlato, non una
# risata. E una densita pesata sulla probabilita: tre parole incerte contano meno
# di tre parole nitide, ed e la differenza fra un frammento strappato a un rumore
# e una frase detta davvero.
#
# **La soglia non e scelta, e misurata.** Fra 0.8 e 1.5 il risultato sui due video
# senza risate non cambia di una unita: 1.2 sta in mezzo a quel pianoro, che e il
# modo per sapere che non e tarata su un caso singolo. A 2.0 comincia a perdere.
PARLATO_SICURO_MAX = 1.2


def scarta_risate_su_parlato(laughs: list[dict], words: list[dict]) -> tuple[list[dict], int]:
    """Toglie le "risate" che cadono su parlato pulito.

    **Il rilevatore cerca modulazione d'ampiezza fra 3 e 8 Hz, che e anche la
    frequenza sillabica del parlato normale**: da solo non distingue una risata
    da qualcuno che parla. Misurato su materiale reale: 11 risate in
    un'intervista pacata e 85 in un monologo di studio, tutte false.

    Il discriminante non costa niente perche usa dati che gia produciamo. Se
    dentro la finestra ci sono parole riconosciute con sicurezza, li si sta
    parlando — le risate non hanno parole sopra, e quando Whisper ci prova
    produce frammenti isolati a bassa probabilita.

    Ritorna (risate tenute, quante scartate).
    """
    if not words or not laughs:
        return laughs, 0

    tenute = []
    for span in laughs:
        a, b = span["start"], span["end"]
        dentro = [w for w in words if w["end"] > a and w["start"] < b]
        indice = sum(w["prob"] for w in dentro) / max(0.1, b - a)
        if indice < PARLATO_SICURO_MAX:
            tenute.append({**span, "parlato": round(indice, 2)})
    return tenute, len(laughs) - len(tenute)


def merge_spans(spans: list[dict], gap: float = 0.6) -> list[dict]:
    """Unisce rilevamenti adiacenti in un unico evento."""
    if not spans:
        return []
    spans = sorted(spans, key=lambda s: s["start"])
    out = [dict(spans[0])]
    for s in spans[1:]:
        if s["start"] - out[-1]["end"] <= gap:
            out[-1]["end"] = max(out[-1]["end"], s["end"])
            out[-1]["confidence"] = round(max(out[-1]["confidence"], s["confidence"]), 2)
        else:
            out.append(dict(s))
    # Un evento di mezzo secondo e quasi sempre un falso positivo.
    return [s for s in out if s["end"] - s["start"] >= 0.8]


# --------------------------------------------------------------------------
# Energia, picchi, pause
# --------------------------------------------------------------------------


def energy_curve(rms: np.ndarray, hop_rate: float, resolution: float = 0.5) -> list[dict]:
    """Curva di energia in dB, ricampionata a una griglia leggibile."""
    per_cell = max(1, int(round(resolution * hop_rate)))
    n_cells = len(rms) // per_cell
    if n_cells == 0:
        return []
    trimmed = rms[: n_cells * per_cell].reshape(n_cells, per_cell)
    cell_rms = np.sqrt(np.mean(trimmed**2, axis=1) + 1e-12)
    db = 20 * np.log10(cell_rms + 1e-9)
    return [{"t": round(i * resolution, 2), "db": round(float(v), 1)} for i, v in enumerate(db)]


def energy_peaks(curve: list[dict], top_n: int = 60) -> list[dict]:
    """I momenti in cui la voce si alza nettamente sopra la propria media:
    candidati per i punch-in dello zoom e indizio di enfasi."""
    if len(curve) < 10:
        return []
    db = np.array([c["db"] for c in curve])
    med = float(np.median(db))
    mad = float(np.median(np.abs(db - med)))

    # Con audio quasi piatto (un tono, una stanza silenziosa) la MAD tende a
    # zero e lo z-score esplode: qualunque oscillazione da mezzo decibel
    # diventerebbe un "picco". Sotto questa soglia non c'e dinamica da leggere.
    if mad < 0.5:
        return []

    z = (db - med) / (1.4826 * mad)

    peaks = []
    for i in range(1, len(z) - 1):
        if z[i] > 1.8 and z[i] >= z[i - 1] and z[i] > z[i + 1]:
            peaks.append({"t": curve[i]["t"], "strength": round(float(z[i]), 2)})
    peaks.sort(key=lambda p: -p["strength"])
    return sorted(peaks[:top_n], key=lambda p: p["t"])


def pauses_from_transcript(transcript: dict, min_gap: float = 0.8) -> list[dict]:
    """Buchi tra una parola e la successiva.

    Preferiti al silenzio ricavato dall'audio perche sono gia allineati ai
    confini delle parole: sono esattamente i punti dove si puo tagliare senza
    mozzare una sillaba.
    """
    words = transcript.get("words", [])
    out = []
    for prev, nxt in zip(words, words[1:]):
        gap = nxt["start"] - prev["end"]
        if gap >= min_gap:
            out.append(
                {
                    "start": round(prev["end"], 2),
                    "end": round(nxt["start"], 2),
                    "duration": round(gap, 2),
                }
            )
    return out


def speech_rate(transcript: dict, window: float = 10.0) -> list[dict]:
    """Parole al minuto per finestra: le accelerazioni segnalano concitazione."""
    words = transcript.get("words", [])
    if not words:
        return []
    end = words[-1]["end"]
    out = []
    t = 0.0
    while t < end:
        n = sum(1 for w in words if t <= w["start"] < t + window)
        out.append({"t": round(t, 1), "wpm": round(n * 60.0 / window)})
        t += window
    return out


# --------------------------------------------------------------------------
# Cambi scena
# --------------------------------------------------------------------------


def detect_scenes(video: Path, frame_skip: int) -> list[dict]:
    """PySceneDetect con AdaptiveDetector: regge molto meglio del filtro
    select di ffmpeg i movimenti di camera e i cambi di luce graduali."""
    from scenedetect import AdaptiveDetector, SceneManager, open_video

    log("rilevo i cambi scena (decodifica del video, e il passaggio piu lento qui)")
    vid = open_video(str(video))
    sm = SceneManager()
    sm.auto_downscale = True  # analizza a risoluzione ridotta: molto piu veloce
    sm.add_detector(AdaptiveDetector())
    sm.detect_scenes(vid, frame_skip=frame_skip, show_progress=False)

    scenes = []
    for start, end in sm.get_scene_list():
        scenes.append({"start": round(start.seconds, 2), "end": round(end.seconds, 2)})
    return scenes


def refine_cuts(
    video: Path, start: float, end: float, cuts: list[float], window: float = 0.3
) -> list[float]:
    """Porta gli stacchi di una clip sul fotogramma esatto in cui cambia l'immagine.

    `cuts` e il ritorno sono in secondi relativi a `start`. Il ritorno e mezzo
    fotogramma **prima** del primo fotogramma della nuova inquadratura, cosi
    chi confronta tempi di fotogramma non lo manca per un arrotondamento.

    **Perche serve.** Gli stacchi di `detect_scenes` arrivano con `frame_skip` e
    con la finestra del rilevatore adattivo: misurato su tre stacchi di un
    podcast, fino a **due fotogrammi dopo** il cambio vero. Per la scelta dei
    momenti non conta niente; per il crop si: per due fotogrammi la nuova
    inquadratura esce ritagliata dove stava la persona di prima, cioe un lampo.
    Qui si decodifica la sola clip, minuscola e a piena cadenza, e si prende il
    salto piu grande attorno a ogni stacco. Uno stacco senza un salto netto (una
    dissolvenza) resta dov'era.
    """
    if not cuts:
        return []
    import subprocess

    from common import tool, video_info

    fps = video_info(video)["fps"] or 30.0
    w, h = 64, 36
    proc = subprocess.run(
        [
            tool("ffmpeg"),
            "-v",
            "error",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{end - start:.3f}",
            "-i",
            str(video),
            "-vf",
            f"scale={w}:{h}",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
    )
    n = len(proc.stdout) // (w * h)
    if n < 3:
        return list(cuts)
    frames = (
        np.frombuffer(proc.stdout[: n * w * h], dtype=np.uint8).reshape(n, h, w).astype(np.int16)
    )
    diffs = np.concatenate([[0.0], np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))])

    out = []
    for c in cuts:
        lo = max(1, int((c - window) * fps))
        hi = min(n, int((c + window) * fps) + 1)
        if hi - lo < 2:
            out.append(c)
            continue
        k = lo + int(np.argmax(diffs[lo:hi]))
        netto = diffs[k] > max(8.0, 3.0 * float(np.median(diffs[lo:hi])))
        out.append(round((k - 0.5) / fps, 4) if netto else c)
    return out


# --------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="Estrazione dei segnali meccanici")
    ap.add_argument("work_dir")
    ap.add_argument(
        "--frame-skip",
        type=int,
        default=2,
        help="frame saltati nel rilevamento scene: piu alto = piu veloce, meno preciso",
    )
    ap.add_argument(
        "--no-scenes",
        action="store_true",
        help="salta il rilevamento scene (il passaggio piu lento)",
    )
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    work = Path(args.work_dir)
    meta = read_json(work / "meta.json")
    out_path = work / "signals.json"

    if out_path.exists() and not args.force:
        log("segnali gia in cache")
        emit({"path": str(out_path), "cached": True})
        return

    # --- audio ---
    log("analizzo l'audio")
    hop_rate = SR / HOP
    parts: dict[str, list[np.ndarray]] = {"rms": [], "zcr": [], "flatness": []}
    for _t0, samples, rate in read_wav_chunks(require_audio(work, meta)):
        if rate != SR:
            raise RuntimeError(f"atteso audio a {SR} Hz, trovato {rate}")
        feats = frame_features(samples, rate)
        for k in parts:
            parts[k].append(feats[k])

    rms = np.concatenate(parts["rms"]) if parts["rms"] else np.zeros(0, np.float32)
    zcr = np.concatenate(parts["zcr"]) if parts["zcr"] else np.zeros(0, np.float32)
    flatness = np.concatenate(parts["flatness"]) if parts["flatness"] else np.zeros(0, np.float32)

    curve = energy_curve(rms, hop_rate)
    peaks = energy_peaks(curve)
    laughs, claps = detect_laughter_applause(rms, zcr, flatness, hop_rate)

    # --- trascrizione, se gia disponibile ---
    #
    # Va letta **prima** di chiudere il conto delle risate: e da li che arriva
    # l'unica cosa capace di distinguere una risata da una persona che parla, e
    # senza il filtro il numero stampato qui sotto sarebbe quello sbagliato.
    pauses: list[dict] = []
    rate_curve: list[dict] = []
    scartate = 0
    tpath = work / "transcript.json"
    if tpath.exists():
        transcript = read_json(tpath)
        pauses = pauses_from_transcript(transcript)
        rate_curve = speech_rate(transcript)
        laughs, scartate = scarta_risate_su_parlato(laughs, transcript.get("words", []))
    else:
        log("  transcript.json assente: salto pause, eloquio e il filtro sulle risate")

    coda = f" ({scartate} scartate: c'era parlato)" if scartate else ""
    log(f"  {len(peaks)} picchi di enfasi, {len(laughs)} risate{coda}, {len(claps)} applausi")
    if pauses:
        log(f"  {len(pauses)} pause utili come punti di taglio")

    # --- video ---
    scenes: list[dict] = []
    if not args.no_scenes:
        try:
            # Sul proxy a 480p invece che sul sorgente: i cambi scena si vedono
            # benissimo a bassa risoluzione, e sul 4K la decodifica da sola non
            # era finita dopo 15 minuti a 9 core.
            analysis_video, _ = analysis_source(meta, work)
            scenes = detect_scenes(analysis_video, args.frame_skip)
            log(f"  {len(scenes)} scene")
        except Exception as exc:  # noqa: BLE001
            # Non fatale: senza scene il reframe usa solo la soglia di salto
            # del volto per decidere gli stacchi.
            log(f"  rilevamento scene fallito ({exc}); proseguo senza")

    result = {
        "slug": meta["slug"],
        "duration": meta["duration"],
        "energy": curve,
        "energy_peaks": peaks,
        "laughter": laughs,
        # Quante ne ha tolte il filtro sul parlato. Il numero e diagnostica, non
        # un segnale: se un giorno fosse zero su un video con del pubblico,
        # vorrebbe dire che il filtro non sta piu facendo il suo lavoro.
        "laughter_discarded": scartate,
        "applause": claps,
        "pauses": pauses,
        "speech_rate": rate_curve,
        "scenes": scenes,
    }
    write_json(out_path, result)
    emit(
        {
            "path": str(out_path),
            "cached": False,
            "counts": {
                "energy_peaks": len(peaks),
                "laughter": len(laughs),
                "laughter_discarded": scartate,
                "applause": len(claps),
                "pauses": len(pauses),
                "scenes": len(scenes),
            },
        }
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
