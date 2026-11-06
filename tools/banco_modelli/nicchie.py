"""Prova alla cieca delle nicchie: stesse campagne, modello ed effort diversi.

    python tools/banco_modelli/nicchie.py <modello> <effort> [lotti] [--etichetta X] [--motore agy]

`lotti` e' un numero (i primi N) o un intervallo `3-4`; il default e' 2, cioe' 100
campagne. Non scrive niente nel radar: costruisce i prompt con `classifica`, chiama
claude con `--output-format json` e salva risposte e consumi in
`%TEMP%/banco_modelli/nicchie/<modello>_<effort><etichetta>/`.

**Alla cieca vuol dire senza `GIA' ASSEGNATA`**: il digest di produzione mostra la
nicchia gia' decisa, e con quella in vista la prova misura la capacita' di
ricopiare. Qui il digest si chiede con `assegnata=None`, e un'asserzione lo
controlla sul prompt finito. Il resto e' quello di `classifica.chiedi`: prompt da
stdin, cartella temporanea, `SNELLO`.

**Con `--motore agy` la cartella e' vuota e nuova.** agy non ha un `--tools ""`:
legge i file della cartella di lavoro, e la temporanea di sistema e' piena di
banchi con le risposte. I risultati vanno in `<modello>_<effort>_agy`, per non
mescolare Opus via agy con Opus via claude.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE / "radar"))
import classifica  # noqa: E402
import motori  # noqa: E402

BASE = Path(tempfile.gettempdir()) / "banco_modelli" / "nicchie"


def intervallo(arg: str) -> tuple[int, int]:
    """`2` -> lotti 1-2, `3-4` -> lotti 3-4, come indici da zero e fine esclusa."""
    if "-" in arg:
        a, b = arg.split("-")
        return int(a) - 1, int(b)
    return 0, int(arg)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("modello")
    ap.add_argument("effort")
    ap.add_argument("lotti", nargs="?", default="2")
    ap.add_argument("--etichetta", default="")
    ap.add_argument("--motore", choices=motori.MOTORI, default="claude")
    args = ap.parse_args()

    primo, ultimo = intervallo(args.lotti)
    suffisso = "_agy" if args.motore == "agy" else ""
    out = BASE / f"{args.modello}_{args.effort}{suffisso}{args.etichetta}"
    out.mkdir(parents=True, exist_ok=True)

    prima = hashlib.md5(classifica.ASSEGNAZIONI.read_bytes()).hexdigest()
    schede = [s for s in classifica.carica(classifica.DETTAGLI, []) if not s.get("errore")]
    lavoro = classifica.da_decidere(schede, {}, True)
    nicchie = classifica.vocabolario()

    for i in range(primo, ultimo):
        lotto = lavoro[i * classifica.LOTTO:(i + 1) * classifica.LOTTO]
        testo = "\n\n".join(classifica.tassonomia.scheda_digest(c, b, None) for c, _, b in lotto)
        prompt = classifica.ISTRUZIONI.format(
            forme=", ".join(classifica.FORME), nicchie=nicchie, campagne=testo
        )
        assert "GIA' ASSEGNATA" not in prompt
        t0 = time.time()
        if args.motore == "agy":
            with tempfile.TemporaryDirectory(prefix="nicchie_agy_") as vuota:
                d = motori.agy(prompt, args.modello, args.effort, vuota, classifica.ATTESA)
            if "errore" in d:
                print(f"lotto {i + 1}: errore di agy: {d['errore'][-300:]}", flush=True)
                continue
        else:
            cmd = [
                classifica.claude(), "-p", "--model", args.modello, "--effort", args.effort,
                "--output-format", "json", *classifica.SNELLO,
            ]
            esito = subprocess.run(
                cmd, cwd=tempfile.gettempdir(), input=prompt, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=classifica.ATTESA,
            )
            try:
                d = json.loads(esito.stdout)
            except json.JSONDecodeError:
                print(
                    f"lotto {i + 1}: uscita non JSON ({esito.returncode}): "
                    f"{esito.stdout[-300:]} {esito.stderr[-300:]}",
                    flush=True,
                )
                continue
        secondi = time.time() - t0
        voci, guai = classifica.leggi_tsv(d.get("result") or "", {c["id"][:8]: c for c, _, _ in lotto})
        uso = d.get("usage", {})
        rec = {
            "lotto": i + 1,
            "secondi": round(secondi, 1),
            "costo": d.get("total_cost_usd"),
            "modelli": list(d.get("modelUsage", {})),
            "contesto": uso.get("input_tokens", 0)
            + uso.get("cache_creation_input_tokens", 0)
            + uso.get("cache_read_input_tokens", 0),
            "uscita": uso.get("output_tokens"),
            "ragionamento": (uso.get("output_tokens_details") or {}).get("thinking_tokens"),
            "voci": voci,
            "problemi": guai,
        }
        (out / f"lotto{i + 1}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        print(
            f"{args.modello} {args.effort} lotto {i + 1}: {len(voci)} voci, {len(guai)} problemi, "
            f"{rec['secondi']}s, ${rec['costo'] or 0:.3f}, ragionamento {rec['ragionamento']}",
            flush=True,
        )

    assert hashlib.md5(classifica.ASSEGNAZIONI.read_bytes()).hexdigest() == prima, "assegnazioni toccate!"
    print(out)


if __name__ == "__main__":
    main()
