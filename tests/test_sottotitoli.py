"""I sottotitoli parola per parola: la parola accesa non deve spostare le vicine.

Prima la riga era un evento unico e la parola accesa si ingrandiva dentro la
riga: la riga centrata si allargava e tutte le altre parole scivolavano a ogni
cambio. Il test che conta e il primo: ogni parola resta nello stesso punto per
tutto il tempo in cui il suo gruppo e a schermo.
"""

from __future__ import annotations

import re
import unittest

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import make_subs

POS = re.compile(r"\\pos\(([-\d.]+),([-\d.]+)\)")


def eventi(ass: str) -> list[tuple[str, str, float, str]]:
    out = []
    for riga in ass.splitlines():
        if not riga.startswith("Dialogue:") or ",Main," not in riga:
            continue
        campi = riga.split(",", 9)
        testo = campi[9]
        pos = POS.search(testo)
        parola = re.sub(r"\{[^}]*\}", "", testo)
        out.append((campi[1], campi[2], float(pos.group(1)), parola))
    return out


PAROLE = [
    {"word": "I", "start": 10.0, "end": 10.2},
    {"word": "was", "start": 10.25, "end": 10.5},
    {"word": "scared.", "start": 10.55, "end": 11.0},
    {"word": "Then", "start": 11.5, "end": 11.8},
    {"word": "this", "start": 11.85, "end": 12.2},
]


class Composizione(unittest.TestCase):
    def test_ogni_parola_resta_nello_stesso_punto(self):
        ass = make_subs.build_ass(PAROLE, 10.0, 13.0, keywords=["scared"])
        per_parola: dict[str, set[float]] = {}
        for _, _, x, parola in eventi(ass):
            per_parola.setdefault(parola, set()).add(x)
        self.assertTrue(per_parola)
        for parola, xs in per_parola.items():
            self.assertEqual(len(xs), 1, f"'{parola}' cambia posizione: {xs}")

    def test_una_sola_parola_accesa_alla_volta(self):
        ass = make_subs.build_ass(PAROLE, 10.0, 13.0)
        accese = [r for r in ass.splitlines() if "\\t(0,90," in r]
        self.assertEqual(len(accese), len(PAROLE))

    def test_la_parola_accesa_non_tocca_le_vicine(self):
        # «NOTBECAUSEOF»: senza spazio riservato, lo scatto al 112% mangiava gli spazi.
        stile = make_subs.STYLES["bold"]
        testi = ["IS", "NOT", "BECAUSE", "OF"]
        centri, fit = make_subs.layout_group(testi, [100] * 4, stile)
        larghezze = [make_subs.text_width(t, stile["font"], stile["size"]) * fit for t in testi]
        spazio = make_subs.text_width(" ", stile["font"], stile["size"]) * fit
        punch = stile["punch"] / 100.0
        for i in range(len(testi) - 1):
            for accesa in (i, i + 1):
                dx = centri[i + 1] - centri[i]
                mezza_i = larghezze[i] * (punch if accesa == i else 1.0) / 2
                mezza_j = larghezze[i + 1] * (punch if accesa == i + 1 else 1.0) / 2
                self.assertGreaterEqual(dx - mezza_i - mezza_j, spazio * 0.99)

    def test_una_riga_troppo_larga_si_rimpicciolisce_invece_di_uscire(self):
        stile = make_subs.STYLES["bold"]
        testi = ["ABCDEFGHIJKLMNOPQRST", "ABCDEFGHIJKLMNOPQRST"]
        centri, fit = make_subs.layout_group(testi, [100, 100], stile)
        self.assertLess(fit, 1.0)
        mezza = make_subs.text_width(testi[0], stile["font"], stile["size"]) * fit / 2
        self.assertGreaterEqual(
            centri[0] - mezza, (make_subs.PLAY_W - make_subs.LINE_MAX_W) / 2 - 1
        )

    def test_un_taglio_dei_silenzi_chiude_la_riga(self):
        # Nessuna punteggiatura e, dopo il taglio, nessuna pausa: l'unica ragione
        # per andare a capo e che le due parole stanno in tratti diversi.
        ws = [
            {"word": "so", "start": 10.0, "end": 10.2},
            {"word": "scared", "start": 10.25, "end": 10.6},
            {"word": "then", "start": 12.0, "end": 12.3},
            {"word": "this", "start": 12.35, "end": 12.8},
        ]
        tenuti = [(0.0, 0.8), (2.0, 3.0)]
        ass = make_subs.build_ass(ws, 10.0, 13.0, tenuti=tenuti)
        scared = [e for e in eventi(ass) if e[3] == "SCARED"]
        then = [e for e in eventi(ass) if e[3] == "THEN"]
        self.assertLessEqual(max(e[1] for e in scared), min(e[0] for e in then))


if __name__ == "__main__":
    unittest.main()
