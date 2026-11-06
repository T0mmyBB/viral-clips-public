"""Una misura di velocita su un portatile dice se stava in carica, e per tutta la durata.

**Perche questa merita una prova.** Il benchmark dei motori di trascrizione partiva
solo con il caricabatterie collegato; per 17 minuti l'alimentatore e stato staccato
e nessun numero lo diceva — un tempo a 0,42x contro una mediana di 0,20 e sembrato
un dato. Controllare lo stato **una volta** all'inizio e un'ipotesi per il resto
della misura. Qui si fissano le tre decisioni: lo stato sconosciuto non e «in
carica», una staccata a meta e una riconnessione prima della fine restano visibili,
e il campo finisce davvero accanto al tempo.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import common
import report


class Riassunto(unittest.TestCase):
    def test_tutto_in_carica(self):
        self.assertEqual(common.riassumi_alimentazione(["rete", "rete"]), "rete")

    def test_tutto_a_batteria(self):
        self.assertEqual(common.riassumi_alimentazione(["batteria"]), "batteria")

    def test_staccato_e_riattaccato_e_mista(self):
        self.assertEqual(common.riassumi_alimentazione(["rete", "batteria", "rete"]), "mista")

    def test_non_saperlo_non_e_rete(self):
        self.assertIsNone(common.riassumi_alimentazione([]))
        self.assertIsNone(common.riassumi_alimentazione([None, None]))

    def test_un_campione_ignoto_non_cancella_quelli_noti(self):
        self.assertEqual(common.riassumi_alimentazione([None, "rete"]), "rete")


class Finestra(unittest.TestCase):
    # campioni ogni 10 s: in carica fino a 25, a batteria da 30 a 45, di nuovo in carica
    CAMPIONI = [(0, "rete"), (10, "rete"), (20, "rete"), (30, "batteria"), (40, "batteria"), (50, "rete")]

    def stati(self, dal, al):
        return common.stati_nella_finestra(self.CAMPIONI, dal, al)

    def test_la_staccata_a_meta_si_vede_anche_se_alla_fine_e_di_nuovo_in_carica(self):
        # e il caso del benchmark: a inizio e a fine in carica, 17 minuti in mezzo no
        self.assertEqual(common.riassumi_alimentazione(self.stati(5, 55)), "mista")

    def test_lo_stato_gia_in_corso_a_inizio_finestra_conta(self):
        # nessun campione cade dentro (31, 39): vale quello in vigore, la batteria
        self.assertEqual(common.riassumi_alimentazione(self.stati(31, 39)), "batteria")

    def test_una_finestra_tutta_in_carica_resta_rete(self):
        self.assertEqual(common.riassumi_alimentazione(self.stati(2, 28)), "rete")


class RecordMetric(unittest.TestCase):
    def test_lo_stato_finisce_accanto_al_tempo(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            common, "alimentazione_durante", lambda secondi: "batteria"
        ):
            common.record_metric(tmp, "render per clip", 30.0)
            fasi = common.read_json(Path(tmp) / "metrics.json")["fasi"]
        self.assertEqual(fasi["render per clip"]["alimentazione"], "batteria")

    def test_la_misura_passa_dalla_finestra_della_sua_durata(self):
        visti = []
        with mock.patch.object(common, "_leggi_alimentazione", lambda: "rete"), mock.patch.object(
            common, "_ALIMENTAZIONE", [(100.0, "rete"), (200.0, "batteria"), (300.0, "rete")]
        ):
            visti.append(common.alimentazione_durante(250.0, ora=310.0))  # da 60 a 310: tutto
            visti.append(common.alimentazione_durante(5.0, ora=310.0))  # da 305 a 310: in carica
        self.assertEqual(visti, ["mista", "rete"])


class Rapporto(unittest.TestCase):
    def test_un_tempo_a_batteria_si_dichiara_nella_tabella(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "meta.json").write_text('{"slug": "x", "duration": 60}', encoding="utf-8")
            (work / "metrics.json").write_text(
                '{"fasi": {"a": {"secondi": 10, "alimentazione": "rete"},'
                ' "b": {"secondi": 20, "alimentazione": "mista"},'
                ' "c": {"secondi": 30, "alimentazione": "batteria"},'
                ' "d": {"secondi": 40}}}',
                encoding="utf-8",
            )
            d = report.descrivi(work)
        # il campo assente (record di prima) non e «in carica», ma nemmeno «a batteria»
        self.assertEqual(d["a_batteria"], {"b", "c"})

        # e la tabella, che e quello che si legge, lo dice accanto al numero giusto
        righe = report.tabella([d]).splitlines()
        cella = {r.split("|")[1].strip(): r.split("|")[2] for r in righe if r.startswith("| ")}
        self.assertNotIn("batteria", cella["a"])
        self.assertIn("a batteria", cella["b"])
        self.assertIn("a batteria", cella["c"])
        self.assertNotIn("batteria", cella["d"])


class Avvio(unittest.TestCase):
    """Il punto che usa la regola: se il driver non avvia il campionamento, ogni
    misura vede solo la propria fine e la staccata a meta resta invisibile."""

    def test_il_driver_avvia_la_sorveglianza(self):
        import run

        chiamate = []
        finto = mock.Mock()
        finto.parse_args.return_value = mock.Mock(func=lambda args: None)
        with mock.patch.object(
            run, "sorveglia_alimentazione", lambda: chiamate.append(1)
        ), mock.patch.object(run, "parser", lambda: finto):
            run.main()
        self.assertEqual(chiamate, [1])


if __name__ == "__main__":
    unittest.main()
