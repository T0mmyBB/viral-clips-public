"""Una clip sospesa a mano non esce, qualunque cosa dica il suo `quando`.

Il 16 settembre 2026 Contoso ha chiuso gli invii Whop: la clip 05 e' stata ritirata da
YouTube e TikTok e tenuta ferma per ripubblicarla su tutte e tre quando la campagna
riapre. La sua data era gia' passata, quindi senza `sospesa` l'esecutore l'avrebbe
rimessa fuori al passaggio successivo — con la campagna ancora chiusa, cioe' una clip
che non si puo' piu' inviare.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import esegui
import registro
import stato


class Scadute(unittest.TestCase):
    def cartella(self, items: list[dict]) -> Path:
        d = Path(tempfile.mkdtemp())
        (d / "publish.json").write_text(
            json.dumps({"items": items, "calendario_piattaforme": ["youtube", "tiktok"]}),
            encoding="utf-8",
        )
        return d

    def test_la_sospesa_non_esce_anche_se_scaduta(self):
        d = self.cartella(
            [
                {"id": "05", "quando": "2026-09-16T19:00", "sospesa": "invii in pausa"},
                {"id": "06", "quando": "2026-09-16T19:00"},
            ]
        )
        fuori = esegui.scadute(d, datetime(2026, 9, 16, 21, 15))
        self.assertEqual([c["id"] for c in fuori], ["06"])

    def test_tolto_il_campo_torna_in_calendario(self):
        d = self.cartella([{"id": "05", "quando": "2026-09-16T19:00"}])
        fuori = esegui.scadute(d, datetime(2026, 9, 16, 21, 15))
        self.assertEqual([c["id"] for c in fuori], ["05"])
        self.assertEqual(fuori[0]["mancanti"], ["youtube", "tiktok"])


class AMano(unittest.TestCase):
    """`a_mano` toglie una piattaforma sola: la prova TikTok del 29 settembre 2026."""

    def test_la_piattaforma_a_mano_non_esce_le_altre_si(self):
        d = Scadute.cartella(
            None, [{"id": "01", "quando": "2026-09-29T19:00", "a_mano": {"tiktok": "prova"}}]
        )
        fuori = esegui.scadute(d, datetime(2026, 9, 29, 19, 15))
        self.assertEqual(fuori[0]["mancanti"], ["youtube"])

    def test_solo_a_mano_rimasta_non_e_scaduta(self):
        d = Scadute.cartella(
            None, [{"id": "01", "quando": "2026-09-29T19:00", "a_mano": {"tiktok": "prova"}}]
        )
        (d / "publish_state.json").write_text(
            json.dumps(
                {"items": {"01@youtube": {"platform": "youtube", "status": "pubblicato"}}}
            ),
            encoding="utf-8",
        )
        self.assertEqual(esegui.scadute(d, datetime(2026, 9, 29, 19, 15)), [])


class PiattaformaSospesa(unittest.TestCase):
    """
    `pubblicazione_sospesa` nel registro ferma una piattaforma intera.

    Il 2 ottobre 2026 Instagram e' stato fermato in attesa dell'API di Meta: le
    clip dovevano continuare a uscire su YouTube e TikTok, in tutte le cartelle
    del gruppo e in quelle non ancora nate. `sospesa` ferma la clip intera, e
    `a_mano` va riscritto clip per clip e chiede a una persona di pubblicare.
    """

    ADESSO = datetime(2026, 10, 2, 21, 15)

    def registro_con(self, valore):
        voci = [
            {"id": f"business-{p}", "gruppo": "business", "piattaforma": p}
            for p in ("youtube", "tiktok", "instagram")
        ]
        if valore is not None:
            voci[2]["pubblicazione_sospesa"] = valore
        return mock.patch.object(registro, "_leggi", lambda: {"identita": voci})

    def cartella(self, items, stato=None) -> Path:
        d = Path(tempfile.mkdtemp())
        (d / "publish.json").write_text(
            json.dumps(
                {
                    "gruppo": "business",
                    "items": items,
                    "calendario_piattaforme": ["youtube", "tiktok", "instagram"],
                }
            ),
            encoding="utf-8",
        )
        if stato:
            (d / "publish_state.json").write_text(json.dumps({"items": stato}), encoding="utf-8")
        return d

    def scadute(self, d, valore="in attesa dell'API"):
        self.righe = []
        with self.registro_con(valore), mock.patch.object(esegui, "log", self.righe.append):
            return esegui.scadute(d, self.ADESSO)

    def test_il_motivo_si_legge_dal_registro(self):
        for valore, atteso in (
            ("in attesa dell'API", "in attesa dell'API"),
            (True, "sospesa"),
            ("   ", "sospesa"),
            (None, None),
            ("", None),
            (False, None),
        ):
            with self.registro_con(valore):
                self.assertEqual(registro.sospesa("business", "instagram"), atteso, valore)
                self.assertIsNone(registro.sospesa("business", "tiktok"))

    def test_la_piattaforma_sospesa_non_esce_le_altre_si(self):
        d = self.cartella([{"id": "10", "quando": "2026-10-02T21:00"}])
        fuori = self.scadute(d)
        self.assertEqual(fuori[0]["mancanti"], ["youtube", "tiktok"])

    def test_tolto_il_campo_la_clip_rimasta_indietro_torna_a_uscire(self):
        d = self.cartella([{"id": "10", "quando": "2026-10-02T21:00"}])
        self.assertEqual(self.scadute(d, valore=None)[0]["mancanti"], ["youtube", "tiktok", "instagram"])

    def test_rimasta_solo_la_sospesa_la_clip_non_e_scaduta(self):
        # Altrimenti ogni giro la conterebbe fra il lavoro da fare, e la
        # lancerebbe con zero piattaforme.
        fatte = {
            "10@youtube": {"platform": "youtube", "status": "pubblicato"},
            "10@tiktok": {"platform": "tiktok", "status": "pubblicato"},
        }
        d = self.cartella([{"id": "10", "quando": "2026-10-02T21:00"}], fatte)
        self.assertEqual(self.scadute(d), [])

    def test_si_dice_a_ogni_giro_quante_clip_aspettano(self):
        # Tolta in silenzio, una piattaforma ferma sembrerebbe finita.
        fatta = {"09@instagram": {"platform": "instagram", "status": "pubblicato"}}
        d = self.cartella(
            [
                {"id": "09", "quando": "2026-10-02T19:00"},
                {"id": "10", "quando": "2026-10-02T21:00"},
                {"id": "11", "quando": "2026-10-02T20:00", "a_mano": {"instagram": "dal telefono"}},
                {"id": "12", "quando": "2026-10-03T19:00"},
            ],
            fatta,
        )
        self.scadute(d)
        avvisi = [r for r in self.righe if "sospesa nel registro" in r]
        self.assertEqual(len(avvisi), 1)
        # Solo la 10: la 09 e' gia' uscita, la 11 e' a mano, la 12 non e' scaduta.
        self.assertIn("1 clip", avvisi[0])
        self.assertIn("in attesa dell'API", avvisi[0])

    def test_una_cartella_senza_gruppo_non_ha_niente_di_sospeso(self):
        d = Scadute.cartella(None, [{"id": "05", "quando": "2026-09-16T19:00"}])
        with self.registro_con("in attesa dell'API"):
            fuori = esegui.scadute(d, self.ADESSO)
        self.assertEqual(fuori[0]["mancanti"], ["youtube", "tiktok"])


class NelPannello(unittest.TestCase):
    """Una piattaforma ferma non e' «in ritardo»: il pannello lo deve dire com'e'."""

    FERME = {"instagram"}

    def test_cio_che_non_e_uscito_su_una_piattaforma_ferma_e_sospeso(self):
        self.assertEqual(stato.stato_coppia({}, "instagram", None, self.FERME), "sospesa")
        # Anche un tentativo rimasto a meta': nessuno lo riprovera' finche' e' ferma.
        self.assertEqual(stato.stato_coppia({"status": "errore_url"}, "instagram", None, self.FERME), "sospesa")
        self.assertEqual(stato.stato_coppia({}, "instagram", {"instagram": "dal telefono"}, self.FERME), "sospesa")

    def test_cio_che_e_gia_uscito_resta_uscito(self):
        self.assertEqual(stato.stato_coppia({"status": "pubblicato"}, "instagram", None, self.FERME), "pubblicato")

    def test_le_altre_piattaforme_non_cambiano(self):
        self.assertEqual(stato.stato_coppia({}, "tiktok", None, self.FERME), "in coda")
        self.assertEqual(stato.stato_coppia({}, "tiktok", {"tiktok": "prova"}, self.FERME), "a mano")
        self.assertEqual(stato.stato_coppia({"status": "errore_url"}, "tiktok", None, self.FERME), "errore_url")
        self.assertEqual(stato.stato_coppia({}, "instagram", None, ()), "in coda")


