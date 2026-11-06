"""Il risolutore delle sorgenti: da una campagna ai video, e mai un esempio al posto del girato.

Il pulsante «prepara» presupponeva un link che e' un video; le campagne vere
consegnano cartelle di Drive, Doc con dentro i link, canali, VOD. `sorgenti.py`
li espande a secco. Qui si fissano le regole che, sbagliate, non danno errore:

- che cosa e' un link, per forma — `clips.twitch.tv` contiene `twitch.tv/`;
- **il Doc in html tiene i link che il txt perde** (Elina e Stanley Meng);
- **un esempio non si preseleziona mai**, ne' sotto un titolo «Examples:», ne'
  in una sottocartella «Example» (la trappola della Acme Show);
- l'ordine e i filtri della preselezione, e i tre esiti;
- che lo stesso video sia la stessa chiave da `meta.json` e dall'elenco di
  una cartella, se no «gia' lavorato» non scatta mai.

Niente rete: le letture sono sostituite, come in `test_ingest_drive.py`.
"""

from __future__ import annotations

import unittest
from datetime import date
from typing import ClassVar
from unittest import mock

import percorsi  # noqa: F401  (mette le cartelle dei moduli su sys.path)

import sorgenti


class CheCosaEUnLink(unittest.TestCase):
    def test_i_tipi(self):
        casi = {
            "https://www.youtube.com/watch?v=WyCRc1TH7ek&t=3": "youtube_video",
            "https://www.youtube.com/watch?feature=share&v=WyCRc1TH7ek": "youtube_video",
            "https://youtu.be/WyCRc1TH7ek": "youtube_video",
            "https://www.youtube.com/live/CUv5XT-JjGs": "youtube_video",
            "https://youtube.com/shorts/WyCRc1TH7ek": "youtube_short",
            "https://www.youtube.com/@CreatorEsempio/videos": "youtube_canale",
            "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv": "youtube_canale",
            "https://www.youtube.com/playlist?list=PL1234567890": "youtube_playlist",
            "https://drive.google.com/drive/u/0/folders/1sZ3QYOjUfLpzo8IxPZLW2VWrmUqXNSTo": (
                "drive_cartella"
            ),
            "https://drive.google.com/file/d/1BNevgK7haPaAB3Xlf8ocWYK49JhKst06/view": "drive_file",
            "https://drive.google.com/open?id=1BNevgK7haPaAB3Xlf8ocWYK49JhKst06": "drive_file",
            "https://docs.google.com/document/d/1cUpnDX46Or8Sr3sRCnDp6jRcpAbCdEf/edit": "gdoc",
            "https://docs.google.com/spreadsheets/d/1cUpnDX46Or8Sr3sRCnDp6jRcpAbCdEf/": "gsheet",
            "https://www.twitch.tv/videos/2885252321": "twitch_vod",
            "https://www.twitch.tv/jshock": "twitch_canale",
            "https://kick.com/ily_bre": "kick_canale",
            "https://kick.com/ily_bre/videos/0a1b2c3d": "kick_vod",
            "https://bucket.s3.us-east-1.amazonaws.com/campaigns/references/videos/x.mp4": "file",
            "https://we.tl/t-hVskSmtaHbud9FGo": "trasferimento",
            "http://next.frame.io/share/0a5953da/view/30cb7155": "frameio",
            # Pubblicato sul web: niente export, la pagina `/pub` e' gia' html.
            "https://docs.google.com/document/d/e/2PACX-1vTIc96HipJmdpSN1FqFJlH45nik5/pub": "gdoc",
            # La pagina clip del canale nel brief vuol dire «clippa il canale».
            "https://www.twitch.tv/diegoloveless/clips?range=7d": "twitch_canale",
            "https://sm01.app.box.com/s/3o86uiy2yrgyvyi2zsllzulbod4icacd": "box",
            "https://tinyurl.com/JWTGoogleDrive": "abbreviato",
        }
        for url, atteso in casi.items():
            with self.subTest(url=url):
                self.assertEqual(sorgenti.tipo_link(url), atteso)

    def test_il_doc_pubblicato_si_legge_dalla_sua_pagina(self):
        url = "https://docs.google.com/document/d/e/2PACX-1vTIc96HipJmdpSN1FqFJlH45nik5/pub"
        self.assertEqual(sorgenti.id_documento(url), "e/2PACX-1vTIc96HipJmdpSN1FqFJlH45nik5")

    def test_quelli_che_non_sono_sorgenti(self):
        # `clips.twitch.tv/X` contiene `twitch.tv/X`: senza l'host ancorato una
        # clip di qualcun altro diventava un canale da elencare.
        for url in (
            "https://clips.twitch.tv/FunnyClipName-abc",
            "https://clips.twitch.tv/AwkwardHelplessSalamanderSwiftRage",
            "https://www.twitch.tv/jshock/clip/FunnyClipName",
            "https://www.instagram.com/reel/Cxyz/",
            "https://www.tiktok.com/@someone/video/123",
            "https://contentrewards.com/discover/abc",
        ):
            with self.subTest(url=url):
                self.assertIsNone(sorgenti.tipo_link(url))


