"""Una campagna finita non si pubblica piu': ne' in coda nel pannello, ne' dall'esecutore.

A fine settembre 2026 Contoso aveva 1,01 $ di residuo su 10.500 e restava `active`. La
regola «budget esaurito = residuo <= 0» non scattava mai, Whop rispondeva «in pausa»
invece di «chiusi», e l'esecutore teneva le clip 05-07 «ferme in attesa della
riapertura» tre volte al giorno, aprendo Whop e una finestra per niente. Le regole da
non rompere: il budget e' esaurito quando il residuo non paga il minimo di una clip,
un residuo che non si legge non chiude niente, la chiusura a mano vale anche per
l'esecutore, e l'esecutore decide **prima** di chiedere a Whop.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import esegui
import tassonomia

CONTOSO = {
    "stato": "active",
    "residuo": 1.01,
    "tariffe": [
        {"piattaforma": "instagram", "cpm": 1.5, "minimo": 2.3},
        {"piattaforma": "youtube", "cpm": 2.0, "minimo": 3.5},
        {"piattaforma": "tiktok", "cpm": 2.0, "minimo": 2.5},
    ],
}


class Budget(unittest.TestCase):
    def test_residuo_sotto_il_minimo_di_una_clip_e_esaurito(self):
        motivo = tassonomia.fine_campagna(CONTOSO, True)
        self.assertIn("esaurito", motivo)
        self.assertIn("2.30", motivo)  # il minimo piu' basso, non il primo

    def test_residuo_che_paga_una_clip_no(self):
        self.assertIsNone(tassonomia.fine_campagna({**CONTOSO, "residuo": 2.3}, True))

    def test_a_zero_e_esaurito_anche_senza_tariffe(self):
        self.assertIn("esaurito", tassonomia.fine_campagna({"stato": "active", "residuo": 0}, True))

    def test_residuo_illeggibile_non_chiude(self):
        self.assertIsNone(tassonomia.fine_campagna({**CONTOSO, "residuo": None}, True))

    def test_contano_solo_le_nostre_piattaforme(self):
        # X paga da 1 $: non e' una piattaforma su cui pubblichiamo.
        scheda = {**CONTOSO, "tariffe": CONTOSO["tariffe"] + [{"piattaforma": "x", "minimo": 1.0}]}
        self.assertIn("esaurito", tassonomia.fine_campagna(scheda, True))

    def test_senza_tariffe_vale_minimo_clip(self):
        scheda = {"stato": "active", "residuo": 1.01, "minimo_clip": 2.0}
        self.assertIn("esaurito", tassonomia.fine_campagna(scheda, True))


class Chiusura(unittest.TestCase):
    def test_la_chiusura_a_mano_viene_prima(self):
        viva = {**CONTOSO, "residuo": 500}
        self.assertEqual(
            tassonomia.campagna_finita("u", viva, True, {"stato": "chiusa"}), "chiusa a mano"
        )
        self.assertIsNone(tassonomia.campagna_finita("u", viva, True, {"stato": "in-corso"}))

    def test_senza_campagna_niente_da_dedurre(self):
        self.assertIsNone(tassonomia.campagna_finita(None, None, True, None))


class Giro(unittest.TestCase):
    """`esegui.main`: a campagna finita non si apre Whop e non esce niente."""

    def giro(self, finita: str | None):
        d = Path(tempfile.mkdtemp())
        (d / "publish.json").write_text(
            json.dumps(
                {
                    "whop_campaign_uuid": "79261cab",
                    "items": [{"id": "05", "quando": "2026-09-16T19:00"}],
                    "calendario_piattaforme": ["youtube", "tiktok"],
                }
            ),
            encoding="utf-8",
        )
        chiesto, pubblicate = [], []
        uscita = io.StringIO()
        with (
            mock.patch.object(esegui, "campagna_finita", return_value=finita),
            mock.patch.object(
                esegui, "controlla_invii", side_effect=lambda c: chiesto.append(c) or "aperti"
            ),
            mock.patch.object(
                esegui, "pubblica", side_effect=lambda *a: pubblicate.append(a) or (True, "ok")
            ),
            mock.patch.object(sys, "argv", ["esegui.py", "--cartella", str(d), "--davvero"]),
            redirect_stdout(uscita),
            redirect_stderr(io.StringIO()),
        ):
            with self.assertRaises(SystemExit) as fine:
                esegui.main()
        return fine.exception.code, chiesto, pubblicate, json.loads(uscita.getvalue())

    def test_finita_non_chiede_a_whop_e_non_pubblica(self):
        codice, chiesto, pubblicate, conto = self.giro("chiusa a mano")
        self.assertEqual((chiesto, pubblicate), ([], []))
        self.assertEqual(conto["finite"], 1)
        self.assertEqual(codice, 0)

    def test_viva_pubblica(self):
        codice, chiesto, pubblicate, conto = self.giro(None)
        self.assertEqual(len(chiesto), 1)
        self.assertEqual(len(pubblicate), 2)

    def test_legge_scelte_e_catalogo(self):
        """Il chiamante vero: la scheda di Contoso dal catalogo basta a fermarla."""
        d = Path(tempfile.mkdtemp())
        (d / "publish.json").write_text(json.dumps({"whop_campaign_uuid": "u1"}), encoding="utf-8")
        cat = d / "dettagli.json"
        cat.write_text(json.dumps([{"id": "u1", **CONTOSO}]), encoding="utf-8")
        with (
            mock.patch.object(esegui, "DETTAGLI", cat),
            mock.patch.object(esegui, "SCELTE", d / "nessuna.json"),
        ):
            self.assertIn("esaurito", esegui.campagna_finita(d))
            cat.write_text(json.dumps([{"id": "u1", **CONTOSO, "residuo": 900}]), encoding="utf-8")
            self.assertIsNone(esegui.campagna_finita(d))


if __name__ == "__main__":
    unittest.main()
