"""Da una cartella di clip renderizzate a una consegna programmata.

    python prepara.py --cartella output/<slug>

Fa due cose e nessuna delle due pubblica:

1. **La consegna** — `clips.md` e le didascalie in `publish.json`, scritte leggendo il
   brief della campagna. E' lavoro editoriale, quindi lo fa Claude: le regole di
   didascalia stanno nel documento del committente (chi taggare, quali hashtag, che CTA
   ruotare) e nessuna regex le trasforma in un testo che suoni nativo.
2. **Il calendario** — `calendario.py` mette un `quando` accanto a ogni clip, rispettando
   la rampa dell'account.

**Perche' esiste separato da `automatico.py`.** Lo chiamano in due: la catena automatica
dopo il render, e il pulsante del pannello su una cartella gia' renderizzata. Due copie
della stessa procedura divergerebbero al primo ritocco — e divergerebbero in silenzio,
perche' nessuno confronta un `publish.json` scritto stamattina con uno scritto la
settimana scorsa.

**Non pubblica, e non arma niente che pubblichi.** Alla fine di questo script esistono
dei file: un elenco di didascalie e un elenco di date. Chi li esegue e' `esegui.py`,
lanciato dall'attivita' pianificata, che e' un'altra decisione presa un'altra volta.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import calendario
import whop_radice

validate = calendario.validate  # il cancello della consegna, in un posto solo

QUI = Path(__file__).resolve().parent
RADICE = QUI.parent

# Gli stessi di `automatico.py`, e per la stessa ragione: `Bash` serve perche'
# Claude deve poter leggere il brief e i punteggi con i suoi strumenti. Non si
# usa `--dangerously-skip-permissions`.
ATTREZZI = ("Read", "Write", "Edit", "Glob", "Grep", "Bash")
ATTESA_CLAUDE = 1800

# I campi di primo livello che la consegna scrive: il prompt li elenca da qui, e
# dopo la scrittura si tolgono quelli in piu'. Il `publish.json` indicato come
# modello e' la consegna chiusa di un'altra campagna, e il 30 settembre 2026
# Sonnet 5.5 ne ha ricopiato `platform`, `submit_within_minutes` e `batch_size`
# anche con «prendine la forma, non i valori» scritto nel prompt: una frase
# abbassa la probabilita' della copia, il codice la toglie.
CAMPI_CONSEGNA = (
    "campaign",
    "provider",
    "gruppo",
    "whop_campaign_uuid",
    "whop_app",
    "source_episode",
    "note_regole",
    "handles",
    "batch_times",
    "min_gap_seconds",
    "max_per_day",
)

# Scritti qui e non ereditati da `settings.json`, che sono le preferenze della
# chat. **Sonnet 5 no, e lo dice una misura** (17 settembre 2026, Contoso
# riscritta da capo alla cieca su una copia del progetto): le regole meccaniche le
# rispettano tutti e due — tag per piattaforma, hashtag obbligatorio, rotazione
# della CTA — ma Sonnet a `high` ha travisato dei fatti in 3 didascalie su 14
# («two minutes» dove l'ospite dice cinque, «on stage» per una puntata registrata),
# Opus in 0 su 7. Il brief respinge le didascalie che travisano l'ospite e nessun
# controllo lo guarda, mentre il risparmio era $2 a consegna a listino.
#
# **Opus 5.5 a `high`** (22 settembre 2026, stessa prova su Contoso): 0 fatti
# travisati su 7 a `medium`, `high` e `xhigh`, contro 1 su 7 di Opus 5 `xhigh`
# («her sister» dove lei dice «mia sorella o una delle mie migliori amiche»), e
# regole meccaniche rispettate da tutti. `high` costa $2,23 in 4 minuti, `xhigh`
# $3,65 in 7,5. `medium` ha passato la stessa prova, ma sette didascalie sono un
# campione piccolo per un errore che nessun controllo vede: il margine costa
# $0,60 a consegna.
#
# **Sonnet 5.5 a `high`** (29 settembre 2026, Contoso e Northwind Podcast alla cieca, 25
# didascalie controllate frase per frase sulla trascrizione): 0 fatti travisati,
# tutti gli elementi obbligatori dei due brief in ogni didascalia, contro Opus
# 5.5 `high` rifatto lo stesso giorno che ne aggiunge tre non detti («right
# before one of the biggest moments», «instead of snapping at each other»). $1,30
# e $1,43 invece di $2,40 e $2,51, in tempi pari. `medium` interpreta («forcing
# "I'm totally fine" is dishonest»), `xhigh` costa quanto Opus. L'id e' scritto
# per intero perche' l'alias lo sposta la versione del CLI (vedi CLAUDE.md).
MODELLO = "claude-sonnet-5-5"
EFFORT = "high"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def leggi(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def trova_claude() -> str:
    """Il percorso vero dell'eseguibile.

    Su Windows e' `claude.CMD` e `CreateProcess` non applica `PATHEXT`: passare
    `"claude"` nudo da' "Impossibile trovare il file specificato", che sembra
    Claude Code non installato. Stessa trappola gia' pagata con ffmpeg.
    """
    import shutil

    trovato = shutil.which("claude")
    if not trovato:
        raise SystemExit(
            "'claude' non trovato: serve Claude Code per scrivere le "
            "didascalie. Installalo, o scrivi publish.json a mano."
        )
    return trovato


def serve_consegna(cartella: Path) -> bool:
    """Se manca la parte editoriale. Un `publish.json` senza `items` non e' una consegna."""
    cfg = leggi(cartella / "publish.json")
    return not (cfg and cfg.get("items") and (cartella / "clips.md").exists())


