"""I materiali di una campagna si leggono dalla scheda, dopo l'iscrizione, e finiscono nell'archivio.

Dal sito nuovo (fra il 5 e il 27 ottobre 2026) l'API pubblica manda i materiali vuoti e la
scheda dice «Visible once you join». L'elenco `discover` del pannello li porta lo stesso per
tutte le campagne, ma e' un dato che il sito nasconde apposta: si e' scelto di non leggerlo
con l'account che incassa (31 ottobre 2026). Quindi i materiali arrivano con l'iscrizione,
che approva l'utente con «scegli», e si leggono da cio' che la scheda **mostra**.

**Perche' merita una prova.** Tutti i passaggi falliscono in silenzio: una sezione chiusa
letta come «nessun materiale» svuoterebbe l'archivio, un esito ritagliato male scriverebbe
nell'archivio una lettura mai fatta, e un'etichetta «Example clips» persa farebbe
preselezionare l'esempio al posto degli episodi.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import dettagli
import orchestrator
import sorgenti


def whop_agent():
    """Importato senza Scrapling, che non e' nel venv del progetto."""
    if "scrapling.fetchers" not in sys.modules:
        finto = types.ModuleType("scrapling.fetchers")
        finto.DynamicSession = object
        sys.modules.setdefault("scrapling", types.ModuleType("scrapling"))
        sys.modules["scrapling.fetchers"] = finto
    import whop_agent as modulo

    return modulo


# La sezione di Elliott Caldwell's Campaign 2026 come l'ha letta il 31 ottobre 2026.
ELLIOTT = {
    "testo": "Reference materials\nExample clips + starter content (optional)\n"
    "All full episodes (clip any of them)\nFull written rules (Start Here)\nBrief",
    "link": [
        [
            "Example clips + starter content (optional)",
            "https://drive.google.com/drive/folders/1yDDMbjT6TXODd-DyK1S-mlmg8o_TW4jw?usp=share_link",
        ],
        ["All full episodes (clip any of them)", "https://www.youtube.com/@samriverapodcast"],
        ["Full written rules (Start Here)", "https://whop.com/on-successful-dna/exp_j4iN0aEDHkowaa/app/"],
    ],
}
CHIUSA = {
    "testo": "Reference materials\n2 files\nVisible once you join",
    "link": [["Join Campaign", "https://whop.com/x/exp_y/app/"]],
}


class LaSezione(unittest.TestCase):
    def test_letti_con_la_loro_etichetta(self):
        esito, voci = whop_agent().materiali_da_sezione(ELLIOTT)
        self.assertEqual(esito, "letti")
        self.assertEqual(len(voci), 3)
        self.assertEqual(voci[0]["etichetta"], "Example clips + starter content (optional)")
        self.assertEqual(set(voci[0]), {"url", "tipo", "mezzo", "etichetta"})

    def test_chiusa_non_e_vuota(self):
        # Il pulsante «Join» dentro la sezione non e' un materiale: chiusa vince sui link.
        self.assertEqual(whop_agent().materiali_da_sezione(CHIUSA), ("chiusi", []))

    def test_senza_sezione_e_vuota_sono_esiti_diversi(self):
        modulo = whop_agent()
        self.assertEqual(modulo.materiali_da_sezione(None), ("senza sezione", []))
        self.assertEqual(modulo.materiali_da_sezione({"testo": "Reference materials", "link": []}), ("vuota", []))

    def test_doppioni_e_link_non_web(self):
        sezione = {"testo": "", "link": [["a", "https://x.com/1"], ["b", "https://x.com/1"], ["c", "mailto:a@b"]]}
        _esito, voci = whop_agent().materiali_da_sezione(sezione)
        self.assertEqual([v["url"] for v in voci], ["https://x.com/1"])


class LEsito(unittest.TestCase):
    def test_letti(self):
        uscita = '[log] qualcosa\nmateriali: letti\nmateriali_json: [{"url": "https://a", "etichetta": "x"}]\n'
        esito, voci = orchestrator.leggi_esito_materiali(uscita)
        self.assertEqual(esito, "letti")
        self.assertEqual(voci[0]["url"], "https://a")

    def test_senza_riga_non_e_una_lettura(self):
        self.assertEqual(orchestrator.leggi_esito_materiali("[log] morto prima\n"), ("non verificato", []))

    def test_le_voci_contano_solo_se_letti(self):
        uscita = 'materiali_json: [{"url": "https://a"}]\nmateriali: chiusi\n'
        self.assertEqual(orchestrator.leggi_esito_materiali(uscita), ("chiusi", []))

    def test_json_rotto(self):
        self.assertEqual(
            orchestrator.leggi_esito_materiali("materiali: letti\nmateriali_json: [{rotto]\n"),
            ("non verificato", []),
        )


