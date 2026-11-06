"""Le analitiche per post: cosa si legge dalle risposte delle piattaforme, e come si mostra.

**Perche' questa merita una prova.** Le risposte di Studio usano due convenzioni
diverse — TikTok quote fra 0 e 1 e secondi, YouTube percentuali fra 0 e 100 e
millisecondi — e un campo «non disponibile» arriva con `status: 2` invece che
mancante. Sbagliare uno dei due non da' errore: da' un 8600% o un post a zero
spettatori che in verita' non si sapeva, cioe' proprio il numero che si guarda per
capire perche' un post non distribuisce. Le forme qui sotto sono ridotte da
risposte vere del 30 settembre e del 1 ottobre 2026.
"""

from __future__ import annotations

import importlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import analitiche  # noqa: E402  (dashboard/)
import leggi_analitiche as la  # noqa: E402  (publish/)
import metriche  # noqa: E402

INSIGHT_APERTURA = {
    "extra": {"now": 1},
    "status_code": 0,
    "realtime_total_video_views": {"value": {"status": 0, "value": 836}},
    "video_per_duration_realtime": {"value": {"status": 0, "value": 8.43}},
    "video_finish_rate_realtime": {"value": {"status": 0, "value": 0.0129}},
    "video_traffic_source_percent_realtime": {
        "value": {"status": 0, "value": [{"key": "For You", "value": 0.993}, {"key": "Personal Profile", "value": 0.007}]}
    },
    "video_retention_rate_realtime": {
        "value": {"list": [{"timestamp": "0", "value": 1}, {"timestamp": "3000", "value": 0.46}]}
    },
    "realtime_video_view_history": {"list": [{"key": "1790701200", "value": 6}]},
    "video_uv": None,
}
INSIGHT_VIEWERS = {
    "video_uv": {"status": 0, "value": 426},
    "video_new_followers": {"status": 2, "value": 0},  # lo status vince sul valore
    "video_viewer_location_percent_realtime": {
        "value": {"country_percent_list": [{"country_name": "GB", "country_vv_percent": 0.842}]}
    },
    "realtime_total_video_views": {"value": {"status": 0, "value": 999}},  # arriva dopo: non vince
}


class TikTok(unittest.TestCase):
    def analisi(self, penalita=None):
        return la.analitiche_tiktok(la.unisci_tiktok([INSIGHT_APERTURA, INSIGHT_VIEWERS]), penalita)

    def test_i_numeri_principali(self):
        a = self.analisi()
        self.assertEqual(a["views"], 836)
        self.assertEqual(a["spettatori_unici"], 426)
        self.assertEqual(a["tempo_medio_s"], 8.43)
        self.assertEqual(a["completamento"], 0.0129)

    def test_fonti_paesi_e_permanenza_restano_quote(self):
        a = self.analisi()
        self.assertEqual(a["fonti"]["For You"], 0.993)
        self.assertEqual(a["paesi"], {"GB": 0.842})
        self.assertEqual(a["permanenza"]["3"], 0.46)  # dai millisecondi ai secondi, chiave come in JSON

    def test_status_2_e_non_disponibile_non_zero(self):
        # i follower nuovi non ci sono ancora: «non lo so», non «zero follower»
        self.assertIsNone(self.analisi()["follower_nuovi"])

    def test_vince_il_primo_valore_non_nullo(self):
        fuso = la.unisci_tiktok([{"x": None}, {"x": 1}, {"x": 2}])
        self.assertEqual(fuso, {"x": 1})

    def test_i_campi_di_servizio_restano_fuori(self):
        self.assertNotIn("extra", la.unisci_tiktok([INSIGHT_APERTURA]))

    def test_la_penalita_zero_non_e_una_penalita(self):
        nessuna = {"top_penalty_details": {"penalty_type": 0}}
        vera = {"top_penalty_details": {"penalty_type": 3, "penalty_source": 1}}
        self.assertIsNone(self.analisi(nessuna)["penalita"])
        self.assertEqual(self.analisi(vera)["penalita"]["penalty_type"], 3)

    def test_le_views_orarie_sono_in_utc(self):
        self.assertEqual(self.analisi()["views_orarie"], [["2026-09-29T17:00+00:00", 6]])