class PortaSospesa(unittest.TestCase):
    """L'orchestratore e' la porta: la sospensione vale anche per chi non passa dall'esecutore."""

    def setUp(self):
        import orchestrator

        self.o = orchestrator
        self.cartella = Path(tempfile.mkdtemp())
        (self.cartella / "publish.json").write_text(
            json.dumps(
                {
                    "campaign": "Una campagna",
                    "gruppo": "business",
                    "whop_campaign_uuid": "uuid-1",
                    "whop_app": "https://whop.com/x/app/",
                    "handles": {"Instagram": "@x"},
                    "items": [{"id": "10", "file": "10.mp4", "title": "t", "caption": "c @x"}],
                }
            ),
            encoding="utf-8",
        )
        self.lanci = []

        def run(cmd, **_kw):
            self.lanci.append(cmd)
            return SimpleNamespace(stdout="", stderr="", returncode=0)

        @contextmanager
        def presa(*_a, **_k):
            yield

        for t in (
            mock.patch.object(orchestrator, "log", lambda *_: None),
            mock.patch.object(orchestrator, "print", lambda *_a, **_k: None, create=True),
            mock.patch.object(orchestrator, "cadenza", lambda *_: None),
            mock.patch.object(orchestrator, "interprete", lambda *_: "python"),
            mock.patch.object(orchestrator, "profilo_pubblicazione", lambda *_: self.cartella),
            mock.patch.object(orchestrator.subprocess, "run", run),
            mock.patch.object(orchestrator.serratura, "presa", presa),
            mock.patch.object(registro, "via", lambda *_: "browser"),
            mock.patch.object(registro, "handle", lambda *_: "x"),
        ):
            t.start()
            self.addCleanup(t.stop)

    def pubblica(self, sospesa, dry_run=False):
        with mock.patch.object(registro, "sospesa", lambda *_: sospesa):
            self.o.cmd_pubblica(
                SimpleNamespace(
                    folder=str(self.cartella), id="10", platform="instagram", dry_run=dry_run, forza=False
                )
            )

    def test_sospesa_non_parte_nessun_agente_e_non_si_scrive_stato(self):
        with self.assertRaises(SystemExit) as presa:
            self.pubblica("in attesa dell'API")
        self.assertIn("in attesa dell'API", str(presa.exception.code))
        self.assertEqual(self.lanci, [])
        self.assertFalse((self.cartella / "publish_state.json").exists())

    def test_la_prova_resta_possibile(self):
        # Non mette fuori niente, e serve a collaudare la strada prima di riaprirla.
        self.pubblica("in attesa dell'API", dry_run=True)
        self.assertEqual(len(self.lanci), 1)
        self.assertIn("--prova", self.lanci[0])

    def test_non_sospesa_parte(self):
        self.pubblica(None, dry_run=True)
        self.assertEqual(len(self.lanci), 1)


if __name__ == "__main__":
    unittest.main()
