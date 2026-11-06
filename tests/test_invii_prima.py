"""Prima di pubblicare si chiede a Whop se la campagna accetta invii, e «non so» ferma.

Il 16 settembre 2026 Contoso ha messo in pausa gli invii e la clip 05 e' uscita lo stesso
su YouTube e TikTok: non si poteva inviarla, la finestra e' di trenta minuti, e i due
post sono stati tolti a mano. Da allora `esegui.py` controlla una volta per cartella,
prima della prima clip. I due errori possibili non costano uguale — una clip ferma per
niente esce al giro dopo, una uscita a campagna chiusa non si recupera — quindi solo
«aperti» lascia passare.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import esegui
import orchestrator


def whop_agent():
    """Importato senza Scrapling, che non e' nel venv del progetto."""
    if "scrapling.fetchers" not in sys.modules:
        finto = types.ModuleType("scrapling.fetchers")
        finto.DynamicSession = object
        sys.modules.setdefault("scrapling", types.ModuleType("scrapling"))
        sys.modules["scrapling.fetchers"] = finto
    import whop_agent as modulo

    return modulo


class Decisione(unittest.TestCase):
    def test_solo_aperti_pubblica(self):
        self.assertEqual(esegui.decidi_invii("aperti"), "pubblica")
        # Senza campagna `orchestrator.py pubblica` si ferma da solo e dice perche'.
        self.assertEqual(esegui.decidi_invii("nessuna campagna"), "pubblica")

    def test_pausa_e_chiusura_fermano_senza_guasto(self):
        self.assertEqual(esegui.decidi_invii("in pausa"), "ferma")
        self.assertEqual(esegui.decidi_invii("chiusi"), "ferma")

    def test_non_sapere_non_e_un_permesso(self):
        for esito in ("non verificato", "non leggibile", "sconosciuto (paused)", "", "Aperti"):
            with self.subTest(esito=esito):
                self.assertEqual(esegui.decidi_invii(esito), "guasto")


class DaWhop(unittest.TestCase):
    """`whop_agent.esito_invii`: aperti vuol dire `intake: open`, e nient'altro."""

    def test_i_quattro_esiti(self):
        esito = whop_agent().esito_invii
        self.assertEqual(esito({"intake": "open"}), "aperti")
        self.assertEqual(esito({"intake": "closed", "intakeClosedReason": "hold"}), "in pausa")
        self.assertEqual(esito({"intake": "closed", "intakeClosedReason": "budget"}), "chiusi")
        self.assertEqual(esito(None), "non leggibile")

    def test_un_valore_mai_visto_non_arriva_a_pubblicare(self):
        esito = whop_agent().esito_invii({"intake": "paused"})
        self.assertNotEqual(esito, "aperti")
        self.assertEqual(esegui.decidi_invii(esito), "guasto")


class Lettura(unittest.TestCase):
    """`orchestrator.leggi_esito_invii`: senza la riga, nessuno ha guardato."""

    def test_la_riga_si_legge(self):
        uscita = (
            "[2026-09-17 19:15:02] stato degli invii: invii in pausa — la campagna ...\n"
            "[2026-09-17 19:15:02] esito invii: in pausa\n"
        )
        self.assertEqual(orchestrator.leggi_esito_invii(uscita), "in pausa")

    def test_la_frase_per_chi_legge_non_basta(self):
        """«stato degli invii: aperti» e' la frase umana: senza la riga d'esito non vale."""
        uscita = "[2026-09-17 19:15:02] stato degli invii: aperti\n"
        self.assertEqual(orchestrator.leggi_esito_invii(uscita), "non verificato")
        self.assertEqual(orchestrator.leggi_esito_invii(""), "non verificato")

    def test_vale_l_ultima(self):
        uscita = "esito invii: non leggibile\nesito invii: aperti\n"
        self.assertEqual(orchestrator.leggi_esito_invii(uscita), "aperti")