class IlDocInHtml(unittest.TestCase):
    """Il txt di Google tiene la parola e butta l'indirizzo: l'html no."""

    HTML = (
        "<html><head><style>.c1{color:red}</style></head><body>"
        '<p>Clip from <a href="https://www.google.com/url?q=https://www.youtube.com/'
        '@CreatorEsempio/videos&amp;sa=D&amp;source=editors">her channel</a> only.</p>'
        "</body></html>"
    )

    def test_il_link_sotto_la_parola_resta(self):
        testo = sorgenti.testo_da_html(self.HTML)
        link = sorgenti.link_da_testo(testo, "doc:x")
        self.assertEqual([v["url"] for v in link], ["https://www.youtube.com/@CreatorEsempio/videos"])
        self.assertEqual(link[0]["tipo"], "youtube_canale")
        self.assertNotIn("color", testo, "lo stile del documento non e' testo")


BRIEF_CAP_TABLE = """Source episode:
https://www.youtube.com/watch?v=WyCRc1TH7ek

Examples of viral clips:
- https://www.youtube.com/watch?v=1P5Y7OcRyz8
- https://www.youtube.com/watch?v=AAAAAAAAAAA
- https://www.youtube.com/watch?v=BBBBBBBBBBB

Full episode two:
https://www.youtube.com/watch?v=CCCCCCCCCCC
Post it like this one https://www.youtube.com/watch?v=DDDDDDDDDDD
Raw file: https://example.com/files/episode.mp4
"""


class GliEsempi(unittest.TestCase):
    def indizi(self) -> dict[str, str]:
        return {
            v["url"][-11:]: v["indizio_esempio"]
            for v in sorgenti.link_da_testo(BRIEF_CAP_TABLE, "b")
        }

    def test_l_episodio_non_e_un_esempio(self):
        self.assertEqual(self.indizi()["WyCRc1TH7ek"], "")

    def test_tutto_l_elenco_sotto_examples(self):
        # Il terzo della lista e' un esempio quanto il primo: il titolo sta tre
        # righe sopra, e le righe in mezzo sono altre voci dello stesso elenco.
        indizi = self.indizi()
        for ident in ("1P5Y7OcRyz8", "AAAAAAAAAAA", "BBBBBBBBBBB"):
            with self.subTest(ident=ident):
                self.assertTrue(indizi[ident])

    def test_un_titolo_nuovo_chiude_l_elenco(self):
        self.assertEqual(self.indizi()["CCCCCCCCCCC"], "")

    def test_la_parola_sulla_stessa_riga(self):
        self.assertEqual(self.indizi()["DDDDDDDDDDD"], "like this")

    def test_il_titolo_sulla_riga_del_primo_link(self):
        testo = "Examples: https://youtu.be/FFFFFFFFFFF\nhttps://youtu.be/GGGGGGGGGGG\n"
        link = sorgenti.link_da_testo(testo, "b")
        self.assertTrue(all(v["indizio_esempio"] for v in link), link)

    def test_l_indirizzo_non_conta(self):
        # `example.com` e' un dominio, non un «ecco un esempio».
        self.assertEqual(self.indizi()["episode.mp4"], "")


def c(ident: str, **campi) -> dict:
    voce = sorgenti.candidato(f"https://youtu.be/{ident}", "youtube_video", "materiali")
    voce.update(campi)
    return voce


