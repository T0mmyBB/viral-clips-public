"""Copia del progetto per le prove alla cieca dei modelli che usano gli strumenti.

    python tools/banco_modelli/banco.py decisioni|didascalie|didascalie_candid [--nome X]

Stampa il percorso del banco. Il banco e' una copia del working tree (file tracciati
e non tracciati non ignorati), senza `work/` e `output/` tranne la cartella della
prova, spogliata di cio' che la prova deve produrre, e senza le frasi che dicono la
risposta. `.venv` e' una junction a quello vero: serve a `windows.py` e `validate.py`.

**Perche' una copia e non il progetto.** Un agente con gli strumenti va a cercare la
risposta: sulle didascalie ha trovato e letto la consegna vera. E non bastano
`decisions.json` e `publish.json`: PROGRESS.md, CLAUDE.md e i commenti sopra
`MODELLO` raccontano dove le prove precedenti hanno visto sbagliare un modello, cioe'
proprio i fatti che la prova cerca. Ogni banco ricostruito a mano li ha riscoperti
uno alla volta.

**Perche' fuori dal progetto.** Claude legge i CLAUDE.md risalendo le cartelle: un
banco dentro `C:\\Projects\\viral-clips\\work` leggerebbe anche quello vero, con le
risposte dentro. Il banco sta nella cartella temporanea, e `destinazione()` rifiuta
qualunque percorso dentro il progetto.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
QUI = Path(__file__).resolve().parent
BASE = Path(tempfile.gettempdir()) / "banco_modelli"

CANDID = "copy-of-northwind-podcast-e106-longform-master-16x9-3840x2160-2608"
TRACEE = "tracee-ellis-ross-stop-dreaming-of-your-wedding-start-dreami"
# La consegna che il messaggio di prepara.py indica come modello di forma.
MODELLO_FORMA = "why-a16z-is-betting-7m-on-clipping-the-cap-table-ep-25-w-clo"

# Le prove: quale cartella, cosa le si lascia oltre a TENUTI, e se serve `output/`.
PROVE = {
    "decisioni": {"cartella": CANDID, "anche": (), "output": False},
    "didascalie": {
        "cartella": TRACEE,
        "anche": ("decisions.json", "contoso-logo.png"),
        "output": True,
    },
    "didascalie_candid": {"cartella": CANDID, "anche": ("decisions.json",), "output": True},
}

# Quello che una cartella di lavoro ha prima della scelta delle clip.
TENUTI = ("brief.txt", "campagna.json", "meta.json", "signals.json", "faces.json", "transcript.json")

# Le spie: frasi che stanno solo dove qualcuno ha scritto la risposta di una prova.
# Si cercano ovunque nella copia, senza distinguere maiuscole. **Una spia trovata
# ferma il banco prima che parta l'agente**: il 1 ottobre 2026 una regola nuova di
# SKILL.md citava come esempio proprio la correzione che la prova delle decisioni
# misura, e un banco ricostruito senza questo controllo l'avrebbe data all'agente.
SPIE = (
    # Northwind Podcast E106: come Whisper sente il nome del programma, e le clip in uso
    "candle club",
    "candy club",
    "climbing club",
    "ritrovate delle 18",
    "camera 18 su 18",
    "18 mp4",  # la riga della tabella che dice quante clip ha la consegna vera
    "Northwind Podcast (18)",
    # Tracee Ellis Ross: i fatti che le didascalie hanno travisato
    "five minutes a night",
    "calling her sister",
    "her sister",
    "two minutes",
    "on stage",
    "biggest moments",
    "stepfather",
)

ESCLUSI = ("work/", "output/", "tools/banco_modelli/", "tests/test_banco_modelli.py")
TESTO = {".md", ".py", ".txt", ".json", ".js", ".ps1", ".html", ".css", ".tsv"}


def destinazione(nome: str, base: Path = BASE) -> Path:
    """Il percorso del banco, che non puo' stare dentro il progetto."""
    dest = (base / nome).resolve()
    if dest == RADICE or RADICE in dest.parents:
        raise SystemExit(
            f"il banco non puo' stare dentro il progetto ({dest}): Claude risale le "
            f"cartelle e leggerebbe anche il CLAUDE.md vero, con le risposte dentro"
        )
    return dest


def ha_spia(riga: str, spie=SPIE) -> bool:
    bassa = riga.lower()
    return any(s.lower() in bassa for s in spie)


