"""Da quale gruppo esce una campagna scelta in una nicchia che un account non l'aveva.

Il 17 settembre 2026 il pulsante «scegli» e' arrivato anche sulle tredici nicchie senza
account: la scelta porta la `nicchia` in cui e' stata cliccata e nessun `gruppo`. Il
gruppo lo dice il registro quando la nicchia ne avra' uno. Le due cose da non rompere:
un gruppo scritto vince sempre, e un registro che non da' una risposta sola non ne
inventa una — con `None` il calendario si ferma invece di programmare da un account
qualunque.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import calendario

ID = "210e40be-92ca-4b3a-912f-231944511254"


class GruppoDellaScelta(unittest.TestCase):
    def setUp(self):
        self.vecchi = calendario.SCELTE, calendario.IDENTITA
        d = Path(tempfile.mkdtemp())
        calendario.SCELTE, calendario.IDENTITA = d / "scelte.json", d / "identita.json"

    def tearDown(self):
        calendario.SCELTE, calendario.IDENTITA = self.vecchi

    def scrivi(self, scelta: dict, gruppi: list[dict]) -> None:
        calendario.SCELTE.write_text(
            json.dumps({"scelte": [{"campagna_id": ID, **scelta}]}), encoding="utf-8"
        )
        calendario.IDENTITA.write_text(json.dumps({"gruppi": gruppi}), encoding="utf-8")

    def test_il_gruppo_scritto_vince_sulla_nicchia(self):
        self.scrivi(
            {"gruppo": "laboratorio", "nicchia": "business"},
            [{"id": "business", "nicchia": "business"}],
        )
        self.assertEqual(calendario.gruppo_di(ID), "laboratorio")

    def test_nicchia_senza_account_non_ha_gruppo(self):
        self.scrivi(
            {"gruppo": None, "nicchia": "notizie-politica"},
            [{"id": "business", "nicchia": "business"}],
        )
        self.assertIsNone(calendario.gruppo_di(ID))

    def test_arrivato_l_account_la_scelta_lo_prende(self):
        self.scrivi(
            {"gruppo": None, "nicchia": "notizie-politica"},
            [
                {"id": "business", "nicchia": "business"},
                {"id": "politica", "nicchia": "notizie-politica"},
            ],
        )
        self.assertEqual(calendario.gruppo_di(ID), "politica")

    def test_due_gruppi_sulla_stessa_nicchia_non_sono_una_risposta(self):
        self.scrivi(
            {"gruppo": None, "nicchia": "notizie-politica"},
            [
                {"id": "politica", "nicchia": "notizie-politica"},
                {"id": "politica-2", "nicchia": "notizie-politica"},
            ],
        )
        self.assertIsNone(calendario.gruppo_di(ID))


if __name__ == "__main__":
    unittest.main()