class LaPreselezione(unittest.TestCase):
    def test_filtri_e_ordine(self):
        candidati = [
            c("esempioAAAA", durata_s=3600, data="2026-09-27", indizio_esempio="examples"),
            c("lavoratoAAA", durata_s=3600, data="2026-09-26"),
            c("cortoAAAAAA", durata_s=300, data="2026-09-25"),
            c("senzadurata", data="2026-09-25"),
            c("tedescoAAAA", durata_s=3600, data="2026-09-25", lingua="de"),
            c("vecchioAAAA", durata_s=3600, data="2026-08-01"),
            c("recenteAAAA", durata_s=1200, data="2026-09-20"),
            c("mediaAAAAAA", durata_s=5400, data="2026-09-10"),
        ]
        sorgenti.preseleziona(candidati, {"yt:lavoratoAAA"}, durata_min=600, per_clic=2)
        scelti = [v["id_sorgente"] for v in candidati if v["preselezionato"]]
        self.assertEqual(scelti, ["yt:recenteAAAA", "yt:mediaAAAAAA"])
        perche = {v["id_sorgente"][3:]: v["perche"] for v in candidati}
        self.assertTrue(perche["esempioAAAA"].startswith("forse un esempio"))
        self.assertEqual(perche["lavoratoAAA"], "gia' lavorato")
        self.assertEqual(perche["cortoAAAAAA"], "sotto i 10 minuti")
        self.assertEqual(perche["senzadurata"], "durata non letta")
        self.assertEqual(perche["tedescoAAAA"], "lingua de")
        self.assertEqual(perche["vecchioAAAA"], "oltre i 2 per clic")

    def test_in_una_cartella_il_non_misurato_non_e_un_guasto(self):
        voce = c("cartellaAAA", da="cartella:XYZ")
        sorgenti.preseleziona([voce])
        self.assertEqual(voce["perche"], "non misurato")

    def test_la_lingua_si_legge_in_ogni_grafia(self):
        casi = {
            "en-US": "en",
            "English (India)": "en",
            "Italian": "it",
            "it": "it",
            "nl-NL": "nl",
            None: None,
        }
        for grezza, attesa in casi.items():
            with self.subTest(grezza=grezza):
                self.assertEqual(sorgenti.lingua_breve(grezza), attesa)
        voce = c("indianoAAAA", durata_s=3600, lingua="english (india)")
        sorgenti.preseleziona([voce])
        self.assertTrue(voce["preselezionato"], "l'inglese dell'India e' inglese")

    def test_senza_date_vale_la_posizione_nell_elenco(self):
        candidati = [
            c("terzoAAAAAA", durata_s=4000, posizione=2),
            c("primoAAAAAA", durata_s=700, posizione=0),
            c("secondoAAAA", durata_s=900, posizione=1),
        ]
        sorgenti.preseleziona(candidati, per_clic=1)
        self.assertEqual(
            [v["id_sorgente"] for v in candidati if v["preselezionato"]], ["yt:primoAAAAAA"]
        )


class GliEsiti(unittest.TestCase):
    def test_tre_esiti(self):
        buono = c("buonoAAAAAA", durata_s=3600, data="2026-09-20")
        sorgenti.preseleziona([buono])
        self.assertEqual(sorgenti.esito([buono], [])[0], "automatico")

        esempio = c("esempioAAAA", durata_s=3600, indizio_esempio="examples")
        sorgenti.preseleziona([esempio])
        quale, motivo = sorgenti.esito([esempio], [{"categoria": "trasferimento"}])
        self.assertEqual(quale, "con_scelta")
        self.assertIn("forse un esempio", motivo)
        self.assertIn("trasferimento", motivo, "il link che non si apre va detto anche qui")

        self.assertEqual(
            sorgenti.esito([], [{"categoria": "trasferimento"}]), ("a_mano", "trasferimento")
        )
        self.assertEqual(sorgenti.esito([], [])[0], "a_mano")

    def test_a_mano_dice_dove_guardare(self):
        # «nessun link a un video» da solo manda a rileggere il brief: i domini
        # degli altri link dicono gia' dove sta il materiale.
        quale, motivo = sorgenti.esito([], [], ["notion.so", "instagram.com"])
        self.assertEqual(quale, "a_mano")
        self.assertIn("notion.so", motivo)