def togli_campi_estranei(nuovo: dict, prima: dict | None) -> tuple[dict, list[str]]:
    """Toglie i campi di primo livello non chiesti che nel file di prima non c'erano.

    Quello che c'era gia' resta: un `--rifai` su una cartella programmata non deve
    cancellare cio' che hanno scritto il calendario o una persona.
    """
    tenuti = {*CAMPI_CONSEGNA, "items", *(prima or {})}
    tolti = sorted(k for k in nuovo if k not in tenuti)
    return {k: v for k, v in nuovo.items() if k in tenuti}, tolti


def mp4_rotti(cartella: Path, prima: dict | None, sonda=validate.video_info) -> list[str]:
    """Gli mp4 che non si possono caricare, esclusi quelli che la consegna ha sospeso.

    Un controllo di presenza non e' un controllo di validita': con solo il `glob`,
    didascalie e `clips.md` venivano scritti per file da zero byte e il guasto
    usciva a fine lavoro, da `validate.py --consegna`. Lo stesso `difetto_clip`
    del calendario, cosi' il criterio e' uno.
    """
    sospesi = {it.get("file") for it in (prima or {}).get("items") or [] if it.get("sospesa")}
    return [
        d
        for f in sorted(cartella.glob("*.mp4"))
        if f.name not in sospesi and (d := validate.difetto_clip(f, sonda))
    ]


def radice_whop(lavoro: Path | None, cartelle: list[Path]) -> str | None:
    """La radice del pannello Whop del provider di questa campagna, o `None`.

    **Non la scrive l'agente**: il prompt indicava come modello il `publish.json` di
    un'altra campagna, il cui `whop_app` e' la radice di *un altro* provider, e su un
    provider nuovo nessun file registra lo slug — la forma
    `/<slug>/<exp>/app/` valeva solo per il primo (osservazioni 111 e 144). L'exp
    viene dal `join` della scheda in `campagna.json`. Se una consegna precedente
    dello stesso provider ha gia' una radice, e' quella: e' l'unica che sappiamo aver
    caricato. Altrimenti `radice_app()` la ricava dal `join`, senza slug e non
    verificata — meglio di una radice presa dal provider sbagliato.
    """
    join = (leggi(lavoro / "campagna.json", {}) if lavoro else {}).get("join") or ""
    m = re.search(r"(exp_[A-Za-z0-9]+)", join)
    if not m:
        return None
    for c in cartelle:
        nota = (leggi(c / "publish.json", {}) or {}).get("whop_app") or ""
        if m.group(1) in nota and nota.startswith("https://whop.com/"):
            return whop_radice.radice_app(nota)
    return whop_radice.radice_app(join)


