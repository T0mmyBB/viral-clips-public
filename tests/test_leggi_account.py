"""Le views di TikTok e Instagram, e i segnali dei post, letti dalle pagine degli account.

Fino al 17 settembre 2026 si leggeva solo YouTube. La prima lettura degli altri due ha
trovato i quattro post TikTok della consegna Contoso a zero views dopo cinque giorni,
senza un avviso da nessuna parte. Qui si fissano le regole che rendono quella lettura
affidabile: un post trova la sua clip per id, uno zero a un giorno dall'uscita e' un
segnale ma un numero mai letto no, un valore che non sappiamo interpretare non diventa
un avviso, e chi legge e chi pubblica chiudono lo stesso profilo con la stessa serratura.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

sys.path.insert(0, str(percorsi.RADICE / "dashboard"))

import leggi_account  # noqa: E402
import orchestrator  # noqa: E402
import registro  # noqa: E402
import serratura  # noqa: E402
import stato  # noqa: E402


class IdDaUrl(unittest.TestCase):
    """Le forme vere degli URL in `publish_state.json`."""

    def test_le_tre_piattaforme(self):
        self.assertEqual(
            leggi_account.id_da_url(
                "tiktok", "https://www.tiktok.com/@canale_business/video/7600000000000000102"
            ),
            "7600000000000000102",
        )
        self.assertEqual(
            leggi_account.id_da_url("instagram", "https://www.instagram.com/reel/DdMwIw7NYHN/"),
            "DdMwIw7NYHN",
        )
        self.assertEqual(
            leggi_account.id_da_url(
                "youtube", "https://youtube.com/shorts/jHMc0eBGhBA?feature=share"
            ),
            "jHMc0eBGhBA",
        )

    def test_la_piattaforma_sbagliata_non_aggancia(self):
        self.assertIsNone(
            leggi_account.id_da_url("tiktok", "https://www.instagram.com/reel/DdMwIw7NYHN/")
        )
        self.assertIsNone(leggi_account.id_da_url("youtube", None))


class TikTok(unittest.TestCase):
    CONTESTO = {
        "uploadUserProfile": {"user": {"uid": "1", "unique_id": "canale_business"}},
        "firstBatchQueryItems": {
            "item_list": [
                {
                    "item_id": "111",
                    "play_count": "0",
                    "like_count": "1",
                    "comment_count": "0",
                    "share_count": "0",
                    "status": 102,
                    "in_review": False,
                    "visibility": 1,
                },
                {
                    "item_id": "222",
                    "play_count": "37",
                    "like_count": "3",
                    "comment_count": "1",
                    "share_count": "0",
                    "status": 102,
                    "in_review": True,
                    "visibility": 1,
                },
            ]
        },
    }

    def test_handle_e_post(self):
        handle, post = leggi_account.post_tiktok(self.CONTESTO)
        self.assertEqual(handle, "canale_business")
        self.assertEqual([(p["id"], p["views"]) for p in post], [("111", 0), ("222", 37)])
        self.assertEqual(post[0]["url"], "https://www.tiktok.com/@canale_business/video/111")

    def test_avvisi(self):
        self.assertEqual(
            leggi_account.avvisi_tiktok({"in_review": "true", "visibility": 1}), ["in revisione"]
        )
        self.assertEqual(
            leggi_account.avvisi_tiktok({"in_review": False, "visibility": 2}),
            ["non pubblico (visibility 2)"],
        )

    def test_uno_stato_che_non_sappiamo_leggere_non_e_un_avviso(self):
        """102 lo portano tutti i post, compresi quelli a zero: non si inventa un significato."""
        self.assertEqual(
            leggi_account.avvisi_tiktok({"status": 144, "in_review": False, "visibility": 1}), []
        )


class Instagram(unittest.TestCase):
    def test_reel_dalla_risposta_graphql(self):
        risposte = [
            {
                "data": {
                    "fetch__XDTUserDict": {
                        "clips_connection": {
                            "edges": [
                                {
                                    "node": {
                                        "media": {
                                            "code": "AAA",
                                            "play_count": 114,
                                            "like_count": 1,
                                            "comment_count": 0,
                                        }
                                    }
                                },
                                {
                                    "node": {
                                        "media": {
                                            "code": "BBB",
                                            "play_count": 0,
                                            "like_count": 1,
                                            "comment_count": 0,
                                        }
                                    }
                                },
                            ]
                        }
                    }
                }
            },
            {"altro": {"code": "AAA", "play_count": 999}},
        ]
        post = leggi_account.post_instagram(risposte)
        # La stessa griglia arriva piu' volte: vale la prima lettura, una volta sola.
        self.assertEqual([(p["id"], p["views"]) for p in post], [("AAA", 114), ("BBB", 0)])


class YouTube(unittest.TestCase):
    def video(self, **campi):
        base = {
            "videoId": "abc123",
            "channelId": "UC1",
            "privacy": "VIDEO_PRIVACY_PUBLIC",
            "publicMetrics": {"externalViewCount": "1206", "likeCount": "25"},
            "allRestrictions": {"summary": {"severity": leggi_account.NESSUNA_RESTRIZIONE}},
        }
        base.update(campi)
        return base

    def test_senza_restrizioni_niente_da_dire(self):
        canali, post = leggi_account.post_youtube([{"videos": [self.video()]}])
        self.assertEqual(canali, {"UC1"})
        self.assertEqual((post[0]["views"], post[0]["avvisi"]), (1206, []))

    def test_restrizioni_e_privacy(self):
        v = self.video(
            privacy="VIDEO_PRIVACY_UNLISTED",
            allRestrictions={"summary": {"severity": "VIDEO_RESTRICTIONS_SEVERITY_WARNING"}},
        )
        self.assertEqual(
            leggi_account.avvisi_youtube(v), ["restrizioni: warning", "non pubblico: unlisted"]
        )


class ZeroViews(unittest.TestCase):
    """`stato.avvisi_post`: zero a un giorno e' un segnale, un numero mai letto no."""

    def test_prima_di_un_giorno_tace(self):
        self.assertEqual(stato.avvisi_post(0, "2026-09-16T19:00:00", "2026-09-17T18:00:00"), [])

    def test_dopo_un_giorno_parla(self):
        self.assertEqual(
            stato.avvisi_post(0, "2026-09-16T19:00:00", "2026-09-17T20:00:00"),
            ["0 views a 25 ore dall'uscita"],
        )
        self.assertEqual(
            stato.avvisi_post(0, "2026-09-12T21:08:00", "2026-09-17T02:06:00"),
            ["0 views a 4 giorni dall'uscita"],
        )

    def test_non_misurato_non_e_zero(self):
        self.assertEqual(stato.avvisi_post(None, "2026-09-12T21:08:00", "2026-09-17T02:06:00"), [])
        self.assertEqual(stato.avvisi_post(5, "2026-09-12T21:08:00", "2026-09-17T02:06:00"), [])

    def test_i_segnali_della_piattaforma_passano(self):
        self.assertEqual(stato.avvisi_post(3, None, None, ["in revisione"]), ["in revisione"])