class IlChiamante(unittest.TestCase):
    """`risolvi` con la rete sostituita: le regole giuste, invocate nel posto giusto."""

    def test_cap_table_dentro_un_doc(self):
        doc = (
            '<p>Episode: <a href="https://www.google.com/url?q=https://youtu.be/WyCRc1TH7ek">'
            "here</a></p><p>Examples:</p>"
            '<p><a href="https://youtu.be/1P5Y7OcRyz8">https://youtu.be/1P5Y7OcRyz8</a></p>'
        )
        scheda = {
            "materiali_dett": [
                {"url": "https://docs.google.com/document/d/1cUpnDX46Or8Sr3sRCnDp6jRcp/edit"},
                # La scheda lo dice da se': un `example` non e' mai un candidato.
                {"url": "https://youtu.be/ZZZZZZZZZZZ", "tipo": "example"},
            ]
        }

        def leggi(ident, tipo):
            return sorgenti.testo_da_html(doc)

        def espandi(voce):
            return [
                sorgenti.candidato(
                    voce["url"], voce["tipo"], voce["da"], voce["indizio_esempio"], durata_s=3400
                )
            ], None

        grezzo = sorgenti.risolvi(scheda, "", leggi=leggi, espandi_link=espandi)
        chiavi = [v["id_sorgente"] for v in grezzo["candidati"]]
        self.assertNotIn("yt:ZZZZZZZZZZZ", chiavi)
        sorgenti.preseleziona(grezzo["candidati"])
        scelti = [v["id_sorgente"] for v in grezzo["candidati"] if v["preselezionato"]]
        self.assertEqual(scelti, ["yt:WyCRc1TH7ek"])

    def test_un_doc_linkato_sotto_examples_porta_esempi(self):
        brief = "Examples:\nhttps://docs.google.com/document/d/2cUpnDX46Or8Sr3sRCnDp6jRcp/edit\n"
        testi = {"2cUpnDX46Or8Sr3sRCnDp6jRcp": "Clip one https://youtu.be/EEEEEEEEEEE"}

        def leggi(ident, tipo):
            return testi.get(ident)

        link, _, _ = sorgenti.raccogli_link({"descrizione": brief}, "", leggi)
        video = [v for v in link if v["tipo"] == "youtube_video"]
        self.assertEqual(len(video), 1)
        self.assertTrue(video[0]["indizio_esempio"])

    def test_un_doc_privato_e_un_problema_non_un_silenzio(self):
        _link, problemi, _altri = sorgenti.raccogli_link(
            {"materiali": ["https://docs.google.com/document/d/3cUpnDX46Or8Sr3sRCnDp6jRcp/edit"]},
            "",
            lambda ident, tipo: None,
        )
        self.assertEqual([p["categoria"] for p in problemi], ["documento non pubblico o non letto"])

    def test_lo_stesso_video_si_legge_una_volta(self):
        # Il brief di MoonPay ripeteva la stessa diretta con trenta `?t=` diversi:
        # trenta letture di rete per un candidato solo.
        brief = "\n".join(
            f"https://www.youtube.com/live/eCjdOfU6rFs?si=x&t={t}" for t in (900, 2146, 2872)
        )
        letture = []

        def espandi(voce):
            letture.append(voce["url"])
            return [sorgenti.candidato(voce["url"], voce["tipo"], voce["da"], durata_s=7000)], None

        grezzo = sorgenti.risolvi({}, brief, leggi=lambda i, t: None, espandi_link=espandi)
        self.assertEqual(len(letture), 1)
        self.assertEqual([c["id_sorgente"] for c in grezzo["candidati"]], ["yt:eCjdOfU6rFs"])

    def test_un_link_abbreviato_si_apre(self):
        cartella = "https://drive.google.com/drive/folders/1UkCpl62dMiGifnpjOi3Vnwek3LLoD9qn"
        link, _, _ = sorgenti.raccogli_link(
            {"materiali": ["https://tinyurl.com/JWTGoogleDrive"]},
            "",
            leggi=lambda i, t: None,
            apri=lambda url: cartella,
        )
        self.assertEqual([(v["url"], v["tipo"]) for v in link], [(cartella, "drive_cartella")])

    def test_i_domini_degli_altri_link_arrivano_all_esito(self):
        grezzo = sorgenti.risolvi(
            {"materiali": ["https://www.notion.so/Guidelines-365ecde"]},
            "",
            leggi=lambda i, t: None,
            espandi_link=lambda v: ([], None),
        )
        self.assertEqual(grezzo["altri_link"], ["notion.so"])


