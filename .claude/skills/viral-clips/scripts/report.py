"""I numeri di PROGRESS.md, generati invece che digitati.

    python report.py                 # la tabella su stdout, per guardarla
    python report.py --write         # la scrive dentro PROGRESS.md, fra i marcatori

**Perche esiste.** Ogni cifra scritta a mano in un documento e un candidato a
mentire al prossimo video. E gia successo: un "~35 minuti" che era una stima mai
misurata e sopravvissuto per settimane alla tabella che lo smentiva, perche una
tabella nuova non cancella un numero vecchio scritto altrove. Un numero che
nessuno digita non puo diventare stantio.

Da dove vengono i numeri:

- **i tempi** da `work/<slug>/metrics.json`, che `run.py` appende mentre lavora.
  Registra solo le fasi che hanno fatto davvero qualcosa: un tempo preso dalla
  cache descrive quanto costa *non* trascrivere, ed e il modo piu diretto per
  scrivere una misura falsa senza accorgersene.
- **le dimensioni** misurate qui, sul momento, dai file che stanno nella
  cartella di lavoro. Non serve registrarle: sono li e si leggono.

Il blocco in PROGRESS.md sta fra due marcatori e viene sostituito per intero:

    <!-- metrics:start -->
    <!-- metrics:end -->

Quello che sta **fuori** dai marcatori non viene toccato — le righe che spiegano
cosa significano i numeri restano scritte a mano, ed e giusto: quelle non
invecchiano con il video.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from common import PROJECT_ROOT, WORK_DIR, emit, fmt_timecode, log, read_json

PROGRESS_MD = PROJECT_ROOT / "PROGRESS.md"
INIZIO = "<!-- metrics:start -->"
FINE = "<!-- metrics:end -->"

# L'ordine in cui le fasi hanno senso lette una sotto l'altra: e quello in cui
# accadono, non quello alfabetico in cui capitano nel dizionario.
ORDINE = [
    "ingest (audio + proxy)",
    "trascrizione",
    "segnali",
    "volti (prescansione)",
    "render per clip",
]


def breve(cpu: str) -> str:
    """Il nome del processore senza il rumore legale, che in tabella occupa e
    non distingue niente."""
    for scarto in ("(R)", "(TM)", "(r)", "(tm)", " CPU"):
        cpu = cpu.replace(scarto, "")
    return " ".join(cpu.split())


def etichetta_cpu(misura: dict) -> str | None:
    """Come si chiama la macchina che ha preso questa misura.

    **L'etichetta non promette mai piu di quello che ha.** I core fisici si
    scrivono solo se qualcuno li ha davvero contati; quando manca solo quel
    campo si dicono i thread e si chiamano thread, che e vero e basta.

    I record scritti prima di questa correzione hanno una chiave `core` che
    conteneva `os.cpu_count()`, cioe i **logici**: qui viene riletta come
    thread. La rimappatura sta in lettura di proposito — riscrivere i file
    significherebbe cambiare misure gia prese, e quelle non si toccano.
    """
    nome = misura.get("cpu")
    if not nome:
        return None

    core = misura.get("cpu_cores")
    thread = misura.get("cpu_threads") or misura.get("core") or misura.get("cores")

    if core:
        if thread and thread != core:
            return f"{breve(nome)} ({core} core / {thread} thread)"
        return f"{breve(nome)} ({core} core)"
    if thread:
        return f"{breve(nome)} ({thread} thread)"
    return breve(nome)


def lavori() -> list[Path]:
    """Le cartelle di lavoro che hanno qualcosa da raccontare."""
    if not WORK_DIR.is_dir():
        return []
    return sorted(
        d for d in WORK_DIR.iterdir() if (d / "metrics.json").exists() or (d / "meta.json").exists()
    )


def descrivi(work: Path) -> dict | None:
    """Cosa sappiamo di un lavoro: com'e fatto il video, e quanto e costato."""
    if not (work / "meta.json").exists():
        return None
    meta = read_json(work / "meta.json")

    fasi = {}
    if (work / "metrics.json").exists():
        try:
            fasi = read_json(work / "metrics.json").get("fasi", {})
        except Exception:  # noqa: BLE001
            fasi = {}

    def peso(nome: str) -> float | None:
        p = work / nome
        return round(p.stat().st_size / 1024 / 1024, 1) if p.exists() else None

    # Il conteggio dei token e approssimato a quattro caratteri l'uno: serve a
    # dire "due ordini di grandezza", non a fatturare.
    def token(nome: str) -> int | None:
        p = work / nome
        return p.stat().st_size // 4 if p.exists() else None

    return {
        "slug": meta.get("slug", work.name),
        "durata": meta.get("duration") or 0,
        "formato": f"{meta.get('width')}x{meta.get('height')}",
        "codec": meta.get("video_codec"),
        "fasi": {k: v.get("secondi") for k, v in fasi.items()},
        "durate_clip": {k: v.get("durata_clip") for k, v in fasi.items() if v.get("durata_clip")},
        # Un tempo preso (anche solo in parte) a batteria non e un tempo di questa
        # macchina in carica: si dice accanto al numero. L'assenza del campo — i
        # record di prima — non si legge come «in carica», e non si segna.
        "a_batteria": {
            k for k, v in fasi.items() if v.get("alimentazione") in ("batteria", "mista")
        },
        # Le macchine che hanno misurato questo lavoro. Di norma una sola, ma una
        # cartella puo attraversare due computer e allora vanno dette entrambe:
        # e proprio il caso in cui confrontare i tempi senza guardarle inganna.
        "macchine": sorted({e for e in (etichetta_cpu(v) for v in fasi.values()) if e}),
        "transcript_mb": peso("transcript.json"),
        "transcript_token": token("transcript.json"),
    }