VOCE = re.compile(r"^(?:\d+\.|[-*]) ")


def _fine_voce(righe: list[str], i: int) -> int:
    """Dove finisce la voce d'elenco che comincia alla riga `i`: alla prima riga non
    rientrata che non sia vuota (la voce dopo, un titolo, un paragrafo)."""
    j = i + 1
    while j < len(righe) and (not righe[j].strip() or righe[j][:1] in (" ", "\t")):
        j += 1
    # le righe vuote in coda appartengono a cio' che segue
    while j > i + 1 and not righe[j - 1].strip():
        j -= 1
    return j


def togli_sezioni(testo: str, spie=SPIE) -> str:
    """Toglie le sezioni `###`, le voci d'elenco e le righe che contengono una spia.

    PROGRESS.md tiene una sezione `###` per giornata, e una voce d'elenco per cosa
    da fare: una prova raccontata li' dentro va tolta intera, perche' le righe
    accanto alla spia dicono la stessa cosa con altre parole. Il 1 ottobre 2026 la
    voce «La consegna Northwind Podcast» perdeva solo la riga con la spia e continuava a
    dire quante clip ha la consegna vera e quali tag servono. Sopra il livello `###`
    le sezioni sono interi capitoli di CLAUDE.md, e l'agente ne ha bisogno: li' si
    toglie la voce o la riga, non il capitolo.
    """
    righe = testo.split("\n")
    fuori: list[str] = []
    i = 0
    while i < len(righe):
        m = re.match(r"^(#{3,6}) ", righe[i])
        if m:
            livello = len(m.group(1))
            j = i + 1
            while j < len(righe):
                n = re.match(r"^(#{1,6}) ", righe[j])
                if n and len(n.group(1)) <= livello:
                    break
                j += 1
            sezione = righe[i:j]
            if not any(ha_spia(r, spie) for r in sezione):
                fuori.extend(sezione)
            i = j
            continue
        if VOCE.match(righe[i]):
            j = _fine_voce(righe, i)
            if not any(ha_spia(r, spie) for r in righe[i:j]):
                fuori.extend(righe[i:j])
            i = j
            continue
        if not ha_spia(righe[i], spie):
            fuori.append(righe[i])
        i += 1
    return "\n".join(fuori)


def togli_commenti_modello(testo: str) -> str:
    """Toglie il blocco di commenti sopra `MODELLO =`, che riporta le misure dei modelli."""
    righe = testo.split("\n")
    m = next((i for i, r in enumerate(righe) if r.startswith("MODELLO =")), None)
    if m is None:
        return testo
    a = m
    while a > 0 and righe[a - 1].startswith("#"):
        a -= 1
    return "\n".join(righe[:a] + righe[m:])


def trova_spie(radice: Path, spie=SPIE) -> list[str]:
    """Ogni riga di codice o documenti della copia che contiene ancora una spia.

    `work/` e `output/` restano fuori: sono il materiale della prova, e le spie ci
    stanno di diritto — il transcript dice «candy club» perche' Whisper ha sentito
    quello. Una fuga e' la risposta scritta *fuori* dal materiale.
    """
    trovate = []
    for f in sorted(radice.rglob("*")):
        rel = f.relative_to(radice)
        if (
            f.is_dir()
            or f.suffix.lower() not in TESTO
            or rel.parts[0] in ("work", "output", ".venv", ".git")
        ):
            continue
        try:
            righe = f.read_text(encoding="utf-8").split("\n")
        except (UnicodeDecodeError, OSError):
            continue
        for n, r in enumerate(righe, 1):
            if ha_spia(r, spie):
                trovate.append(f"{rel}:{n}: {r.strip()[:100]}")
    return trovate


def pulisci(dest: Path, spie=SPIE) -> None:
    """Spoglia la copia delle risposte: sezioni e righe nei .md, commenti sopra MODELLO.

    In byte e non in testo: `read_text` porta i CRLF a LF e `write_text` li rimette
    secondo il sistema, quindi un file LF usciva tutto CRLF e ogni confronto col
    progetto vero diceva «cambiato tutto».
    """
    for modo, pulitore in (("*.md", lambda t: togli_sezioni(t, spie)), ("*.py", togli_commenti_modello)):
        for f in dest.rglob(modo):
            if f.relative_to(dest).parts[0] in ("work", "output", ".venv", ".git"):
                continue
            testo = f.read_bytes().decode("utf-8")
            nuovo = pulitore(testo)
            if nuovo != testo:
                f.write_bytes(nuovo.encode("utf-8"))


