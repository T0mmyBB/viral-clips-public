"""Un transcript nato col modello ritirato lo dice la vista, prima di scegliere.

Il 25 settembre 2026 `small` e' stato ritirato perche' perde frasi intere, ma le cartelle
preparate prima restano in cache e nessuna riga in cima alla vista lo segnalava
(osservazione 137). Il modello atteso si legge da `transcribe.DEFAULT_MODEL`, non si
riscrive qui: un test che ricalcola la regola verifica la propria copia.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import check_material
from transcribe import DEFAULT_MODEL


class ModelloVecchio(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.work = Path(self._tmp.name)
        (self.work / "meta.json").write_text(
            json.dumps({"width": 1920, "height": 1080, "duration": 600}), encoding="utf-8"
        )

    def tearDown(self):
        self._tmp.cleanup()

    def codici(self, **transcript) -> list[str]:
        (self.work / "transcript.json").write_text(
            json.dumps({"word_count": 1500, "duration": 600, **transcript}), encoding="utf-8"
        )
        return [n["codice"] for n in check_material.check(self.work)]

    def test_un_modello_diverso_dal_default_si_segnala(self):
        self.assertIn("modello-vecchio", self.codici(model="small"))

    def test_il_default_non_segnala(self):
        self.assertNotIn("modello-vecchio", self.codici(model=DEFAULT_MODEL))

    def test_senza_modello_dichiarato_non_si_accusa_nessuno(self):
        self.assertNotIn("modello-vecchio", self.codici())

    def test_la_nota_arriva_in_cima_alla_vista(self):
        self.codici(model="small")
        testo = check_material.as_text(check_material.check(self.work))
        self.assertIn("small", testo)
        self.assertIn(DEFAULT_MODEL, testo)


if __name__ == "__main__":
    unittest.main()
