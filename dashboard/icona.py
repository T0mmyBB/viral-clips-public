#!/usr/bin/env python
"""
icona.py — disegna `plancia.ico`, l'icona del collegamento sul desktop.

    python icona.py            # riscrive dashboard/plancia.ico

Perche' un generatore e non un file binario
-------------------------------------------
Un `.ico` committato e' un blob che nessuno sa piu' modificare, e questo
progetto non tiene binari che sa rifare (vedi la regola sui media in
`export_skill.py`). Qui il disegno sono venti righe di numpy e i colori sono
gli **stessi token della plancia** (`static/style.css`): se un giorno cambia il
tema, l'icona si rifa' con un comando invece di restare l'unico pezzo di
interfaccia col colore vecchio.

Formato: un ICO che contiene un PNG. Windows lo accetta da Vista in poi, e
costa un header di ventidue byte invece di una conversione a BMP con la maschera
di trasparenza scritta al contrario.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
DESTINAZIONE = HERE / "plancia.ico"

LATO = 256  # Windows scala in giu' meglio di quanto scali in su
SFONDO = (0x26, 0x1D, 0x1A)  # BGR di #1a1d26, il pannello della plancia
ACCENTO = (0xEF, 0x8D, 0x5B)  # BGR di #5b8def, l'accento
CHIARO = (0xEB, 0xE6, 0xE4)  # BGR di #e4e6eb, il testo


def disegna(lato: int = LATO) -> np.ndarray:
    """Tre barre che salgono, dentro un quadrato arrotondato. BGRA."""
    img = np.zeros((lato, lato, 4), dtype=np.uint8)

    # Il quadrato arrotondato: si disegna pieno e poi si smussano gli angoli
    # con quattro cerchi, che e' piu' corto di qualunque poligono.
    raggio = int(lato * 0.22)
    pieno = np.zeros((lato, lato), dtype=np.uint8)
    cv2.rectangle(pieno, (raggio, 0), (lato - raggio, lato), 255, -1)
    cv2.rectangle(pieno, (0, raggio), (lato, lato - raggio), 255, -1)
    for cx, cy in (
        (raggio, raggio),
        (lato - raggio, raggio),
        (raggio, lato - raggio),
        (lato - raggio, lato - raggio),
    ):
        cv2.circle(pieno, (cx, cy), raggio, 255, -1)

    img[..., :3] = SFONDO
    img[..., 3] = pieno

    # Tre barre di altezza crescente: e' una plancia, non un lettore video.
    larghezza = int(lato * 0.13)
    spazio = int(lato * 0.075)
    base = int(lato * 0.76)
    altezze = (0.22, 0.36, 0.52)
    colori = (CHIARO, CHIARO, ACCENTO)
    totale = 3 * larghezza + 2 * spazio
    x = (lato - totale) // 2
    for altezza, colore in zip(altezze, colori):
        cima = base - int(lato * altezza)
        cv2.rectangle(img, (x, cima), (x + larghezza, base), (*colore, 255), -1)
        x += larghezza + spazio

    # La riga di base, che tiene insieme le tre barre.
    cv2.rectangle(
        img,
        (int(lato * 0.20), base),
        (int(lato * 0.80), base + max(2, lato // 40)),
        (*ACCENTO, 255),
        -1,
    )
    return img


def scrivi_ico(png: bytes, destinazione: Path, lato: int) -> None:
    """ICONDIR + una ICONDIRENTRY + il PNG cosi' com'e'."""
    misura = 0 if lato >= 256 else lato  # 0 vuol dire 256, nel formato
    intestazione = struct.pack("<HHH", 0, 1, 1)
    voce = struct.pack("<BBBBHHII", misura, misura, 0, 0, 1, 32, len(png), 22)
    destinazione.write_bytes(intestazione + voce + png)


def main() -> None:
    img = disegna()
    ok, buffer = cv2.imencode(".png", img)
    if not ok:
        sys.exit("non sono riuscito a codificare il PNG")
    scrivi_ico(buffer.tobytes(), DESTINAZIONE, LATO)
    print(
        f"{DESTINAZIONE} scritto: {DESTINAZIONE.stat().st_size} byte, {LATO}x{LATO}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