class Iscrizione(unittest.TestCase):
    """`orchestrator.leggi_esito_iscrizione`: l'esito di `join`, e «non so» non e' «fatta»."""

    def test_la_riga_si_legge(self):
        uscita = "[00:41:17] la scheda ha gia' «Submit clip»\niscrizione: gia' dentro\n"
        self.assertEqual(orchestrator.leggi_esito_iscrizione(uscita), "gia' dentro")
        self.assertIn("gia' dentro", orchestrator.ISCRIZIONE_A_POSTO)

    def test_senza_riga_non_e_fatta(self):
        # La frase del log non e' la riga d'esito: dentro una frase non vale.
        uscita = "[00:41:17] l'iscrizione: fatta per meta'\n"
        self.assertEqual(orchestrator.leggi_esito_iscrizione(uscita), "non verificato")
        self.assertNotIn("non verificato", orchestrator.ISCRIZIONE_A_POSTO)
        self.assertNotIn("da guardare", orchestrator.ISCRIZIONE_A_POSTO)


class StessaSerratura(unittest.TestCase):
    """Controllo e invio aprono lo stesso profilo: il nome della serratura e' quello del profilo."""

    def test_il_nome_viene_dal_profilo_non_dal_gruppo(self):
        profili = {
            "laboratorio": Path("publish/.whop-profile"),
            "business": Path("publish/.whop-business"),
        }
        with mock.patch.object(
            orchestrator.registro, "whop_profilo", side_effect=lambda g: profili.get(g)
        ):
            self.assertEqual(orchestrator.whop_di("laboratorio")[1], orchestrator.whop_di(None)[1])
            self.assertEqual(orchestrator.whop_di("business")[1], "whop-.whop-business")


class Giro(unittest.TestCase):
    """`esegui.main`: il controllo sta prima della prima pubblicazione."""

    def cartella(self) -> Path:
        d = Path(tempfile.mkdtemp())
        (d / "publish.json").write_text(
            json.dumps(
                {
                    "items": [
                        {"id": "06", "quando": "2026-09-16T19:00"},
                        {"id": "07", "quando": "2026-09-16T21:00"},
                    ],
                    "calendario_piattaforme": ["youtube", "tiktok", "instagram"],
                }
            ),
            encoding="utf-8",
        )
        return d

    def giro(self, esito: str) -> tuple[int, list, dict]:
        d = self.cartella()
        pubblicate = []

        def finta_pubblica(cartella, cid, piattaforma, davvero):
            pubblicate.append((cid, piattaforma))
            return True, "ok"

        uscita = io.StringIO()
        with (
            mock.patch.object(esegui, "controlla_invii", return_value=esito),
            mock.patch.object(esegui, "pubblica", side_effect=finta_pubblica),
            mock.patch.object(sys, "argv", ["esegui.py", "--cartella", str(d), "--davvero"]),
            redirect_stdout(uscita),
            redirect_stderr(io.StringIO()),
        ):
            with self.assertRaises(SystemExit) as fine:
                esegui.main()
        return fine.exception.code, pubblicate, json.loads(uscita.getvalue())

    def test_aperti_pubblica(self):
        codice, pubblicate, conto = self.giro("aperti")
        self.assertEqual(len(pubblicate), 6)
        self.assertEqual(codice, 0)

    def test_in_pausa_niente_esce_e_non_e_un_guasto(self):
        codice, pubblicate, conto = self.giro("in pausa")
        self.assertEqual(pubblicate, [])
        self.assertEqual(conto["ferme"], 2)
        self.assertEqual(codice, 0)

    def test_non_verificato_niente_esce_e_l_attivita_si_tinge_di_rosso(self):
        codice, pubblicate, conto = self.giro("non verificato")
        self.assertEqual(pubblicate, [])
        self.assertEqual(conto["fallite"], 2)
        self.assertEqual(codice, 1)


if __name__ == "__main__":
    unittest.main()
