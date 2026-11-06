"""Verifica che i documenti descrivano il codice che esiste davvero.

    python check_docs.py

**Perche esiste.** I tre documenti del progetto divergono in silenzio: si modifica uno
script e non si rilegge l'insieme, oppure si sposta una sezione e nessuno ricontrolla i
riferimenti. Non e disattenzione occasionale, e il comportamento normale quando tre file
descrivono lo stesso sistema — e l'unica cura che funziona e automatica.

Casi reali che questo script avrebbe intercettato:

- `windows.py` scritto, testato e funzionante, con la casella ancora vuota in PROGRESS.md
- `ranking.py` cancellato, ma ancora citato due volte in CLAUDE.md
- `SKILL.md` che prescriveva `windows.py` mesi prima che esistesse
- `validate.py` che validava una rubrica sostituita da tempo
- una sezione numerata 7 due volte
- tre conteggi di token in SKILL.md rimasti indietro rispetto alla misura vera

Esce con codice 1 se trova problemi, cosi puo stare in un hook o in CI.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPTS_DIR.parent
PROJECT_ROOT = SKILL_ROOT.parents[2]

SKILL_MD = SKILL_ROOT / "SKILL.md"
PROGRESS_MD = PROJECT_ROOT / "PROGRESS.md"
CLAUDE_MD = PROJECT_ROOT / "CLAUDE.md"

# Script che non compaiono nella tabella di PROGRESS.md di proposito: sono utilita di
# servizio, non fasi della pipeline.
NOT_PIPELINE = {"common.py", "check_docs.py"}


def scripts_on_disk() -> set[str]:
    return {p.name for p in SCRIPTS_DIR.glob("*.py") if not p.name.startswith("_")} | {
        p.name for p in SCRIPTS_DIR.glob("*.ps1")
    }


def check_scripts_documented(progress: str) -> list[str]:
    """Ogni script della pipeline compare nella tabella di PROGRESS.md."""
    problems = []
    for name in sorted(scripts_on_disk() - NOT_PIPELINE):
        if name not in progress:
            problems.append(
                f"{name} esiste ma non e mai nominato in PROGRESS.md: "
                f"e stato aggiunto senza aggiornare la tabella?"
            )
    return problems


def check_no_ghost_scripts(progress: str) -> list[str]:
    """Nessuno script elencato come *fatto* e sparito dal disco.

    Si guarda solo la tabella dei componenti in PROGRESS.md, non ogni menzione nei
    documenti: `run.py` e `feedback.py` sono nominati di continuo perche sono da
    scrivere, e CLAUDE.md cita `hook.py` come esempio di cosa **non** creare.
    Segnalare quelli sarebbe rumore, e un controllore rumoroso viene ignorato.
    """
    problems = []
    on_disk = scripts_on_disk()

    # Le righe di tabella "| `nome.py` | descrizione | verificato su |".
    for match in re.finditer(r"^\|\s*`([a-z_]+\.(?:py|ps1))`\s*\|", progress, re.M):
        name = match.group(1)
        if name not in on_disk:
            problems.append(
                f"PROGRESS.md elenca `{name}` tra i componenti fatti, "
                f"ma il file non esiste: cancellato o rinominato?"
            )
    return problems


# Se una voce "da fare" contiene una di queste, parla di *modificare* uno script
# esistente, non di crearlo: e legittimo che il file ci sia gia.
MODIFY_HINTS = (
    "--",  # aggiungere un'opzione a uno script che c'e
    "aggiung",
    "corregg",
    "colleg",
    "restring",
    "rinomin",
    "sostitu",
    "sposta",
    "misura",
    "verific",
    "allinea",
    "toglie",
    "estend",
)


def check_todo_not_done(progress: str) -> list[str]:
    """Nessuna voce 'Da fare' chiede di *creare* uno script che esiste gia.

    E il caso `windows.py`: scritto, testato e funzionante, casella ancora vuota.
    Le voci che chiedono di *modificare* uno script esistente sono legittime e non
    vanno segnalate, altrimenti meta della lista diventa un falso allarme.
    """
    problems = []
    on_disk = scripts_on_disk()
    in_todo = False

    for line in progress.splitlines():
        if line.startswith("## Da fare"):
            in_todo = True
            continue
        if in_todo and line.startswith("## "):
            break
        if not in_todo or not re.match(r"^\s*\d+\.\s*\[ \]", line):
            continue

        low = line.lower()
        if any(h in low for h in MODIFY_HINTS):
            continue

        for name in on_disk:
            if name in line and name not in NOT_PIPELINE:
                problems.append(
                    f"PROGRESS.md chiede di creare `{name}`, che pero esiste gia: "
                    f"la casella va spuntata o la voce riscritta.\n"
                    f"      {line.strip()[:90]}"
                )
    return problems


def check_heading_numbers(skill: str) -> list[str]:
    """Le sezioni numerate di SKILL.md non saltano e non si ripetono."""
    numbers = [int(n) for n in re.findall(r"^### (\d+)\.", skill, re.M)]
    problems = []
    seen = set()
    for n in numbers:
        if n in seen:
            problems.append(f"SKILL.md ha due sezioni numerate {n}")
        seen.add(n)
    expected = list(range(1, len(numbers) + 1))
    if sorted(numbers) != expected and not problems:
        problems.append(f"SKILL.md: numerazione delle sezioni non consecutiva ({numbers})")
    return problems


def check_list_numbers(progress: str) -> list[str]:
    """La lista 'Da fare, in ordine' non ripete numeri: si chiama 'in ordine'."""
    section = re.search(r"^## Da fare.*?(?=^## )", progress, re.M | re.S)
    if not section:
        return []
    numbers = [int(n) for n in re.findall(r"^(\d+)\.", section.group(0), re.M)]
    problems = []
    seen = set()
    for n in numbers:
        if n in seen:
            problems.append(f"PROGRESS.md: la lista 'Da fare' ripete il numero {n}")
        seen.add(n)
    return problems


# Una cifra con separatore delle migliaia e quasi sempre una misura: conteggi di token,
# dimensioni, durate. Tre cifre dopo il separatore sono il filtro: prendono `269.000` e
# lasciano stare `12.8s`, `1920x1080`, `608x1080` e la versione `2026.07.23`, che sono
# legittime ovunque.
THOUSANDS = re.compile(r"\b\d{1,3}[.,]\d{3}\b")

# Cifre che sono *aritmetica*, non misure: non dipendono da nessuna esecuzione, quindi
# non possono diventare stantie. Un'ora a 3 fps fa 10.800 fotogrammi oggi e tra sei mesi.
# Qui si aggiunge solo cio che si puo riderivare a mente.
DERIVATIONS = {"10.800"}


def check_skill_has_no_measures(skill: str) -> list[str]:
    """SKILL.md non contiene misure, nemmeno giuste.

    La sua intestazione dice "come si esegue: niente giustificazioni, niente stato", e
    una misura e stato. Caso reale: il passo 4 ha portato per settimane 255.000 token,
    41 volte e ~13.000 token quando le misure vere erano 269.000, 39 volte e ~15.200 —
    e nessun controllo poteva accorgersene, perche guardavano tutti il codice.

    Il punto non e correggere la cifra: e che una cifra scritta qui **ricomincera** a
    divergere al primo video diverso. La versione che non invecchia e qualitativa:
    "quasi due ordini di grandezza piu grande della vista a segmenti".
    """
    problems = []
    for match in THOUSANDS.finditer(skill):
        line = skill[: match.start()].count("\n") + 1
        problems.append(
            f"SKILL.md:{line} contiene la misura {match.group(0)}: qui non ci vanno "
            f"numeri, invecchiano. Il valore sta in PROGRESS.md, qui va il principio."
        )
    return problems


def check_claude_measures(claude: str) -> list[str]:
    """Avviso: una misura in CLAUDE.md e legittima solo se giustifica una decisione.

    CLAUDE.md dice di se stesso che le misure che reggono una decisione permanente
    restano — senza i quattro tempi dell'accelerazione hardware la trappola sull'hwaccel
    e un'opinione, e qualcuno la riprovera. Ma una misura che *descrive lo stato* qui e
    solo una copia destinata a divergere da PROGRESS.md.

    La differenza non e decidibile leggendo il testo, quindi e un avviso e non un errore.
    """
    problems = []
    for match in THOUSANDS.finditer(claude):
        if match.group(0) in DERIVATIONS:
            continue
        line = claude[: match.start()].count("\n") + 1
        problems.append(
            f"CLAUDE.md:{line} contiene la misura {match.group(0)}: giustifica una "
            f"decisione permanente, o e stato che duplica PROGRESS.md?"
        )
    return problems


def check_commands_exist(skill: str) -> list[str]:
    """Ogni `python <script>` citato in SKILL.md punta a uno script esistente,
    con le opzioni che usa davvero."""
    problems = []
    on_disk = scripts_on_disk()

    for match in re.finditer(r"python (\w+\.py)([^\n`]*)", skill):
        name, rest = match.group(1), match.group(2)
        if name not in on_disk:
            # Marcato come non ancora disponibile? Allora e onesto.
            context = skill[max(0, match.start() - 300) : match.start()]
            if "non ancora disponibile" in context or "da implementare" in context:
                continue
            problems.append(
                f"SKILL.md usa `python {name}` ma lo script non esiste, "
                f"e non e marcato come non ancora disponibile"
            )
            continue

        source = (SCRIPTS_DIR / name).read_text(encoding="utf-8")
        for opt in re.findall(r"(--[a-z][a-z-]+)", rest):
            if opt in ("--help",):
                continue
            if opt not in source:
                context = skill[max(0, match.start() - 300) : match.start()]
                if "non ancora disponibile" in context or "da implementare" in context:
                    continue
                problems.append(f"SKILL.md usa `{name} {opt}`, ma {name} non accetta quell'opzione")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description="Verifica la coerenza dei documenti")
    ap.add_argument("--quiet", action="store_true", help="stampa solo i problemi")
    args = ap.parse_args()

    skill = SKILL_MD.read_text(encoding="utf-8")
    progress = PROGRESS_MD.read_text(encoding="utf-8")
    claude = CLAUDE_MD.read_text(encoding="utf-8")

    # Errori: decidibili con certezza guardando il disco. Bloccano.
    errors = [
        ("script non documentati", check_scripts_documented(progress)),
        ("script fantasma", check_no_ghost_scripts(progress)),
        ("numerazione sezioni", check_heading_numbers(skill)),
        ("numerazione lista", check_list_numbers(progress)),
        ("comandi inesistenti", check_commands_exist(skill)),
        ("misure in SKILL.md", check_skill_has_no_measures(skill)),
    ]

    # Avvisi: "questa voce da fare e gia fatta?" non e decidibile leggendo il testo,
    # perche modificare uno script esistente e una voce legittima. Vanno guardati da
    # una persona, non fanno fallire il controllo. Un controllore che fallisce sempre
    # smette di significare qualcosa.
    warnings = [
        ("da guardare: voci che nominano script esistenti", check_todo_not_done(progress)),
        ("da guardare: misure in CLAUDE.md", check_claude_measures(claude)),
    ]

    n_err = 0
    for label, problems in errors:
        if problems:
            print(f"\n{label.upper()}")
            for p in problems:
                print(f"  - {p}")
            n_err += len(problems)
        elif not args.quiet:
            print(f"  ok  {label}")

    for label, problems in warnings:
        if problems and not args.quiet:
            print(f"\n{label.upper()}")
            for p in problems:
                print(f"  ? {p}")

    if n_err:
        print(f"\n{n_err} errori: i documenti non descrivono il codice che esiste.")
        sys.exit(1)

    if not args.quiet:
        print("\nDocumenti e codice allineati.")


if __name__ == "__main__":
    main()
