"""Percorsi, helper ffmpeg e utilita condivise dagli script della pipeline."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPTS_DIR.parent
PROJECT_ROOT = SKILL_ROOT.parents[2]  # .../.claude/skills/viral-clips -> progetto-x

WORK_DIR = PROJECT_ROOT / "work"
OUTPUT_DIR = PROJECT_ROOT / "output"
ASSETS_DIR = SKILL_ROOT / "assets"
MODELS_DIR = ASSETS_DIR / "models"
FONTS_DIR = ASSETS_DIR / "fonts"

YUNET_MODEL = MODELS_DIR / "face_detection_yunet.onnx"
WHISPER_CACHE = MODELS_DIR / "whisper"


def _stdio_utf8() -> None:
    """Uscita in UTF-8, qualunque sia la codepage della console.

    Su Windows stdout arriva in cp1252, che non sa rappresentare buona parte di
    quello che questi script stampano. Non e teoria: `windows.py` moriva con
    UnicodeEncodeError perche Whisper aveva prodotto un surrogato spaiato dentro
    un segmento — e la vista di `windows.py` e l'unica cosa che si legge per
    scegliere, quindi quel crash toglieva di mezzo la parte editoriale intera.

    `errors="replace"` invece di lasciar propagare: un carattere illeggibile in
    un sottotitolo e un difetto, un comando che non stampa niente e un muro. E
    chi cattura questa uscita — `run.py`, `render_clip.py` — decodifica gia in
    UTF-8, quindi qui si smette anche di dipendere dalla codepage della macchina.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass  # stream rediretto o gia a posto


_stdio_utf8()


def strip_surrogates(text: str) -> str:
    """Toglie i surrogati spaiati, che non sono testo ma un token spezzato.

    Whisper li produce quando un token multibyte viene troncato: finiscono nel
    transcript, da li nei sottotitoli e nella vista. Non c'e niente da
    recuperare — nessun carattere vero corrisponde a un surrogato isolato — e
    lasciarli dentro significa portarsi il problema fino all'ASS impresso.
    """
    if not any(0xD800 <= ord(c) <= 0xDFFF for c in text):
        return text
    return "".join(c for c in text if not 0xD800 <= ord(c) <= 0xDFFF)


def log(msg: str) -> None:
    """Diagnostica su stderr, cosi stdout resta pulito per l'output JSON."""
    print(msg, file=sys.stderr, flush=True)


def find_tool(name: str) -> str:
    """Percorso di un eseguibile, cercandolo oltre il PATH del processo.

    winget aggiorna il PATH nel registro ma i processi gia avviati continuano a
    vedere quello vecchio: un terminale aperto prima dell'installazione non
    troverebbe ffmpeg, con un errore che sembra un'installazione mancante.
    """
    found = shutil.which(name)
    if found:
        return found

    # PATH aggiornato dal registro (solo Windows).
    if os.name == "nt":
        try:
            import winreg

            paths: list[str] = []
            for root, key in (
                (
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                ),
                (winreg.HKEY_CURRENT_USER, "Environment"),
            ):
                try:
                    with winreg.OpenKey(root, key) as k:
                        value, _ = winreg.QueryValueEx(k, "Path")
                        paths.extend(os.path.expandvars(p) for p in value.split(";") if p)
                except OSError:
                    continue

            found = shutil.which(name, path=os.pathsep.join(paths))
            if found:
                return found
        except ImportError:
            pass

        # Ultima spiaggia: la cartella dei pacchetti winget.
        pkg_root = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
        if pkg_root.is_dir():
            for match in pkg_root.glob(f"**/bin/{name}.exe"):
                return str(match)

    raise RuntimeError(
        f"'{name}' non trovato. Lancia setup.ps1, oppure riapri il terminale "
        f"se l'hai appena installato."
    )


_TOOL_CACHE: dict[str, str] = {}