def scheda_tabella(dimensione, nomi, valori, chiave="enumValues"):
    return {
        "tableCardData": {
            "mainTableData": {
                "dimensionColumns": [{"dimension": {"type": dimensione}, chiave: {"values": nomi}}],
                "metricColumns": [{"metric": {"type": "EXTERNAL_VIEWS"}, "percentages": {"values": valori}}],
            }
        }
    }


SCHEDE_YOUTUBE = [
    {
        "keyMetricCardData": {
            "keyMetricTabs": [
                {"primaryContent": {"metric": "EXTERNAL_VIEWS", "total": 21}},
                {"primaryContent": {"metric": "AVERAGE_WATCH_TIME", "total": 33040}},
                {"primaryContent": {"metric": "ENGAGED_VIEWS", "total": 10}},
            ]
        }
    },
    scheda_tabella("TRAFFIC_SOURCE_TYPE", ["EXT_URL", "YT_SEARCH"], [47.62, 28.57]),
    scheda_tabella("COUNTRY", ["US", "GB"], [60, 40], chiave="strings"),
    {
        "audienceRetentionHighlightsCardData": {
            "videosData": [{"metricTotals": {"avgPercentageWatched": 0.5162, "shortsFeedImpressionsVtr": 55.56}}]
        }
    },
]


class YouTube(unittest.TestCase):
    def test_millisecondi_in_secondi(self):
        self.assertEqual(la.analitiche_youtube(SCHEDE_YOUTUBE)["tempo_medio_s"], 33.04)

    def test_percentuali_in_quote(self):
        a = la.analitiche_youtube(SCHEDE_YOUTUBE)
        self.assertEqual(a["fonti"], {"EXT_URL": 0.4762, "YT_SEARCH": 0.2857})
        self.assertEqual(a["paesi"], {"US": 0.6, "GB": 0.4})
        self.assertEqual(a["guardato_invece_di_scorrere"], 0.5556)

    def test_la_percentuale_guardata_e_gia_una_quota(self):
        # Studio la da' fra 0 e 1, a differenza delle tabelle: dividerla la renderebbe 0,5%
        self.assertEqual(la.analitiche_youtube(SCHEDE_YOUTUBE)["percentuale_guardata"], 0.5162)

    def test_cio_che_studio_non_da_resta_vuoto(self):
        a = la.analitiche_youtube(SCHEDE_YOUTUBE[:1])
        self.assertEqual(a["views"], 21)
        self.assertIsNone(a["paesi"])
        self.assertIsNone(a["spettatori_unici"])