def messaggio_consegna(cartella: Path, lavoro: Path | None) -> str:
    """La richiesta con cui si scrivono `clips.md` e le didascalie.

    Funzione a se perche ha un secondo lettore, `tools/banco_modelli/agente.py`:
    il messaggio si scrive in un posto solo.
    """
    riferimenti = f"La cartella di lavoro con brief e decisioni e' {lavoro}. " if lavoro else ""
    return (
        f"Prepara la consegna della cartella {cartella}. {riferimenti}"
        f"Segui il passo 8 di .claude/skills/viral-clips/SKILL.md: scrivi `clips.md` con "
        f"per ogni clip il file, i timestamp, i sei punteggi, il testo in sovrimpressione, "
        f"la motivazione, e in fondo cosa non hai scelto e perche'. "
        f"Poi scrivi `publish.json` nella stessa cartella, con la stessa struttura di "
        f"output/why-a16z-is-betting-7m-on-clipping-the-cap-table-ep-25-w-clo/publish.json: "
        f"{', '.join(CAMPI_CONSEGNA)}, e `items` con "
        f"id, file, title, caption per ogni mp4 presente. "
        f"**Le didascalie si scrivono leggendo il brief della campagna** (`brief.txt` nella "
        f"cartella di lavoro): rispetta chi va taggato su ogni piattaforma, gli hashtag "
        f"obbligatori, e ruota la CTA se il brief lo chiede. Ogni didascalia deve essere "
        f"diversa dalle altre e specifica della sua clip. "
        f"Non aggiungere nessun campo `quando`: il calendario lo scrive un altro programma. "
        f"Non pubblicare niente e non scrivere altro in risposta."
    )


def scrivi_consegna(cartella: Path, lavoro: Path | None, sonda=validate.video_info) -> None:
    """Didascalie e `clips.md`, scritte leggendo il brief."""
    prima = leggi(cartella / "publish.json")
    if rotti := mp4_rotti(cartella, prima, sonda):
        raise SystemExit(
            "non scrivo la consegna: clip non caricabili\n  " + "\n  ".join(rotti)
        )
    messaggio = messaggio_consegna(cartella, lavoro)

    esito = subprocess.run(
        [
            trova_claude(),
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
        cwd=str(RADICE),
        timeout=ATTESA_CLAUDE,
    )
    if esito.returncode != 0:
        raise SystemExit(f"la scrittura della consegna e' uscita con {esito.returncode}")

    # Non ci si fida della risposta: si guarda il disco. Una risposta che *sembra*
    # fatta e' il modo in cui una consegna vuota arriva in fondo dicendo "pronto".
    if serve_consegna(cartella):
        raise SystemExit(
            f"dopo la scrittura mancano ancora clips.md o gli `items` di publish.json in "
            f"{cartella}. Non proseguo: senza didascalie non c'e' niente da programmare."
        )

    p = cartella / "publish.json"
    pulito, tolti = togli_campi_estranei(leggi(p, {}), prima)
    if tolti:
        p.write_text(json.dumps(pulito, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"tolti da publish.json i campi non chiesti: {', '.join(tolti)}")

    altre = [c for c in sorted((RADICE / "output").glob("*/")) if c != cartella]
    radice = radice_whop(lavoro, altre)
    if radice and pulito.get("whop_app") != radice:
        pulito["whop_app"] = radice
        p.write_text(json.dumps(pulito, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"whop_app scritto dal codice: {radice}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Didascalie e calendario di una cartella gia' renderizzata"
    )
    ap.add_argument("--cartella", required=True, help="una cartella di output/")
    ap.add_argument(
        "--lavoro",
        default=None,
        help="la cartella work/ corrispondente, se si conosce: contiene il brief",
    )
    ap.add_argument(
        "--rifai", action="store_true", help="riscrive le didascalie anche se ci sono gia'"
    )
    args = ap.parse_args()

    cartella = Path(args.cartella).resolve()
    if not cartella.is_dir():
        raise SystemExit(f"{cartella} non esiste")
    if not list(cartella.glob("*.mp4")):
        raise SystemExit(f"{cartella} non contiene nessun mp4: non c'e' niente da consegnare")

    lavoro = Path(args.lavoro).resolve() if args.lavoro else None
    if lavoro is None:
        candidata = RADICE / "work" / cartella.name
        lavoro = candidata if candidata.is_dir() else None

    if args.rifai or serve_consegna(cartella):
        log("scrivo didascalie e clips.md leggendo il brief...")
        scrivi_consegna(cartella, lavoro)
    else:
        log("didascalie e clips.md ci sono gia', salto")

    piano = calendario.calcola(cartella)
    calendario.scrivi(cartella, piano)
    log(f"\ncalendario: {piano['piazzate']} clip su {', '.join(piano['piattaforme'])} insieme")
    for v in piano["piano"]:
        log(f"  {v['quando'].replace('T', ' ')}  clip {v['id']}")
    log("\nNiente e' stato pubblicato. Lo fara' `esegui.py` quando le date arrivano.")

    print(
        json.dumps(
            {
                "cartella": str(cartella),
                **{k: piano[k] for k in ("gruppo", "clip", "piazzate", "piano")},
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
