"""Il banco dei modelli: la copia non deve contenere la risposta, e il messaggio e' quello vero.

**Perche' questa merita una prova.** Una prova alla cieca che vede la risposta, o
che parla all'agente con un messaggio diverso dalla produzione, non fallisce:
misura un'altra cosa e restituisce un numero credibile. Tutte e due le cose sono
gia' successe — l'agente ha trovato e letto la consegna vera, e la copia a mano del
messaggio in `agente.py` si era staccata da `automatico.py`. Qui si fissano: cosa
si toglie dalla copia, cosa no, e che produzione e banco leggono lo stesso
messaggio dalla stessa funzione.

Le spie dei test sono inventate: quelle vere stanno in `banco.SPIE`.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

sys.path.insert(0, str(percorsi.RADICE / "tools" / "banco_modelli"))

import agente  # noqa: E402
import automatico  # noqa: E402
import banco  # noqa: E402
import motori  # noqa: E402
import prepara  # noqa: E402

SPIE = ("risposta segreta",)


class TogliSezioni(unittest.TestCase):
    def test_la_sezione_con_la_spia_esce_intera(self):
        testo = "# Titolo\n### 22 settembre: prova\nil modello ha detto\nla RISPOSTA SEGRETA\n### 23 settembre\naltro\n"
        fuori = banco.togli_sezioni(testo, SPIE)
        self.assertNotIn("22 settembre", fuori)
        self.assertNotIn("il modello ha detto", fuori)  # la riga accanto dice la stessa cosa
        self.assertIn("### 23 settembre\naltro", fuori)

    def test_la_voce_d_elenco_esce_con_le_sue_righe_rientrate(self):
        testo = "## Cosa fare\n0. **La consegna X**\n   18 clip in output/x\n   risposta segreta qui\n1. **Altro**\n   resta\n"
        fuori = banco.togli_sezioni(testo, SPIE)
        self.assertNotIn("18 clip", fuori)
        self.assertIn("1. **Altro**\n   resta", fuori)
        self.assertIn("## Cosa fare", fuori)

    def test_fuori_da_sezioni_e_voci_si_toglie_solo_la_riga(self):
        # un capitolo di CLAUDE.md serve all'agente: non lo si butta per una riga
        testo = "## Capitolo\nprima riga utile\nqui c'e' la risposta segreta\nterza riga utile\n"
        fuori = banco.togli_sezioni(testo, SPIE)
        self.assertEqual(fuori, "## Capitolo\nprima riga utile\nterza riga utile\n")

    def test_senza_spie_il_testo_non_cambia(self):
        testo = "# A\n### B\n- voce\n  seguito\n\ntesto\n"
        self.assertEqual(banco.togli_sezioni(testo, SPIE), testo)

    def test_i_crlf_restano_crlf(self):
        testo = "## A\r\nuno\r\nrisposta segreta\r\ndue\r\n"
        self.assertEqual(banco.togli_sezioni(testo, SPIE), "## A\r\nuno\r\ndue\r\n")

    def test_maiuscole_e_minuscole_non_contano(self):
        self.assertTrue(banco.ha_spia("La Risposta Segreta.", SPIE))


class CommentiModello(unittest.TestCase):
    def test_il_blocco_sopra_modello_esce(self):
        testo = "ATTESA = 5\n\n# misurato il 22: Opus ha sbagliato qui\n#\n# e qui\nMODELLO = \"x\"\nEFFORT = \"y\"\n"
        fuori = banco.togli_commenti_modello(testo)
        self.assertNotIn("Opus ha sbagliato", fuori)
        self.assertEqual(fuori, "ATTESA = 5\n\nMODELLO = \"x\"\nEFFORT = \"y\"\n")

    def test_senza_modello_non_cambia(self):
        self.assertEqual(banco.togli_commenti_modello("# solo\nA = 1\n"), "# solo\nA = 1\n")


class TrovaSpie(unittest.TestCase):
    def test_trova_nel_codice_e_nei_documenti_ma_non_nel_materiale(self):
        with tempfile.TemporaryDirectory() as tmp:
            radice = Path(tmp)
            (radice / "docs").mkdir()
            (radice / "docs" / "nota.md").write_text("ok\nla Risposta Segreta\n", encoding="utf-8")
            (radice / "x.py").write_text("# risposta segreta\n", encoding="utf-8")
            for cartella in ("work/v", "output/v"):
                (radice / cartella).mkdir(parents=True)
                # il transcript dice quello che Whisper ha sentito: e' l'input della prova
                (radice / cartella / "transcript.json").write_text('"risposta segreta"', encoding="utf-8")
            trovate = banco.trova_spie(radice, SPIE)
        self.assertEqual(len(trovate), 2)
        self.assertTrue(any(t.startswith(str(Path("docs/nota.md")) + ":2:") for t in trovate))
        self.assertFalse(any(t.startswith(("work", "output")) for t in trovate))


class Destinazione(unittest.TestCase):
    def test_dentro_il_progetto_no(self):
        # Claude risale le cartelle: da li' leggerebbe il CLAUDE.md vero
        with self.assertRaises(SystemExit):
            # `docs` e non `work`: sul server `work` e' un collegamento al disco dei
            # dati, e risolto sta fuori dal progetto davvero
            banco.destinazione("x", base=banco.RADICE / "docs")

    def test_nella_cartella_temporanea_si(self):
        self.assertEqual(banco.destinazione("x", base=Path(tempfile.gettempdir())).name, "x")


class StessoMessaggio(unittest.TestCase):
    """Produzione e banco leggono il messaggio dalla stessa funzione, dai due lati."""

    def test_il_banco_usa_le_funzioni_della_produzione(self):
        with mock.patch.object(automatico, "messaggio_decisioni", lambda w: "DECISIONI"), mock.patch.object(
            prepara, "messaggio_consegna", lambda c, w: "CONSEGNA"
        ):
            self.assertEqual(agente.messaggio("decisioni", Path("w"), None), "DECISIONI")
            self.assertEqual(agente.messaggio("didascalie", Path("w"), Path("c")), "CONSEGNA")

    def test_la_scelta_delle_clip_usa_messaggio_decisioni(self):
        chiesti = []
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            automatico, "messaggio_decisioni", lambda w: "DECISIONI"
        ), mock.patch.object(
            automatico, "chiedi_a_claude", lambda m, attesa: chiesti.append(m)
        ), mock.patch.object(automatico, "valida", lambda w: (True, "")):
            automatico.fase_decisioni(Path(tmp))
        self.assertEqual(chiesti, ["DECISIONI"])

    def test_la_consegna_usa_messaggio_consegna(self):
        visto = {}

        def fermo(cmd, **kw):
            visto["messaggio"] = cmd[2]
            raise SystemExit("fermo")

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            prepara, "messaggio_consegna", lambda c, w: "CONSEGNA"
        ), mock.patch.object(prepara, "mp4_rotti", lambda *a, **k: []), mock.patch.object(
            prepara, "trova_claude", lambda: "claude"
        ), mock.patch.object(prepara.subprocess, "run", fermo):
            with self.assertRaises(SystemExit):
                prepara.scrivi_consegna(Path(tmp), Path(tmp))
        self.assertEqual(visto["messaggio"], "CONSEGNA")


class MotoreAgy(unittest.TestCase):
    """agy risponde in NDJSON: conta l'evento `result`, e il prompt passa da stdin."""

    def chiama(self, righe, returncode=0):
        visto = {}

        def finto(cmd, **kw):
            visto["cmd"], visto["input"] = cmd, kw["input"]
            return mock.Mock(stdout="\n".join(righe), stderr="", returncode=returncode)

        with mock.patch.object(motori, "agy_exe", lambda: "agy"), mock.patch.object(
            motori.subprocess, "run", finto
        ):
            return motori.agy("PROMPT LUNGO", "gemini-4-argon", "high", ".", 60), visto

    def test_legge_il_result_e_lo_traduce(self):
        d, visto = self.chiama([
            '{"event":"init"}',
            '{"event":"step_update","step_update":{"text_delta":"x"}}',
            '{"event":"result","result":{"status":"SUCCESS","response":"FATTO","num_turns":3,'
            '"usage":{"input_tokens":10,"output_tokens":5,"thinking_tokens":2,"cache_read_tokens":7}}}',
        ])
        self.assertEqual(d["result"], "FATTO")
        self.assertEqual(d["num_turns"], 3)
        self.assertEqual(d["usage"]["output_tokens_details"]["thinking_tokens"], 2)
        self.assertEqual(d["usage"]["cache_read_input_tokens"], 7)
        self.assertIn("gemini-4-argon-high", visto["cmd"])
        self.assertNotIn("PROMPT LUNGO", " ".join(visto["cmd"]))  # da stdin, non dalla riga di comando
        self.assertIn("PROMPT LUNGO", visto["input"])

    def test_nessuna_approvazione_totale(self):
        _, visto = self.chiama(['{"event":"result","result":{"status":"SUCCESS","response":""}}'])
        self.assertNotIn("--dangerously-skip-permissions", visto["cmd"])

    def test_errore_senza_result(self):
        d, _ = self.chiama(["authentication required"], returncode=1)
        self.assertIn("authentication required", d["errore"])
        self.assertNotIn("result", d)


if __name__ == "__main__":
    unittest.main()