def tool(name: str) -> str:
    """find_tool con memoizzazione: la ricerca nel registro non va ripetuta."""
    if name not in _TOOL_CACHE:
        _TOOL_CACHE[name] = find_tool(name)
    return _TOOL_CACHE[name]


def run(
    cmd: list[str], *, capture: bool = False, check: bool = True
) -> subprocess.CompletedProcess:
    """Esegue un comando esterno. In caso di errore mostra la coda di stderr,
    che per ffmpeg e l'unico posto dove finisce la causa vera del fallimento."""
    cmd = list(cmd)
    if cmd and cmd[0] in ("ffmpeg", "ffprobe"):
        cmd[0] = tool(cmd[0])
    proc = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and proc.returncode != 0:
        detail = ""
        if capture and proc.stderr:
            detail = "\n" + "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise RuntimeError(f"comando fallito ({proc.returncode}): {' '.join(cmd[:4])}...{detail}")
    return proc


def ffprobe(path: Path) -> dict:
    """Metadati del contenitore e degli stream."""
    proc = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture=True,
    )
    return json.loads(proc.stdout)


def video_info(path: Path) -> dict:
    """Estrae in forma piatta cio che serve alla pipeline: durata, dimensioni,
    fps e presenza di audio."""
    data = ffprobe(path)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video is None:
        raise RuntimeError(f"nessuna traccia video in {path.name}")

    # I frame rate arrivano come frazione ("30000/1001"). Non ci si puo fidare
    # di un singolo campo: i WebM dichiarano spesso avg_frame_rate=30000/1,
    # cioe 30000 fps, mentre r_frame_rate riporta il valore giusto. Prendiamo
    # il primo che cade in un intervallo plausibile.
    def parse_rate(raw: str | None) -> float | None:
        if not raw:
            return None
        try:
            num, den = (float(x) for x in raw.split("/"))
        except ValueError:
            return None
        if den <= 0:
            return None
        value = num / den
        return value if 1.0 <= value <= 240.0 else None

    fps = parse_rate(video.get("avg_frame_rate")) or parse_rate(video.get("r_frame_rate")) or 30.0

    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0.0)

    # Quanti byte l'indice attribuisce alle tracce. In un mp4 con `+faststart`
    # l'indice sta in testa, quindi un file troncato dopo la scrittura si sonda
    # come intero e dichiara la durata piena: il troncamento si vede solo
    # confrontando questo numero col peso vero. `None` se una traccia non lo dice.
    rates = [s.get("bit_rate") for s in streams]
    stream_bytes = sum(int(r) for r in rates) * duration / 8 if streams and all(rates) else None

    return {
        "path": str(path),
        "duration": duration,
        "width": int(video.get("width", 0)),
        "height": int(video.get("height", 0)),
        "fps": round(fps, 3),
        "has_audio": audio is not None,
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name") if audio else None,
        "stream_bytes": stream_bytes,
    }


def work_file(work: Path | str, stored: str | None, *patterns: str) -> Path | None:
    """Un file della cartella di lavoro, cercato **dove la cartella sta adesso**.

    `audio.wav`, `proxy.*` e `source.*` vivono per definizione dentro la cartella
    di lavoro, quindi il percorso scritto in `meta.json` e ridondante — e appena
    la cartella si sposta diventa peggio che ridondante, diventa falso. Non e
    teoria: `export_skill.py` esiste apposta per portare le cartelle sull'altra
    macchina, e li dentro c'erano percorsi di un utente che su questo computer
    non esiste (`C:\\Users\\pinuc\\...`) accanto ad altri relativi alla radice
    del progetto, che risolvono solo se il comando parte da li. Tre convenzioni
    nello stesso campo, e nessuna che sopravviva a un trasloco.

    L'ordine e: il percorso scritto se il file c'e davvero, poi lo stesso nome
    dentro questa cartella, poi i `patterns` (glob) come ultima rete. `None`
    quando non si trova niente — chi chiama sa dire meglio di qui cosa manca.

    Il campo `video` puo invece puntare **fuori**: da un file locale la pipeline
    adotta il sorgente dov'e, senza copiarlo. Per quello il primo ramo e l'unico
    che conta, e il ripiego non fa danni perche non trova niente.
    """
    work = Path(work)
    if stored:
        diretto = Path(stored)
        if diretto.is_file():
            return diretto
        accanto = work / diretto.name
        if accanto.is_file():
            return accanto
    for pattern in patterns:
        trovati = sorted(
            p for p in work.glob(pattern) if p.is_file() and p.suffix.lower() != ".part"
        )
        if trovati:
            return trovati[0]
    return None