def tabella(righe: list[dict]) -> str:
    """La tabella markdown dei tempi, un video per colonna."""
    righe = [r for r in righe if r and r["fasi"]]
    if not righe:
        return (
            "_Nessuna misura registrata. I tempi si raccolgono da soli lanciando `run.py prepare`._"
        )

    out = [
        "| fase | " + " | ".join(f"{r['slug'][:22]}" for r in righe) + " |",
        "|---|" + "---|" * len(righe),
    ]

    fasi = [f for f in ORDINE if any(f in r["fasi"] for r in righe)]
    fasi += sorted({f for r in righe for f in r["fasi"]} - set(ORDINE))

    for fase in fasi:
        celle = []
        for r in righe:
            v = r["fasi"].get(fase)
            if v is None:
                celle.append("—")
                continue
            testo = f"{v:.0f} s" if v < 90 else f"{v / 60:.1f} min"
            # Dove la durata di cio che si e prodotto e nota, si affianca il
            # rapporto: "34 s" non dice niente finche non si sa se la clip
            # durava venti secondi o sessanta, ed e la cifra da confrontare fra
            # macchine diverse.
            riferimento = r["durate_clip"].get(fase)
            if riferimento:
                testo += f" · {v / riferimento:.1f}x"
            if fase in r["a_batteria"]:
                testo += " · a batteria"
            celle.append(testo)
        out.append(f"| {fase} | " + " | ".join(celle) + " |")

    intestazione = [
        "| | " + " | ".join(f"{r['slug'][:22]}" for r in righe) + " |",
        "|---|" + "---|" * len(righe),
        "| durata | " + " | ".join(fmt_timecode(r["durata"]) for r in righe) + " |",
        "| formato | "
        + " | ".join(f"{r['formato']} {r['codec'] or ''}".strip() for r in righe)
        + " |",
        "| macchina | "
        + " | ".join("—" if not r["macchine"] else "<br>".join(r["macchine"]) for r in righe)
        + " |",
        "| `transcript.json` | "
        + " | ".join(
            "—"
            if r["transcript_mb"] is None
            else f"{r['transcript_mb']} MB · ~{r['transcript_token']:,} token".replace(",", ".")
            for r in righe
        )
        + " |",
        "",
    ]
    return "\n".join(intestazione + out)


def scrivi(blocco: str) -> bool:
    """Sostituisce il blocco fra i marcatori. Ritorna False se non ci sono."""
    testo = PROGRESS_MD.read_text(encoding="utf-8")
    if INIZIO not in testo or FINE not in testo:
        return False
    prima = testo.split(INIZIO)[0]
    dopo = testo.split(FINE, 1)[1]
    nuovo = f"{prima}{INIZIO}\n\n{blocco}\n\n{FINE}{dopo}"
    if nuovo != testo:
        PROGRESS_MD.write_text(nuovo, encoding="utf-8")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="Rigenera i numeri misurati di PROGRESS.md")
    ap.add_argument(
        "--write",
        action="store_true",
        help="scrive il blocco dentro PROGRESS.md invece di stamparlo",
    )
    args = ap.parse_args()

    righe = [d for d in (descrivi(w) for w in lavori()) if d]
    blocco = tabella(righe)

    if not args.write:
        print(blocco)
        log(f"\n{len(righe)} cartelle di lavoro esaminate")
        return

    if not scrivi(blocco):
        raise SystemExit(
            f"in {PROGRESS_MD.name} mancano i marcatori {INIZIO} e {FINE}: "
            f"senza quelli non so dove mettere la tabella."
        )
    log(f"{PROGRESS_MD.name} aggiornato con {len(righe)} lavori")
    emit({"lavori": len(righe), "scritto": True})


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
