"""Quali account legge il giro delle metriche (`metriche.da_leggere`).

**Perche' questa merita una prova.** Un account saltato non da' errore: il suo
ultimo numero resta sul pannello e sembra solo un account che non cresce. Cosi'
Instagram e' rimasto fermo dal 4 al 31 ottobre 2026: si legge dall'API di Meta,
la cartella del profilo sul server non c'e', e `da_leggere` la pretendeva.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import instagram_api  # publish/
import metriche  # dashboard/


def identita(piattaforma: str, profilo: str) -> dict:
    return {
        "id": f"business-{piattaforma}",
        "gruppo": "business",
        "piattaforma": piattaforma,
        "profilo_browser": profilo,
    }


class DaLeggere(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.radice = Path(self.tmp.name)
        self.c_e = self.radice / "c-e"
        self.c_e.mkdir()
        self.manca = self.radice / "manca"

    def leggi(self, identita_: list[dict], token: bool) -> list[str]:
        with (
            mock.patch.object(metriche.stato, "identita_attive", return_value=identita_),
            mock.patch.object(instagram_api, "ha_token", return_value=token),
        ):
            return [i["id"] for i in metriche.da_leggere()]

    def test_instagram_col_token_senza_profilo_si_legge(self):
        self.assertEqual(
            self.leggi([identita("instagram", str(self.manca))], token=True),
            ["business-instagram"],
        )

    def test_instagram_senza_token_e_senza_profilo_si_salta(self):
        self.assertEqual(self.leggi([identita("instagram", str(self.manca))], token=False), [])

    def test_il_token_non_vale_per_tiktok(self):
        self.assertEqual(self.leggi([identita("tiktok", str(self.manca))], token=True), [])

    def test_col_profilo_si_legge(self):
        self.assertEqual(
            self.leggi([identita("tiktok", str(self.c_e))], token=False),
            ["business-tiktok"],
        )


if __name__ == "__main__":
    unittest.main()
