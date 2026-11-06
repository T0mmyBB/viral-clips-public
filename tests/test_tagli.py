"""I tagli dei silenzi: cosa si toglie, cosa non si tocca mai, e i tempi dopo.

Due errori qui non danno nessun errore. Togliere un «silenzio» che era musica o
lo stacco prima di una battuta consegna una clip che sembra montata male; e un
tempo convertito male fra clip montata e sorgente sfasa i sottotitoli di un
taglio intero. Sono le due cose fissate sotto.
"""

from __future__ import annotations

import unittest

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import tagli


def parole(*coppie: tuple[float, float]) -> list[dict]:
    return [{"word": f"w{i}", "start": a, "end": b} for i, (a, b) in enumerate(coppie)]


def sempre_quieto(a: float, b: float) -> bool:
    return True


class Piano(unittest.TestCase):
    def test_una_pausa_lunga_e_quieta_si_toglie_lasciando_respiro(self):
        ws = parole((100, 101), (101.1, 102), (104, 105), (105.1, 130))
        p = tagli.plan(ws, 100, 130, quiet=sempre_quieto, min_duration=5)
        self.assertEqual(len(p["tolti"]), 1)
        a, b = p["tolti"][0]
        self.assertAlmostEqual(a, 2 + tagli.PAD_AFTER_S)
        self.assertAlmostEqual(b, 4 - tagli.PAD_BEFORE_S)

    def test_una_pausa_con_audio_non_si_tocca(self):
        ws = parole((100, 101), (104, 130))
        p = tagli.plan(ws, 100, 130, quiet=lambda a, b: False, min_duration=5)
        self.assertEqual(p["tolti"], [])

    def test_una_risata_non_si_toglie_anche_se_quieta_per_il_transcript(self):
        ws = parole((100, 101), (104, 130))
        p = tagli.plan(
            ws, 100, 130, quiet=sempre_quieto, protected=[(101.5, 102.5)], min_duration=5
        )
        self.assertEqual(p["tolti"], [])

    def test_niente_taglio_accanto_a_uno_stacco_del_girato(self):
        # Pausa quieta fra 102 e 104; uno stacco a 104,3, subito dopo: un lampo.
        ws = parole((100, 102), (104, 130))
        p = tagli.plan(ws, 100, 130, quiet=sempre_quieto, min_duration=5, scene_cuts=[104.3])
        self.assertEqual(p["tolti"], [])
        # Lo stesso stacco dentro il tratto tolto va bene: taglio e stacco coincidono.
        p = tagli.plan(ws, 100, 130, quiet=sempre_quieto, min_duration=5, scene_cuts=[103.0])
        self.assertEqual(len(p["tolti"]), 1)

    def test_le_pause_brevi_restano(self):
        ws = parole((100, 101), (101.6, 130))
        p = tagli.plan(ws, 100, 130, quiet=sempre_quieto, min_duration=5)
        self.assertEqual(p["tolti"], [])

    def test_i_bordi_della_clip_non_si_toccano(self):
        # Tre secondi prima della prima parola e dopo l'ultima: sono dei bordi.
        ws = parole((103, 110), (110.2, 127))
        p = tagli.plan(ws, 100, 130, quiet=sempre_quieto, min_duration=5)
        self.assertEqual(p["tolti"], [])

    def test_mai_sotto_il_minimo_di_durata(self):
        ws = parole((100, 105), (107, 112), (114, 119), (121, 122))
        p = tagli.plan(ws, 100, 122, quiet=sempre_quieto, min_duration=20.0)
        self.assertGreaterEqual(p["durata"], 20.0)

    def test_niente_spezzoni_lampo_fra_due_tagli(self):
        # Una parola sola da 0,3 s fra due pause lunghe.
        ws = parole((100, 101), (103, 103.3), (105, 130))
        p = tagli.plan(ws, 100, 130, quiet=sempre_quieto, min_duration=5)
        for (a0, b0), (a1, b1) in zip(p["tenuti"], p["tenuti"][1:]):
            pass
        corti = [b - a for a, b in p["tenuti"][1:-1] if b - a < tagli.KEEP_MIN_S]
        self.assertEqual(corti, [])


class Tempi(unittest.TestCase):
    TENUTI = [(0.0, 10.0), (12.0, 20.0), (25.0, 30.0)]

    def test_andata_e_ritorno(self):
        for t_src in (0.0, 5.0, 12.0, 19.9, 25.0, 29.0):
            t_out = tagli.to_output(self.TENUTI, t_src)
            self.assertIsNotNone(t_out)
            self.assertAlmostEqual(tagli.to_source(self.TENUTI, t_out), t_src)

    def test_un_istante_tagliato_non_ha_posto_nella_clip(self):
        self.assertIsNone(tagli.to_output(self.TENUTI, 11.0))

    def test_le_parole_portano_il_tratto_e_i_tempi_montati(self):
        ws = [{"word": "a", "start": 9.0, "end": 9.5}, {"word": "b", "start": 12.5, "end": 13.0}]
        out = tagli.remap_words(ws, self.TENUTI)
        self.assertEqual([w["tratto"] for w in out], [0, 1])
        self.assertAlmostEqual(out[1]["start"], 10.5)


if __name__ == "__main__":
    unittest.main()