class IFileDiDrive(unittest.TestCase):
    VOCE: ClassVar[dict] = {
        "url": "https://drive.google.com/file/d/1tTe5uNTLdHyngppX2vCDB-3G60YGCFBs/view",
        "tipo": "drive_file",
        "da": "materiali",
        "indizio_esempio": "",
    }

    def test_un_pdf_non_e_un_sorgente_ne_un_problema(self):
        def info(url):
            raise AssertionError("yt-dlp su un PDF risponde 400, e sembra un guasto")

        trovati = sorgenti.file_drive(self.VOCE, titolo_di=lambda i: "Guide.pdf", info_di=info)
        self.assertEqual(trovati, [])

    def test_un_video_si_misura(self):
        trovati = sorgenti.file_drive(
            self.VOCE,
            titolo_di=lambda i: "Episode 12.mp4",
            info_di=lambda url: {"duration": 3600.4, "title": "x"},
        )
        self.assertEqual(
            [(v["titolo"], v["durata_s"]) for v in trovati], [("Episode 12.mp4", 3600)]
        )


class Kick(unittest.TestCase):
    def test_i_vod_del_canale_dall_api(self):
        dati = [
            {
                "session_title": "Stream",
                "duration": 2179000,
                "language": "English",
                "created_at": "2026-09-12 21:07:16",
                "source": "https://stream.kick.com/x/master.m3u8",
                "video": {"uuid": "a594550d-065c-4591-8871-eb44dae8572a"},
            },
            {"session_title": "senza video", "video": None},
        ]
        trovati = sorgenti.kick(
            {"url": "https://kick.com/globex", "tipo": "kick_canale", "indizio_esempio": ""},
            api=lambda percorso: dati,
        )
        self.assertEqual(len(trovati), 1)
        v = trovati[0]
        self.assertEqual(
            v["url"], "https://kick.com/globex/videos/a594550d-065c-4591-8871-eb44dae8572a"
        )
        self.assertEqual((v["durata_s"], v["lingua"], v["data"]), (2179, "en", "2026-09-12"))
        self.assertEqual(v["id_sorgente"], "kick:a594550d-065c-4591-8871-eb44dae8572a")
        self.assertTrue(v["flusso"].endswith(".m3u8"))

    def test_il_canale_porta_nome_e_follower(self):
        def api(percorso):
            if percorso == "v2/channels/globex":
                return {"followers_count": 6291, "user": {"username": "Globex"}}
            return [{"session_title": "Stream", "views": 812, "video": {"uuid": "u-1"}}]

        v = sorgenti.kick(
            {"url": "https://kick.com/globex", "tipo": "kick_canale", "indizio_esempio": ""},
            api=api,
        )[0]
        self.assertEqual((v["canale"], v["follower"], v["views"]), ("Globex", 6291, 812))


class IlCreator(unittest.TestCase):
    """Canale, follower e interazioni: lo Scout ci giudica quanto una campagna puo' girare."""

    VOCE: ClassVar[dict] = {
        "url": "https://youtu.be/abc",
        "tipo": "youtube_video",
        "da": "brief",
        "indizio_esempio": "",
    }

    def test_un_video_porta_il_suo_creator(self):
        v = sorgenti.da_info(
            self.VOCE,
            {
                "title": "x",
                "channel": "PowerfulJRE",
                "channel_follower_count": 21200000,
                "view_count": 881897,
                "like_count": 12968,
                "comment_count": 3000,
            },
        )
        self.assertEqual(
            (v["canale"], v["follower"], v["views"], v["like"], v["commenti"]),
            ("PowerfulJRE", 21200000, 881897, 12968, 3000),
        )

    def test_senza_dati_i_campi_non_ci_sono(self):
        v = sorgenti.da_info(self.VOCE, {"title": "x"})
        self.assertNotIn("follower", v)

    def test_nell_elenco_il_canale_sta_in_cima(self):
        info = {
            "channel": "PowerfulJRE",
            "channel_follower_count": 21200000,
            "entries": [{"url": "https://www.youtube.com/watch?v=abc", "view_count": 313000}],
        }
        with mock.patch.object(sorgenti, "_ytdlp", return_value=info):
            v = sorgenti.elenco(
                {
                    "url": "https://www.youtube.com/@podcastfamoso",
                    "tipo": "youtube_canale",
                    "indizio_esempio": "",
                }
            )[0]
        self.assertEqual(
            (v["canale"], v["follower"], v["views"]), ("PowerfulJRE", 21200000, 313000)
        )


