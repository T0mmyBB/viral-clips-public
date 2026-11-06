"""
interpreti.py — dove sta il python di ogni venv, su Windows e su Linux.

Tre venv, nessuno intercambiabile (perche', in `accedi.py`): quello del progetto
(whisper, opencv), quello di `publish/` (Playwright) e quello condiviso dello
scraping nella home. Il **posto** di un venv si trova, non si scrive — e' la
regola di CLAUDE.md sul venv condiviso — e da quando la pipeline gira anche sul
server lo stesso vale per la **forma** dell'interprete: `Scripts\\python.exe` su
Windows, `bin/python` altrove. Scritta a mano in otto file, la forma di Windows
fa fallire ogni subprocess su Linux con un FileNotFoundError che nomina un file
e non dice che e' quello di un altro sistema.
"""

from __future__ import annotations

import os
from pathlib import Path

QUI = Path(__file__).resolve().parent
RADICE = QUI.parent


def python_di(venv: Path) -> Path:
    """L'interprete dentro un venv, nella forma del sistema su cui si gira."""
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


VENV_SCRAPING = Path.home() / ".venvs" / "scraping"
PYTHON_SCRAPING = python_di(VENV_SCRAPING)
PYTHON_PUBLISH = python_di(QUI / ".venv")
PYTHON_PROGETTO = python_di(RADICE / ".venv")