def copia_lavoro(dest: Path, nome: str, anche=()) -> Path:
    src, dst = RADICE / "work" / nome, dest / "work" / nome
    dst.mkdir(parents=True)
    for f in (*TENUTI, *anche):
        if (src / f).exists():
            shutil.copy2(src / f, dst / f)
    meta = json.loads((dst / "meta.json").read_text(encoding="utf-8"))
    meta["work_dir"] = str(dst)
    (dst / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    faces = json.loads((dst / "faces.json").read_text(encoding="utf-8"))
    faces.pop("tracks", None)  # tracce dense: calcolate sulle clip gia' scelte
    (dst / "faces.json").write_text(json.dumps(faces), encoding="utf-8")
    return dst


def costruisci(prova: str, dest: Path) -> Path:
    if prova not in PROVE:
        raise SystemExit(f"prova sconosciuta: {prova} (valide: {', '.join(PROVE)})")
    conf = PROVE[prova]
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    files = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"],
        cwd=RADICE, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    for f in files:
        if f.startswith(ESCLUSI):
            continue
        src = RADICE / f
        if src.is_file():
            (dest / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / f)
    # git init: CLAUDE.md e la skill si trovano risalendo, ma gli agenti usano git status
    subprocess.run(["git", "init", "-q"], cwd=dest, check=True)
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(dest / ".venv"), str(RADICE / ".venv")],
        check=True, capture_output=True,
    )

    nome = conf["cartella"]
    copia_lavoro(dest, nome, conf["anche"])
    if conf["output"]:
        out = dest / "output" / nome
        out.mkdir(parents=True)
        for mp4 in (RADICE / "output" / nome).glob("*.mp4"):
            # Il video vero, non un segnaposto: dal commit 0007460 la skill dice che una
            # clip vuota non e' caricabile, e il 27/10 Sonnet si e' fermato senza consegna
            shutil.copy2(mp4, out / mp4.name)
        forma = dest / "output" / MODELLO_FORMA
        forma.mkdir(parents=True)
        shutil.copy2(RADICE / "output" / MODELLO_FORMA / "publish.json", forma / "publish.json")

    pulisci(dest)
    # agy (motori.py) non legge CLAUDE.md ma AGENTS.md: la stessa copia, gia' pulita,
    # se no Gemini lavorerebbe senza le regole del progetto e la prova misurerebbe quello
    shutil.copy2(dest / "CLAUDE.md", dest / "AGENTS.md")
    if rimaste := trova_spie(dest):
        raise SystemExit(
            "il banco contiene ancora la risposta: togli la frase dal file, o insegna "
            "a pulisci() a toglierla, e rifai.\n  " + "\n  ".join(rimaste)
        )

    versione = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=RADICE, capture_output=True, text=True
    ).stdout.strip()
    sporco = bool(
        subprocess.run(["git", "status", "--porcelain"], cwd=RADICE, capture_output=True, text=True).stdout.strip()
    )
    (dest / "_banco.json").write_text(
        json.dumps(
            {
                "prova": prova,
                "cartella": nome,
                "creato": datetime.now().isoformat(timespec="seconds"),
                "commit": versione + ("+modifiche" if sporco else ""),
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    return dest


def elimina() -> None:
    """Toglie tutti i banchi. Con `shutil.rmtree`, che sulla junction `.venv` toglie il
    collegamento e non entra: un `rm -rf` di Git Bash potrebbe seguirla e svuotare il
    `.venv` vero."""
    for d in sorted(BASE.iterdir()) if BASE.is_dir() else []:
        if (d / "_banco.json").exists():
            shutil.rmtree(d)
            print("tolto", d)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("prova", nargs="?", choices=sorted(PROVE))
    ap.add_argument("--nome", help="nome della cartella del banco (default: la prova)")
    ap.add_argument("--elimina", action="store_true", help="toglie tutti i banchi gia' costruiti")
    args = ap.parse_args()
    if args.elimina:
        elimina()
        return
    if not args.prova:
        ap.error("serve la prova, o --elimina")
    print(costruisci(args.prova, destinazione(args.nome or args.prova)))


if __name__ == "__main__":
    main()
