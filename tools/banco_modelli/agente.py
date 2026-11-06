"""Una prova di agente su un banco, con lo stesso messaggio della produzione.

    python tools/banco_modelli/agente.py <banco> <modello> <effort> [--motore agy]

La prova (decisioni o didascalie) la dice `_banco.json`, scritto da `banco.py`.
Con `--motore agy` il modello e' lo slug di `agy models` senza l'effort (vedi
`motori.py`).
Consumi, tempi ed esito finiscono in `<banco>/_esito.json`.

**Il messaggio non e' una copia.** Lo costruiscono `automatico.messaggio_decisioni`
e `prepara.messaggio_consegna`, le stesse funzioni che usa la pipeline: la copia a
mano che stava qui si era gia' staccata dalla produzione, e una prova con un
messaggio diverso misura un'altra cosa.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
for cartella in (RADICE / ".claude" / "skills" / "viral-clips" / "scripts", RADICE / "publish"):
    if str(cartella) not in sys.path:
        sys.path.insert(0, str(cartella))

import automatico  # noqa: E402
import motori  # noqa: E402
import prepara  # noqa: E402
from common import tool  # noqa: E402

ATTREZZI = ("Read", "Write", "Edit", "Glob", "Grep", "Bash")


def messaggio(prova: str, work: Path, cartella: Path | None) -> str:
    """La richiesta della produzione, con i percorsi del banco."""
    if prova == "decisioni":
        return automatico.messaggio_decisioni(work)
    return prepara.messaggio_consegna(cartella, work)


def chiedi(banco: Path, testo: str, modello: str, effort: str, consumi: list, motore: str = "claude") -> None:
    t0 = time.time()
    if motore == "agy":
        d = motori.agy(testo, modello, effort, str(banco), 3600)
    else:
        cmd = [
            tool("claude"), "-p", testo, "--model", modello, "--effort", effort,
            "--permission-mode", "acceptEdits", "--allowed-tools", *ATTREZZI,
            "--output-format", "json",
        ]
        esito = subprocess.run(
            cmd, cwd=str(banco), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=3600,
        )
        try:
            d = json.loads(esito.stdout)
        except json.JSONDecodeError:
            d = {"errore": (esito.stdout or "")[-500:] + (esito.stderr or "")[-500:]}
    uso = d.get("usage", {})
    consumi.append(
        {
            "secondi": round(time.time() - t0),
            "costo": d.get("total_cost_usd"),
            "turni": d.get("num_turns"),
            "modelli": {k: round(v.get("costUSD", 0), 3) for k, v in d.get("modelUsage", {}).items()},
            "uscita": uso.get("output_tokens"),
            "ragionamento": (uso.get("output_tokens_details") or {}).get("thinking_tokens"),
            "risposta": (d.get("result") or d.get("errore") or "")[-300:],
            **({"negati": d["negati"]} if d.get("negati") else {}),
        }
    )


def valida(banco: Path, work: Path) -> tuple[bool, str]:
    py = banco / ".venv" / "Scripts" / "python.exe"
    script = banco / ".claude" / "skills" / "viral-clips" / "scripts" / "validate.py"
    e = subprocess.run(
        [str(py), str(script), str(work)], cwd=str(banco), capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    return e.returncode == 0, (e.stderr or "").strip()


def main() -> None:
    argomenti = sys.argv[1:]
    motore = "claude"
    if "--motore" in argomenti:
        i = argomenti.index("--motore")
        motore = argomenti[i + 1] if i + 1 < len(argomenti) else ""
        del argomenti[i:i + 2]
    if len(argomenti) != 3 or motore not in motori.MOTORI:
        raise SystemExit(__doc__)
    banco = Path(argomenti[0]).resolve()
    modello, effort = argomenti[1], argomenti[2]
    info = json.loads((banco / "_banco.json").read_text(encoding="utf-8"))
    prova = info["prova"]
    work = banco / "work" / info["cartella"]
    cartella = banco / "output" / info["cartella"]
    consumi: list = []

    base = messaggio(prova, work, cartella)
    chiedi(banco, base, modello, effort, consumi, motore)
    if prova == "decisioni":
        # Come `automatico.fase_decisioni`: una correzione sola se il cancello respinge.
        ok, referto = valida(banco, work)
        tentativi = 1
        if not ok and (work / "decisions.json").exists():
            chiedi(banco, automatico.messaggio_correzione(base, referto), modello, effort, consumi, motore)
            ok, referto = valida(banco, work)
            tentativi = 2
        esito = {"valido": ok, "tentativi": tentativi, "referto": referto[-3000:]}
    else:
        esito = {
            "publish": (cartella / "publish.json").exists(),
            "clips_md": (cartella / "clips.md").exists(),
        }

    esito.update(
        {
            "modello": modello,
            "effort": effort,
            "motore": motore,
            "consumi": consumi,
            "costo_totale": round(sum(c["costo"] or 0 for c in consumi), 3),
            "secondi_totali": sum(c["secondi"] for c in consumi),
        }
    )
    (banco / "_esito.json").write_text(json.dumps(esito, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in esito.items() if k != "referto"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