def require_audio(work: Path | str, meta: dict) -> Path:
    """L'`audio.wav` della cartella di lavoro, o un errore che dice cosa manca.

    Il messaggio conta: una cartella arrivata da un'altra macchina ha tutti i
    metadati e nessun media, e senza questa riga l'errore che si vede e un
    `FileNotFoundError` su un percorso di un utente che qui non esiste — che
    sembra un difetto del codice e invece e un file che non ha mai viaggiato.
    """
    audio = work_file(work, meta.get("audio"), "audio.wav", "audio.*")
    if audio is None:
        raise SystemExit(
            f"in {work} non c'e l'audio da trascrivere.\n"
            f"I metadati ci sono, il media no: `export_skill.py` non impacchetta i "
            f"file pesanti, quindi una cartella arrivata da un'altra macchina va "
            f"ricostruita con `ingest.py` dal sorgente."
        )
    return audio


def analysis_source(meta: dict, work: Path | str | None = None) -> tuple[Path, float]:
    """Video da usare per l'analisi, e il fattore per riportare le coordinate
    al sorgente.

    Tutta la fase di analisi (cambi scena, volti, tracking) lavora su un proxy a
    480p invece che sul sorgente: la decodifica e il collo di bottiglia di ogni
    passaggio, e a 4K costa 15 minuti solo per i cambi scena. Il proxy si paga
    una volta e vale per tutti. Il sorgente resta intoccato: lo usa solo il
    render finale, dove la qualita conta davvero.

    Ritorna (percorso, scala) dove scala moltiplica le coordinate del proxy per
    ottenere quelle del sorgente.

    `work` va passata: senza, resta solo `meta["work_dir"]`, che e il campo che
    ha gia mentito una volta — descrive dove la cartella *era* quando e stata
    creata, non dove la si sta leggendo adesso.
    """
    work = Path(work) if work is not None else Path(meta.get("work_dir") or ".")

    proxy = work_file(work, meta.get("proxy"), "proxy.*")
    if proxy and meta.get("proxy_width"):
        return proxy, meta["width"] / meta["proxy_width"]

    video = work_file(work, meta.get("video"), "source.*")
    if video is None:
        raise SystemExit(
            f"in {work} non c'e nessun video su cui analizzare: manca sia il proxy "
            f"sia il sorgente.\nI metadati ci sono, i media no — e il caso di una "
            f"cartella arrivata da un'altra macchina: i file pesanti non viaggiano."
        )
    return video, 1.0


