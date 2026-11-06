"""Stage 1 - Trascrizione con timestamp per parola.

    python transcribe.py work/<slug>              # usa meta.json della cartella
    python transcribe.py work/<slug> --model medium

I timestamp per parola non sono un extra: servono per tagliare senza mozzare le
frasi e per sincronizzare i sottotitoli karaoke. Senza quelli mezza pipeline
non funziona.

Il risultato viene messo in cache: e il passaggio piu lento dell'intera pipeline
(10-15 min per un'ora di video su CPU) e non va mai rifatto due volte.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from common import (
    WHISPER_CACHE,
    default_threads,
    emit,
    fmt_timecode,
    log,
    read_json,
    require_audio,
    strip_surrogates,
    write_json,
)

# int8 su CPU: ~4x piu veloce di float32 con una perdita di qualita trascurabile
# per il parlato. Su questa macchina non c'e CUDA, quindi non c'e alternativa.
COMPUTE_TYPE = "int8"
# Un modello solo per le due lingue (25 settembre 2026). `small` era il default per
# tutte, e l'italiano chiedeva `--model large-v3-turbo` a mano: il pannello non lo
# passava, quindi i video italiani lavorati da li' uscivano bucati. Sul banco del
# 25/09 (PROGRESS.md) anche in inglese `small` perde piu' di turbo — WER 16,9 contro
# 12,7, 63 s di parlato mancato contro 15 — e non e' piu' veloce: 4,3x contro 3,9x.
DEFAULT_MODEL = "large-v3-turbo"

# Le lingue che questo progetto si aspetta. Il rilevamento sceglie **solo** qui
# dentro: una lingua fuori insieme non puo vincere per errore.
EXPECTED_LANGUAGES = ("it", "en")
DETECT_SAMPLES = 5
DETECT_WINDOW = 30.0  # secondi per campione: quanto Whisper guarda comunque
SAMPLE_RATE = 16000


def detect_language_voted(
    model, audio: Path, allowed: tuple[str, ...], samples: int = DETECT_SAMPLES
) -> tuple[str, float]:
    """La lingua, votata in piu punti del video e ristretta a quelle attese.

    Il rilevamento di Whisper guarda i primi 30 secondi e basta. Su un video
    reale in inglese ha risposto gallese con confidenza 0.73, e ha poi
    decodificato *tutto il resto* in gallese inventato: non degrada, rompe.
    Bastano una sigla musicale, dei saluti o una frase in un'altra lingua.

    Due correzioni, e servono entrambe: si guarda in piu punti sparsi lungo il
    video, e si sceglie solo tra le lingue attese. Il voto e la somma delle
    probabilita, non la maggioranza dei campioni: un campione incerto pesa poco
    invece di valere quanto uno sicuro.
    """
    from faster_whisper import decode_audio

    data = decode_audio(str(audio), sampling_rate=SAMPLE_RATE)
    total = len(data) / SAMPLE_RATE
    window = min(DETECT_WINDOW, total)
    n = min(samples, max(1, int(total // window)))

    # Punti sparsi nel corpo del video: inizio e coda sono i meno rappresentativi.
    frazioni = [0.5] if n == 1 else [0.1 + 0.8 * i / (n - 1) for i in range(n)]

    punteggi = {lang: 0.0 for lang in allowed}
    for frazione in frazioni:
        istante = min(max(0.0, frazione * total), max(0.0, total - window))
        inizio = int(istante * SAMPLE_RATE)
        chunk = data[inizio : inizio + int(window * SAMPLE_RATE)]
        if len(chunk) < SAMPLE_RATE:
            continue

        _, _, tutte = model.detect_language(audio=chunk)
        probs = dict(tutte)
        for lang in allowed:
            punteggi[lang] += probs.get(lang, 0.0)

        migliore = max(probs, key=probs.get)
        ammessa = max(allowed, key=lambda l: probs.get(l, 0.0))
        nota = "" if migliore == ammessa else f"  (fuori insieme: {migliore} {probs[migliore]:.2f})"
        log(f"  {fmt_timecode(istante)}: {ammessa} {probs.get(ammessa, 0.0):.2f}{nota}")

    scelta = max(punteggi, key=punteggi.get)
    totale = sum(punteggi.values()) or 1.0
    return scelta, punteggi[scelta] / totale


def transcribe(
    audio: Path,
    model_size: str,
    language: str | None,
    threads: int,
    allowed: tuple[str, ...] = EXPECTED_LANGUAGES,
    provenienza: str = "core fisici",
) -> dict:
    from faster_whisper import WhisperModel

    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

    # Da dove viene il numero di thread, non solo quale: una misura registrata
    # con un valore ricavato dalla macchina e una misura di quella macchina,
    # una presa con un valore chiesto a mano e un'altra cosa.
    log(
        f"carico il modello Whisper '{model_size}' "
        f"(CPU, {COMPUTE_TYPE}, {threads} thread — {provenienza})"
    )
    model = WhisperModel(
        model_size,
        device="cpu",
        compute_type=COMPUTE_TYPE,
        cpu_threads=threads,
        download_root=str(WHISPER_CACHE),
    )

    if language is None:
        log(
            f"nessuna lingua nota: la cerco in piu punti del video, "
            f"scegliendo solo tra {' e '.join(allowed)}"
        )
        language, quota = detect_language_voted(model, audio, allowed)
        log(f"lingua scelta: {language} ({quota:.0%} del voto)")

    log("trascrivo (il primo output arriva dopo qualche minuto)")
    segments, info = model.transcribe(
        str(audio),
        # Con None Whisper rileva la lingua **senza vincoli**, guardando i primi 30
        # secondi: e il caso che ha prodotto un video inglese trascritto in gallese
        # inventato. Il chiamante evita di arrivarci passando la lingua o deducendola
        # da meta.json; la restrizione a it/en con voto su piu campioni non c'e ancora.
        language=language,
        word_timestamps=True,
        vad_filter=True,  # senza, Whisper inventa testo sui silenzi
        vad_parameters={"min_silence_duration_ms": 500},
        beam_size=5,
        condition_on_previous_text=False,  # limita la deriva su video lunghi
    )

    log(f"lingua rilevata: {info.language} (confidenza {info.language_probability:.2f})")

    out_segments: list[dict] = []
    words: list[dict] = []
    last_log = 0.0

    for seg in segments:
        seg_words = []
        for w in seg.words or []:
            entry = {
                "start": round(w.start, 3),
                "end": round(w.end, 3),
                # I surrogati spaiati vanno tolti **qui**, dove il testo entra
                # nella pipeline: piu a valle finirebbero nei sottotitoli
                # impressi, e da un mp4 non si tolgono piu.
                "word": strip_surrogates(w.word.strip()),
                "prob": round(w.probability, 3),
            }
            words.append(entry)
            seg_words.append(entry)

        out_segments.append(
            {
                "start": round(seg.start, 3),
                "end": round(seg.end, 3),
                "text": strip_surrogates(seg.text.strip()),
                "words": seg_words,
            }
        )

        # Avanzamento ogni 5 minuti di materiale: su un'ora di video senza
        # feedback sembrerebbe bloccato.
        if seg.end - last_log > 300:
            last_log = seg.end
            log(f"  ... {seg.end / 60:.0f} min trascritti")

    return {
        "language": info.language,
        "language_probability": round(info.language_probability, 3),
        "model": model_size,
        "duration": round(info.duration, 2),
        "segments": out_segments,
        "words": words,
        "word_count": len(words),
    }


def stop_se_muto(word_count: int) -> None:
    """Zero parole non e una trascrizione povera: e l'assenza della materia prima.

    Questa skill sceglie **leggendo**, e senza testo non c'e niente da leggere.
    Fermarsi qui risparmia segnali e volti, ma soprattutto evita che la pipeline
    arrivi in fondo e annunci "pronto" consegnando una vista vuota — che e
    esattamente cosa faceva su un video con la traccia audio muta.

    Il controllo vale **anche sulla trascrizione in cache**, e non e un dettaglio:
    la prima esecuzione scrive comunque `transcript.json` prima di fermarsi, e
    senza questo la seconda passerebbe dalla cache con codice 0, rimettendo in
    piedi lo stesso silenzio che il controllo esiste per togliere.
    """
    if word_count:
        return
    raise SystemExit(
        "nessuna parola trascritta: in questo audio non c'e parlato riconoscibile.\n"
        "Non e un problema di modello o di lingua — non c'e testo su cui giudicare "
        "un momento, quindi non c'e nessuna clip da scegliere.\n"
        "Se invece il parlato c'e, e la traccia audio sbagliata: controlla "
        "'audio_lang' in meta.json."
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Trascrizione con timestamp per parola")
    ap.add_argument("work_dir", help="cartella di lavoro creata da ingest.py")
    ap.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="modello Whisper (default: large-v3-turbo, per italiano e inglese)",
    )
    ap.add_argument(
        "--language",
        default=None,
        help="forza la lingua (es. 'it'); default: rilevamento automatico",
    )
    ap.add_argument(
        "--languages",
        default=",".join(EXPECTED_LANGUAGES),
        help="tra quali lingue puo scegliere il rilevamento, se la lingua "
        "non e nota (default: it,en). Restringere e la protezione: "
        "senza, Whisper puo rispondere qualunque cosa.",
    )
    ap.add_argument(
        "--threads",
        type=int,
        default=None,
        help="thread della trascrizione. Default: i **core fisici** della "
        "macchina, ricavati e mai digitati. Oltre i fisici lo scaling "
        "si appiattisce, perche i thread SMT si contendono le stesse "
        "unita di calcolo.",
    )
    ap.add_argument("--force", action="store_true", help="riscrive anche se esiste gia")
    args = ap.parse_args()

    work = Path(args.work_dir)
    meta_path = work / "meta.json"
    if not meta_path.exists():
        raise SystemExit(f"meta.json non trovato in {work} - esegui prima ingest.py")

    meta = read_json(meta_path)
    out_path = work / "transcript.json"

    if out_path.exists() and not args.force:
        cached = read_json(out_path)
        if cached.get("model") == args.model:
            log(
                f"trascrizione gia in cache ({cached['word_count']} parole, modello {cached['model']})"
            )
            stop_se_muto(cached["word_count"])
            emit(
                {
                    "path": str(out_path),
                    "cached": True,
                    **{k: cached[k] for k in ("language", "model", "word_count")},
                }
            )
            return
        log(
            f"in cache c'e il modello '{cached.get('model')}', ne serve '{args.model}': ritrascrivo"
        )

    # La lingua deriva dalla traccia audio scelta in ingest, non dall'attenzione di
    # chi lancia il comando: scaricare la traccia inglese e poi trascriverla lasciando
    # decidere a Whisper e la stessa classe di errore che ha prodotto il gallese.
    language = args.language or meta.get("audio_lang")
    if language and not args.language:
        log(f"lingua dedotta da meta.json (traccia audio scaricata): {language}")
    elif not language:
        log("nessuna lingua indicata ne deducibile: la decide il voto sui campioni")

    allowed = tuple(x.strip() for x in args.languages.split(",") if x.strip())

    # Il default non e un numero: e la macchina su cui si sta girando. Un `8`
    # scritto a mano era vero sull'host su cui era stato scritto e lasciava meta
    # macchina ferma sul successivo, senza che niente lo segnalasse.
    threads = args.threads if args.threads else default_threads()
    provenienza = "richiesti" if args.threads else "core fisici"

    result = transcribe(
        require_audio(work, meta), args.model, language, threads, allowed, provenienza
    )
    write_json(out_path, result)

    log(f"fatto: {result['word_count']} parole, {len(result['segments'])} segmenti")
    stop_se_muto(result["word_count"])
    emit(
        {
            "path": str(out_path),
            "cached": False,
            "language": result["language"],
            "model": result["model"],
            "word_count": result["word_count"],
        }
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