class DallArchivioAlRisolutore(unittest.TestCase):
    """La lettura della scheda attraversa archivio e radar fino al risolutore."""

    def test_l_esempio_resta_un_esempio(self):
        _esito, voci = whop_agent().materiali_da_sezione(ELLIOTT)
        archivio = orchestrator.archivia_letti({}, "c1", voci, "2026-10-31")
        schede = [{"id": "c1", "materiali": [], "materiali_dett": []}]
        self.assertEqual(dettagli.riporta_materiali(schede, archivio), 1)
        self.assertEqual(schede[0]["materiali_letti_il"], "2026-10-31")

        link, _problemi, _altri = sorgenti.raccogli_link(schede[0], "", lambda ident, tipo: None)
        per_url = {v["url"].split("?")[0]: v for v in link}
        cartella = per_url["https://drive.google.com/drive/folders/1yDDMbjT6TXODd-DyK1S-mlmg8o_TW4jw"]
        canale = per_url["https://www.youtube.com/@samriverapodcast"]
        self.assertTrue(cartella["indizio_esempio"])
        self.assertEqual(canale["indizio_esempio"], "")


class IlChiamante(unittest.TestCase):
    """`leggi_materiali` con browser e rete sostituiti."""

    def esegui(self, uscita: str, archivio_prima: dict) -> tuple[dict, list, list]:
        with tempfile.TemporaryDirectory() as cartella:
            noti = Path(cartella) / "materiali_noti.json"
            radar = Path(cartella) / "radar_dettagli.json"
            noti.write_text(json.dumps(archivio_prima), encoding="utf-8")
            radar.write_text(json.dumps([{"id": "c1", "materiali_dett": []}]), encoding="utf-8")
            lanciati = []

            def run(cmd, **_kw):
                lanciati.append(cmd)
                return types.SimpleNamespace(stdout=uscita if "materiali" in cmd else "", stderr="")

            with (
                mock.patch.object(orchestrator, "MATERIALI_NOTI", noti),
                mock.patch.object(orchestrator, "DETTAGLI_RADAR", radar),
                mock.patch.object(orchestrator, "comando_whop", lambda g: (["agente"], None, "s")),
                mock.patch.object(orchestrator.serratura, "presa", lambda *a, **k: contextlib.nullcontext()),
                mock.patch.object(orchestrator.subprocess, "run", run),
                mock.patch.object(orchestrator, "log", lambda m: None),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                orchestrator.leggi_materiali({"id": "c1", "join": "https://j", "titolo": "T"}, "business")
            return (
                json.loads(noti.read_text(encoding="utf-8")),
                json.loads(radar.read_text(encoding="utf-8")),
                lanciati,
            )

    def test_letti_vanno_in_archivio_e_nella_scheda(self):
        uscita = 'materiali: letti\nmateriali_json: [{"url": "https://youtu.be/AAAAAAAAAAA", "etichetta": "ep"}]\n'
        archivio, radar, lanciati = self.esegui(uscita, {})
        self.assertEqual(archivio["c1"]["materiali"], ["https://youtu.be/AAAAAAAAAAA"])
        self.assertEqual(archivio["c1"]["da"], "scheda")
        self.assertEqual(radar[0]["materiali"], ["https://youtu.be/AAAAAAAAAAA"])
        self.assertIn("sorgenti.py", lanciati[-1])

    def test_una_lettura_fallita_non_tocca_l_archivio(self):
        vecchio = {"c1": {"materiali": ["https://old"], "materiali_dett": [{"url": "https://old"}], "letto_il": "2026-10-05"}}
        for uscita in ("materiali: chiusi\n", "[log] morto\n"):
            archivio, radar, lanciati = self.esegui(uscita, vecchio)
            self.assertEqual(archivio, vecchio)
            self.assertEqual(radar[0]["materiali_dett"], [])
            self.assertEqual(len(lanciati), 1)


class DopoLIscrizione(unittest.TestCase):
    """`cmd_iscrivi` legge i materiali solo quando l'iscrizione e' a posto."""

    def iscrivi(self, esito: str) -> list:
        letti = []
        with tempfile.TemporaryDirectory() as cartella:
            radar = Path(cartella) / "radar_dettagli.json"
            radar.write_text(json.dumps([{"id": "c1", "join": "https://j", "titolo": "T"}]), encoding="utf-8")
            with (
                mock.patch.object(orchestrator, "DETTAGLI_RADAR", radar),
                mock.patch.object(orchestrator, "ISCRIZIONI", Path(cartella) / "iscrizioni.json"),
                mock.patch.object(orchestrator, "comando_whop", lambda g: (["agente"], None, "s")),
                mock.patch.object(orchestrator.serratura, "presa", lambda *a, **k: contextlib.nullcontext()),
                mock.patch.object(
                    orchestrator.subprocess,
                    "run",
                    lambda *a, **k: types.SimpleNamespace(stdout=f"iscrizione: {esito}\n", stderr=""),
                ),
                mock.patch.object(orchestrator.notifica, "avvisa", lambda *a: None),
                mock.patch.object(orchestrator, "leggi_materiali", lambda s, g: letti.append(s["id"])),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                orchestrator.cmd_iscrivi(types.SimpleNamespace(campagna="c1", gruppo="business"))
        return letti

    def test_fatta_e_gia_dentro_leggono(self):
        self.assertEqual(self.iscrivi("fatta"), ["c1"])
        self.assertEqual(self.iscrivi("gia' dentro"), ["c1"])

    def test_gli_altri_esiti_no(self):
        for esito in ("da guardare", "non loggato", "prova"):
            self.assertEqual(self.iscrivi(esito), [], esito)


if __name__ == "__main__":
    unittest.main()