def voce_html(ident: str, nome: str, cartella: bool, quando: str = "Sep 20") -> str:
    href = (
        f"https://drive.google.com/drive/folders/{ident}"
        if cartella
        else f"https://drive.google.com/file/d/{ident}/view"
    )
    return (
        f'<div class="flip-entry" id="entry-{ident}"><div class="flip-entry-info">'
        f'<a href="{href}"><div class="flip-entry-title">{nome}</div></a></div>'
        f'<div class="flip-entry-last-modified"><div>{quando}</div></div></div>'
    )


class LaCartellaDiDrive(unittest.TestCase):
    PAGINE: ClassVar[dict[str, str]] = {
        "RADICE": '<div class="flip-entries">'
        + voce_html("SOTTO", "Example clips", True)
        + voce_html("FILEEPISODIO1234567890", "Episode 12.mp4", False)
        + voce_html("FILELOGO1234567890123", "logo.png", False)
        + "</div>",
        "SOTTO": '<div class="flip-entries">'
        + voce_html("FILECLIP12345678901234", "final_v2.mp4", False, "Sep 25")
        + "</div>",
    }

    def test_una_sottocartella_example_marca_i_suoi_file(self):
        misurati = []

        def misura(url):
            misurati.append(url)
            return 3600

        trovati, problema = sorgenti.cartella(
            {
                "url": "https://drive.google.com/drive/folders/RADICE",
                "indizio_esempio": "",
                "da": "materiali",
            },
            html_di=self.PAGINE.get,
            misura=misura,
        )
        self.assertIsNone(problema)
        per_nome = {v["titolo"]: v for v in trovati}
        self.assertEqual(set(per_nome), {"Episode 12.mp4", "Example clips/final_v2.mp4"})
        self.assertEqual(per_nome["Episode 12.mp4"]["indizio_esempio"], "")
        self.assertTrue(per_nome["Example clips/final_v2.mp4"]["indizio_esempio"])
        self.assertEqual(per_nome["Episode 12.mp4"]["durata_s"], 3600)
        self.assertEqual(len(misurati), 1, "un esempio non si misura: costa una lettura")

    def test_un_video_senza_estensione_si_riconosce_dal_tipo(self):
        # Elliott Caldwell, 7 ottobre 2026: episodi chiamati «Eric Michael», senza
        # estensione. Il tipo sta nell'icona della voce, ed e' quello che vale.
        icona = '<div class="flip-entry-list-icon"><img src="https://x/16/type/{}"/></div>'
        html = '<div class="flip-entries">' + "".join(
            voce_html(ident, nome, False).replace(
                '<div class="flip-entry-title">', icona.format(tipo) + '<div class="flip-entry-title">'
            )
            for ident, nome, tipo in (
                ("FILEEPISODIO1234567890", "Eric Michael", "video/mp4"),
                ("FILEGUIDA123456789012", "Guida", "application/pdf"),
            )
        ) + "</div>"
        trovati, problema = sorgenti.cartella(
            {"url": "https://drive.google.com/drive/folders/EP", "indizio_esempio": "", "da": ""},
            html_di={"EP": html}.get,
            misura=lambda url: 4297,
        )
        self.assertIsNone(problema)
        self.assertEqual([v["titolo"] for v in trovati], ["Eric Michael"])

    def test_una_risoluzione_vecchia_si_rifa(self):
        from datetime import datetime, timedelta

        adesso = datetime(2026, 10, 7, 12, 0)
        self.assertFalse(sorgenti.scaduta((adesso - timedelta(days=6)).isoformat(), adesso))
        self.assertTrue(sorgenti.scaduta((adesso - timedelta(days=8)).isoformat(), adesso))
        self.assertTrue(sorgenti.scaduta(None, adesso), "senza data non si sa quanto e' vecchia")

    def test_una_cartella_senza_video_lo_dice(self):
        # Loghi e grafiche: la cartella c'e', il girato no. «Nessun link» sarebbe falso.
        trovati, problema = sorgenti.cartella(
            {
                "url": "https://drive.google.com/drive/folders/LOGHI",
                "indizio_esempio": "",
                "da": "",
            },
            html_di={
                "LOGHI": '<div class="flip-entries">'
                + voce_html("FILELOGO1234567890123", "logo.png", False)
                + "</div>"
            }.get,
            misura=lambda url: 1,
        )
        self.assertEqual(trovati, [])
        self.assertEqual(problema["categoria"], "cartella Drive senza video")
        self.assertIn("png", problema["dettaglio"])

    def test_una_cartella_enorme_si_taglia_e_lo_dice(self):
        voci = "".join(
            voce_html(f"FILE{i:04d}ABCDEFGHIJKLMNOP", f"clip {i}.mp4", False, f"Sep {1 + i % 28}")
            for i in range(sorgenti.FILE_PER_CARTELLA + 5)
        )
        trovati, problema = sorgenti.cartella(
            {
                "url": "https://drive.google.com/drive/folders/TANTE",
                "indizio_esempio": "",
                "da": "",
            },
            html_di={"TANTE": f'<div class="flip-entries">{voci}</div>'}.get,
            misura=lambda url: 40,
        )
        self.assertEqual(len(trovati), sorgenti.FILE_PER_CARTELLA)
        self.assertEqual(problema["categoria"], "cartella Drive grande")
        misurati = [v for v in trovati if v["durata_s"] == 40]
        self.assertEqual(len(misurati), sorgenti.MISURATI_PER_CARTELLA, "i misurati restano")

    def test_una_cartella_privata_e_un_problema(self):
        trovati, problema = sorgenti.cartella(
            {"url": "https://drive.google.com/drive/folders/X", "indizio_esempio": "", "da": ""},
            html_di=lambda ident: None,
            misura=lambda url: 1,
        )
        self.assertEqual(trovati, [])
        self.assertIn("privata", problema["categoria"])


