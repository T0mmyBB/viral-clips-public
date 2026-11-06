"""Stage 0 - Ingest.

Porta un video (link o file locale) dentro work/<slug>/ e ne estrae l'audio
per la trascrizione.

    python ingest.py "https://youtube.com/watch?v=..."
    python ingest.py "C:\\video\\intervista.mp4"
    python ingest.py work/<slug> --fetch-source     # il sorgente pesante, dopo

**Da un link si scarica in due tempi.** La prima fase prende solo audio e proxy
480p: e tutto quello che serve per trascrivere, analizzare e *scegliere*, e costa
una manciata di secondi. Il sorgente a piena risoluzione serve al solo render, e
scaricarlo subito vorrebbe dire aspettare minuti prima di poter leggere una riga
di trascrizione. Lo prende `ensure_source()` quando serve davvero — il render lo
chiama da solo — oppure `--fetch-source` per portarselo avanti mentre si legge.

Da un file locale non c'e niente da scaricare: il proxy si transcodifica, ed e
l'unico caso in cui tocca ancora farlo.

Idempotente: se i file esistono gia li riusa invece di riscaricare.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from common import (
    WORK_DIR,
    emit,
    log,
    read_json,
    run,
    slugify,
    tool,
    video_info,
    work_file,
    write_json,
)


def in_work(work: Path, path: Path | None) -> str | None:
    """Come scrivere in `meta.json` un file che sta nella cartella di lavoro.

    **Solo il nome, non il percorso.** `audio.wav`, `proxy.*` e `source.*` sono
    per definizione li dentro: scriverne il percorso assoluto significa
    registrare *dove stava la cartella* il giorno in cui e stata creata, e le
    cartelle viaggiano — `export_skill.py` esiste per quello. Un sorgente locale
    adottato senza copiarlo sta invece fuori, e quello resta assoluto perche il
    suo percorso e l'unica cosa che lo ritrova.
    """
    if path is None:
        return None
    path = Path(path)
    try:
        dentro = path.resolve().parent == Path(work).resolve()
    except OSError:
        dentro = False
    return path.name if dentro else str(path)


# Si scarica la risoluzione piu alta disponibile, non il 1080p.
# Il motivo e geometrico: la finestra 9:16 ritagliata da un 1920x1080 e larga
# 608 px, che portata a 1080x1920 e un ingrandimento di 1.78x. Da un 1440p il
# ritaglio e 810 px (1.33x); da un 2160p e 1215 px, cioe una *riduzione*, quindi
# la clip esce piu nitida del sorgente invece che piu morbida.
PREFERRED_MIN_HEIGHT = 1440

# Altezza del proxy di analisi. 480p e piu che sufficiente per rilevare cambi
# scena e volti, e riduce di ~20 volte i pixel da decodificare.
PROXY_HEIGHT = 480


def crop_fields(width: int, height: int) -> dict:
    """Quanto dovra ingrandire il render.

    La finestra 9:16 e alta quanto il sorgente e larga 9/16 di quello. Sotto i
    1080 px di larghezza serve un ingrandimento, sopra una riduzione — e in quel
    caso la clip esce piu nitida del sorgente.

    **La larghezza serve, non e un di piu.** Su un sorgente piu alto del 9:16 la
    formula da sola sfora il fotogramma: da un 1080x2400 direbbe 1350 px di
    ritaglio, cioe una riduzione, mentre la finestra vera e larga 1080 e non
    ingrandisce niente. Il numero sbagliato non fermerebbe nessun render — si
    limiterebbe a mentire nei metadati, che e il modo in cui questi errori
    sopravvivono.
    """
    crop_width = min(round(height * 9 / 16), width) if width else round(height * 9 / 16)
    return {
        "crop_width_9_16": crop_width,
        "upscale_required": bool(crop_width and crop_width < 1080),
        "upscale_factor": round(1080 / crop_width, 3) if crop_width else None,
        # Il formato in chiaro: lo legge check_material.py per dire che un
        # sorgente gia verticale non ha nessuna reinquadratura da fare.
        "orientation": (
            "verticale" if height > width else "quadrato" if height == width else "orizzontale"
        ),
    }


def build_format(audio_lang: str | None, min_height: int = PREFERRED_MIN_HEIGHT) -> str:
    """Selettore di formato, con ripieghi a scalare.

    Se `audio_lang` e indicato si prova prima a prendere quella traccia: i video
    doppiati di YouTube espongono piu tracce audio e quella "originale" spesso
    non e la lingua che si vuole. Se la lingua non esiste si ripiega sulla
    migliore disponibile invece di fallire.
    """
    a_lang = f"ba[language^={audio_lang}]" if audio_lang else None
    tiers: list[str] = []

    for audio in [a_lang, "ba"] if a_lang else ["ba"]:
        tiers.append(f"bv*[height>={min_height}]+{audio}")
        tiers.append(f"bv*+{audio}")
    tiers.append("b")
    return "/".join(tiers)


def ensure_ffmpeg_on_path() -> None:
    """yt-dlp cerca ffmpeg sul PATH del processo per unire video e audio.

    Non basta passargli `ffmpeg_location`: alcuni controlli interni guardano
    comunque il PATH, e senza questo il download di qualunque formato separato
    fallisce con "ffmpeg is not installed" anche se ffmpeg c'e.
    """
    ffdir = os.path.dirname(tool("ffmpeg"))
    if ffdir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = ffdir + os.pathsep + os.environ.get("PATH", "")


# Una cartella di Drive, in tutte le grafie che i brief usano davvero:
# `/drive/folders/<id>`, `/drive/u/0/folders/<id>`, con o senza `?usp=sharing`.
CARTELLA_DRIVE = re.compile(r"drive\.google\.com/drive/(?:u/\d+/)?folders/([\w-]+)")

# L'elenco di una cartella pubblica, in HTML piatto e senza autenticazione.
# La pagina normale di Drive si disegna in JavaScript e non si legge; questa
# vista sopravvive perche' serve a chi incorpora una cartella in un sito.
VISTA_CARTELLA = "https://drive.google.com/embeddedfolderview?id={}#list"

# Un'entrata dell'elenco: l'id sta nell'attributo del contenitore, il tipo si
# legge dall'indirizzo (`/drive/folders/` = cartella, `/file/d/` = file) e il
# nome e' l'ultimo pezzo. Un solo passaggio, cosi' i tre restano appaiati.
ENTRATA_CARTELLA = re.compile(
    r'id="entry-([\w-]+)".*?<a href="([^"]+)".*?flip-entry-title">([^<]+)<', re.S
)


def voci_da_html(html: str) -> list[tuple[str, str, str]]:
    """Le entrate dell'elenco: [(tipo, id, nome)], con `tipo` «cartella» o «file».

    Il tipo si legge dall'indirizzo dell'ancora, non dall'icona: l'icona e' un
    `div` con una classe di sprite che cambia col tema, l'indirizzo dice
    `/drive/folders/` o `/file/d/` e non ha altri modi di dirlo.
    """
    return [
        ("cartella" if "/drive/folders/" in href else "file", entry_id, nome.strip())
        for entry_id, href, nome in ENTRATA_CARTELLA.findall(html)
    ]


def elenca_cartella_drive(folder_id: str) -> list[tuple[str, str, str]]:
    """Cosa c'e' in una cartella pubblica di Drive: [(tipo, id, nome)].

    Se la cartella non e' pubblica, o la pagina cambia forma, torna una lista
    vuota: il chiamante deve dire lo stesso che quello era un indirizzo di
    cartella — elencarne il contenuto e' un di piu', non la ragione del
    messaggio.
    """
    import urllib.request

    try:
        with urllib.request.urlopen(VISTA_CARTELLA.format(folder_id), timeout=30) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception:  # rete assente, cartella privata, pagina cambiata
        return []
    return voci_da_html(html)


def rifiuta_cartella_drive(url: str) -> None:
    """Una cartella di Drive non e' un video: si ferma qui, dicendo cosa c'e' dentro.

    I brief delle campagne consegnano il materiale come **cartella** — sei
    episodi da un'ora, piu' le sottocartelle degli extra — e scegliere quale
    lavorare e' una decisione editoriale, non un ripiego che uno script possa
    prendere al posto di chi legge il brief.

    Quello che invece lo script deve fare e' non far sembrare un guasto una
    scelta che manca. Lasciato a `yt-dlp`, un indirizzo di cartella muore
    dentro l'estrattore con `TypeError: expected string or bytes-like object,
    got 'bool'` — Google risponde 400 all'API interna e il `False` finisce in
    una regex — cioe' con un messaggio che non nomina ne' Drive ne' la
    cartella, e che manda a cercare il difetto nel posto sbagliato.
    """
    m = CARTELLA_DRIVE.search(url)
    if not m:
        return
    voci = elenca_cartella_drive(m.group(1))
    righe = [
        "questo e' l'indirizzo di una cartella di Drive, non di un video.",
        "Scegli il file da lavorare e rilancia con il suo link.",
    ]
    if voci:
        righe.append("")
        righe.append("Dentro ci sono:")
        for tipo, entry_id, nome in voci:
            dove = (
                f"https://drive.google.com/drive/folders/{entry_id}"
                if tipo == "cartella"
                else f"https://drive.google.com/file/d/{entry_id}/view"
            )
            righe.append(f"  [{tipo}] {nome}\n      {dove}")
    else:
        righe.append("")
        righe.append(
            "Non sono riuscito a elencarla: puo' essere privata, oppure "
            "servono le credenziali del provider."
        )
    raise SystemExit("\n".join(righe))


def available_audio_languages(info: dict) -> list[str]:
    return sorted(
        {
            f["language"]
            for f in info.get("formats", [])
            if f.get("language")
            and f.get("acodec", "none") != "none"
            and f.get("vcodec", "none") == "none"
        }
    )


def altezza_piu_alta(info: dict) -> int:
    """L'altezza piu' grande fra i formati che ce l'hanno dichiarata.

    Serve quando il formato scelto **non** la dichiara, che e' il caso di Drive:
    il master caricato dal committente e' il formato `source`, e di quello Drive
    non dice ne' risoluzione ne' codec — accanto ci mette i suoi transcodificati
    (360p, 720p, 1080p), che invece la dichiarano.

    **Zero non vuol dire «piccolo».** Letto come altezza vera, un'altezza
    sconosciuta faceva saltare il proxy di analisi: da li' in poi segnali,
    volti e cambi scena giravano sul master a piena risoluzione, che e'
    esattamente il lavoro che il proxy esiste per non fare — e non come un
    errore, ma come una preparazione che non finisce piu'.
    """
    return max((f.get("height") or 0 for f in info.get("formats", [])), default=0)


def misure_dal_flusso(video_fmt: dict) -> dict:
    """Cosa c'e' davvero nel flusso, letto a distanza e **senza scaricarlo**.

    `ffprobe` su un indirizzo http legge l'intestazione del contenitore e
    smette: misurato su un master di 7,0 GB su Drive, cinque secondi per sapere
    3840x2160, h264, 23,976 fps. E' l'unica fonte vera quando il formato non
    dichiara niente di se', e costa meno di qualunque ipotesi sbagliata — da
    `width` mancante `crop_fields()` concludeva **«orientamento verticale»** su
    un 16:9, cioe' la condizione con cui la regia decide di non muovere la
    camera e di non ingrandire.

    Se il flusso non risponde torna vuoto: il chiamante ha i suoi ripieghi, e
    il sorgente vero arriva comunque prima del render (`attach_source`).
    """
    url = video_fmt.get("url")
    if not url:
        return {}
    try:
        return {k: v for k, v in video_info(url).items() if k != "path"}
    except Exception:  # flusso scaduto, rete assente, contenitore illeggibile
        log("non sono riuscito a misurare il flusso a distanza")
        return {}


def probe(url: str, audio_lang: str | None) -> dict:
    """Estrazione senza scaricare, con il selettore di formato gia applicato.

    Da qui escono il titolo, le tracce audio disponibili e — cosa che serve alla
    prima fase — le dimensioni vere del sorgente. `meta.json` puo cosi dire
    quanto e grande il video e quanto dovra ingrandire il ritaglio, mentre il
    file pesante non e ancora stato toccato.
    """
    import yt_dlp

    with yt_dlp.YoutubeDL(
        {
            "format": build_format(audio_lang),
            "quiet": True,
            "no_warnings": True,
            "simulate": True,
        }
    ) as y:
        return y.extract_info(url, download=False)


def traccia_assente(f: dict, campo: str) -> bool:
    """Vero solo se il formato **dichiara** di non avere quella traccia.

    `"none"` e' una dichiarazione — cosi' YouTube segna le tracce separate — e
    un campo che manca del tutto e' un'altra cosa: vuol dire che la fonte non
    l'ha detto. Il master caricato su Drive non dice niente di se', ne' codec ne'
    risoluzione, e leggere quel silenzio come «nessuna traccia video» fermava
    l'ingest con «nessuna traccia video selezionabile per questo link» sul file
    che *e'* il video della campagna.

    Cosa contiene davvero lo dice `ffprobe` quando il file e' sul disco
    (`attach_source`): una supposizione fatta prima di scaricarlo puo' solo
    scegliere fra lo sbagliarsi e il fermarsi, e fermarsi qui costa la consegna.
    """
    return f.get(campo) == "none"


def selected_streams(info: dict) -> tuple[dict, dict | None]:
    """I formati che il selettore ha scelto, come (video, audio)."""
    chosen = info.get("requested_formats") or [info]
    video = next((f for f in chosen if not traccia_assente(f, "vcodec")), None)
    audio = next((f for f in chosen if not traccia_assente(f, "acodec")), None)
    if video is None:
        raise SystemExit("nessuna traccia video selezionabile per questo link")
    return video, audio


def fetch(url: str, work: Path, fmt: str, name: str, **extra) -> Path:
    """Scarica un formato dentro `work/<name>.<ext>` e ritorna il file."""
    import yt_dlp

    ensure_ffmpeg_on_path()
    opts = {
        "format": fmt,
        "outtmpl": str(work / f"{name}.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 5,
        "fragment_retries": 10,
        # A blocchi da 10 MiB: senza, googlevideo ha servito 12 MiB di un
        # formato e poi solo 500, sempre allo stesso byte e anche da zero.
        "http_chunk_size": 10 * 1024 * 1024,
        **extra,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    files = [p for p in sorted(work.glob(f"{name}.*")) if p.suffix.lower() != ".part"]
    if not files:
        raise RuntimeError(f"yt-dlp non ha prodotto nessun file per '{name}'")
    return files[0]


def download_audio(url: str, work: Path, audio_lang: str | None) -> Path:
    """La sola traccia audio, che e quanto basta per trascrivere."""
    existing = [p for p in sorted(work.glob("audio_src.*")) if p.suffix.lower() != ".part"]
    if existing:
        log(f"traccia audio gia scaricata: {existing[0].name}")
        return existing[0]

    fmt = f"ba[language^={audio_lang}]/ba" if audio_lang else "ba"
    log(f"scarico la traccia audio{f' ({audio_lang})' if audio_lang else ''}")
    return fetch(url, work, fmt, "audio_src")


def download_proxy(url: str, work: Path) -> Path:
    """Il proxy 480p **scaricato**, invece che transcodificato dal sorgente.

    YouTube il 480p ce l'ha gia pronto: prenderlo costa secondi di rete contro
    minuti di CPU, e il file esce persino piu piccolo di quello che
    produrremmo noi.

    Si preferisce l'**H.264**: non e una preferenza estetica, e la ragione per
    cui il proxy esiste. Lo decodificano quattro passaggi diversi (segnali,
    prescansione volti, volti densi, tracking) e su CPU l'H.264 e molto piu
    veloce di VP9 e AV1. Se non c'e, si ripiega su qualunque cosa stia sotto i
    480p.
    """
    existing = [p for p in sorted(work.glob("proxy.*")) if p.suffix.lower() != ".part"]
    if existing and existing[0].stat().st_size > 1024:
        log(f"proxy gia presente: {existing[0].name}")
        return existing[0]

    fmt = f"bv*[height<={PROXY_HEIGHT}][vcodec^=avc1]/bv*[height<={PROXY_HEIGHT}]/wv*"
    log(f"scarico il proxy di analisi a {PROXY_HEIGHT}p")
    return fetch(url, work, fmt, "proxy")


def download_source(url: str, work: Path, audio_lang: str | None) -> Path:
    """Il sorgente a piena risoluzione: serve al solo render."""
    existing = [
        p for p in sorted(work.glob("source.*")) if p.suffix.lower() in {".mp4", ".mkv", ".webm"}
    ]
    if existing:
        log(f"sorgente gia scaricato: {existing[0].name}")
        return existing[0]

    log("scarico il sorgente a piena risoluzione")
    if audio_lang:
        log(f"traccia audio richiesta: {audio_lang}")
    return fetch(
        url,
        work,
        build_format(audio_lang),
        "source",
        # mkv accetta vp9/av1 e opus senza ricodifica: qualunque altro
        # contenitore costringerebbe a riconvertire, cioe a perdere qualita.
        merge_output_format="mkv",
    )


def attach_source(work: Path, meta: dict, video: Path) -> dict:
    """Registra in `meta.json` il sorgente appena arrivato.

    I dati stimati dal probe vengono sostituiti da quelli letti dal file: di
    norma coincidono, ma se il selettore ha ripiegato su un formato diverso da
    quello previsto e il file ad avere ragione.
    """
    meta = dict(meta)
    meta["video"] = in_work(work, video)
    meta["source_pending"] = False
    info = {k: v for k, v in video_info(video).items() if k != "path"}
    meta.update(info)
    meta.update(crop_fields(info["width"], info["height"]))
    write_json(work / "meta.json", meta)
    return meta


def ensure_source(work: Path) -> Path:
    """Il sorgente a piena risoluzione, scaricato solo quando serve davvero.

    La chiama il render. Fino a quel momento tutta la pipeline gira sul proxy, e
    chi legge e sceglie non aspetta il file pesante.
    """
    meta = read_json(work / "meta.json")
    video = work_file(work, meta.get("video"), "source.*")
    if video is not None:
        return video

    if not meta.get("is_url"):
        raise SystemExit(
            f"il file locale di questo lavoro non esiste piu: {meta.get('video')}\n"
            f"Non e scaricabile: e un file tuo, non un link."
        )

    log("il sorgente a piena risoluzione non c'e ancora: lo scarico adesso")
    downloaded = download_source(meta["source"], work, meta.get("audio_lang"))
    attach_source(work, meta, downloaded)
    return downloaded


def adopt_local(path: Path, work_root: Path) -> tuple[Path, str]:
    """Usa un file locale senza copiarlo: su video da 1 ora la copia sarebbe
    solo tempo e spazio sprecati."""
    path = path.expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"file non trovato: {path}")

    title = path.stem
    work = work_root / slugify(title)
    work.mkdir(parents=True, exist_ok=True)
    return path, title


def make_proxy(video: Path, dest: Path, source_height: int) -> Path | None:
    """Copia a bassa risoluzione per la fase di analisi, **transcodificata**.

    Resta solo per i file locali: da un link il proxy si scarica gia pronto
    (`download_proxy`), che costa secondi invece di minuti.

    Misurato su un 4K AV1 da 27 minuti: rilevare i cambi scena direttamente sul
    sorgente non era finito dopo 15 minuti a 9 core saturi. Il proxy si genera in
    ~6 minuti una volta sola e poi ogni analisi ci gira sopra in una frazione del
    tempo.

    L'accelerazione hardware qui *peggiora* le cose (misurato: 19-44s contro 13s
    software su 60s di 4K AV1): i fotogrammi decodificati sulla GPU vanno
    ricopiati in memoria di sistema per i filtri CPU, e a 4K quel trasferimento
    costa piu di quanto la GPU faccia risparmiare.
    """
    if source_height <= PROXY_HEIGHT:
        log("sorgente gia a bassa risoluzione: nessun proxy necessario")
        return None
    if dest.exists() and dest.stat().st_size > 1024:
        log(f"proxy gia presente: {dest.name}")
        return dest

    log(f"genero il proxy di analisi a {PROXY_HEIGHT}p (una volta sola)")
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video),
            "-vf",
            f"scale=-2:{PROXY_HEIGHT}",
            "-an",  # l'audio dell'analisi e gia in audio.wav
            "-c:v",
            "libx264",
            "-crf",
            "28",
            "-preset",
            "veryfast",
            "-g",
            "30",  # keyframe fitti: il seek deve essere preciso
            str(dest),
        ],
        capture=True,
    )
    return dest


def extract_audio(video: Path, dest: Path) -> Path:
    """WAV 16 kHz mono: il formato che Whisper si aspetta."""
    if dest.exists() and dest.stat().st_size > 1024:
        log(f"audio gia estratto: {dest.name}")
        return dest

    log("estraggo l'audio")
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(dest),
        ],
        capture=True,
    )
    return dest


def ingest_url(url: str, work_root: Path, audio_lang: str | None) -> tuple[Path, dict]:
    """Prima fase: audio e proxy, cioe tutto quello che serve per **decidere**."""
    rifiuta_cartella_drive(url)
    info = probe(url, audio_lang)
    title = info.get("title") or "video"
    slug = slugify(title)
    work = work_root / slug
    work.mkdir(parents=True, exist_ok=True)

    langs = available_audio_languages(info)
    if langs:
        log(f"tracce audio disponibili: {', '.join(langs)}")
    if audio_lang and langs and not any(l.startswith(audio_lang) for l in langs):
        # Meglio fermarsi che scaricare in silenzio la lingua sbagliata: sono
        # 27 minuti di download e una trascrizione intera buttati.
        raise SystemExit(
            f"la traccia audio '{audio_lang}' non esiste per questo video.\n"
            f"Disponibili: {', '.join(langs)}"
        )

    video_fmt, audio_fmt = selected_streams(info)
    if audio_fmt is None and video_fmt.get("acodec", "none") == "none":
        raise SystemExit(
            "il video non ha traccia audio: senza parlato non c'e trascrizione "
            "e quindi nessun criterio per scegliere i momenti"
        )

    # Un formato che non dichiara la propria altezza si misura, non si indovina:
    # `altezza_piu_alta` e' il ripiego di ultima istanza, e dice al massimo
    # quanto sono alti i *transcodificati* che stanno accanto al master.
    misure = {} if video_fmt.get("height") else misure_dal_flusso(video_fmt)
    height = video_fmt.get("height") or misure.get("height") or altezza_piu_alta(info)
    width = video_fmt.get("width") or misure.get("width") or 0
    audio = extract_audio(download_audio(url, work, audio_lang), work / "audio.wav")

    # Sorgente gia piccolo: un proxy sarebbe una copia di se stesso. Si scarica
    # subito il sorgente, che a quelle dimensioni costa quanto il proxy.
    proxy = download_proxy(url, work) if height > PROXY_HEIGHT else None
    proxy_info = video_info(proxy) if proxy else None

    meta = {
        "slug": slug,
        "title": title,
        "source": url,
        "is_url": True,
        "video": None,
        # Il render lo scarichera da solo: vedi ensure_source().
        "source_pending": True,
        "audio": in_work(work, audio),
        "proxy": in_work(work, proxy),
        "proxy_width": proxy_info["width"] if proxy_info else None,
        "proxy_height": proxy_info["height"] if proxy_info else None,
        "work_dir": str(work),
        "audio_lang": audio_lang,
        "available_audio": langs,
        "duration": float(info.get("duration") or misure.get("duration") or 0.0),
        "width": width or None,
        "height": height,
        "fps": video_fmt.get("fps") or misure.get("fps") or 30.0,
        "has_audio": True,
        "video_codec": (video_fmt.get("vcodec") or misure.get("video_codec") or "").split(".")[0],
        "audio_codec": (audio_fmt.get("acodec") if audio_fmt else None)
        or misure.get("audio_codec"),
        **crop_fields(width, height),
    }
    write_json(work / "meta.json", meta)

    if proxy is None:
        # Senza proxy l'analisi non avrebbe su cosa girare: il sorgente serve subito.
        meta = attach_source(work, meta, download_source(url, work, audio_lang))
    else:
        # Rilanciare l'ingest su un lavoro gia avanti non deve *dimenticare* un
        # sorgente che e li sul disco.
        already = [
            p
            for p in sorted(work.glob("source.*"))
            if p.suffix.lower() in {".mp4", ".mkv", ".webm"}
        ]
        if already:
            log(f"sorgente gia scaricato: {already[0].name}")
            meta = attach_source(work, meta, already[0])
    return work, meta


def ingest_local(path: Path, work_root: Path) -> tuple[Path, dict]:
    """File locale: niente da scaricare, il proxy si transcodifica."""
    video, title = adopt_local(path, work_root)
    slug = slugify(title)
    work = work_root / slug
    work.mkdir(parents=True, exist_ok=True)

    info = video_info(video)
    if not info["has_audio"]:
        raise SystemExit(
            "il video non ha traccia audio: senza parlato non c'e trascrizione "
            "e quindi nessun criterio per scegliere i momenti"
        )

    audio = extract_audio(video, work / "audio.wav")
    proxy = make_proxy(video, work / "proxy.mp4", info["height"])
    proxy_info = video_info(proxy) if proxy else None

    meta = {
        "slug": slug,
        "title": title,
        # Il percorso **risolto**, non quello come e stato digitato: `..\\..\\video.mp4`
        # e vero solo rispetto alla cartella da cui e partito quel comando, e in
        # `meta.json` diventa un percorso che non si sa piu da dove leggere.
        "source": in_work(work, video),
        "is_url": False,
        # Il sorgente locale non si copia: resta dov'e. Se sta fuori dalla
        # cartella di lavoro il percorso intero e l'unica cosa che lo ritrova;
        # se sta dentro — le fixture di prova — vale la regola di `in_work`.
        "video": in_work(work, video),
        "source_pending": False,
        "audio": in_work(work, audio),
        "proxy": in_work(work, proxy),
        "proxy_width": proxy_info["width"] if proxy_info else None,
        "proxy_height": proxy_info["height"] if proxy_info else None,
        "work_dir": str(work),
        **crop_fields(info["width"], info["height"]),
        **{k: v for k, v in info.items() if k != "path"},
    }
    write_json(work / "meta.json", meta)
    return work, meta


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest di un video da link o file locale")
    ap.add_argument(
        "source",
        help="URL, percorso di un file video, oppure la cartella di "
        "lavoro insieme a --fetch-source",
    )
    ap.add_argument(
        "--work-root", default=str(WORK_DIR), help="cartella di lavoro (default: work/)"
    )
    ap.add_argument(
        "--audio-lang",
        default=None,
        help="codice della traccia audio da scaricare (es. 'en'). "
        "Sui video doppiati di YouTube la traccia originale spesso "
        "non e quella che si vuole.",
    )
    ap.add_argument(
        "--fetch-source",
        action="store_true",
        help="seconda fase: scarica il sorgente a piena risoluzione per una "
        "cartella di lavoro gia preparata. Il render lo fa da solo, "
        "questo serve a portarselo avanti mentre si legge e si sceglie.",
    )
    args = ap.parse_args()

    if args.fetch_source:
        work = Path(args.source)
        if not (work / "meta.json").exists():
            raise SystemExit(f"{work} non e una cartella di lavoro: manca meta.json")
        ensure_source(work)
        emit(read_json(work / "meta.json"))
        return

    work_root = Path(args.work_root)
    work_root.mkdir(parents=True, exist_ok=True)

    if args.source.lower().startswith(("http://", "https://", "www.")):
        work, meta = ingest_url(args.source, work_root, args.audio_lang)
    else:
        work, meta = ingest_local(Path(args.source), work_root)

    verdict = (
        f"ingrandimento {meta['upscale_factor']}x"
        if meta["upscale_required"]
        else f"riduzione {meta['upscale_factor']}x, nessuna perdita di nitidezza"
    )
    log(
        f"pronto: {meta['width']}x{meta['height']} @ {meta['fps']}fps, "
        f"{meta['duration'] / 60:.1f} min | ritaglio 9:16 = "
        f"{meta['crop_width_9_16']}px -> {verdict}"
    )
    if meta.get("source_pending"):
        log("il sorgente a piena risoluzione arrivera al render, o con --fetch-source")
    emit(meta)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
