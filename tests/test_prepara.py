"""Nel `publish.json` di una consegna restano solo i campi chiesti, e quelli che c'erano gia'.

Il prompt delle didascalie indica come modello il `publish.json` di una consegna chiusa
di un'altra campagna, perche' ne serve la forma. Il 30 settembre 2026 Sonnet 5.5 ne ha
ricopiato `platform`, `submit_within_minutes` e `batch_size` anche con «prendine la
forma, non i valori» scritto nel prompt: `platform` e' il campo vecchio che faceva
contare 7 pubblicazioni dove il piano ne ha 21. La regola sta in
`prepara.togli_campi_estranei`, e il secondo gruppo di test prova che
`scrivi_consegna` la chiama con il file **di prima** — letto dopo la scrittura,
sembrerebbe gia' tutto «di prima» e non si toglierebbe niente.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import prepara

CHIESTI = {k: f"valore di {k}" for k in prepara.CAMPI_CONSEGNA}
ITEMS = [{"id": "01", "file": "01-a.mp4", "title": "t", "caption": "c"}]
DAL_MODELLO = {"platform": "YouTube", "submit_within_minutes": 30, "batch_size": 1}


class CampiEstranei(unittest.TestCase):
    def test_i_campi_copiati_dal_modello_si_tolgono(self):
        pulito, tolti = prepara.togli_campi_estranei(
            {**CHIESTI, "items": ITEMS, **DAL_MODELLO}, None
        )
        self.assertEqual(tolti, sorted(DAL_MODELLO))
        self.assertEqual(set(pulito), {*CHIESTI, "items"})

    def test_i_campi_chiesti_restano_tutti(self):
        nuovo = {**CHIESTI, "items": ITEMS}
        pulito, tolti = prepara.togli_campi_estranei(nuovo, None)
        self.assertEqual(tolti, [])
        self.assertEqual(pulito, nuovo)

    def test_quello_che_cera_prima_resta(self):
        prima = {"items": ITEMS, "calendario_piattaforme": ["youtube"], "account_started": {}}
        nuovo = {**CHIESTI, **prima, "platform": "YouTube"}
        pulito, tolti = prepara.togli_campi_estranei(nuovo, prima)
        self.assertEqual(tolti, ["platform"])
        self.assertEqual(pulito["calendario_piattaforme"], ["youtube"])
        self.assertIn("account_started", pulito)


class ScriviConsegna(unittest.TestCase):
    """Il chiamante: l'agente e' sostituito da una funzione che scrive i file."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cartella = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def scrivi(self, scritto_dall_agente: dict) -> dict:
        def agente(cmd, **kw):
            (self.cartella / "publish.json").write_text(
                json.dumps(scritto_dall_agente), encoding="utf-8"
            )
            (self.cartella / "clips.md").write_text("# clip", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0)

        with mock.patch.object(prepara, "trova_claude", return_value="claude"), mock.patch.object(
            prepara.subprocess, "run", agente
        ), mock.patch.object(prepara, "log"):
            prepara.scrivi_consegna(self.cartella, None)
        return json.loads((self.cartella / "publish.json").read_text(encoding="utf-8"))

    def test_la_consegna_nuova_perde_i_campi_del_modello(self):
        finale = self.scrivi({**CHIESTI, "items": ITEMS, **DAL_MODELLO})
        self.assertEqual(set(finale), {*CHIESTI, "items"})

    def test_un_rifai_non_cancella_il_calendario(self):
        (self.cartella / "publish.json").write_text(
            json.dumps({"items": ITEMS, "calendario_piattaforme": ["youtube", "tiktok"]}),
            encoding="utf-8",
        )
        finale = self.scrivi(
            {**CHIESTI, "items": ITEMS, "calendario_piattaforme": ["youtube", "tiktok"],
             "platform": "YouTube"}
        )
        self.assertEqual(finale["calendario_piattaforme"], ["youtube", "tiktok"])
        self.assertNotIn("platform", finale)


