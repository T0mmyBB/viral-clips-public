"""Render di una clip: taglio, crop 9:16, sottotitoli e normalizzazione audio.

    python render_clip.py work/<slug> --decisions decisions.json --slug 01-il-rospo
    python render_clip.py work/<slug> --start 12 --end 48 --out output/clip.mp4 --preview

**La forma normale e la prima.** Bordi, hook e keyword arrivano da
`decisions.json`, mai da riga di comando: un hook italiano contiene un apostrofo
quasi sempre ("l'unica volta che"), e tra PowerShell — che espande anche `$` e
i doppi apici — subprocess e parser dei filtri ffmpeg il testo si rompe. Passare
il *nome di un file* toglie di mezzo l'intera classe di problemi invece di
aggirarla un apostrofo per volta.

La seconda forma resta per le prove veloci, quando non c'e ancora un giudizio da
persistere.

**Un solo passaggio ffmpeg.** Taglio, crop, scale, sottotitoli e formato sono
nello stesso filtergraph: ogni file intermedio sarebbe una generazione di encode
persa, e il requisito e qualita pari al sorgente.

Le due passate di loudnorm non violano quel requisito: la prima misura soltanto,
non codifica. Nemmeno i **tagli dei silenzi**: sono `trim` e `concat` dentro lo
stesso filtergraph, e crop e sottotitoli vengono dopo, sul tempo della clip montata.

L'audio esce dalla **traccia originale del video**, mai da `audio.wav`: quello e
16 kHz mono e serve solo a Whisper. E un errore facile da fare perche il file e
li nella stessa cartella.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from common import (
    FONTS_DIR,
    OUTPUT_DIR,
    emit,
    ffprobe,
    log,
    read_json,
    resolve_logo,
    tool,
    video_info,
    write_json,
)

TARGET_W, TARGET_H = 1080, 1920
DEFAULT_STYLE = "bold"

# Logo del creatore impresso per tutta la clip: alcune campagne di clipping lo
# richiedono come condizione di attribuzione. Sta in fondo perche il resto del
# fotogramma e occupato — l'hook in cima nei primi secondi, i sottotitoli al
# centro — e a 1500 px resta sopra la fascia che TikTok e Reels coprono con
# didascalia e nome utente.
LOGO_WIDTH = 400
LOGO_Y = 1500

LOUDNESS_TARGET = {"I": -14, "TP": -1.5, "LRA": 11}  # standard di fatto dei social

AUDIO_EDGE_FADE_S = 0.015

# Un tratto senza parole si toglie solo se l'audio li sta almeno cosi sotto il
# livello tipico del parlato della clip (95° percentile del tratto contro la
# mediana dentro le parole). Musica di sottofondo, stacchi sonori e folla stanno
# sopra, e restano.
QUIETE_DB = 18.0

# Le funzioni di trasferimento HDR. Scaricando la risoluzione massima si
# incappa in stream PQ o HLG: taggarli BT.709 senza convertirli non li
# corregge, li etichetta male, e la clip esce slavata.
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}


def stage_assets(
    work: Path, subs: Path | None, sendcmd: Path | None, logo: Path | None = None
) -> dict[str, str]:
    """Porta i file citati dai filtri dentro la cartella di lavoro.

    ffmpeg viene poi eseguito con la working directory su quella cartella e i
    filtri usano percorsi relativi. Il motivo e concreto: dentro un argomento
    di filtro i due punti separano le opzioni, e la lettera di unita di Windows
    (`C:/...`) manda in errore il parser. L'escaping esiste ma attraversa piu
    livelli di quoting ed e fragile; un nome relativo non ha il problema.
    """
    staged: dict[str, str] = {}

    fonts_dir = work / "fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)
    for font in FONTS_DIR.glob("*.tt[fc]"):
        target = fonts_dir / font.name
        if not target.exists() or target.stat().st_size != font.stat().st_size:
            shutil.copy2(font, target)
    staged["fontsdir"] = "fonts"

    for key, path in (("subs", subs), ("sendcmd", sendcmd), ("logo", logo)):
        if path is None:
            continue
        path = Path(path).resolve()
        if path.parent != work.resolve():
            target = work / path.name
            shutil.copy2(path, target)
            path = target
        staged[key] = path.name

    return staged


def crop_geometry(width: int, height: int) -> tuple[int, int, int, int, bool]:
    """Finestra 9:16 piu grande possibile, centrata.

    Ritorna (w, h, x, y, serve_upscale). L'upscale interessa a valle: da un
    1920x1080 la finestra e 608x1080 e verso 1080x1920 e un ingrandimento di
    1.78x; da un 3840x2160 e 1215x2160, cioe una riduzione.
    """
    crop_w = int(round(height * 9 / 16))
    crop_w -= crop_w % 2

    if crop_w <= width:
        crop_h = height - (height % 2)
        x = (width - crop_w) // 2
        y = 0
    else:
        # Sorgente gia stretto o verticale: si limita l'altezza.
        crop_w = width - (width % 2)
        crop_h = int(round(width * 16 / 9))
        crop_h -= crop_h % 2
        crop_h = min(crop_h, height - (height % 2))
        x = 0
        y = (height - crop_h) // 2

    return crop_w, crop_h, x, y, crop_w < TARGET_W


def detect_hdr(source: Path) -> tuple[bool, str | None]:
    data = ffprobe(source)
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            transfer = stream.get("color_transfer")
            return (transfer in HDR_TRANSFERS), transfer
    return False, None


def has_filter(name: str) -> bool:
    """Alcune build di ffmpeg non includono zimg, e senza quello niente zscale."""
    proc = subprocess.run(
        [tool("ffmpeg"), "-hide_banner", "-filters"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return bool(re.search(rf"^\s*\S+\s+{re.escape(name)}\s", proc.stdout, re.M))


def measure_loudness(source: Path, start: float, duration: float) -> dict | None:
    """Prima passata di loudnorm: misura e basta, non codifica nulla."""
    args = ":".join(f"{k}={v}" for k, v in LOUDNESS_TARGET.items())
    cmd = [
        tool("ffmpeg"),
        "-hide_banner",
        "-nostats",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(source),
        "-t",
        f"{duration:.3f}",
        "-map",
        "0:a:0",
        "-af",
        f"loudnorm={args}:print_format=json",
        "-f",
        "null",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    # loudnorm stampa il JSON in coda a stderr.
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr, re.S)
    if not match:
        log("  misura di loudness non riuscita: uso loudnorm a passata singola")
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def build_audio_filter(measured: dict | None) -> str:
    args = dict(LOUDNESS_TARGET)
    if measured:
        args.update(
            {
                "measured_I": measured["input_i"],
                "measured_TP": measured["input_tp"],
                "measured_LRA": measured["input_lra"],
                "measured_thresh": measured["input_thresh"],
                "offset": measured["target_offset"],
                "linear": "true",
            }
        )
    return "loudnorm=" + ":".join(f"{k}={v}" for k, v in args.items())


def build_filter_graph(
    crop: tuple[int, int, int, int],
    staged: dict[str, str],
    is_hdr: bool,
    audio_filter: str,
    tenuti: list[tuple[float, float]],
    duration: float,
    sovrapposte: list[dict] | None = None,
) -> str:
    """Il filtergraph intero: tagli, crop, scale, sottotitoli, grafiche, logo, audio.

    **I tagli dei silenzi stanno qui dentro, non in file a parte.** Ogni tratto
    tenuto e un `trim`/`atrim` dello stesso ingresso, e `concat` li ricuce: e lo
    stesso passaggio di encode di una clip senza tagli. Il crop e i sottotitoli
    vengono *dopo* il concat, quindi lavorano sul tempo della clip montata — ed e
    in quel tempo che `track_subject.py` e `make_subs.py` scrivono i loro file.
    """
    graph: list[str] = []
    pre = []
    if is_hdr:
        if has_filter("zscale"):
            # Tone mapping vero: linearizza, comprime, riporta a BT.709.
            pre.append(
                "zscale=t=linear:npl=100,format=gbrpf32le,"
                "tonemap=tonemap=hable:desat=0,"
                "zscale=p=bt709:t=bt709:m=bt709:r=tv"
            )
        else:
            log(
                "  ATTENZIONE: sorgente HDR ma zscale non disponibile in questa build "
                "di ffmpeg; il colore uscira sbagliato"
            )

    # Una dissolvenza di un centinaio di campioni a ogni bordo di audio: tagliare
    # un'onda a meta produce un click, e ai bordi della clip e a ogni taglio dei
    # silenzi l'onda si taglia sempre a meta. Troppo corta per sentirsi come
    # dissolvenza.
    fade = AUDIO_EDGE_FADE_S
    n = len(tenuti)
    if n > 1:
        graph.append(
            "[0:v]"
            + (",".join(pre) + "," if pre else "")
            + f"split={n}"
            + "".join(f"[vs{i}]" for i in range(n))
        )
        graph.append(f"[0:a]asplit={n}" + "".join(f"[as{i}]" for i in range(n)))
        coppie = []
        for i, (a, b) in enumerate(tenuti):
            d = b - a
            graph.append(f"[vs{i}]trim=start={a:.4f}:end={b:.4f},setpts=PTS-STARTPTS[v{i}]")
            graph.append(
                f"[as{i}]atrim=start={a:.4f}:end={b:.4f},asetpts=PTS-STARTPTS,"
                f"afade=t=in:d={fade},afade=t=out:st={max(0.0, d - fade):.4f}:d={fade}[a{i}]"
            )
            coppie.append(f"[v{i}][a{i}]")
        graph.append("".join(coppie) + f"concat=n={n}:v=1:a=1[vc][ac]")
    else:
        graph.append("[0:v]" + (",".join(pre) if pre else "null") + "[vc]")
        graph.append(
            f"[0:a]afade=t=in:d={fade},afade=t=out:st={max(0.0, duration - fade):.4f}:d={fade}[ac]"
        )

    chain = []
    if "sendcmd" in staged:
        chain.append(f"sendcmd=f={staged['sendcmd']}")

    # `exact=1`: senza, il filtro allinea x, y, larghezza e altezza a numeri pari
    # sui sorgenti 4:2:0, e il movimento procede a scatti di due pixel — tre e
    # mezzo dopo l'ingrandimento di un 1080p.
    w, h, x, y = crop
    chain.append(f"crop={w}:{h}:{x}:{y}:exact=1")
    chain.append(f"scale={TARGET_W}:{TARGET_H}:flags=lanczos")

    if "subs" in staged:
        # Dopo lo scale: lo script ASS dichiara PlayRes 1080x1920, cosi le
        # coordinate dei sottotitoli corrispondono 1:1 ai pixel di uscita.
        chain.append(f"ass=f={staged['subs']}:fontsdir={staged['fontsdir']}")

    if sovrapposte:
        # Le grafiche (`grafiche.py`): sopra i sottotitoli, sotto il logo. Ognuna
        # e un `movie` spostato al suo istante; prima di quell'istante `overlay`
        # lascia passare il girato com'e, e `eof_action=pass` fa lo stesso dopo
        # l'ultimo fotogramma invece di tenerlo fermo a schermo. Il giallo esce
        # 254,226,0 contro il 255,229,0 dell'hook (mediana su 108mila pixel):
        # nessuna conversione di matrice da aggiungere.
        from grafiche import TELA_Y

        graph.append("[vc]" + ",".join(chain) + "[g0]")
        for k, s in enumerate(sovrapposte):
            graph.append(f"movie={s['nome']},setpts=PTS-STARTPTS+{s['t']:.4f}/TB[m{k}]")
            graph.append(
                f"[g{k}][m{k}]overlay=0:{TELA_Y}:eof_action=pass:format=auto[g{k + 1}]"
            )
        chain = ["null"]
        testa = f"[g{len(sovrapposte)}]"
    else:
        testa = "[vc]"

    if "logo" in staged:
        # Ultimo della catena: se il logo dev'essere visibile per tutta la clip,
        # non puo passarci sopra nient'altro. Resta un solo passaggio di encode —
        # `movie` e un secondo ingresso dentro lo stesso filtergraph, non un file
        # intermedio.
        source = f"movie={staged['logo']},scale={LOGO_WIDTH}:-1:flags=lanczos,format=rgba"
        graph.append(testa + ",".join(chain) + "[base]")
        graph.append(f"{source}[logo]")
        graph.append(f"[base][logo]overlay=(W-w)/2:{LOGO_Y}:format=auto,format=yuv420p[vout]")
    else:
        chain.append("format=yuv420p")
        graph.append(testa + ",".join(chain) + "[vout]")

    graph.append(f"[ac]{audio_filter}[aout]")
    return ";".join(graph)


def snap_to_frames(
    tenuti: list[tuple[float, float]], start: float, fps: float, duration: float
) -> list[tuple[float, float]]:
    """Porta i bordi dei tratti sulla griglia dei fotogrammi del sorgente.

    Un taglio a meta fotogramma lascia video e audio di un tratto con durate
    diverse di qualche millisecondo, e `concat` compensa allungando il piu corto:
    su dieci tagli e un fotogramma di sfasamento. Sulla griglia i due coincidono.
    """

    def snap(t: float) -> float:
        if t <= 0.0 or t >= duration:
            return t
        return round((start + t) * fps) / fps - start

    out = [(snap(a), snap(b)) for a, b in tenuti]
    return [(max(0.0, a), min(duration, b)) for a, b in out if b - a > 0.05]


def render(
    work: Path,
    source: Path,
    start: float,
    end: float,
    out: Path,
    subs: Path | None,
    preview: bool = False,
    sendcmd: Path | None = None,
    logo: Path | None = None,
    tenuti: list[tuple[float, float]] | None = None,
    grafiche: dict | None = None,
) -> dict:
    duration = end - start
    tenuti = [tuple(t) for t in tenuti] if tenuti else [(0.0, duration)]
    durata_uscita = sum(b - a for a, b in tenuti)
    info = video_info(source)
    crop_w, crop_h, cx, cy, needs_upscale = crop_geometry(info["width"], info["height"])
    is_hdr, transfer = detect_hdr(source)
    staged = stage_assets(work, subs, sendcmd, logo)

    if is_hdr:
        log(f"  sorgente HDR ({transfer}): applico tone mapping verso BT.709")
    if needs_upscale:
        factor = TARGET_W / crop_w
        log(
            f"  crop {crop_w}x{crop_h} -> ingrandimento {factor:.2f}x "
            f"(sorgente {info['width']}x{info['height']})"
        )
    else:
        log(f"  crop {crop_w}x{crop_h} -> riduzione, nessuna perdita di nitidezza")

    measured = None if preview else measure_loudness(source, start, duration)
    af = build_audio_filter(measured)
    # Le grafiche stanno gia in `work/grafiche/`, e ffmpeg gira con la cartella di
    # lavoro come cwd: basta il percorso relativo, senza lettere di unita.
    sovrapposte = [
        {"nome": s["file"].resolve().relative_to(work.resolve()).as_posix(), "t": s["t"]}
        for s in (grafiche or {}).get("sovrapposte", [])
    ]
    graph = build_filter_graph(
        (crop_w, crop_h, cx, cy), staged, is_hdr, af, tenuti, duration, sovrapposte
    )

    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        tool("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        # -ss prima di -i: seek veloce e comunque accurato, e i timestamp in
        # uscita ripartono da zero, come si aspettano i sottotitoli.
        "-ss",
        f"{start:.3f}",
        "-i",
        str(source.resolve()),
        "-t",
        f"{durata_uscita:.3f}",
        "-filter_complex",
        graph,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
    ]

    if preview:
        # Solo per la revisione: veloce, la qualita non conta.
        cmd += ["-c:v", "libx264", "-crf", "26", "-preset", "veryfast"]
    else:
        cmd += [
            "-c:v",
            "libx264",
            "-crf",
            "17",
            "-preset",
            "slow",
            "-profile:v",
            "high",
            "-level",
            "4.2",
        ]

    cmd += [
        # I tag di colore vanno forzati: attraversando il filtergraph i
        # sorgenti VP9/AV1 li perdono e la clip esce slavata.
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(out),
    ]

    log(f"  render {'anteprima' if preview else 'finale'}: {durata_uscita:.1f}s")
    proc = subprocess.run(
        cmd,
        cwd=str(work.resolve()),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-20:])
        raise RuntimeError(f"ffmpeg ha fallito:\n{tail}")

    result = video_info(out)
    return {
        "path": str(out),
        "logo": staged.get("logo"),
        "duration": round(result["duration"], 2),
        "size_mb": round(out.stat().st_size / 1024 / 1024, 2),
        "resolution": f"{result['width']}x{result['height']}",
        "source_crop": f"{crop_w}x{crop_h}",
        "upscale_required": needs_upscale,
        "upscale_factor": round(TARGET_W / crop_w, 3),
        "hdr_source": is_hdr,
        "loudnorm": "due passate" if measured else "passata singola",
        "silence_cuts": len(tenuti) - 1,
        "removed_s": round(duration - durata_uscita, 2),
        "grafiche": [
            {"modello": s["modello"], "t": s["t"], "durata": s["durata"]}
            for s in (grafiche or {}).get("sovrapposte", [])
        ],
        "grafiche_saltate": (grafiche or {}).get("saltate", []),
        "preview": preview,
    }


def plan_silence_cuts(
    work: Path,
    meta: dict,
    start: float,
    end: float,
    words: list[dict],
    scene_cuts: list[float] | None = None,
) -> dict:
    """I tagli dei silenzi di questa clip: le regole in `tagli.py`, le misure qui.

    Qui si misura cio che il transcript non sa: se il tratto senza parole e
    davvero quieto. Si legge `audio.wav` — che per l'audio della clip non si usa
    mai, ma per misurare un livello e esattamente quello che serve — a finestre
    di 20 ms.
    """
    import wave

    import numpy as np

    import tagli
    from common import require_audio

    audio = require_audio(work, meta)
    with wave.open(str(audio), "rb") as wf:
        rate = wf.getframerate()
        wf.setpos(max(0, int(start * rate)))
        raw = wf.readframes(int((end - start) * rate) + 1)
    campioni = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    passo = max(1, int(rate * 0.02))
    n = len(campioni) // passo
    if n == 0:
        return {"tenuti": [(0.0, end - start)], "tolti": [], "durata": end - start, "tolto_s": 0.0}
    rms = np.sqrt(np.mean(campioni[: n * passo].reshape(n, passo) ** 2, axis=1) + 1e-12)
    db = 20.0 * np.log10(rms)
    istanti = start + (np.arange(n) + 0.5) * 0.02

    dentro = np.zeros(n, dtype=bool)
    for w in words:
        if w["end"] > start and w["start"] < end:
            dentro |= (istanti >= w["start"]) & (istanti <= w["end"])
    parlato = float(np.median(db[dentro])) if dentro.any() else float(np.median(db))

    def quiet(a: float, b: float) -> bool:
        sel = (istanti >= a) & (istanti <= b)
        if not sel.any():
            return True
        return float(np.percentile(db[sel], 95)) <= parlato - QUIETE_DB

    protetti: list[tuple[float, float]] = []
    if (work / "signals.json").exists():
        sig = read_json(work / "signals.json")
        for chiave in ("laughter", "applause"):
            protetti += [(s["start"], s["end"]) for s in sig.get(chiave, [])]

    minimo = 20.0
    try:
        import campagna

        minimo = campagna.limiti(work)[0]
    except Exception:
        pass

    piano = tagli.plan(
        words,
        start,
        end,
        quiet=quiet,
        protected=protetti,
        min_duration=minimo,
        scene_cuts=scene_cuts,
    )
    piano["parlato_db"] = round(parlato, 1)
    return piano


def load_decision(work: Path, decisions_file: str, slug: str) -> dict:
    """La clip richiesta, letta da `decisions.json` e ricontrollata.

    Il controllo non e ridondante anche se esiste `validate.py`: quello si lancia
    sull'intero file una volta, questo gira sulla clip che si sta per
    renderizzare. Un campo mancante scoperto a meta di un render da minuti costa
    l'intero render.
    """
    src = work / decisions_file
    if not src.exists():
        raise SystemExit(
            f"{src} non trovato. Lo scrive Claude dopo aver letto la vista "
            f"prodotta da windows.py - vedi SKILL.md, passo 5."
        )

    clips = read_json(src).get("clips") or []
    clip = next((c for c in clips if c.get("slug") == slug), None)
    if clip is None:
        available = ", ".join(c.get("slug", "?") for c in clips) or "nessuna"
        raise SystemExit(f"slug '{slug}' assente da {src.name}. Presenti: {available}")

    from validate import check_clip

    duration = None
    if (work / "meta.json").exists():
        duration = read_json(work / "meta.json").get("duration")

    errors = check_clip(clip, 0, duration)
    if errors:
        for e in errors:
            log(f"  ERRORE: {e}")
        raise SystemExit(f"'{slug}' non e renderizzabile: correggi {src.name}.")

    return clip


def main() -> None:
    ap = argparse.ArgumentParser(description="Render di una singola clip verticale")
    ap.add_argument("work_dir")
    ap.add_argument(
        "--decisions",
        default=None,
        help="file delle decisioni dentro work_dir (di norma decisions.json): "
        "da qui arrivano bordi, hook e keyword. Richiede --slug.",
    )
    ap.add_argument("--slug", default=None, help="quale clip di --decisions renderizzare")
    ap.add_argument("--start", type=float, default=None)
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument(
        "--out",
        default=None,
        help="file di uscita. Con --decisions il valore predefinito e output/<video>/<slug>.mp4",
    )
    ap.add_argument("--subs", default=None, help="file .ass da imprimere")
    ap.add_argument("--no-subs", action="store_true")
    ap.add_argument("--style", default=None)
    ap.add_argument("--keywords", default="")
    ap.add_argument(
        "--hook", default=None, help="frase d'aggancio impressa in alto all'inizio della clip"
    )
    ap.add_argument(
        "--hook-seconds",
        type=float,
        default=None,
        help="durata dell'hook in secondi (0 = tutta la clip)",
    )
    ap.add_argument(
        "--cta",
        default=None,
        help="richiamo impresso dalla fine dell'hook alla fine della clip. "
        "Di norma arriva dal campo 'cta' di decisions.json.",
    )
    ap.add_argument(
        "--preview", action="store_true", help="render veloce a bassa qualita per la revisione"
    )
    ap.add_argument(
        "--sendcmd",
        default=None,
        help="file di comandi da track_subject.py per il crop dinamico. "
        "Se assente si usa un crop centrale fisso.",
    )
    ap.add_argument(
        "--auto-track",
        action="store_true",
        help="genera il crop dinamico al volo invece di passare --sendcmd",
    )
    ap.add_argument(
        "--logo",
        default=None,
        help="PNG impresso in basso per tutta la clip (attribuzione al "
        "creatore). Di norma arriva dal campo 'logo' di decisions.json.",
    )
    ap.add_argument(
        "--no-tagli",
        action="store_true",
        help="non togliere i silenzi dentro la clip. Per una consegna si "
        'scrive "tagli": false in decisions.json, non si passa il flag.',
    )
    ap.add_argument(
        "--no-grafiche",
        action="store_true",
        help="ignora il campo 'grafiche' di decisions.json (prove veloci)",
    )
    args = ap.parse_args()

    work = Path(args.work_dir)
    meta = read_json(work / "meta.json")

    # Il sorgente a piena risoluzione puo non essere ancora sul disco: da un link
    # si scaricano prima solo audio e proxy, per non far aspettare la parte in cui
    # si legge e si sceglie. Qui serve davvero, quindi qui si prende.
    from ingest import ensure_source

    source = ensure_source(work)

    logo_name = args.logo

    if args.decisions:
        if not args.slug:
            ap.error("--decisions ha bisogno di --slug: dice quale clip renderizzare")
        clip = load_decision(work, args.decisions, args.slug)
        # Il logo vale per tutta la consegna, quindi sta in cima al file; una
        # clip puo scavalcarlo, ma non deve doverlo ripetere.
        decisions = read_json(work / args.decisions)
        logo_name = clip.get("logo") or decisions.get("logo") or logo_name
        start, end = float(clip["start"]), float(clip["end"])
        hook = clip.get("hook")
        keywords = [k for k in (clip.get("keywords") or []) if str(k).strip()]
        style = clip.get("style") or args.style or DEFAULT_STYLE
        hook_seconds = clip.get("hook_seconds", args.hook_seconds)
        # Come il logo: il richiamo vale per tutta la consegna, quindi sta in
        # cima al file e una clip non deve ripeterlo. Puo scavalcarlo.
        cta = clip.get("cta") or decisions.get("cta") or args.cta
        out = (
            Path(args.out)
            if args.out
            else (OUTPUT_DIR / meta.get("slug", work.name) / f"{args.slug}.mp4")
        )
        log(f"  {args.slug}: {start:.1f}-{end:.1f}s da {args.decisions}")
    else:
        if args.slug:
            ap.error("--slug sceglie una clip dentro --decisions: da solo non sceglie niente")
        missing = [
            name
            for name, value in (("--start", args.start), ("--end", args.end), ("--out", args.out))
            if value is None
        ]
        if missing:
            ap.error(f"senza --decisions servono {', '.join(missing)}")
        start, end, out = args.start, args.end, Path(args.out)
        hook = args.hook
        keywords = [k for k in args.keywords.split(",") if k.strip()]
        style = args.style or DEFAULT_STYLE
        hook_seconds = args.hook_seconds
        cta = args.cta

    transcript = read_json(work / "transcript.json")
    words = transcript["words"]

    # Gli stacchi del girato, al fotogramma esatto: servono ai tagli dei silenzi
    # (che non ci cadono accanto) e alla camera (che ci cambia inquadratura).
    stacchi: list[float] = []
    if (work / "signals.json").exists():
        from common import analysis_source
        from signals import refine_cuts

        sig = read_json(work / "signals.json")
        grezzi = [s["start"] - start for s in sig.get("scenes", [])[1:] if start < s["start"] < end]
        stacchi = refine_cuts(analysis_source(meta, work)[0], start, end, grezzi)

    # **I tagli dei silenzi sono il default**, e si spengono per clip o per
    # consegna con `"tagli": false` in decisions.json. Il piano si scrive accanto
    # ai sottotitoli, con lo stesso nome: `check_sync.py` ne ha bisogno per
    # ritrovare nel sorgente l'istante di un fotogramma della clip montata.
    vuole_tagli = not args.no_tagli
    if args.decisions:
        vuole_tagli = vuole_tagli and clip.get("tagli", decisions.get("tagli", True)) is not False
    if args.sendcmd and vuole_tagli:
        log("  --sendcmd esterno: i suoi tempi sono quelli del sorgente, niente tagli dei silenzi")
        vuole_tagli = False
    tagli_path = work / f"tagli_{start:.0f}_{end:.0f}.json"
    if vuole_tagli:
        piano = plan_silence_cuts(
            work, meta, start, end, words, scene_cuts=[start + c for c in stacchi]
        )
        fps = video_info(source)["fps"]
        tenuti = snap_to_frames(piano["tenuti"], start, fps, end - start)
        piano["tenuti"] = tenuti
        piano["durata"] = round(sum(b - a for a, b in tenuti), 3)
        write_json(tagli_path, {"start": start, "end": end, **piano})
        if len(tenuti) > 1:
            log(
                f"  {len(tenuti) - 1} silenzi tolti, {piano['tolto_s']:.1f}s in meno "
                f"({end - start:.1f}s -> {piano['durata']:.1f}s)"
            )
        else:
            log("  nessun silenzio da togliere")
    else:
        tenuti = [(0.0, end - start)]
        tagli_path.unlink(missing_ok=True)

    # Le grafiche dopo i tagli, perche il loro istante va nel tempo della clip
    # montata; prima dei sottotitoli, che devono sapere quando tacere.
    grafiche = None
    if args.decisions and clip.get("grafiche") and not args.no_grafiche:
        from grafiche import prepara

        grafiche = prepara(
            work, clip["grafiche"], words, start, end, tenuti, video_info(source)["fps"]
        )

    subs_path = None
    if not args.no_subs:
        if args.subs:
            subs_path = Path(args.subs)
            if len(tenuti) > 1:
                log(
                    "  ATTENZIONE: --subs esterno con i tagli dei silenzi attivi: "
                    "i tempi delle parole nel file non terranno conto dei tagli"
                )
        else:
            # Genera i sottotitoli al volo dalla trascrizione.
            from make_subs import build_ass

            subs_path = work / f"subs_{start:.0f}_{end:.0f}.ass"
            subs_path.write_text(
                build_ass(
                    words,
                    start,
                    end,
                    style_name=style,
                    keywords=keywords,
                    hook=hook,
                    hook_seconds=hook_seconds,
                    cta=cta,
                    tenuti=tenuti,
                    pause=(grafiche or {}).get("pause"),
                ),
                encoding="utf-8",
            )
            log(f"  sottotitoli generati: {subs_path.name}")

    sendcmd_path = Path(args.sendcmd) if args.sendcmd else None
    if args.auto_track and sendcmd_path is None:
        from track_subject import build_crop_path, emphasis_times

        sendcmd_path = build_crop_path(
            work,
            start,
            end,
            tenuti=tenuti,
            enfasi=emphasis_times(words, keywords, start, end),
            fps_out=video_info(source)["fps"],
            stacchi=stacchi,
        )
        if sendcmd_path is None:
            # Sorgente gia 9:16: la finestra e il fotogramma intero, e il crop
            # statico di `crop_geometry` fa gia la cosa giusta.
            log("  nessun crop dinamico: il sorgente e gia nel formato di uscita")
        else:
            log(f"  crop dinamico generato: {sendcmd_path.name}")

    # Con il crop dinamico la geometria la decidono i comandi, non i valori
    # statici: si parte comunque da una finestra valida perche il primo comando
    # arriva dopo il primo fotogramma.
    logo_path = resolve_logo(work, logo_name)
    if logo_name and logo_path is None:
        raise SystemExit(
            f"logo '{logo_name}' non trovato in {work}, in assets/, "
            f"ne come percorso. Lo segnala anche validate.py."
        )
    if logo_path:
        log(f"  logo impresso per tutta la clip: {logo_path.name}")

    result = render(
        work,
        source,
        start,
        end,
        out,
        subs_path,
        preview=args.preview,
        sendcmd=sendcmd_path,
        logo=logo_path,
        tenuti=tenuti,
        grafiche=grafiche,
    )
    if args.slug:
        result["slug"] = args.slug
    log(f"  fatto: {result['size_mb']} MB, {result['resolution']}")
    emit(result)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
