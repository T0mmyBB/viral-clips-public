"""Dal clic sul pannello alle clip renderizzate, senza fermarsi in mezzo.

    python automatico.py --campagna <uuid> --sorgente "<url>"

**Cosa cambia rispetto a `run.py`.** `run.py` sono due comandi apposta: in mezzo c'e un
giudizio, e nessun processo sa restare bloccato ad aspettarlo. Questo file non nega quel
principio, lo **impacchetta**: il giudizio continua a farlo Claude, solo che invece di
aspettare che qualcuno apra una chat lo si invoca (`claude -p`) e si aspetta che finisca.
Le due decisioni restano esattamente dove erano — quali vincoli ha la campagna, quali
momenti reggono — e nessuna delle due e passata a uno script.

**Il prezzo va detto.** Nessuno guarda le clip fra la scelta e il render: se il giudizio
sbaglia, sbaglia su tutta la consegna e il primo a saperlo sara chi apre gli mp4. E la
condizione che l'utente ha scelto sapendola.

**Quindi questo file non si fida di quello che Claude gli risponde: verifica.** Ogni fase
che passa da un modello ha subito dopo un controllo meccanico, e se non passa la fase
fallisce invece di andare avanti — `regole` deve risultare compilato, `decisions.json`
deve superare `validate.py`. Una risposta che *sembra* fatta e il modo in cui una consegna
sbagliata arriva in fondo dicendo "pronto".

Le istruzioni editoriali **non stanno qui**: stanno in SKILL.md, dove stavano gia. I
messaggi qui sotto dicono quale passo eseguire e su quale cartella, e per il resto
rimandano li — due copie delle stesse istruzioni divergerebbero al primo ritocco, e
divergerebbero in silenzio.

Ogni fase ha la sua cache: rilanciare dopo un'interruzione riprende da dove era rimasto.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import campagna
from common import PROJECT_ROOT, emit, log, read_json, tool

SCRIPTS = Path(__file__).resolve().parent

# Quanto si aspetta ogni fase. Generosi di proposito: su un'ora di video la
# trascrizione da sola sta sopra i dieci minuti e un render da 38 clip sopra i
# venti. Un tetto stretto qui non protegge da niente, ammazza un giro sano.
ATTESA = {"prepare": 3 * 3600, "regole": 900, "decisioni": 1800, "render": 6 * 3600}

# Modello ed effort si scrivono qui e non si ereditano da `settings.json`: quelle
# sono le preferenze della chat, e abbassarle per risparmiare in chat non deve
# abbassare in silenzio la scelta delle clip. Opus perche e il giudizio per cui
# esiste il progetto e `validate.py` ne controlla solo la meccanica. Non `max`:
# le decisioni hanno 30 minuti, e oltre `xhigh` il guadagno cala.
#
# **Opus 5.5 a `high`, non a `xhigh`** (22 settembre 2026, Northwind Podcast E106 alla
# cieca, su una copia del progetto senza le decisioni in uso): `high` 12 clip in 4
# minuti per $2,01, `xhigh` 13 in 9 minuti per $3,53, Opus 5 `xhigh` 11 in 7
# minuti per $3,25. Tutti validi al primo colpo e tutti gli hook fedeli alla
# trascrizione. Solo i due 5.5 hanno scritto `corrections.json` per il nome del
# programma che Whisper sente «candle club». Su un 16 minuti `xhigh` fa 32 turni,
# quindi su un'ora rischia di superare i 30 minuti di `ATTESA["decisioni"]` per
# guadagnare una clip.
#
# **Opus, non Sonnet 5.5** (29 settembre 2026, stessa prova): Sonnet `high` in
# due giri trova 10 e 12 clip contro le 12 di Opus, piu' corte (329 e 389 secondi
# contro 427), a meta' costo ($1,05 contro $2,05) ma **non piu' in fretta** (3-4
# minuti contro 4). `medium` ne trova 9 e non scrive `corrections.json`, quindi
# «Candy Club» resterebbe impresso nei sottotitoli; `xhigh` ne trova 14 ma costa
# $2,75 in 10 minuti. Un dollaro a sorgente non paga una clip in meno su tre
# piattaforme. L'id e' scritto per intero perche' l'alias `opus` lo sposta la
# versione del CLI, non una decisione (vedi CLAUDE.md).
MODELLO = "claude-opus-5-5"
EFFORT = "high"

# Gli strumenti che Claude puo usare senza che nessuno risponda a un permesso.
# `Bash` serve davvero: senza, non puo consultare i timestamp per parola
# (`windows.py --words`), e i bordi tornerebbero a cadere in mezzo alle parole —
# il difetto che una volta e costato 38 clip su 38. Non si usa
# `--dangerously-skip-permissions`: `acceptEdits` piu questa lista bastano, ed e
# verificato.
ATTREZZI = ("Read", "Write", "Edit", "Glob", "Grep", "Bash")


def _python() -> str:
    return sys.executable


def esegui(nome: str, cmd: list[str], attesa: int, cwd: Path = PROJECT_ROOT) -> None:
    """Un passo, con il suo tempo e il suo codice di uscita. Fallisce rumorosamente."""
    log(f"\n===== {nome} =====")
    partito = time.time()
    try:
        esito = subprocess.run(cmd, cwd=str(cwd), timeout=attesa)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"{nome}: superati {attesa}s senza finire. Mi fermo qui.")
    durata = time.time() - partito
    if esito.returncode != 0:
        raise SystemExit(f"{nome}: uscito con {esito.returncode} dopo {durata:.0f}s.")
    log(f"===== {nome}: {durata:.0f}s =====")


def chiedi_a_claude(messaggio: str, attesa: int) -> None:
    """Una domanda a Claude in modalita non interattiva, dalla radice del progetto.

    `cwd` e la radice e non la cartella di lavoro: da li Claude vede CLAUDE.md e
    la skill, che sono meta delle istruzioni. Passarle nel messaggio sarebbe la
    seconda copia che questo file esiste per non fare.
    """
    esegui(
        "claude",
        [
            tool("claude"),
            "-p",
            messaggio,
            "--model",
            MODELLO,
            "--effort",
            EFFORT,
            "--permission-mode",
            "acceptEdits",
            "--allowed-tools",
            *ATTREZZI,
        ],
        attesa,
    )


def fase_regole(work: Path) -> None:
    """Il brief diventa il blocco `regole` di `campagna.json`. Poi si controlla."""
    dati = campagna.load(work) or {}
    if (dati.get("regole") or {}).get("letto_il"):
        log("regole gia scritte, salto")
        return
    if not dati.get("brief"):
        log("nessun brief da leggere: le regole restano vuote")
        return

    chiedi_a_claude(
        f"Leggi il brief in {work / dati['brief']} e compila il blocco `regole` di "
        f"{work / campagna.SCHEDA_FILE}, seguendo il passo 3 di "
        f".claude/skills/viral-clips/SKILL.md. Metti `letto_il` alla data di oggi. "
        f"Se un vincolo non ha un campo che lo rappresenti, scrivilo in chiaro fra i "
        f"`vincoli_render` o i `vincoli_pubblicazione` invece di lasciarlo fuori. "
        f"Non modificare nessun altro file e non scrivere altro in risposta.",
        ATTESA["regole"],
    )

    dopo = (campagna.load(work) or {}).get("regole") or {}
    if not dopo.get("letto_il"):
        raise SystemExit(
            f"le regole non sono state scritte: `letto_il` e ancora vuoto in "
            f"{work / campagna.SCHEDA_FILE}. Non proseguo: renderizzare adesso "
            f"vorrebbe dire consegnare senza i vincoli della campagna."
        )
    log(
        f"regole scritte: {len(dopo.get('vincoli_render') or [])} vincoli sul taglio, "
        f"{len(dopo.get('vincoli_pubblicazione') or [])} sulla pubblicazione"
    )


def valida(work: Path) -> tuple[bool, str]:
    """`validate.py` come cancello, con il suo referto in chiaro."""
    esito = subprocess.run(
        [_python(), str(SCRIPTS / "validate.py"), str(work)],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return esito.returncode == 0, (esito.stderr or "").strip()


def messaggio_decisioni(work: Path) -> str:
    """La richiesta con cui si fanno scegliere le clip.

    Funzione a se perche ha un secondo lettore, `tools/banco_modelli/agente.py`:
    il messaggio si scrive in un posto solo.
    """
    return (
        f"Lavora sulla cartella {work}. Segui i passi 4 e 5 di "
        f".claude/skills/viral-clips/SKILL.md: leggi la vista di windows.py, "
        f"scegli i momenti che reggono da soli e scrivi decisions.json. "
        f"Rispetta i vincoli del blocco `regole` di {campagna.SCHEDA_FILE}, che "
        f"valgono solo per questa campagna. Se `regole.logo_richiesto` e vero ma "
        f"non trovi nessun file immagine gia scaricato in questa cartella (ne in "
        f"`regole.logo`), **non inventare un nome di file**: lascia il campo "
        f"'logo' assente da decisions.json. Un nome inventato fa fallire il "
        f"cancello con l'errore sbagliato, e nessuno gira qui in mezzo per "
        f"correggerlo. Non renderizzare niente e non scrivere altro in risposta."
    )


def messaggio_correzione(base: str, referto: str) -> str:
    """La seconda e ultima richiesta, quando `validate.py` respinge le decisioni."""
    return (
        f"{base}\n\nIl file esiste gia ma validate.py lo respinge. Correggi "
        f"**solo** quello che segnala, senza rifare le scelte:\n\n{referto[:4000]}"
    )


def fase_decisioni(work: Path) -> None:
    """Le clip si scelgono, e non si va avanti finche il cancello non le accetta.

    **La ripetizione e una sola, e non e un dettaglio di robustezza.** Un errore
    di `validate.py` e quasi sempre un bordo da spostare di mezzo secondo, che si
    corregge rileggendo; se non basta la seconda volta, insistere vorrebbe dire
    far girare un modello contro un cancello finche uno dei due cede, e il
    cancello e l'unica cosa che qui protegge la consegna.
    """
    if (work / "decisions.json").exists():
        ok, _ = valida(work)
        if ok:
            log("decisions.json c'e gia ed e valido, salto")
            return
        log("decisions.json c'e ma non passa il cancello: lo rifaccio")

    base = messaggio_decisioni(work)
    chiedi_a_claude(base, ATTESA["decisioni"])

    ok, referto = valida(work)
    if not ok:
        log("il cancello ha respinto le decisioni: una correzione e basta")
        chiedi_a_claude(messaggio_correzione(base, referto), ATTESA["decisioni"])
        ok, referto = valida(work)
    if not ok:
        raise SystemExit(
            f"decisions.json non passa validate.py nemmeno alla seconda:\n"
            f"{referto}\n\nMi fermo prima del render."
        )


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Tutta la catena: prepara, legge il brief, sceglie, valida, renderizza"
    )
    ap.add_argument("--campagna", required=True, help="uuid della campagna nel radar")
    ap.add_argument("--sorgente", required=True, help="link del video da clippare")
    ap.add_argument("--work-root", default=None)
    ap.add_argument(
        "--fino-a",
        choices=("prepare", "regole", "decisioni", "render"),
        default="render",
        help="si ferma dopo questa fase (default: fa tutto)",
    )
    args = ap.parse_args()

    fasi = ("prepare", "regole", "decisioni", "render")
    ultima = fasi.index(args.fino_a)
    partito = time.time()

    cmd = [
        _python(),
        str(SCRIPTS / "run.py"),
        "prepare",
        args.sorgente,
        "--campagna",
        args.campagna,
    ]
    if args.work_root:
        cmd += ["--work-root", args.work_root]
    esegui("prepara", cmd, ATTESA["prepare"])

    # La cartella la si conosce solo adesso: da un link il nome viene dal titolo
    # del video. La si ritrova come la piu recente che porta questa campagna,
    # invece di rileggere lo stdout di run.py — che e JSON, ma passa attraverso
    # un altro processo e una pipe, e qui basta guardare il disco.
    radice = Path(args.work_root) if args.work_root else PROJECT_ROOT / "work"
    candidate = [
        d for d in radice.glob("*/campagna.json") if read_json(d).get("id") == args.campagna
    ]
    if not candidate:
        raise SystemExit(
            f"nessuna cartella di lavoro agganciata a {args.campagna} "
            f"sotto {radice}: prepare non e arrivato in fondo?"
        )
    work = max(candidate, key=lambda p: p.stat().st_mtime).parent
    log(f"\ncartella di lavoro: {work}")

    if ultima >= 1:
        fase_regole(work)
    if ultima >= 2:
        fase_decisioni(work)
    if ultima >= 3:
        esegui(
            "render", [_python(), str(SCRIPTS / "run.py"), "render", str(work)], ATTESA["render"]
        )

    decisioni = read_json(work / "decisions.json") if (work / "decisions.json").exists() else {}
    emit(
        {
            "work_dir": str(work),
            "slug": work.name,
            "campagna": args.campagna,
            "clip": len(decisioni.get("clips") or []),
            "fino_a": args.fino_a,
            "secondi": round(time.time() - partito, 1),
        }
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
