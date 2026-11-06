"""Impacchetta la skill per portarla su un'altra macchina.

    python export_skill.py <destinazione.zip>
    python export_skill.py <destinazione.zip> --con-modelli

**Perche esiste.** `git` porta il codice, ma tre cose che valgono piu del codice
stanno in cartelle ignorate: le **decisioni** (quali momenti sono stati scelti,
con bordi, hook e motivazioni), le **correzioni** alle parole sentite male, e le
**trascrizioni**. I video si riscaricano e le clip si rirenderizzano; il giudizio
editoriale no, e ritrascrivere costa minuti di CPU per ogni video.

Cosa entra sempre:

- tutto il codice e i documenti (quello che git traccia gia)
- font e modello YuNet, che pesano insieme meno di mezzo mega
- da `work/`: metadati, trascrizioni, segnali, volti, decisioni, correzioni,
  sottotitoli e percorsi di crop — cioe tutto **tranne i media**
- da `output/`: i soli `clips.md`, che sono le didascalie scritte a mano
- l'**operazione** (`radar/`, `publish/`, `dashboard/`): il codice e, soprattutto,
  `radar/assegnazioni.json` — la nicchia di ogni campagna, decisa leggendo i
  brief. E il `decisions.json` delle campagne: nessuno script la rigenera, e
  senza, dall'altra parte il catalogo torna un elenco di titoli.

Cosa resta fuori, sempre: video sorgente, proxy, audio, clip renderizzate,
fotogrammi. Sono i tre quarti di gigabyte per video, e si rifanno tutti. E,
dell'operazione, quello che si riscarica o che descrive *questa* macchina: i
brief (13 minuti di rete), i log dei tentativi di pubblicazione — 277 MB di HTML
e screenshot in tre giorni — e **i profili browser**, che sono 2,5 GB e
contengono le sessioni gia autenticate degli account: uno zip che li porta
regala l'accesso a chiunque lo apra.

Cosa resta fuori a meno di chiederlo: i **modelli Whisper** in cache. Sono
qualche giga e `setup.ps1` li riscarica da solo, ma con `--con-modelli`
viaggiano nel pacchetto e la macchina nuova non tocca la rete.

Il `.venv` non entra mai, e non e una dimenticanza: dentro ha percorsi assoluti
scritti al momento della creazione, quindi copiato altrove non funziona. Lo
rifa `setup.ps1` in un paio di minuti.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

from common import OUTPUT_DIR, PROJECT_ROOT, SKILL_ROOT, WORK_DIR, emit, log, read_json

# I media: si rifanno tutti, e sono il 99% del peso.
MEDIA = {
    ".mp4",
    ".mkv",
    ".webm",
    ".wav",
    ".png",
    ".jpg",
    ".jpeg",
    ".m4a",
    ".opus",
    ".part",
    ".unknown_video",
    ".ts",
    ".mov",
}

# Cartelle che non hanno senso portarsi: ricreabili, o legate a questa macchina.
#
# `briefs` e `logs` non sono un'eccezione arbitraria: i primi si riscaricano in
# tredici minuti, i secondi sono la prova di cosa vedeva il browser durante un
# tentativo di pubblicazione *qui* — 277 MB in tre giorni, che sull'altra
# macchina non dicono niente a nessuno.
SALTA_DIR = {".venv", ".tools", "__pycache__", ".git", "briefs", "logs"}

# Le cartelle dell'operazione. Stanno fuori dalla skill ma dentro lo stesso
# progetto, e contengono la stessa specie di cosa che giustifica questo script:
# `radar/assegnazioni.json` e un giudizio scritto leggendo 265 brief, e nessuno
# script sa rifarlo. Il resto e codice, che pesa poco e serve subito.
OPERAZIONE = ("radar", "publish", "dashboard")


# Le copie del font che il render mette in `work/<slug>/fonts/` per aggirare i
# due punti nei percorsi Windows dentro i filtri ffmpeg. Sono duplicati e si
# rifanno da soli. **Solo quelle**: il font vero sta in `assets/fonts/` e deve
# viaggiare, perche scaricarlo ha una trappola sua (vedi CLAUDE.md, git-lfs).
def e_font_di_lavoro(p: Path) -> bool:
    try:
        return "fonts" in p.relative_to(WORK_DIR).parts
    except ValueError:
        return False


WHISPER_DIR = "whisper"


# Le cache di HuggingFace. `assets/models/whisper/` e dove le mette transcribe.py
# passando download_root, ma una cartella `models--<org>--<nome>` puo comparire
# anche altrove: faster-whisper la crea al volo quando qualcuno lo invoca senza
# quel download_root. Vanno riconosciute per **forma**, non per posizione: un
# doppione fuori posto pesa quanto l'originale e passa un filtro sul percorso
# senza farsi notare. Misurato: 3.1 GB di large-v3-turbo dentro un pacchetto che
# si dichiarava "senza modelli", e il peso in fondo non lo guarda nessuno.
def e_cache_modelli(p: Path) -> bool:
    return any(parte == WHISPER_DIR or parte.startswith("models--") for parte in p.parts)


def e_nascosta(p: Path, root: Path) -> bool:
    """Una cartella che comincia col punto, **sotto la radice che si sta leggendo**.

    Non e una regola di stile: in `publish/` ci sono `.browser-profile`,
    `.whop-profile`, `.tiktok-canale_lab`, `.youtube-chrome` — 2,5 GB di profili
    Chrome con dentro **le sessioni gia autenticate** dei nostri account. Uno zip
    che li porta e' un file da 1,2 GB che regala l'accesso a chiunque lo apra, e
    che nessuno guarda perche' il pacchetto "si e' creato". Stesso difetto dei
    3.1 GB di modelli Whisper, con in piu' le credenziali dentro.

    Il confronto e' sulle parti **relative alla radice**: `.claude` e' la radice
    della skill, non una cartella nascosta dentro di essa.
    """
    try:
        return any(parte.startswith(".") for parte in p.relative_to(root).parts[:-1])
    except ValueError:
        return False


def da_portare(root: Path, con_modelli: bool):
    """I file di una cartella che vale la pena impacchettare."""
    if not root.is_dir():
        return
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(parte in SALTA_DIR for parte in p.parts):
            continue
        if e_nascosta(p, root):
            continue
        if e_font_di_lavoro(p):
            continue
        if p.suffix.lower() in MEDIA:
            continue
        if not con_modelli and e_cache_modelli(p):
            continue
        yield p


def loghi_citati() -> list[Path]:
    """I PNG che un `decisions.json` dichiara come logo da imprimere.

    Sono l'eccezione alla regola "i media si rifanno": questo arriva da fuori —
    il committente della campagna — e nessuno script sa ricrearlo. Senza, il
    render della stessa consegna sull'altra macchina si ferma sulla prima clip.
    Pesano decine di KB, quindi l'eccezione non costa niente.
    """
    trovati = []
    for decisioni in sorted(WORK_DIR.glob("*/decisions*.json")):
        try:
            nome = read_json(decisioni).get("logo")
        except (ValueError, OSError):
            # Un file rotto non deve fermare l'esportazione di tutto il resto.
            continue
        if not nome:
            continue
        for candidato in (Path(nome), decisioni.parent / nome, SKILL_ROOT / "assets" / nome):
            if candidato.is_file():
                trovati.append(candidato.resolve())
                break
    return trovati


def raccogli(con_modelli: bool) -> list[Path]:
    file: list[Path] = []

    # Codice e documenti: si prendono dal disco, non da git, cosi il pacchetto
    # e coerente con quello che c'e adesso anche se qualcosa non e committato.
    for nome in ("CLAUDE.md", "PROGRESS.md", ".gitignore"):
        p = PROJECT_ROOT / nome
        if p.is_file():
            file.append(p)
    file += list(da_portare(SKILL_ROOT, con_modelli))
    impostazioni = PROJECT_ROOT / ".claude" / "settings.json"
    if impostazioni.is_file():
        file.append(impostazioni)

    for nome in OPERAZIONE:
        file += list(da_portare(PROJECT_ROOT / nome, con_modelli))
    pianifica = PROJECT_ROOT / "pianifica.ps1"
    if pianifica.is_file():
        file.append(pianifica)
    diario = PROJECT_ROOT / "docs" / "diario.md"
    if diario.is_file():
        file.append(diario)

    file += list(da_portare(WORK_DIR, con_modelli))
    file += loghi_citati()

    # Da output/ solo i clips.md: gli mp4 si rirenderizzano da decisions.json.
    if OUTPUT_DIR.is_dir():
        file += sorted(OUTPUT_DIR.glob("*/clips.md"))

    # Dedup mantenendo l'ordine.
    visti, unici = set(), []
    for p in file:
        if p not in visti:
            visti.add(p)
            unici.append(p)
    return unici


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Impacchetta la skill senza i media, per un'altra macchina"
    )
    ap.add_argument("destinazione", help="file .zip da creare")
    ap.add_argument(
        "--con-modelli",
        action="store_true",
        help="include anche i modelli Whisper in cache (qualche giga): "
        "la macchina nuova non scarica niente",
    )
    args = ap.parse_args()

    dest = Path(args.destinazione).resolve()
    if dest.exists():
        raise SystemExit(f"{dest} esiste gia: scegli un altro nome o cancellalo prima.")
    dest.parent.mkdir(parents=True, exist_ok=True)

    file = raccogli(args.con_modelli)
    if not file:
        raise SystemExit("non ho trovato niente da impacchettare: sei nella cartella giusta?")

    peso = sum(p.stat().st_size for p in file)
    log(
        f"{len(file)} file, {peso / 1048576:.1f} MB da comprimere"
        + ("  (modelli Whisper inclusi)" if args.con_modelli else "")
    )

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for i, p in enumerate(file, 1):
            z.write(p, p.relative_to(PROJECT_ROOT).as_posix())
            if i % 100 == 0:
                log(f"  ... {i}/{len(file)}")

    finale = dest.stat().st_size
    log(f"\nfatto: {dest}")
    log(f"  {finale / 1048576:.1f} MB compressi (da {peso / 1048576:.1f})")
    log("\nSulla macchina nuova:")
    log("  1. scompatta lo zip in una cartella, per esempio C:\\progetti\\progetto-x")
    log(
        "  2. powershell -ExecutionPolicy Bypass -File "
        ".claude\\skills\\viral-clips\\scripts\\setup.ps1"
    )
    if not args.con_modelli:
        log(
            "  Il setup riscarica i modelli. Sull'italiano il primo video "
            "scarica large-v3-turbo (~1.5 GB) da solo."
        )
    else:
        log("  I modelli sono gia dentro: il setup li trova e li salta.")

    emit(
        {
            "zip": str(dest),
            "file": len(file),
            "mb": round(finale / 1048576, 1),
            "modelli": args.con_modelli,
        }
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