class LeDateDiDrive(unittest.TestCase):
    OGGI = date(2026, 9, 28)

    def test_forme(self):
        self.assertEqual(sorgenti.data_drive("Sep 14", self.OGGI), "2026-09-14")
        # Senza anno e nel futuro: e' l'anno scorso.
        self.assertEqual(sorgenti.data_drive("Dec 3", self.OGGI), "2025-12-03")
        self.assertEqual(sorgenti.data_drive("Sep 14, 2025", self.OGGI), "2025-09-14")
        self.assertEqual(sorgenti.data_drive("10:32 AM", self.OGGI), "2026-09-28")
        self.assertIsNone(sorgenti.data_drive("ieri", self.OGGI))


class LaChiaveDelVideo(unittest.TestCase):
    """Se `meta.json` e l'elenco scrivono chiavi diverse, «gia' lavorato» non scatta mai."""

    def test_stesso_video_stessa_chiave(self):
        coppie = (
            (
                "https://drive.google.com/file/d/1BNevgK7haPaAB3Xlf8ocWYK49JhKst06/view",
                "https://drive.google.com/open?id=1BNevgK7haPaAB3Xlf8ocWYK49JhKst06",
            ),
            ("https://www.youtube.com/watch?v=WyCRc1TH7ek&t=5", "https://youtu.be/WyCRc1TH7ek"),
        )
        for a, b in coppie:
            with self.subTest(a=a):
                self.assertEqual(
                    sorgenti.id_sorgente(a, sorgenti.tipo_link(a)),
                    sorgenti.id_sorgente(b, sorgenti.tipo_link(b)),
                )


if __name__ == "__main__":
    unittest.main()