class Mp4Rotti(unittest.TestCase):
    """La consegna non si scrive su file inservibili: la sonda e' sostituita."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cartella = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_un_mp4_vuoto_e_rotto(self):
        (self.cartella / "01-a.mp4").write_bytes(b"")
        rotti = prepara.mp4_rotti(self.cartella, None, sonda=lambda f: {})
        self.assertEqual(len(rotti), 1)
        self.assertIn("01-a.mp4", rotti[0])

    def test_una_clip_sospesa_non_si_guarda(self):
        (self.cartella / "01-a.mp4").write_bytes(b"")
        prima = {"items": [{"id": "01", "file": "01-a.mp4", "sospesa": True}]}
        self.assertEqual(prepara.mp4_rotti(self.cartella, prima, sonda=lambda f: {}), [])

    def test_il_chiamante_si_ferma_prima_dell_agente(self):
        (self.cartella / "01-a.mp4").write_bytes(b"")
        with mock.patch.object(prepara.subprocess, "run") as agente, mock.patch.object(
            prepara, "trova_claude", return_value="claude"
        ), mock.patch.object(prepara, "log"):
            with self.assertRaises(SystemExit) as e:
                prepara.scrivi_consegna(self.cartella, None, sonda=lambda f: {})
        agente.assert_not_called()
        self.assertIn("01-a.mp4", str(e.exception))


class RadiceWhop(unittest.TestCase):
    """`whop_app` lo scrive il codice, dal provider della campagna e non da un modello.

    Osservazioni 111 e 144: la radice `/<slug>/<exp>/app/` valeva solo per il primo
    provider, e su un provider nuovo l'agente ricopiava quella di un altro.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.lavoro = self.dir / "work"
        self.lavoro.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def campagna(self, exp):
        (self.lavoro / "campagna.json").write_text(
            json.dumps({"join": f"https://whop.com/experiences/{exp}/campaigns/uuid-1"}),
            encoding="utf-8",
        )

    def consegna(self, nome, whop_app):
        c = self.dir / nome
        c.mkdir()
        (c / "publish.json").write_text(json.dumps({"whop_app": whop_app}), encoding="utf-8")
        return c

    def test_senza_consegne_prima_si_ricava_dal_join(self):
        self.campagna("exp_nuovo")
        self.assertEqual(
            prepara.radice_whop(self.lavoro, []), "https://whop.com/experiences/exp_nuovo/app/"
        )

    def test_una_consegna_dello_stesso_provider_da_lo_slug(self):
        self.campagna("exp_uno")
        altre = [self.consegna("a", "https://whop.com/slug-uno/exp_uno/app/campaigns/x")]
        self.assertEqual(
            prepara.radice_whop(self.lavoro, altre), "https://whop.com/slug-uno/exp_uno/app/"
        )

    def test_la_radice_di_un_altro_provider_non_si_prende(self):
        self.campagna("exp_due")
        altre = [self.consegna("a", "https://whop.com/slug-uno/exp_uno/app/")]
        self.assertEqual(
            prepara.radice_whop(self.lavoro, altre), "https://whop.com/experiences/exp_due/app/"
        )

    def test_senza_campagna_non_si_indovina(self):
        self.assertIsNone(prepara.radice_whop(None, []))
        (self.lavoro / "campagna.json").write_text(
            json.dumps({"join": "https://app.altrofornitore.com/campaigns/abc"}), encoding="utf-8"
        )
        self.assertIsNone(prepara.radice_whop(self.lavoro, []))


class WhopAppNelChiamante(unittest.TestCase):
    """Il chiamante: qualunque cosa scriva l'agente in `whop_app`, resta quella del codice."""

    def test_l_agente_copia_il_provider_sbagliato(self):
        with tempfile.TemporaryDirectory() as d:
            cartella = Path(d) / "out"
            cartella.mkdir()
            lavoro = Path(d) / "work"
            lavoro.mkdir()
            (lavoro / "campagna.json").write_text(
                json.dumps({"join": "https://whop.com/experiences/exp_due/campaigns/u"}),
                encoding="utf-8",
            )

            def agente(cmd, **kw):
                (cartella / "publish.json").write_text(
                    json.dumps(
                        {**CHIESTI, "items": ITEMS, "whop_app": "https://whop.com/altro/exp_uno/app/"}
                    ),
                    encoding="utf-8",
                )
                (cartella / "clips.md").write_text("# clip", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 0)

            with mock.patch.object(prepara, "trova_claude", return_value="claude"), mock.patch.object(
                prepara.subprocess, "run", agente
            ), mock.patch.object(prepara, "log"), mock.patch.object(prepara, "RADICE", Path(d)):
                prepara.scrivi_consegna(cartella, lavoro)
            finale = json.loads((cartella / "publish.json").read_text(encoding="utf-8"))
        self.assertEqual(finale["whop_app"], "https://whop.com/experiences/exp_due/app/")


if __name__ == "__main__":
    unittest.main()
