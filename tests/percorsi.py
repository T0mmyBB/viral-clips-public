"""Rende importabili i moduli sotto esame, che stanno in cartelle diverse.

Nessuna e' un package e nessuna sta sulla radice: `ritmo.py` e' in `publish/`,
`validate.py` dentro la skill, `tassonomia.py` in `radar/`, `stato.py` in
`dashboard/`. Non e' disordine — sono parti che girano in momenti diversi — ma
vuol dire che prima di importarle va detto dove sono.

`validate` importa a sua volta `campagna` e `common`, che gli stanno accanto:
basta la loro cartella sul percorso, e non serve nient'altro. Sono tutti moduli
di sola libreria standard, quindi importarli costa 0,03 s e **non** tira dentro
opencv o whisper: e' la ragione per cui questi test si possono lanciare sempre,
anche su una macchina dove il `.venv` grosso non e' ancora stato creato.
"""

from __future__ import annotations

import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent

CARTELLE = (
    RADICE / "publish",
    RADICE / ".claude" / "skills" / "viral-clips" / "scripts",
    RADICE / "radar",
    RADICE / "dashboard",
)


def aggiungi() -> None:
    """Mette le tre cartelle in testa a `sys.path`, una volta sola."""
    for cartella in CARTELLE:
        percorso = str(cartella)
        if percorso not in sys.path:
            sys.path.insert(0, percorso)


aggiungi()
