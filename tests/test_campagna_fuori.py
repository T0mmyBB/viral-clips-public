"""Una campagna fuori dal catalogo del radar entra dalla stessa porta di una del catalogo.

La consegna AltroFornitore del 17 settembre 2026 aveva un `campagna.json` scritto a mano: titolo,
CPM e piattaforme digitati, indistinguibili da quelli di una scheda, e il materiale su un
link SwissTransfer che scade. Le regole da non rompere: il brief passato si copia e dice
da dove viene, `origine` separa catalogo e fuori catalogo, fuori catalogo i numeri restano
vuoti invece di essere inventati, il brief gia' copiato sopravvive a un nuovo `stage` della
stessa campagna e sparisce con un'altra, e i link a scadenza escono in cima alla vista.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import campagna

BRIEF = (
    "Clip 48 ORE nella VERA VITA.\nDevi taggare @creator_esempio in caption.\n"
    "Materiale: https://www.swisstransfer.com/d/abc-123.\n"
)


class FuoriCatalogo(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = Path(self._tmp.name)
        self.vecchi = campagna.DETTAGLI, campagna.ASSEGNAZIONI, campagna.BRIEFS
        campagna.DETTAGLI, campagna.ASSEGNAZIONI = d / "dettagli.json", d / "assegnazioni.json"
        campagna.BRIEFS = d / "briefs"
        campagna.BRIEFS.mkdir()
        self.work = d / "work"
        self.work.mkdir()

    def tearDown(self):
        campagna.DETTAGLI, campagna.ASSEGNAZIONI, campagna.BRIEFS = self.vecchi
        self._tmp.cleanup()

    def test_il_brief_passato_si_copia_e_dice_da_dove_viene(self):
        dati = campagna.stage(
            self.work, "6aabbca3", brief=BRIEF, fornitore="altrofornitore", titolo="48 ORE"
        )
        self.assertEqual((self.work / "brief.txt").read_text(encoding="utf-8"), BRIEF)
        self.assertEqual(dati["brief_da"], "incollato")
        self.assertEqual(dati["origine"], "fuori catalogo")
        self.assertEqual((dati["fornitore"], dati["titolo"]), ("altrofornitore", "48 ORE"))
        self.assertTrue(dati["da_leggere"])

    def test_fuori_catalogo_i_numeri_restano_vuoti(self):
        dati = campagna.stage(self.work, "6aabbca3", brief=BRIEF)
        self.assertIsNone(dati["cpm"])
        self.assertEqual(dati["piattaforme"], [])

    def test_un_nuovo_stage_tiene_brief_e_fornitore(self):
        campagna.stage(
            self.work,
            "6aabbca3",
            brief=BRIEF,
            brief_da="file brief.txt",
            fornitore="altrofornitore",
            titolo="48 ORE",
        )
        dati = campagna.stage(self.work, "6aabbca3")
        self.assertEqual(dati["brief"], "brief.txt")
        self.assertEqual(dati["brief_da"], "file brief.txt")
        self.assertEqual((dati["fornitore"], dati["titolo"]), ("altrofornitore", "48 ORE"))
        self.assertEqual(dati["da_scaricare_a_mano"], ["https://www.swisstransfer.com/d/abc-123"])

    def test_un_altra_campagna_non_eredita_il_brief(self):
        campagna.stage(self.work, "6aabbca3", brief=BRIEF, fornitore="altrofornitore")
        dati = campagna.stage(self.work, "altra")
        self.assertIsNone(dati["brief"])
        self.assertIsNone(dati["fornitore"])
        self.assertEqual(dati["titolo"], "altra")

    def test_nel_catalogo_vince_la_scheda(self):
        campagna.DETTAGLI.write_text(
            json.dumps(
                [
                    {
                        "id": "79261cab",
                        "titolo": "Contoso",
                        "cpm": 2.0,
                        "provider": "Clipping Culture",
                        "piattaforme": ["youtube"],
                        "materiali": [],
                    }
                ]
            ),
            encoding="utf-8",
        )
        (campagna.BRIEFS / "79261cab.txt").write_text("brief del radar\n", encoding="utf-8")
        dati = campagna.stage(self.work, "79261cab", titolo="altro titolo")
        self.assertEqual(
            (dati["origine"], dati["titolo"], dati["cpm"]), ("catalogo", "Contoso", 2.0)
        )
        self.assertEqual((dati["brief_da"], dati["fornitore"]), ("radar", "Clipping Culture"))

    def test_la_vista_dice_fuori_catalogo_e_il_link_che_scade(self):
        campagna.stage(self.work, "6aabbca3", brief=BRIEF, fornitore="altrofornitore")
        testo = campagna.as_text(self.work)
        self.assertIn("FUORI CATALOGO (altrofornitore)", testo)
        self.assertIn("DA SCARICARE A MANO", testo)
        self.assertIn("https://www.swisstransfer.com/d/abc-123", testo)
        self.assertIn("da: incollato", testo)


class DaRunPrepare(FuoriCatalogo):
    """Il chiamante: `run.py prepare --campagna ... --brief ...` arriva fino a `stage`."""

    def test_il_brief_di_prepare_arriva_nella_cartella(self):
        import run

        f = self.work.parent / "brief-altrofornitore.txt"
        f.write_text(BRIEF, encoding="utf-8")
        args = run.parser().parse_args(
            [
                "prepare",
                "video.mp4",
                "--campagna",
                "6aabbca3",
                "--brief",
                str(f),
                "--fornitore",
                "altrofornitore",
                "--titolo",
                "48 ORE",
            ]
        )
        dati = run.aggancia_campagna(self.work, args)
        self.assertEqual(
            (dati["brief_da"], dati["fornitore"], dati["titolo"]),
            ("file brief-altrofornitore.txt", "altrofornitore", "48 ORE"),
        )
        self.assertTrue((self.work / "brief.txt").exists())

    def test_prepare_aggancia_prima_della_trascrizione(self):
        # I sottoprocessi sono finti: l'ingest dice dov'e' la cartella, la
        # trascrizione ferma tutto. A quel punto la campagna dev'essere agganciata.
        from unittest import mock

        import run

        class Fermo(Exception):
            pass

        def finto(nome, script, *a, **kw):
            if script == "ingest.py":
                return {"work_dir": str(self.work)}, 0.1
            raise Fermo(script)

        args = run.parser().parse_args(
            [
                "prepare",
                "video.mp4",
                "--campagna",
                "6aabbca3",
                "--brief",
                "-",
                "--fornitore",
                "altrofornitore",
            ]
        )
        with (
            mock.patch.object(run, "stage", side_effect=finto),
            mock.patch("sys.stdin", mock.Mock(read=lambda: BRIEF)),
        ):
            with self.assertRaises(Fermo):
                run.prepare(args)
        dati = campagna.load(self.work)
        self.assertEqual((dati["brief_da"], dati["fornitore"]), ("incollato", "altrofornitore"))

    def test_senza_campagna_niente(self):
        import run

        args = run.parser().parse_args(["prepare", "video.mp4"])
        self.assertIsNone(run.aggancia_campagna(self.work, args))


class LinkAScadenza(unittest.TestCase):
    def test_trasferimenti_si_dropbox_no(self):
        brief = (
            "scarica da https://we.tl/t-AbC, oppure https://www.dropbox.com/s/x/v.mp4 "
            "e ancora https://we.tl/t-AbC."
        )
        materiali = ["https://wetransfer.com/downloads/123", "https://youtu.be/abc"]
        self.assertEqual(
            campagna.link_a_scadenza(brief, materiali),
            ["https://we.tl/t-AbC", "https://wetransfer.com/downloads/123"],
        )


class Argomenti(unittest.TestCase):
    def test_brief_da_file(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "brief-altrofornitore.txt"
            f.write_text(BRIEF, encoding="utf-8")
            args = argparse.Namespace(brief=str(f), fornitore="altrofornitore", titolo=None)
            kw = campagna.da_argomenti(args)
        self.assertEqual((kw["brief"], kw["brief_da"]), (BRIEF, "file brief-altrofornitore.txt"))

    def test_brief_che_non_esiste_ferma(self):
        args = argparse.Namespace(brief="non-esiste.txt", fornitore=None, titolo=None)
        with self.assertRaises(SystemExit):
            campagna.da_argomenti(args)


if __name__ == "__main__":
    unittest.main()