class StessoProfilo(unittest.TestCase):
    """Chi pubblica e chi legge chiudono la stessa cartella con lo stesso nome."""

    REGISTRO = {
        "gruppi": [{"id": "business"}],
        "identita": [
            {
                "id": "business-tiktok",
                "gruppo": "business",
                "piattaforma": "tiktok",
                "handle": "@x",
                "profilo_browser": "publish/.tiktok-business",
            },
            {
                "id": "business-instagram",
                "gruppo": "business",
                "piattaforma": "instagram",
                "handle": "@x",
                "profilo_browser": "publish/.instagram-business",
            },
            {
                "id": "business-youtube",
                "gruppo": "business",
                "piattaforma": "youtube",
                "handle": "@x",
                "profilo_browser": "publish/.youtube-business",
            },
        ],
    }

    def test_stessa_serratura_su_tutte_e_tre(self):
        cfg = {"instagram_profile": ".instagram-profile"}  # il campo vecchio non vince
        with mock.patch.object(registro, "_leggi", return_value=self.REGISTRO):
            for piattaforma in ("tiktok", "instagram", "youtube"):
                with self.subTest(piattaforma=piattaforma):
                    pubblica = orchestrator.profilo_pubblicazione(cfg, "business", piattaforma)
                    legge = registro.profilo_obbligatorio("business", piattaforma)
                    self.assertEqual(Path(pubblica), Path(legge))
                    self.assertEqual(
                        serratura.nome_profilo(pubblica), serratura.nome_profilo(legge)
                    )


if __name__ == "__main__":
    unittest.main()
