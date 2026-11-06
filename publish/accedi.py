#!/usr/bin/env python
"""
accedi.py — i login di un gruppo, in fila, ognuno sul profilo giusto.

    python accedi.py --gruppo business
    python accedi.py --gruppo business --solo tiktok,whop
    python accedi.py --gruppo business --elenco     # cosa farebbe, senza aprire niente

Cosa fa e cosa non fa
---------------------
Apre il browser sulla cartella di profilo giusta e ti lascia digitare. **Le
credenziali non passano da qui**: non le chiede, non le legge, non le salva. Da
questo script esce solo la sessione, che resta dentro il profilo e serve agli
agenti per pubblicare. E' la stessa cosa che facevano gia' i quattro comandi
`login`; qui sono in fila e col profilo del gruppo giusto invece che con quello
storico.

Perche' un file in piu'
-----------------------
Perche' i quattro accessi non si somigliano: YouTube vuole il Chrome installato
(Google rifiuta l'accesso da un browser di automazione), TikTok e Instagram
vogliono Chromium di Playwright, Whop vuole Scrapling e un secondo accesso
dentro il pannello del provider. Ricordarsi quattro comandi diversi con quattro
interpreti diversi e' esattamente il modo in cui uno finisce sul profilo
sbagliato.

L'ordine non e' casuale: prima le piattaforme dove si pubblica, Whop per ultimo.
Su Whop si collegano gli account social, quindi conviene arrivarci quando
esistono gia'.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import registro  # noqa: E402

# Tre interpreti, non uno, e nessuno dei tre e' quello della pipeline video.
# `ROOT/.venv` serve a whisper e opencv e **non ha Playwright**: puntarci
# l'agente Instagram lo faceva morire con «Playwright non e' installato», cioe'
# con l'aria di un'installazione incompleta invece che di un indirizzo
# sbagliato. L'agente Instagram sta in `publish/.venv`, come sa gia'
# `orchestrator.PYTHON_PUBLISH`.
from interpreti import PYTHON_PUBLISH as PY_PUBLISH  # noqa: E402
from interpreti import PYTHON_SCRAPING as PY_SCRAPING  # noqa: E402

# piattaforma -> (interprete, script, serve un'identita', comandi da eseguire)
#
# Whop non ha un'identita': non e' un posto dove si pubblica, e' dove si incassa.
# Il suo profilo si chiama col nome del gruppo e basta.
#
# **Su Whop i login sono due.** Entrare in whop.com non apre il pannello
# Content Rewards, che e' un'applicazione su un altro dominio con la sua
# autenticazione a email piu' codice. Con il solo `login` la cartella del
# profilo esiste, questo elenco dice «gia' fatto», e il difetto si manifesta
# molto piu' tardi: la campagna mostra un modulo di accesso al posto del
# contenuto, che da fuori somiglia a una pagina vuota.
PASSI = (
    ("youtube", PY_SCRAPING, "youtube_agent.py", True, ("login",)),
    ("tiktok", PY_SCRAPING, "publish_agent.py", True, ("login",)),
    ("instagram", PY_PUBLISH, "instagram_agent.py", True, ("login",)),
    ("whop", PY_SCRAPING, "whop_agent.py", False, ("login", "login-app")),
)

# La cartella del profilo prova che il browser si e' aperto, non che la sessione
# sia viva: per Whop non basta, perche' l'accesso all'app scade per conto suo.
SEMPRE = {"whop"}


def descrivi(gruppo: str) -> list[dict]:
    fuori = []
    for piattaforma, interprete, script, serve_identita, comandi in PASSI:
        voce = registro.identita(gruppo, piattaforma) if serve_identita else None
        if serve_identita:
            profilo = registro.profilo(gruppo, piattaforma, Path("?")) if voce else None
        else:
            profilo = HERE / f".whop-{gruppo}"
        fuori.append(
            {
                "piattaforma": piattaforma,
                "interprete": interprete,
                "script": script,
                "handle": (voce or {}).get("handle"),
                "profilo": profilo,
                "comandi": comandi,
                "pronto": (voce is not None) if serve_identita else True,
                "gia_fatto": (bool(profilo and profilo.is_dir()) and piattaforma not in SEMPRE),
            }
        )
    return fuori


def main() -> None:
    ap = argparse.ArgumentParser(description="I login di un gruppo, uno per piattaforma")
    ap.add_argument("--gruppo", required=True)
    ap.add_argument("--solo", default="", help="solo queste piattaforme, separate da virgola")
    ap.add_argument("--elenco", action="store_true", help="dice cosa farebbe e si ferma")
    ap.add_argument("--rifai", action="store_true", help="riapre anche i profili che esistono gia'")
    args = ap.parse_args()

    passi = descrivi(args.gruppo)
    if args.solo:
        volute = {s.strip().lower() for s in args.solo.split(",")}
        passi = [p for p in passi if p["piattaforma"] in volute]

    print(f"\nGruppo {args.gruppo}\n")
    for p in passi:
        if not p["pronto"]:
            stato = "MANCA nel registro"
        elif p["gia_fatto"] and not args.rifai:
            stato = "profilo gia' presente, salto"
        else:
            stato = "da fare"
        chi = f"@{p['handle'].lstrip('@')}" if p["handle"] else "-"
        print(
            f"  {p['piattaforma']:10s} {chi:22s} {Path(p['profilo']).name if p['profilo'] else '-':24s} {stato}"
        )
    print()

    if args.elenco:
        return

    mancanti = [p for p in passi if not p["pronto"]]
    if mancanti:
        sys.exit(
            f"Prima registra le identita' mancanti in dashboard/identita.json: "
            f"{', '.join(p['piattaforma'] for p in mancanti)}.\n"
            f"  Per ognuna servono id, handle, profilo_browser e account_started "
            f"(la data di apertura: la usa la rampa di ritmo.py)."
        )

    for p in passi:
        if p["gia_fatto"] and not args.rifai:
            continue
        if not p["interprete"].exists():
            print(f"  {p['piattaforma']}: manca l'interprete {p['interprete']} — salto")
            continue
        print(f"\n===== {p['piattaforma']} =====")
        if p["handle"]:
            print(f"Accedi con {p['handle']} e con nient'altro: questo profilo e' suo.")
        interrotto = False
        for comando in p["comandi"]:
            if len(p["comandi"]) > 1:
                print(f"\n--- {p['piattaforma']}: {comando} ---")
            esito = subprocess.run(
                [str(p["interprete"]), str(HERE / p["script"]), "--gruppo", args.gruppo, comando],
                cwd=str(HERE),
            )
            if esito.returncode != 0:
                print(
                    f"  {p['piattaforma']} ({comando}): uscita {esito.returncode} — controlla sopra"
                )
                if input("  continuo? [s/N] ").strip().lower() != "s":
                    interrotto = True
                    break
        if interrotto:
            return

    print("\nFatto. Controlla con:  python registro.py --gruppo " + args.gruppo)
    print("Poi la rampa: sette giorni di sola presenza prima del primo post (publish/ritmo.py).")


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
