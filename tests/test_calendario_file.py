"""Il calendario non assegna una data a un file che non si puo' caricare.

La strada del pannello — `automatico.py`, `prepara.py`, `calendario.calcola` — non passa
da `validate.py --consegna`. Il 22 settembre 2026 sette mp4 da zero byte hanno passato
il cancello della consegna perche' `clips.md` li citava: senza un controllo qui avrebbero
avuto una data, e il guasto sarebbe arrivato dall'agente di pubblicazione. Questi test
stanno sul **chiamante**: la regola e' in `validate.difetto_clip`, provata in
`test_validate`, e qui si prova che `calcola` la invoca prima di programmare.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import calendario
import validate


def sana(f):
    return {"width": 1080, "height": 1920, "has_audio": True, "duration": 34.2}


class FileDaProgrammare(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cartella = Path(self._tmp.name)
        self.vecchi = calendario.SCELTE, calendario.IDENTITA
        # Nessuna scelta e nessuna identita': superato il controllo sui file, il
        # calendario si ferma sul gruppo, con un messaggio diverso.
        calendario.SCELTE = self.cartella / "scelte.json"
        calendario.IDENTITA = self.cartella / "identita.json"
        for nome in ("01-a.mp4", "02-b.mp4"):
            (self.cartella / nome).write_bytes(b"\0" * validate.MIN_BYTES_CLIP)

    def tearDown(self):
        calendario.SCELTE, calendario.IDENTITA = self.vecchi
        self._tmp.cleanup()

    def pubblica(self, items: list[dict]) -> None:
        (self.cartella / "publish.json").write_text(json.dumps({"items": items}), encoding="utf-8")

    def calcola(self) -> str:
        with self.assertRaises(SystemExit) as fermo:
            calendario.calcola(self.cartella, sonda=sana)
        return str(fermo.exception)

    def test_un_file_vuoto_ferma_il_calendario(self):
        (self.cartella / "02-b.mp4").write_bytes(b"")
        self.pubblica([{"id": "01", "file": "01-a.mp4"}, {"id": "02", "file": "02-b.mp4"}])
        messaggio = self.calcola()
        self.assertIn("clip 02", messaggio)
        self.assertNotIn("clip 01", messaggio)

    def test_un_file_assente_ferma_il_calendario(self):
        self.pubblica([{"id": "01", "file": "01-a.mp4"}, {"id": "03", "file": "03-c.mp4"}])
        self.assertIn("clip 03", self.calcola())

    def test_i_file_sani_passano_al_controllo_dopo(self):
        self.pubblica([{"id": "01", "file": "01-a.mp4"}, {"id": "02", "file": "02-b.mp4"}])
        self.assertIn("nessun gruppo", self.calcola())

    def test_una_sospesa_non_si_guarda(self):
        # Non riceve una data, quindi il suo file non deve ancora potersi caricare.
        (self.cartella / "02-b.mp4").write_bytes(b"")
        self.pubblica(
            [
                {"id": "01", "file": "01-a.mp4"},
                {"id": "02", "file": "02-b.mp4", "sospesa": "invii in pausa"},
            ]
        )
        self.assertIn("nessun gruppo", self.calcola())


if __name__ == "__main__":
    unittest.main()


class OrariDopoMezzanotte(unittest.TestCase):
    """19:00, 21:30, 00:00: il terzo post e' la mezzanotte che segue, non quella che apre il giorno."""

    def test_un_orario_che_precede_il_precedente_e_il_giorno_dopo(self):
        from datetime import date, datetime

        momenti = calendario.momenti_del_giorno(["19:00", "21:30", "00:00"], date(2026, 10, 3))
        self.assertEqual(
            momenti,
            [datetime(2026, 10, 3, 19, 0), datetime(2026, 10, 3, 21, 30), datetime(2026, 10, 4, 0, 0)],
        )

    def test_gli_orari_in_ordine_restano_nello_stesso_giorno(self):
        from datetime import date

        momenti = calendario.momenti_del_giorno(["19:00", "21:00", "23:00"], date(2026, 10, 3))
        self.assertEqual({m.date() for m in momenti}, {date(2026, 10, 3)})


class SoloNuove(unittest.TestCase):
    def test_conta_come_uscita_una_piattaforma_qualunque_e_ogni_forma_di_chiave(self):
        with tempfile.TemporaryDirectory() as t:
            cartella = Path(t)
            (cartella / "publish_state.json").write_text(
                json.dumps(
                    {
                        "items": {
                            "01": {"platform": "youtube", "status": "pubblicato"},
                            "06@youtube": {"platform": "youtube", "status": "pubblicato"},
                            "07@tiktok": {"platform": "tiktok", "status": "errore"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(calendario.gia_uscite(cartella), {"01", "06"})


class Dopo(unittest.TestCase):
    """Settimana senza approvazioni: si parte oggi, dal primo orario ancora davanti."""

    def test_oggi_solo_gli_orari_dopo_e_poi_domani(self):
        from datetime import date, datetime

        with tempfile.TemporaryDirectory() as t:
            cartella = Path(t)
            vecchi = calendario.SCELTE, calendario.IDENTITA
            self.addCleanup(lambda: setattr(calendario, "SCELTE", vecchi[0]))
            self.addCleanup(lambda: setattr(calendario, "IDENTITA", vecchi[1]))
            calendario.IDENTITA = cartella / "identita.json"
            calendario.IDENTITA.write_text(json.dumps({"identita": [
                {"gruppo": "g", "piattaforma": "tiktok", "max_per_day": 3,
                 "account_started": "2026-01-01"}]}), encoding="utf-8")
            items = []
            for n in range(4):
                (cartella / f"0{n}.mp4").write_bytes(b"\0" * validate.MIN_BYTES_CLIP)
                items.append({"id": f"0{n}", "file": f"0{n}.mp4"})
            (cartella / "publish.json").write_text(json.dumps(
                {"gruppo": "g", "items": items, "batch_times": ["15:00", "19:00", "22:00"]}),
                encoding="utf-8")
            piano = calendario.calcola(cartella, da=date(2026, 11, 2), sonda=sana,
                                       dopo=datetime(2026, 11, 3, 16, 0))
        self.assertEqual([v["quando"] for v in piano["piano"]],
                         ["2026-11-03T19:00", "2026-11-03T22:00",
                          "2026-11-04T15:00", "2026-11-04T19:00"])