class Storico(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(metriche, "DB", Path(self.tmp.name) / "s.sqlite")
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def lettura(self, quando, views, guasto=None):
        return {
            "piattaforma": "tiktok",
            "identita": "business-tiktok",
            "letto_il": quando,
            "post": [
                {
                    "id": "769",
                    "url": "https://www.tiktok.com/@x/video/769",
                    "analitiche": None if guasto else {"views": views},
                    "guasto": guasto,
                }
            ],
        }

    def test_si_mostra_l_ultima_lettura_agganciata_alla_clip(self):
        abbinati = {("tiktok", "769"): {"cartella": "umbrella", "clip_id": "01", "url": "U"}}
        con = metriche.db()
        analitiche.salva(con, self.lettura("2026-10-01T10:00:00", 500), abbinati)
        analitiche.salva(con, self.lettura("2026-10-01T12:00:00", 836), abbinati)
        con.close()
        righe = analitiche.ultime()
        self.assertEqual(len(righe), 1)
        self.assertEqual(righe[0]["dati"], {"views": 836})
        self.assertEqual((righe[0]["cartella"], righe[0]["clip_id"], righe[0]["post_id"]), ("umbrella", "01", "U"))

    def test_un_post_non_letto_resta_col_suo_guasto(self):
        con = metriche.db()
        analitiche.salva(con, self.lettura("2026-10-01T12:00:00", None, guasto="nessuna risposta"), {})
        con.close()
        riga = analitiche.ultime()[0]
        self.assertIsNone(riga["dati"])
        self.assertIn("nessuna risposta", "\n".join(analitiche.descrivi(riga)))

    def test_la_storia_resta(self):
        con = metriche.db()
        analitiche.salva(con, self.lettura("2026-10-01T10:00:00", 500), {})
        analitiche.salva(con, self.lettura("2026-10-01T12:00:00", 836), {})
        n = con.execute("SELECT COUNT(*) FROM analitiche").fetchone()[0]
        con.close()
        self.assertEqual(n, 2)


class Mostra(unittest.TestCase):
    def riga(self, piattaforma, dati):
        return {"post_id": "P", "cartella": None, "clip_id": None, "piattaforma": piattaforma,
                "dati": dati, "guasto": None}

    def test_un_valore_mancante_si_scrive_trattino_non_zero(self):
        testo = "\n".join(analitiche.descrivi(self.riga("youtube", {"views": 1, "percentuale_guardata": None})))
        self.assertIn("guardato —", testo)
        self.assertNotIn("guardato 0%", testo)

    def test_le_quote_si_scrivono_in_percentuale(self):
        testo = "\n".join(analitiche.descrivi(self.riga("tiktok", {"fonti": {"For You": 0.993}, "permanenza": {"3": 0.46}})))
        self.assertIn("For You 99%", testo)
        self.assertIn("al 3° s 46%", testo)

    def test_le_fonti_youtube_hanno_un_nome(self):
        testo = "\n".join(analitiche.descrivi(self.riga("youtube", {"fonti": {"SHORTS": 0.9}})))
        self.assertIn("feed Shorts 90%", testo)


if __name__ == "__main__":
    unittest.main()


class Sintesi(unittest.TestCase):
    """La riga del pannello: la tenuta porta il suo nome, e «Others» non e' un paese."""

    def riga(self, piattaforma, dati):
        return {"post_id": "P", "piattaforma": piattaforma, "dati": dati, "guasto": None}

    def test_tiktok_tiene_al_terzo_secondo(self):
        s = analitiche.sintesi(self.riga("tiktok", {"permanenza": {"3": 0.46}, "fonti": {"For You": 0.99}}))
        self.assertEqual(s["tenuta"], ["al 3° s", 0.46])
        self.assertEqual(s["fonte"], ["For You", 0.99])

    def test_youtube_tiene_in_percentuale_guardata_e_le_fonti_hanno_un_nome(self):
        s = analitiche.sintesi(self.riga("youtube", {"percentuale_guardata": 0.37, "fonti": {"SHORTS": 0.97, "YT_SEARCH": 0.01}}))
        self.assertEqual(s["tenuta"], ["guardato", 0.37])
        self.assertEqual(s["fonte"], ["feed Shorts", 0.97])

    def test_others_non_e_un_paese(self):
        s = analitiche.sintesi(self.riga("tiktok", {"paesi": {"Others": 0.6, "GB": 0.3}}))
        self.assertEqual(s["paese"], ["GB", 0.3])

    def test_un_post_non_letto_resta_non_letto(self):
        s = analitiche.sintesi({"post_id": "P", "piattaforma": "tiktok", "dati": None, "guasto": "x"})
        self.assertFalse(s["letto"])
        self.assertIsNone(s["views"])


class DueTasti(unittest.TestCase):
    """«Aggiorna radar» e «Aggiorna social» non si pestano, e insieme fanno il giro di aggiorna.ps1."""

    @classmethod
    def setUpClass(cls):
        import server

        cls.server = server

    def nomi(self, cosa):
        return [p[0] for p in self.server.AGGIORNAMENTI[cosa][0]]

    def test_il_radar_non_legge_i_social(self):
        self.assertEqual(self.nomi("radar"), ["radar", "sorgenti"])

    def test_i_social_non_rileggono_il_catalogo_e_portano_le_analitiche(self):
        self.assertEqual(self.nomi("social"), ["metriche", "analitiche"])

    def test_insieme_fanno_il_giro_completo_e_aggiorna_ps1_lo_stesso(self):
        self.assertEqual(self.nomi("tutto"), self.nomi("radar") + self.nomi("social"))
        ps1 = (percorsi.RADICE / "aggiorna.ps1").read_text(encoding="utf-8")
        for _, _, argomenti in self.server.PASSI:
            self.assertIn(f'"{argomenti[0]}"', ps1, f"{argomenti[0]} manca da aggiorna.ps1")


class PortaDelPannello(unittest.TestCase):
    """Il riuso della porta: vietato su Windows (due server vivi), necessario altrove (TIME_WAIT)."""

    def test_riuso_della_porta_secondo_il_sistema(self):
        import server

        for piattaforma, atteso in (("win32", False), ("linux", True)):
            with mock.patch.object(sys, "platform", piattaforma):
                ricaricato = importlib.reload(server)
                self.assertEqual(ricaricato.Server.allow_reuse_address, atteso, piattaforma)
        importlib.reload(server)


class ResocontoDelGiro(unittest.TestCase):
    """Il resoconto di un lavoro sopravvive al pannello; il lavoro no, e lo si dice."""

    def test_un_giro_in_corso_al_riavvio_e_interrotto(self):
        import json as _json

        import server

        with tempfile.TemporaryDirectory() as cartella:
            f = Path(cartella) / "giro.json"
            coda = ["RuntimeError: claude e' uscito con 1"]
            f.write_text(
                _json.dumps({"in_corso": True, "che": "tutto", "passo": "tutto",
                             "passi": [{"passo": "tutto", "codice": 1, "coda": coda}]}),
                encoding="utf-8",
            )
            giro = server.carica_giro(f)
        self.assertFalse(giro["in_corso"], "in corso per sempre spegnerebbe i pulsanti")
        self.assertEqual(giro["esito"], "interrotto")
        self.assertEqual(giro["passi"][0]["coda"], coda, "l'errore e' la ragione per cui si salva")

    def test_senza_file_un_giro_vuoto(self):
        import server

        self.assertFalse(server.carica_giro(Path("non-esiste.json"))["in_corso"])


class TettiDeiLavori(unittest.TestCase):
    """Il pannello non tronca un lavoro sulle clip prima del tetto che il lavoro si concede."""

    @classmethod
    def setUpClass(cls):
        import ast

        import server

        cls.server = server
        sorgente = (server.AUTOMATICO).read_text(encoding="utf-8")
        for nodo in ast.parse(sorgente).body:
            if isinstance(nodo, ast.Assign) and getattr(nodo.targets[0], "id", "") == "ATTESA":
                # `3 * 3600` non e' un letterale: si valuta l'espressione da sola,
                # senza importare automatico e le sue dipendenze.
                espressione = compile(ast.Expression(nodo.value), "ATTESA", "eval")
                cls.attesa = eval(espressione, {"__builtins__": {}})

    def tetto(self, corpo):
        passo = self.server.passi_lavoro(corpo)[0]
        return passo[3] if len(passo) > 3 else self.server.ATTESA_PASSO

    def test_tutto_regge_l_intera_catena_di_automatico(self):
        # decisioni puo' girare due volte: la scelta e una correzione.
        catena = sum(self.attesa.values()) + self.attesa["decisioni"]
        corpo = {"azione": "tutto", "sorgente": "https://x", "campagna_id": "c"}
        self.assertGreaterEqual(self.tetto(corpo), catena)

    def test_prepara_regge_la_preparazione(self):
        corpo = {"azione": "prepara", "sorgente": "https://x"}
        self.assertGreaterEqual(self.tetto(corpo), self.attesa["prepare"])