def slugify(text: str, max_len: int = 60) -> str:
    """Nome cartella/file sicuro su Windows a partire da un titolo arbitrario."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:max_len].strip("-") or "video"


def resolve_logo(work: Path, value: str | None) -> Path | None:
    """Il file del logo dichiarato da `decisions.json`, cercato dove ha senso.

    Prima la cartella della consegna: e li che il logo appartiene, ed e la copia
    che `export_skill.py` si porta dietro. Il percorso nudo per ultimo, altrimenti
    un file omonimo nella directory da cui si lancia lo script vincerebbe.

    Sta qui e non nel render perche la stessa domanda se la fa `validate.py`: se
    il nome e sbagliato deve dirlo il cancello, una volta, non trentaquattro
    render uno dopo l'altro.
    """
    if not value:
        return None
    for candidato in (Path(work) / value, ASSETS_DIR / value, Path(value)):
        if candidato.exists():
            return candidato.resolve()
    return None


def read_json(path: Path) -> dict:
    # utf-8-sig e non utf-8: `decisions.json` e `corrections.json` li scrive una
    # persona, e su Windows quasi ogni editor ci mette il BOM davanti. Con utf-8
    # secco il file muore con "Unexpected UTF-8 BOM" alla riga 1, che non dice a
    # nessuno di aver salvato col Blocco note. Senza BOM il decoder si comporta
    # identico.
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def emit(data) -> None:
    """Risultato strutturato su stdout, per il chiamante."""
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _processor_name() -> str:
    """Nome commerciale del processore, non la sigla di famiglia.

    `platform.processor()` su Windows restituisce "Intel64 Family 6 Model 170
    Stepping 4, GenuineIntel": e vero e non serve a niente, perche due macchine
    diverse possono darne uno identico. Il nome che si legge sulla scatola sta
    nel registro, ed e lo stesso posto da cui `find_tool` recupera il PATH.
    """
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as k:
                nome, _ = winreg.QueryValueEx(k, "ProcessorNameString")
                if nome:
                    # Nel registro il nome arriva spesso con spazi doppi.
                    return " ".join(str(nome).split())
        except (ImportError, OSError):
            pass
    else:
        # Su Linux `platform.processor()` e spesso vuoto: il nome sta in cpuinfo.
        try:
            for riga in Path("/proc/cpuinfo").read_text(errors="replace").splitlines():
                if riga.startswith("model name"):
                    return " ".join(riga.split(":", 1)[1].split())
        except OSError:
            pass

    import platform

    return platform.processor() or platform.machine() or "sconosciuto"


def _physical_cores() -> int | None:
    """Core **fisici**, chiesti a CIM. `None` quando non si riesce a saperlo.

    `os.cpu_count()` non risponde a questa domanda: restituisce i processori
    logici. Sulle due macchine del progetto la differenza e esattamente il
    difetto — un Ryzen 7 7735HS e 8 core / 16 thread, e chiamare "16 core" i suoi
    thread e falso; un Core Ultra 7 255H e 16 e 16, e li l'etichetta sarebbe vera
    per caso.

    **Se la query fallisce si resta a `None`.** Dedurre i core dai thread
    vorrebbe dire dividere per due, cioe assumere l'SMT su una macchina che
    potrebbe non averlo: e come digitare il numero a mano, solo con piu passaggi.
    Meglio un'etichetta che promette meno.
    """
    if os.name != "nt":
        return _physical_cores_linux()

    ps = shutil.which("powershell") or shutil.which("pwsh")
    if not ps:
        return None

    try:
        proc = subprocess.run(
            [
                ps,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "(Get-CimInstance Win32_Processor | "
                "Measure-Object -Property NumberOfCores -Sum).Sum",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if proc.returncode != 0:
        return None
    testo = proc.stdout.strip()
    return int(testo) if testo.isdigit() and int(testo) > 0 else None


def _physical_cores_linux() -> int | None:
    """Core fisici **fra le CPU che questo processo puo usare**, letti dalla
    topologia del kernel. `None` se la topologia non si legge.

    Non i core della macchina: in un container la CPU e del padrone di casa e il
    processo ne vede una parte (`sched_getaffinity`). Sul server `lavoro` sono 6
    CPU logiche su 12, e capita che siano 6 core diversi — ma potrebbero essere
    3 core con i loro fratelli SMT, e contarli dal numero di CPU sarebbe di nuovo
    assumere qualcosa sull'SMT.
    """
    try:
        cpu = os.sched_getaffinity(0)
    except (AttributeError, OSError):
        return None
    core: set[tuple[str, str]] = set()
    for n in cpu:
        topo = Path(f"/sys/devices/system/cpu/cpu{n}/topology")
        try:
            core.add(
                (
                    (topo / "physical_package_id").read_text().strip(),
                    (topo / "core_id").read_text().strip(),
                )
            )
        except OSError:
            return None
    return len(core) or None


_CPU_IDENTITY: dict | None = None


def cpu_identity() -> dict:
    """Identita del processore che sta misurando, rilevata e mai digitata.

    **Serve perche un tempo senza la macchina non e confrontabile.** La tabella
    di PROGRESS.md ha portato per settimane un "render per clip" che mescolava
    due processori diversi: 78.7 minuti per 34 clip su una macchina e 35.4 su
    un'altra sembravano un miglioramento della pipeline, ed erano due CPU.

    Tre campi e non due, perche core e thread rispondono a domande diverse: i
    **thread** dicono quanti render possono girare insieme, i **core fisici**
    dicono quanta macchina c'e davvero sotto. Confonderli e cio che rendeva
    l'etichetta precedente vera solo su questa macchina.

    In cache per l'intero processo: la query CIM costa circa un secondo e
    `record_metric` viene chiamata una volta per clip.
    """
    global _CPU_IDENTITY
    if _CPU_IDENTITY is None:
        _CPU_IDENTITY = {
            "cpu": _processor_name(),
            "cpu_cores": _physical_cores(),
            "cpu_threads": os.cpu_count() or None,
        }
    return dict(_CPU_IDENTITY)


# Quando i core fisici non si sanno: l'unico valore che una misura sostiene.
FALLBACK_THREADS = 8


def default_threads() -> int:
    """Quanti thread dare alla trascrizione: i core **fisici** della macchina.

    **Un numero digitato e vero sull'host su cui e stato scritto e falso sul
    successivo.** Il default era `8` scritto a mano, e su un 16 core fisici
    lasciava meta macchina ferma senza che niente lo segnalasse: il comando
    funziona lo stesso, solo piu piano.

    Il tetto sono i **fisici** e non i logici: oltre quel punto lo scaling si
    appiattisce, perche due thread SMT sullo stesso core si contendono le stesse
    unita di calcolo e ctranslate2 le satura gia con un thread per core.

    Quando la query CIM fallisce si resta a `FALLBACK_THREADS`: dedurre i core da
    `os.cpu_count()` vorrebbe dire dividere per due, cioe assumere l'SMT su una
    macchina che potrebbe non averlo. Passa da `cpu_identity()` per riusarne la
    cache — una sola query per processo.
    """
    return cpu_identity()["cpu_cores"] or FALLBACK_THREADS


def _leggi_alimentazione() -> str | None:
    """`rete`, `batteria` o `None` se Windows non lo dice (`GetSystemPowerStatus`)."""
    if os.name != "nt":
        return None
    try:
        import ctypes

        class Stato(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", ctypes.c_ubyte),
                ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte),
                ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", ctypes.c_ulong),
                ("BatteryFullLifeTime", ctypes.c_ulong),
            ]

        stato = Stato()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(stato)):
            return None
    except (AttributeError, OSError):
        return None
    return {0: "batteria", 1: "rete"}.get(stato.ACLineStatus)  # 255 = non lo so


def riassumi_alimentazione(stati: list[str | None]) -> str | None:
    """`rete`, `batteria`, `mista` (entrambe durante la misura) o `None` (mai saputo).

    **`None` non e `rete`.** Una macchina che non risponde non ha dimostrato di
    stare in carica, e un tempo senza stato noto non si legge come misurato
    sotto alimentazione.
    """
    visti = {s for s in stati if s}
    if not visti:
        return None
    return visti.pop() if len(visti) == 1 else "mista"


def stati_nella_finestra(campioni: list[tuple[float, str | None]], dal: float, al: float):
    """Gli stati in vigore fra `dal` e `al`, compreso quello gia in corso a `dal`."""
    prima = [s for t, s in campioni if t <= dal]
    dentro = [s for t, s in campioni if dal < t <= al]
    return prima[-1:] + dentro


_ALIMENTAZIONE: list[tuple[float, str | None]] = []
_ALIMENTAZIONE_ATTIVA = False
INTERVALLO_ALIMENTAZIONE = 5.0


def sorveglia_alimentazione(intervallo: float = INTERVALLO_ALIMENTAZIONE) -> None:
    """Campiona lo stato di alimentazione in sottofondo, per tutta la vita del processo.

    **Una condizione controllata una volta all'inizio e un'ipotesi per il resto
    della misura.** Il benchmark dei motori di trascrizione partiva solo con il
    caricabatterie collegato, e per 17 minuti l'alimentatore e stato staccato senza
    che nessun numero lo dicesse: un tempo a 0,42x contro una mediana di 0,20 e'
    sembrato un dato. Campionando, `record_metric` puo' dire cos'e' successo
    *durante* la fase, non solo alla fine. Un thread daemon: non trattiene l'uscita.
    """
    global _ALIMENTAZIONE_ATTIVA
    if _ALIMENTAZIONE_ATTIVA:
        return
    _ALIMENTAZIONE_ATTIVA = True
    _ALIMENTAZIONE.append((time.time(), _leggi_alimentazione()))

    def ciclo() -> None:
        while True:
            time.sleep(intervallo)
            _ALIMENTAZIONE.append((time.time(), _leggi_alimentazione()))

    threading.Thread(target=ciclo, daemon=True, name="alimentazione").start()


def alimentazione_durante(secondi: float, ora: float | None = None) -> str | None:
    """Lo stato di alimentazione nell'ultima misura di `secondi`.

    Con il campionamento attivo guarda tutta la finestra; senza, legge lo stato di
    adesso, che e' meglio di niente ma vede solo la fine.
    """
    ora = time.time() if ora is None else ora
    adesso = (ora, _leggi_alimentazione())
    campioni = [*_ALIMENTAZIONE, adesso]
    return riassumi_alimentazione(stati_nella_finestra(campioni, ora - secondi, ora))


def record_metric(work: Path | str, fase: str, secondi: float, **extra) -> None:
    """Appende una misura in `work/<slug>/metrics.json`.

    **Un numero che nessuno digita non puo diventare stantio.** Ogni cifra
    scritta a mano in PROGRESS.md e un candidato a mentire al prossimo video: e
    gia successo con un "~35 minuti" che era una stima mai misurata e che e
    sopravvissuta alla tabella che la smentiva.

    L'ultima esecuzione vince, e va bene cosi: la misura utile e quella fresca,
    non la media di quelle vecchie su materiale diverso.

    La macchina si registra **per fase e non per file**: una cartella di lavoro
    puo attraversare due computer — e il caso normale, visto che `export_skill.py`
    esiste per quello — e una fase ripescata da un'altra macchina deve tenersi la
    sua etichetta invece di ereditare quella di chi ha lanciato per ultimo.
    """
    work = Path(work)
    if not work.is_dir():
        return
    path = work / "metrics.json"
    try:
        dati = read_json(path) if path.exists() else {}
    except (json.JSONDecodeError, OSError):
        dati = {}  # un file rotto non deve fermare la pipeline

    dati.setdefault("fasi", {})[fase] = {
        "secondi": round(float(secondi), 1),
        "quando": datetime.now().isoformat(timespec="seconds"),
        **cpu_identity(),
        "alimentazione": alimentazione_durante(secondi),
        **extra,
    }
    try:
        write_json(path, dati)
    except OSError:
        pass  # misurare non e mai un buon motivo per fallire


def fmt_timecode(seconds: float) -> str:
    """Secondi -> h:mm:ss, per riferirsi a un punto del video sorgente."""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
