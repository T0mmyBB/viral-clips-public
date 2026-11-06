"""A quale identita' del registro appartiene una clip pubblicata, quando il file non lo dice.

Osservazione 138: `handles["YouTube"]` e' il brand da taggare, non la nostra identita'.
Con due identita' YouTube nel registro il ripiego su tutto il registro tornava `None`
senza errore, e la pubblicazione restava senza attribuzione. Il gruppo della consegna
restringe il ripiego a chi puo' averla pubblicata.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import stato

REGISTRO = {
    ("youtube", "canale_business"): "business-youtube",
    ("youtube", "altrocanale"): "laboratorio-youtube",
}
CFG = {"gruppo": "business", "handles": {"YouTube": "@janedoe"}}
REC = {"platform": "youtube"}
GRUPPO = {"business-youtube", "business-tiktok"}


class Ripiego(unittest.TestCase):
    def test_il_brand_da_taggare_non_e_una_identita_e_il_gruppo_decide(self):
        self.assertEqual(stato.identifica(REC, CFG, REGISTRO, GRUPPO), "business-youtube")

    def test_senza_gruppo_due_identita_restano_ambigue(self):
        self.assertIsNone(stato.identifica(REC, CFG, REGISTRO))

    def test_un_handle_dichiarato_vince_sul_gruppo(self):
        cfg = {**CFG, "youtube_handle": "@altrocanale"}
        self.assertEqual(stato.identifica(REC, cfg, REGISTRO, GRUPPO), "laboratorio-youtube")


class NelChiamante(unittest.TestCase):
    """`campagne_output` passa il gruppo: una regola giusta invocata senza il dato e' inerte."""

    def test_la_plancia_attribuisce_col_gruppo_della_consegna(self):
        with tempfile.TemporaryDirectory() as d:
            cartella = Path(d) / "camp"
            cartella.mkdir()
            (cartella / "publish.json").write_text(
                json.dumps({**CFG, "items": [{"id": "01", "file": "01.mp4"}]}), encoding="utf-8"
            )
            reg = {
                "gruppi": [
                    {"id": "business", "identita": sorted(GRUPPO), "piattaforme_attese": ["youtube"]}
                ]
            }
            identita = [
                {"id": "business-youtube", "piattaforma": "youtube", "handle": "@canale_business"},
                {"id": "laboratorio-youtube", "piattaforma": "youtube", "handle": "@altrocanale"},
            ]
            with mock.patch.object(stato, "OUTPUT_DIR", Path(d)), mock.patch.object(
                stato, "registro", return_value=reg
            ), mock.patch.object(stato, "identita_attive", return_value=identita), mock.patch.object(
                stato, "cartella_esclusa", return_value=False
            ), mock.patch.object(
                stato, "normalizza_stato", return_value={("01", "youtube"): {"status": "pubblicato", "platform": "youtube"}}
            ):
                risultato = stato.campagne_output()
        self.assertEqual(risultato[0]["clip"][0]["identita"], "business-youtube")


if __name__ == "__main__":
    unittest.main()
