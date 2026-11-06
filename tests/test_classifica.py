"""Una risposta di Claude con una riga storta non lascia campagne fuori per un giorno.

Il 16 settembre 2026 una risposta ha scritto l'identificativo `1c5f a61` invece di
`1c5fa61f`: la riga e' stata scartata e la campagna e' rimasta senza nicchia — cioe'
fuori da ogni somma — fino al giro dopo. Gli identificativi sono esadecimali, quindi
uno spazio dentro e' un refuso, non un'altra campagna. Quello che resta davvero
senza risposta `classifica.py` lo richiede subito, una volta.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import percorsi  # mette le cartelle del progetto su sys.path

import classifica


def lotto(*ids: str) -> dict[str, dict]:
    return {i[:8]: {"id": i, "titolo": f"campagna {i[:4]}"} for i in ids}


def riga(chiave: str, forma: str = "parlato_lungo", lavorabile: str = "si") -> str:
    return "\t".join([chiave, "business", "podcast", forma, "en", lavorabile, "nota"])


class LeggiTsv(unittest.TestCase):
    def test_identificativo_con_uno_spazio_si_accetta(self):
        voci, problemi = classifica.leggi_tsv(riga("1c5f a61f"), lotto("1c5fa61f-0000"))
        self.assertEqual([v["id"] for v in voci], ["1c5fa61f-0000"])
        self.assertEqual(problemi, [])

    def test_la_campagna_saltata_e_senza_risposta(self):
        voci, problemi = classifica.leggi_tsv(
            riga("aaaaaaaa"), lotto("aaaaaaaa-0000", "bbbbbbbb-0000")
        )
        self.assertEqual([v["id"] for v in voci], ["aaaaaaaa-0000"])
        self.assertEqual(len(problemi), 1)
        self.assertIn("senza risposta: bbbbbbbb", problemi[0])

    def test_un_identificativo_di_un_altro_lotto_non_si_indovina(self):
        voci, problemi = classifica.leggi_tsv(riga("cccccccc"), lotto("aaaaaaaa-0000"))
        self.assertEqual(voci, [])
        self.assertTrue(any("non e' di questo lotto" in p for p in problemi))

    def test_forma_fuori_vocabolario_si_scarta(self):
        voci, problemi = classifica.leggi_tsv(riga("aaaaaaaa", forma="boh"), lotto("aaaaaaaa-0000"))
        self.assertEqual(voci, [])
        self.assertTrue(any("forma='boh'" in p for p in problemi))


class ChiamataSnella(unittest.TestCase):
    """La lettura non eredita la chat e non si porta dietro il progetto.

    Il 17 settembre 2026 la stessa domanda costava 107mila token di contesto dalla
    radice del progetto e 10mila da fuori, senza strumenti: CLAUDE.md si trova
    risalendo dalla cartella di lavoro, quindi riportare `cwd` dentro il progetto
    non rompe niente e costa il doppio a ogni giro. E senza `--model`/`--effort`
    la lettura seguirebbe le impostazioni della chat.
    """

    def chiamata(self) -> tuple[list[str], dict]:
        visto = {}

        def finto_run(cmd, **kw):
            visto["cmd"], visto["kw"] = cmd, kw
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with (
            mock.patch.object(classifica, "claude", return_value="claude"),
            mock.patch.object(classifica.subprocess, "run", side_effect=finto_run),
        ):
            classifica.chiedi("domanda", classifica.MODELLO)
        return visto["cmd"], visto["kw"]

    def test_gira_fuori_dal_progetto(self):
        _, kw = self.chiamata()
        self.assertFalse(Path(kw["cwd"]).resolve().is_relative_to(percorsi.RADICE))

    def test_niente_strumenti(self):
        cmd, _ = self.chiamata()
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")

    def test_modello_ed_effort_scritti(self):
        cmd, _ = self.chiamata()
        self.assertEqual(cmd[cmd.index("--model") + 1], classifica.MODELLO)
        self.assertEqual(cmd[cmd.index("--effort") + 1], classifica.EFFORT)

    def test_la_domanda_passa_da_stdin(self):
        cmd, kw = self.chiamata()
        self.assertEqual(kw["input"], "domanda")
        self.assertNotIn("domanda", cmd)


class GiroMuto(unittest.TestCase):
    """Un giro in cui Claude non risponde a nessun lotto non esce «ok».

    Il 7 ottobre 2026 sul server ogni lotto falliva con «Not logged in», e il
    passo usciva 0: il pannello diceva ok con 31 campagne senza nicchia.
    """

    def esegui(self, risposta) -> int:
        campagna = {"id": "abcd1234-0000", "titolo": "t"}
        with tempfile.TemporaryDirectory() as cartella:
            return self._esegui(campagna, risposta, cartella)

    def _esegui(self, campagna, risposta, cartella) -> int:
        with (
            mock.patch.object(sys, "argv", ["classifica.py"]),
            mock.patch.object(
                classifica, "carica", side_effect=lambda p, d: [campagna] if d == [] else {}
            ),
            mock.patch.object(classifica, "da_decidere", return_value=[(campagna, "imp", "")]),
            mock.patch.object(classifica, "vocabolario", return_value="x"),
            mock.patch.object(classifica.tassonomia, "scheda_digest", return_value="d"),
            mock.patch.object(
                classifica.tassonomia, "rigenera_report", return_value={"senza_nicchia": []}
            ),
            mock.patch.object(classifica, "chiedi", side_effect=risposta),
            mock.patch.object(classifica, "ULTIMA", Path(cartella) / "ultima.json"),
        ):
            return classifica.main()

    def test_nessun_lotto_risponde_esce_con_errore(self):
        self.assertEqual(self.esegui(RuntimeError("Not logged in")), 1)

    def test_un_lotto_che_risponde_senza_righe_non_e_un_guasto(self):
        self.assertEqual(self.esegui(lambda *a: ""), 0)


if __name__ == "__main__":
    unittest.main()
