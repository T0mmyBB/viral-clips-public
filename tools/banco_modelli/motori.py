"""Il secondo motore del banco: Antigravity CLI (`agy`), con l'abbonamento Google.

`claude -p` resta il motore della produzione e del banco; `agy` serve a provare
Gemini (e Claude o GPT-OSS sulla quota Google) con lo stesso messaggio. Il modello
e' lo slug di `agy models` senza l'effort: `gemini-4-argon` + `high` diventa
`gemini-4-argon-high`.

**Il prompt passa da stdin**, in `stream-json`: come argomento un lotto di nicchie
supera il limite della riga di comando di Windows. Una riga `user`, e si legge
l'evento `result`.

**I comandi li permettono le regole di agy, non un flag.** In headless agy legge e
scrive i file della cartella di lavoro e nega in silenzio i comandi non permessi.
Le decisioni hanno bisogno di `windows.py` e `validate.py`: si permettono con
`permissions.allow` in `~/.gemini/antigravity-cli/settings.json` (vedi README), non
con `--dangerously-skip-permissions`, che approverebbe qualunque comando.

**agy non da' il costo**, solo i token: il confronto col dollaro di `claude -p` si
fa sui token e sui secondi, e sulla quota che `agy -p /usage` mostra prima e dopo.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

MOTORI = ("claude", "agy")


def agy_exe() -> str:
    """Dal PATH, o dove lo mette `install.ps1`: un terminale aperto prima
    dell'installazione ha ancora il PATH vecchio."""
    trovato = shutil.which("agy")
    windows = Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe"
    if not trovato and windows.is_file():
        trovato = str(windows)
    if not trovato:
        raise RuntimeError("'agy' non trovato: serve Antigravity CLI installato e col login fatto")
    return trovato


def agy(testo: str, modello: str, effort: str, cwd: str, timeout: int) -> dict:
    """Una richiesta ad agy; torna i campi di `claude --output-format json` che il banco legge."""
    cmd = [
        agy_exe(), "--model", f"{modello}-{effort}",
        "--input-format", "stream-json", "--output-format", "stream-json",
    ]
    riga = json.dumps({"event": "user", "message": {"content": testo}}, ensure_ascii=False)
    esito = subprocess.run(
        cmd, cwd=cwd, input=riga + "\n", capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    fine = {}
    for r in esito.stdout.splitlines():
        try:
            evento = json.loads(r)
        except json.JSONDecodeError:
            continue
        if evento.get("event") == "result":
            fine = evento["result"]
    if fine.get("status") != "SUCCESS":
        errore = fine.get("error") or (esito.stdout or "")[-500:] + (esito.stderr or "")[-500:]
        return {"errore": errore, "usage": fine.get("usage", {})}
    uso = fine.get("usage", {})
    return {
        "result": fine.get("response"),
        "num_turns": fine.get("num_turns"),
        "total_cost_usd": None,
        "modelUsage": {f"{modello}-{effort}": {}},
        # stderr: i comandi negati dalle regole finiscono li', e senza vederli una
        # prova fallita sembra colpa del modello
        "negati": [r for r in (esito.stderr or "").splitlines() if "permission" in r.lower()][-5:],
        "usage": {
            "input_tokens": uso.get("input_tokens", 0),
            "cache_read_input_tokens": uso.get("cache_read_tokens", 0),
            "output_tokens": uso.get("output_tokens"),
            "output_tokens_details": {"thinking_tokens": uso.get("thinking_tokens")},
        },
    }
