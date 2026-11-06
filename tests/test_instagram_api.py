"""Instagram dall'API di Meta: le regole che non danno errore se rotte.

E' la quinta porta da cui una clip puo' uscire, e i modi di sbagliarla sono
quelli gia' pagati sulle altre quattro, piu' due suoi:

- **l'account**: nel file del gruppo c'e' il token di un altro account. La
  pubblicazione riesce comunque.
- **il doppione**: un contenitore rimasto «in corso» al giro prima puo' essere
  diventato pronto, o essere gia' uscito. Crearne un secondo mette la stessa
  clip online due volte.
- **l'esito**: uno stato che non conosciamo non e' un'uscita. E un reel uscito
  senza indirizzo non e' un reel da rifare.
- **il suo, uno**: la richiesta che pubblica puo' restare senza risposta, e
  «non so» non e' ne' «uscito» ne' «fallito»: lo dice il contenitore.
- **il suo, due**: due unita' che la documentazione non scrive. Un tempo
  diviso per mille quando era gia' in secondi, o una quota presa per
  percentuale, sono numeri plausibili e sbagliati.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import analitiche
import instagram_api as ig
import leggi_analitiche as la
import ospita
import registro

CONTO = {"user_id": "178414", "username": "canale_business", "tipo": None}
REEL = "https://www.instagram.com/reel/DdNuovo123/"


class DifettiTesto(unittest.TestCase):
    def test_trenta_hashtag_passano_trentuno_no(self):
        self.assertEqual(ig.difetti_testo("t " + " ".join(f"#h{i}" for i in range(30))), [])
        self.assertTrue(ig.difetti_testo("t " + " ".join(f"#h{i}" for i in range(31))))

    def test_venti_menzioni_passano_ventuno_no(self):
        self.assertEqual(ig.difetti_testo("t " + " ".join(f"@u{i}" for i in range(20))), [])
        self.assertTrue(ig.difetti_testo("t " + " ".join(f"@u{i}" for i in range(21))))

    def test_una_mail_non_e_una_menzione(self):
        self.assertEqual(ig.difetti_testo(" ".join(f"a{i}@b.com" for i in range(25))), [])

    def test_troppo_lunga_e_vuota(self):
        self.assertTrue(ig.difetti_testo("x" * (ig.MAX_CARATTERI + 1)))
        self.assertEqual(ig.difetti_testo("x" * ig.MAX_CARATTERI), [])
        self.assertTrue(ig.difetti_testo("   "))


class ChiE(unittest.TestCase):
    def test_la_risposta_piatta(self):
        self.assertEqual(
            ig.chi_e({"user_id": 178414, "username": "canale_business", "id": "999"}),
            {"user_id": "178414", "username": "canale_business", "tipo": None},
        )

    def test_la_risposta_dentro_data_come_nella_documentazione(self):
        conto = ig.chi_e({"data": [{"user_id": "178414", "username": "canale_business"}]})
        self.assertEqual((conto["user_id"], conto["username"]), ("178414", "canale_business"))

    def test_senza_user_id_non_si_prende_un_altro_campo(self):
        # `id` e' l'id dell'utente *per questa app*, un'altra cosa: senza
        # `user_id` si usa il nodo `me`, non un numero che gli somiglia.
        conto = ig.chi_e({"id": "999", "username": "x"})
        self.assertIsNone(conto["user_id"])
        self.assertEqual(ig._nodo(conto), "me")
        self.assertEqual(ig._nodo(CONTO), "178414")

    def test_una_risposta_vuota_non_dice_chi_e(self):
        self.assertIsNone(ig.chi_e({})["username"])
        self.assertIsNone(ig.chi_e(None)["username"])


class AccountAtteso(unittest.TestCase):
    def test_laccount_giusto_passa(self):
        self.assertEqual(ig.account_atteso(CONTO, "canale_business"), CONTO)

    def test_il_token_di_un_altro_account_ferma(self):
        with self.assertRaises(registro.AccountSbagliato):
            ig.account_atteso({**CONTO, "username": "canale_lab"}, "canale_business")

    def test_un_token_che_non_dice_di_chi_e_ferma(self):
        with self.assertRaises(registro.AccountSbagliato):
            ig.account_atteso({**CONTO, "username": None}, "canale_business")


class EsitoContenitore(unittest.TestCase):
    def test_pronto_non_e_uscito(self):
        self.assertEqual(ig.esito_contenitore({"status_code": "FINISHED"}), ("pronto", None))
        self.assertEqual(ig.esito_contenitore({"status_code": "PUBLISHED"}), ("uscito", None))

    def test_fallito_porta_il_motivo(self):
        esito, errore = ig.esito_contenitore({"status_code": "ERROR", "status": "Error: 2207026"})
        self.assertEqual(esito, "fallito")
        self.assertIn("2207026", errore)
        self.assertEqual(ig.esito_contenitore({"status_code": "EXPIRED"}), ("fallito", "EXPIRED"))

    def test_uno_stato_mai_visto_non_e_ne_pronto_ne_uscito(self):
        for stato in ("IN_PROGRESS", "MAI_VISTO", "", None):
            self.assertEqual(ig.esito_contenitore({"status_code": stato})[0], "in corso", stato)
        self.assertEqual(ig.esito_contenitore(None)[0], "in corso")


class Indirizzo(unittest.TestCase):
    """Cosa va a Whop: l'indirizzo di *quel* reel, o niente."""

    def test_il_permalink_di_un_reel_nella_forma_dello_stato(self):
        self.assertEqual(ig.link_reel(REEL), REEL)
        self.assertEqual(ig.link_reel("https://instagram.com/reel/DdNuovo123"), REEL)
        self.assertEqual(ig.link_reel("https://www.instagram.com/p/DdNuovo123/"), REEL)

    def test_cio_che_non_e_un_reel_non_vale(self):
        for altro in (
            None,
            "",
            "https://www.instagram.com/canale_business/",
            "https://www.instagram.com/",
            "https://www.instagram.com.finto.example/reel/abc/",
            "https://l.instagram.com/?u=https://www.instagram.com/reel/abc/",
        ):
            self.assertIsNone(ig.link_reel(altro), altro)

    def test_il_codice_da_solo_vale_solo_se_il_permalink_manca(self):
        self.assertEqual(ig.link_reel(None, "DdNuovo123"), REEL)
        # Un permalink che c'e' e non e' di un reel non si corregge col codice.
        self.assertIsNone(ig.link_reel("https://www.instagram.com/canale_business/", "DdNuovo123"))
        self.assertIsNone(ig.link_reel(None, "non/un/codice"))


class NuovoReel(unittest.TestCase):
    DA = datetime(2026, 10, 2, 19, 0, tzinfo=timezone.utc)

    @staticmethod
    def media(codice: str, quando: str) -> dict:
        return {
            "id": "m_" + codice,
            "shortcode": codice,
            "timestamp": quando,
            "permalink": f"https://www.instagram.com/reel/{codice}/",
        }

    def test_il_reel_nato_dopo_e_non_ancora_noto(self):
        vecchio = self.media("ieri", "2026-10-01T19:05:00+0000")
        noto = self.media("noto", "2026-10-02T19:01:00+0000")
        nuovo = self.media("nuovo", "2026-10-02T19:04:00+0000")
        self.assertEqual(ig.nuovo_reel([vecchio, noto, nuovo], self.DA, {"noto"}), nuovo)

    def test_due_nuovi_non_si_sceglie(self):
        # Il link di un'altra clip mandato a Whop risulta «inviato»: peggio
        # che non mandarne nessuno.
        a = self.media("a", "2026-10-02T19:02:00+0000")
        b = self.media("b", "2026-10-02T19:05:00+0000")
        self.assertIsNone(ig.nuovo_reel([a, b], self.DA, set()))

    def test_nessun_nuovo_niente_indirizzo(self):
        prima = self.media("prima", "2026-10-02T18:59:59+0000")
        self.assertIsNone(ig.nuovo_reel([prima], self.DA, set()))
        self.assertIsNone(ig.nuovo_reel([], self.DA, set()))
        self.assertIsNone(ig.nuovo_reel([self.media("x", "non una data")], self.DA, set()))

    def test_il_fuso_dellistante_conta(self):
        # Le 21:04 a Roma sono le 19:04 UTC: dopo la soglia.
        roma = self.media("roma", "2026-10-02T21:04:00+0200")
        self.assertEqual(ig.nuovo_reel([roma], self.DA, set()), roma)


class ValoriInsight(unittest.TestCase):
    def test_un_valore_per_metrica(self):
        risposta = {
            "data": [
                {"name": "views", "period": "lifetime", "values": [{"value": 151}]},
                {"name": "reach", "values": [{"value": 120}]},
            ]
        }
        self.assertEqual(ig.valori_insight(risposta), {"views": 151, "reach": 120})

    def test_il_valore_puo_stare_in_total_value(self):
        risposta = {"data": [{"name": "shares", "total_value": {"value": 4}}]}
        self.assertEqual(ig.valori_insight(risposta), {"shares": 4})

    def test_una_metrica_senza_dato_resta_assente_non_zero(self):
        risposta = {"data": [{"name": "reels_skip_rate", "values": []}, {"name": "views", "values": [{"value": 0}]}]}
        self.assertEqual(ig.valori_insight(risposta), {"views": 0})
        self.assertEqual(ig.valori_insight({"data": []}), {})
        self.assertEqual(ig.valori_insight(None), {})


class Scadenza(unittest.TestCase):
    ADESSO = datetime(2026, 10, 20, 12, 0)

    def stato(self, **campi):
        return {"impronta": "x", "visto_il": None, "rinnovato_il": None, "scade_il": None, **campi}

    def test_si_rinnova_dopo_una_settimana_dallultimo_rinnovo(self):
        self.assertFalse(ig.da_rinnovare(self.stato(rinnovato_il="2026-10-14T12:00:00"), self.ADESSO))
        self.assertTrue(ig.da_rinnovare(self.stato(rinnovato_il="2026-10-13T12:00:00"), self.ADESSO))

    def test_un_token_mai_rinnovato_si_conta_da_quando_e_stato_visto(self):
        self.assertFalse(ig.da_rinnovare(self.stato(visto_il="2026-10-19T12:00:00"), self.ADESSO))
        self.assertTrue(ig.da_rinnovare(self.stato(visto_il="2026-10-02T12:00:00"), self.ADESSO))

    def test_lultimo_rinnovo_vince_sulla_prima_vista(self):
        stato = self.stato(visto_il="2026-09-01T12:00:00", rinnovato_il="2026-10-19T12:00:00")
        self.assertFalse(ig.da_rinnovare(stato, self.ADESSO))

    def test_senza_date_non_si_rinnova_alla_cieca(self):
        self.assertFalse(ig.da_rinnovare({}, self.ADESSO))
        self.assertFalse(ig.da_rinnovare(self.stato(visto_il="non una data"), self.ADESSO))

    def test_i_giorni_rimasti(self):
        self.assertEqual(ig.giorni_rimasti(self.stato(scade_il="2026-10-30T12:00:00"), self.ADESSO), 10)
        # Senza una scadenza detta da Meta: al piu' tardi 60 giorni dalla prima vista.
        self.assertEqual(ig.giorni_rimasti(self.stato(visto_il="2026-10-10T12:00:00"), self.ADESSO), 50)
        self.assertIsNone(ig.giorni_rimasti({}, self.ADESSO))


class Token(unittest.TestCase):
    """Il chiamante del rinnovo: quando parte, cosa scrive, e che un guasto non ferma niente."""

    ADESSO = datetime(2026, 10, 20, 12, 0)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cartella = Path(self.tmp.name)
        (self.cartella / "business.txt").write_text("TOKEN_VECCHIO\n", encoding="utf-8")
        self.richieste = []
        self.risposta = {"access_token": "TOKEN_NUOVO", "token_type": "bearer", "expires_in": 5184000}
        self.avvisi = []

        def chiedi(metodo, percorso, tok, dati=None, **kw):
            self.richieste.append((metodo, percorso, tok, dati, kw))
            if isinstance(self.risposta, Exception):
                raise self.risposta
            return self.risposta

        @contextmanager
        def presa(*_a, **_k):
            yield

        for t in (
            mock.patch.object(ig, "CARTELLA", self.cartella),
            mock.patch.object(ig, "chiedi", chiedi),
            mock.patch.object(ig, "log", lambda *_: None),
            mock.patch.object(ig.serratura, "presa", presa),
            mock.patch.object(ig.notifica, "avvisa", lambda t, x: self.avvisi.append(t)),
        ):
            t.start()
            self.addCleanup(t.stop)

    def scrivi_stato(self, **campi):
        stato = {"impronta": ig.impronta("TOKEN_VECCHIO"), "visto_il": None, "rinnovato_il": None, "scade_il": None}
        (self.cartella / "business.json").write_text(json.dumps({**stato, **campi}), encoding="utf-8")

    def sul_disco(self) -> str:
        return (self.cartella / "business.txt").read_text(encoding="utf-8").strip()

    def test_un_token_mai_visto_si_registra_e_non_si_rinnova(self):
        # Meta rifiuta il rinnovo di un token con meno di 24 ore.
        self.assertEqual(ig.token_fresco("business", self.ADESSO), "TOKEN_VECCHIO")
        self.assertEqual(self.richieste, [])
        stato = ig.stato_token("business")
        self.assertEqual((stato["impronta"], stato["visto_il"]), (ig.impronta("TOKEN_VECCHIO"), "2026-10-20T12:00:00"))

    def test_un_token_giovane_non_si_rinnova(self):
        self.scrivi_stato(rinnovato_il="2026-10-18T12:00:00")
        self.assertEqual(ig.token_fresco("business", self.ADESSO), "TOKEN_VECCHIO")
        self.assertEqual(self.richieste, [])

    def test_dopo_una_settimana_si_rinnova_e_si_riscrive(self):
        self.scrivi_stato(visto_il="2026-10-01T12:00:00")
        self.assertEqual(ig.token_fresco("business", self.ADESSO), "TOKEN_NUOVO")
        self.assertEqual(self.sul_disco(), "TOKEN_NUOVO")
        metodo, percorso, tok, dati, kw = self.richieste[0]
        self.assertEqual((metodo, percorso, tok), ("GET", "refresh_access_token", "TOKEN_VECCHIO"))
        self.assertEqual((dati, kw), ({"grant_type": "ig_refresh_token"}, {"versione": False}))
        stato = ig.stato_token("business")
        self.assertEqual(stato["impronta"], ig.impronta("TOKEN_NUOVO"))
        self.assertEqual(stato["scade_il"], "2026-12-19T12:00:00")

    def test_un_rinnovo_fallito_non_ferma_chi_chiama(self):
        self.scrivi_stato(visto_il="2026-10-01T12:00:00")
        self.risposta = ig.ErroreMeta("rete", incerto=True)
        self.assertEqual(ig.token_fresco("business", self.ADESSO), "TOKEN_VECCHIO")
        self.assertEqual(self.sul_disco(), "TOKEN_VECCHIO")
        self.assertEqual(self.avvisi, [])  # mancano 41 giorni: non serve una persona

    def test_vicino_alla_scadenza_il_rinnovo_fallito_avvisa(self):
        self.scrivi_stato(rinnovato_il="2026-09-01T12:00:00", scade_il="2026-10-30T12:00:00")
        self.risposta = ig.ErroreMeta("rete", incerto=True)
        ig.token_fresco("business", self.ADESSO)
        self.assertEqual(len(self.avvisi), 1)

    def test_una_risposta_senza_token_non_tocca_il_file(self):
        self.risposta = {"token_type": "bearer"}
        with self.assertRaises(ig.ErroreMeta):
            ig.rinnova("business", self.ADESSO)
        self.assertEqual(self.sul_disco(), "TOKEN_VECCHIO")

    def test_chi_arriva_secondo_non_rinnova_di_nuovo(self):
        self.scrivi_stato(rinnovato_il="2026-10-20T11:55:00")
        self.assertEqual(ig.rinnova("business", self.ADESSO), "TOKEN_VECCHIO")
        self.assertEqual(self.richieste, [])

    def test_senza_file_si_dice_dove_metterlo(self):
        with self.assertRaises(ig.ErroreMeta) as presa:
            ig.token("podcast")
        self.assertIn("podcast.txt", str(presa.exception))
        self.assertFalse(ig.ha_token("podcast"))
        self.assertTrue(ig.ha_token("business"))


class Insight(unittest.TestCase):
    NO = ig.ErroreMeta("(#100) metric not supported", codice=100)
    SCADUTO = ig.ErroreMeta("scaduto", codice=190)
    RETE = ig.ErroreMeta("rete", incerto=True)

    def chiedi_con(self, rifiuti: dict, intera: Exception | None = None):
        """
        Un finto `chiedi`. La richiesta con tutte le metriche fallisce con
        `intera`, o con un rifiuto qualunque se ne contiene una rifiutata; la
        richiesta di una metrica sola fallisce col rifiuto di quella.
        """
        self.richieste = []

        def chiedi(_metodo, _percorso, _tok, dati=None, **_kw):
            metriche = dati["metric"].split(",")
            self.richieste.append(metriche)
            if len(metriche) > 1 and (intera or rifiuti):
                raise intera or self.NO
            if len(metriche) == 1 and metriche[0] in rifiuti:
                raise rifiuti[metriche[0]]
            return {"data": [{"name": m, "values": [{"value": 7}]} for m in metriche]}

        return mock.patch.object(ig, "chiedi", chiedi)

    def test_tutte_insieme_in_una_richiesta(self):
        with self.chiedi_con({}):
            valori, rifiutate = ig.insight("m1", "tok")
        self.assertEqual((len(self.richieste), rifiutate), (1, {}))
        self.assertEqual(set(valori), set(ig.METRICHE))

    def test_una_metrica_rifiutata_toglie_se_stessa_non_le_altre(self):
        with self.chiedi_con({"reels_skip_rate": self.NO}):
            valori, rifiutate = ig.insight("m1", "tok")
        self.assertEqual(set(rifiutate), {"reels_skip_rate"})
        self.assertEqual(set(valori), set(ig.METRICHE) - {"reels_skip_rate"})

    def test_un_token_scaduto_o_una_rete_muta_non_si_aggirano(self):
        for guasto in (self.SCADUTO, self.RETE):
            with self.chiedi_con({}, intera=guasto):
                with self.assertRaises(ig.ErroreMeta):
                    ig.insight("m1", "tok")
            self.assertEqual(len(self.richieste), 1)

    def test_nemmeno_a_meta_delle_richieste_una_per_una(self):
        # Scritto fra le «rifiutate», un token scaduto farebbe sembrare ogni
        # reel un reel senza numeri: e' un guasto di tutta la lettura.
        for guasto in (self.SCADUTO, self.RETE):
            with self.chiedi_con({"saved": self.NO, "shares": guasto}):
                with self.assertRaises(ig.ErroreMeta):
                    ig.insight("m1", "tok")


class SeguiContenitore(unittest.TestCase):
    def segui(self, risposte, attese=(1, 1, 1)):
        letture = iter(risposte)

        def leggi(_cid, _tok):
            r = next(letture)
            if isinstance(r, Exception):
                raise r
            return r

        with mock.patch.object(ig, "leggi_contenitore", leggi), mock.patch.object(ig, "log", lambda *_: None):
            return ig.segui_contenitore("c1", "tok", attese=attese, dormi=lambda _s: None)

    def test_si_ferma_quando_e_pronto(self):
        esito, _ = self.segui([{"status_code": "IN_PROGRESS"}, {"status_code": "FINISHED"}, AssertionError()])
        self.assertEqual(esito, "pronto")

    def test_a_tempo_scaduto_resta_in_corso(self):
        self.assertEqual(self.segui([{"status_code": "IN_PROGRESS"}] * 3)[0], "in corso")

    def test_una_lettura_mancata_non_cambia_quel_che_si_sa(self):
        esito, _ = self.segui([ig.ErroreMeta("rete"), ig.ErroreMeta("rete"), {"status_code": "FINISHED"}])
        self.assertEqual(esito, "pronto")
        self.assertEqual(self.segui([ig.ErroreMeta("rete")] * 3)[0], "in corso")

    def test_un_fallimento_ferma_subito_e_porta_il_motivo(self):
        esito, errore = self.segui([{"status_code": "ERROR", "status": "2207026"}, AssertionError()])
        self.assertEqual((esito, errore), ("fallito", "2207026"))


class FaiUscire(unittest.TestCase):
    """Il passo che non torna indietro: cosa si dice quando Meta non risponde."""

    def esce(self, pubblica, contenitore=None, elenco=()):
        def pubblica_contenitore(_conto, _tok, _cid):
            if isinstance(pubblica, Exception):
                raise pubblica
            return pubblica

        def leggi_contenitore(_cid, _tok):
            if isinstance(contenitore, Exception):
                raise contenitore
            return contenitore

        toppe = [
            mock.patch.object(ig, "log", lambda *_: None),
            mock.patch.object(ig, "pubblica_contenitore", pubblica_contenitore),
            mock.patch.object(ig, "leggi_contenitore", leggi_contenitore),
            mock.patch.object(ig, "leggi_media", lambda mid, _t: {"permalink": REEL, "shortcode": "DdNuovo123"}),
            mock.patch.object(ig, "elenco_media", lambda *_a, **_k: list(elenco)),
        ]
        for t in toppe:
            t.start()
        try:
            return ig.fai_uscire(CONTO, "tok", "c1", NuovoReel.DA, set())
        finally:
            for t in toppe:
                t.stop()

    def test_uscito_con_il_suo_indirizzo(self):
        with mock.patch.object(ig, "ATTESE_LINK", (0,)):
            fuori = self.esce("media_1")
        self.assertEqual((fuori["esito"], fuori["url"], fuori["media"]), ("uscito", REEL, "media_1"))

    def test_un_no_di_meta_e_un_fallimento(self):
        fuori = self.esce(ig.ErroreMeta("(#9) limite raggiunto", codice=9))
        self.assertEqual((fuori["esito"], fuori["contenitore"]), ("fallito", "c1"))
        self.assertIn("limite", fuori["errore"])

    def test_senza_risposta_e_contenitore_pubblicato_il_reel_e_uscito(self):
        # Dirlo «in corso» o «fallito» vorrebbe dire rifarlo: e' online.
        nuovo = NuovoReel.media("DdNuovo123", "2026-10-02T19:03:00+0000")
        fuori = self.esce(ig.ErroreMeta("timeout", incerto=True), {"status_code": "PUBLISHED"}, [nuovo])
        self.assertEqual((fuori["esito"], fuori["url"], fuori["media"]), ("uscito", REEL, "m_DdNuovo123"))

    def test_uscito_senza_trovarne_lindirizzo_resta_uscito(self):
        fuori = self.esce(ig.ErroreMeta("timeout", incerto=True), {"status_code": "PUBLISHED"}, [])
        self.assertEqual((fuori["esito"], fuori["url"]), ("uscito", None))

    def test_senza_risposta_e_contenitore_ancora_pronto_si_riprova_dopo(self):
        fuori = self.esce(ig.ErroreMeta("timeout", incerto=True), {"status_code": "FINISHED"})
        self.assertEqual((fuori["esito"], fuori["contenitore"]), ("in corso", "c1"))

    def test_senza_risposta_e_senza_stato_non_si_sa_e_non_si_inventa(self):
        fuori = self.esce(ig.ErroreMeta("timeout", incerto=True), ig.ErroreMeta("rete", incerto=True))
        self.assertEqual(fuori["esito"], "in corso")

    def test_senza_risposta_e_contenitore_in_errore_e_fallito(self):
        fuori = self.esce(ig.ErroreMeta("timeout", incerto=True), {"status_code": "ERROR", "status": "x"})
        self.assertEqual(fuori["esito"], "fallito")


class Pubblica(unittest.TestCase):
    """Il chiamante: una regola giusta invocata nel posto sbagliato non si vede."""

    def setUp(self):
        self.esposti = []
        self.creati = []
        self.pubblicati = []
        self.stati = {}
        self.seguito = ("pronto", None)

        @contextmanager
        def espositore(video):
            self.esposti.append(video)
            yield ospita.Esposizione(url="https://finto.trycloudflare.com/gettone/clip.mp4")

        self.espositore = espositore

        def crea(conto, _tok, url, testo):
            self.creati.append((conto["user_id"], url, testo))
            return "c_nuovo"

        def pubblica_contenitore(_conto, _tok, cid):
            self.pubblicati.append(cid)
            return "media_1"

        toppe = [
            mock.patch.object(ig, "log", lambda *_: None),
            mock.patch.object(ig, "token_fresco", lambda *_: "tok"),
            mock.patch.object(ig, "account", lambda _tok: dict(CONTO)),
            mock.patch.object(registro, "handle", lambda *_: "canale_business"),
            mock.patch.object(ig, "crea_contenitore", crea),
            mock.patch.object(ig, "segui_contenitore", lambda *_a, **_k: self.seguito),
            mock.patch.object(ig, "leggi_contenitore", lambda cid, _t: self.stati[cid]),
            mock.patch.object(ig, "pubblica_contenitore", pubblica_contenitore),
            mock.patch.object(ig, "leggi_media", lambda *_: {"permalink": REEL}),
            mock.patch.object(ig, "ATTESE_LINK", (0,)),
        ]
        for t in toppe:
            t.start()
            self.addCleanup(t.stop)

    def pubblica(self, testo="una didascalia #uno", **kw):
        return ig.pubblica(Path("clip.mp4"), testo, "business", espositore=self.espositore, **kw)

    def test_davvero_crea_il_contenitore_e_lo_pubblica(self):
        fuori = self.pubblica(davvero=True)
        self.assertEqual((fuori["esito"], fuori["url"], fuori["contenitore"]), ("uscito", REEL, "c_nuovo"))
        self.assertEqual(
            self.creati,
            [("178414", "https://finto.trycloudflare.com/gettone/clip.mp4", "una didascalia #uno")],
        )
        self.assertEqual(self.pubblicati, ["c_nuovo"])

    def test_la_prova_arriva_al_contenitore_pronto_e_non_pubblica(self):
        fuori = self.pubblica(davvero=False)
        self.assertEqual((fuori["esito"], fuori["contenitore"]), ("prova", "c_nuovo"))
        self.assertEqual((len(self.creati), self.pubblicati), (1, []))

    def test_un_video_che_meta_rifiuta_non_si_pubblica(self):
        self.seguito = ("fallito", "Error: 2207026")
        fuori = self.pubblica(davvero=True)
        self.assertEqual((fuori["esito"], fuori["errore"]), ("fallito", "Error: 2207026"))
        self.assertEqual(self.pubblicati, [])

    def test_un_contenitore_non_pronto_in_tempo_resta_in_corso_col_suo_id(self):
        self.seguito = ("in corso", None)
        fuori = self.pubblica(davvero=True)
        self.assertEqual((fuori["esito"], fuori["contenitore"]), ("in corso", "c_nuovo"))
        self.assertEqual(self.pubblicati, [])

    def test_un_contenitore_ancora_in_corso_non_se_ne_crea_un_altro(self):
        self.stati["c_vecchio"] = {"status_code": "IN_PROGRESS"}
        fuori = self.pubblica(davvero=True, contenitore_precedente="c_vecchio")
        self.assertEqual((fuori["esito"], fuori["contenitore"]), ("in corso", "c_vecchio"))
        self.assertEqual((self.creati, self.esposti, self.pubblicati), ([], [], []))

    def test_un_contenitore_diventato_pronto_si_pubblica_senza_ricaricare(self):
        self.stati["c_vecchio"] = {"status_code": "FINISHED"}
        fuori = self.pubblica(davvero=True, contenitore_precedente="c_vecchio")
        self.assertEqual((fuori["esito"], fuori["url"]), ("uscito", REEL))
        self.assertEqual((self.creati, self.esposti, self.pubblicati), ([], [], ["c_vecchio"]))

    def test_un_contenitore_gia_uscito_non_si_rifa(self):
        self.stati["c_vecchio"] = {"status_code": "PUBLISHED"}
        fuori = self.pubblica(davvero=True, contenitore_precedente="c_vecchio", media_precedente="media_0")
        self.assertEqual((fuori["esito"], fuori["url"], fuori["media"]), ("uscito", REEL, "media_0"))
        self.assertEqual((self.creati, self.pubblicati), ([], []))

    def test_un_contenitore_fallito_si_rifa(self):
        self.stati["c_vecchio"] = {"status_code": "EXPIRED"}
        fuori = self.pubblica(davvero=True, contenitore_precedente="c_vecchio")
        self.assertEqual((fuori["contenitore"], len(self.creati), self.pubblicati), ("c_nuovo", 1, ["c_nuovo"]))

    def test_se_il_contenitore_precedente_non_si_legge_non_se_ne_crea_un_altro(self):
        # «Non so com'e' finito» non e' un permesso di ricominciare.
        def rotto(_cid, _tok):
            raise ig.ErroreMeta("rete", incerto=True)

        with mock.patch.object(ig, "leggi_contenitore", rotto):
            with self.assertRaises(ig.ErroreMeta):
                self.pubblica(davvero=True, contenitore_precedente="c_vecchio")
        self.assertEqual((self.creati, self.esposti), ([], []))

    def test_laccount_sbagliato_ferma_prima_di_esporre_il_file(self):
        with mock.patch.object(ig, "account", lambda _tok: {**CONTO, "username": "canale_lab"}):
            with self.assertRaises(registro.AccountSbagliato):
                self.pubblica(davvero=True)
        self.assertEqual((self.creati, self.esposti), ([], []))

    def test_una_didascalia_che_meta_rifiuta_ferma_prima(self):
        with self.assertRaises(ig.ErroreMeta):
            self.pubblica(testo=" ".join(f"#h{i}" for i in range(31)), davvero=True)
        self.assertEqual((self.creati, self.esposti), ([], []))

    def test_un_tunnel_che_non_parte_e_un_errore_dellagente(self):
        @contextmanager
        def rotto(_video):
            raise RuntimeError("il tunnel non ha dato un indirizzo")
            yield  # pragma: no cover

        with self.assertRaises(ig.ErroreMeta):
            ig.pubblica(Path("clip.mp4"), "testo", "business", davvero=True, espositore=rotto)
        self.assertEqual(self.creati, [])


class DaApiAWhop(unittest.TestCase):
    """
    Il giro intero dell'orchestratore: uscita dall'API, stato, invio a Whop.

    E' il punto che decide se la clip incassa: Whop accetta l'invio per trenta
    minuti dalla pubblicazione, e l'indirizzo che riceve deve essere quello
    del reel uscito adesso.
    """

    def setUp(self):
        import orchestrator

        self.o = orchestrator
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cartella = Path(self.tmp.name)
        (self.cartella / "07.mp4").write_bytes(b"x")
        (self.cartella / "publish.json").write_text(
            json.dumps(
                {
                    "campaign": "Una campagna",
                    "gruppo": "business",
                    "whop_campaign_uuid": "uuid-1",
                    "whop_app": "https://whop.com/x/app/",
                    "handles": {"Instagram": "@acmeshow.tv"},
                    "items": [{"id": "07", "file": "07.mp4", "title": "t", "caption": "c @acmeshow.tv"}],
                }
            ),
            encoding="utf-8",
        )
        self.whop = []
        self.avvisi = []
        self.chiamate = []
        self.api = {"contenitore": "c1", "media": "media_1", "esito": "uscito", "url": REEL, "errore": None}

        def run(cmd, **_kw):
            self.whop.append(cmd)
            return SimpleNamespace(stdout="esito: inviato — ok\n", stderr="", returncode=0)

        @contextmanager
        def presa(*_a, **_k):
            yield

        def pubblica(*a, **k):
            self.chiamate.append(k)
            return self.api

        toppe = [
            mock.patch.object(orchestrator, "log", lambda *_: None),
            mock.patch.object(orchestrator, "print", lambda *_a, **_k: None, create=True),
            mock.patch.object(orchestrator, "cadenza", lambda *_: None),
            mock.patch.object(orchestrator, "interprete", lambda *_: "python"),
            mock.patch.object(orchestrator, "profilo_pubblicazione", lambda *_: self.cartella),
            mock.patch.object(orchestrator.subprocess, "run", run),
            mock.patch.object(orchestrator.serratura, "presa", presa),
            mock.patch.object(orchestrator.notifica, "avvisa", lambda t, x: self.avvisi.append(t)),
            mock.patch.object(registro, "via", lambda *_: "api"),
            # Il registro vero puo' tenere ferma la piattaforma: la prova non
            # deve cambiare esito quando qualcuno la sospende o la riapre.
            mock.patch.object(registro, "sospesa", lambda *_: None),
            mock.patch.object(registro, "whop_profilo", lambda *_: self.cartella),
            mock.patch.object(ig, "pubblica", pubblica),
        ]
        for t in toppe:
            t.start()
            self.addCleanup(t.stop)

    def pubblica(self) -> dict:
        self.o.cmd_pubblica(
            SimpleNamespace(folder=str(self.cartella), id="07", platform="instagram", dry_run=False, forza=False)
        )
        return self.stato()

    def stato(self) -> dict:
        stato = json.loads((self.cartella / "publish_state.json").read_text(encoding="utf-8"))
        return stato["items"]["07@instagram"]

    def inviato_a_whop(self) -> str | None:
        # L'agente del browser non deve partire affatto: l'unico sottoprocesso
        # di questo giro e' Whop.
        self.assertTrue(all("whop_agent.py" in " ".join(map(str, c)) for c in self.whop))
        if not self.whop:
            return None
        cmd = self.whop[0]
        return cmd[cmd.index("--post") + 1]

    def test_lindirizzo_del_reel_va_a_whop(self):
        r = self.pubblica()
        self.assertEqual(self.inviato_a_whop(), REEL)
        self.assertEqual((r["status"], r["url"], r["via"], r["whop_status"]),
                         ("pubblicato", REEL, "api", "inviato"))
        self.assertEqual(self.chiamate[0]["davvero"], True)
        self.assertIsNone(self.chiamate[0]["contenitore_precedente"])

    def test_senza_indirizzo_resta_pubblicato_e_da_inviare(self):
        # «Pubblicato» e non `errore_url`: altrimenti il giro dopo lo rimette
        # online. E qualcuno va avvisato, perche' i trenta minuti corrono.
        self.api["url"] = None
        r = self.pubblica()
        self.assertIsNone(self.inviato_a_whop())
        self.assertEqual((r["status"], r["url"], r["whop_status"], r["api_media"]),
                         ("pubblicato", None, "da inviare", "media_1"))
        self.assertEqual(len(self.avvisi), 1)

    def test_un_contenitore_in_corso_non_va_a_whop_e_tiene_il_suo_id(self):
        self.api.update(esito="in corso", url=None, media=None)
        r = self.pubblica()
        self.assertIsNone(self.inviato_a_whop())
        self.assertEqual((r["status"], r["api_contenitore"], r["via"]), ("api_in_corso", "c1", "api"))

    def test_il_giro_dopo_richiede_quel_contenitore_e_sa_quali_reel_conosce(self):
        self.api.update(esito="in corso", url=None, media=None)
        self.pubblica()
        stato_file = self.cartella / "publish_state.json"
        stato = json.loads(stato_file.read_text(encoding="utf-8"))
        stato["items"]["06@instagram"] = {
            "platform": "instagram", "status": "pubblicato", "url": "https://www.instagram.com/reel/DdNoto/"
        }
        stato_file.write_text(json.dumps(stato), encoding="utf-8")
        self.api.update(esito="uscito", url=REEL, media="media_1")
        r = self.pubblica()
        seconda = self.chiamate[1]
        self.assertEqual(seconda["contenitore_precedente"], "c1")
        self.assertEqual(seconda["noti"], {"DdNoto"})
        self.assertLess(seconda["nato_dopo"], datetime.now() - timedelta(minutes=25))
        self.assertEqual((r["status"], r["url"]), ("pubblicato", REEL))

    def test_un_reel_fallito_non_risulta_pubblicato_e_non_esce_con_zero(self):
        # `esegui.py` legge il codice zero come «fatto».
        self.api.update(esito="fallito", url=None, media=None, errore="Error: 2207026")
        with self.assertRaises(SystemExit) as presa:
            self.pubblica()
        self.assertNotIn(presa.exception.code, (0, None))
        r = self.stato()
        self.assertIsNone(self.inviato_a_whop())
        self.assertEqual((r["status"], r["errore"], r["api_contenitore"]), ("api_fallito", "Error: 2207026", "c1"))

    def test_laccount_sbagliato_non_scrive_nessuno_stato(self):
        def sbagliato(*_a, **_k):
            raise registro.AccountSbagliato("instagram (API): collegato come @altro")

        with mock.patch.object(ig, "pubblica", sbagliato):
            with self.assertRaises(SystemExit):
                self.pubblica()
        self.assertFalse((self.cartella / "publish_state.json").exists())
        self.assertEqual(self.whop, [])


class Via(unittest.TestCase):
    def via(self, piattaforma, valore):
        voce = {"id": f"business-{piattaforma}", "gruppo": "business", "piattaforma": piattaforma}
        if valore is not None:
            voce["pubblica_via"] = valore
        with mock.patch.object(registro, "_leggi", lambda: {"identita": [voce]}):
            return registro.via("business", piattaforma)

    def test_instagram_dal_browser_o_dallapi(self):
        self.assertEqual(self.via("instagram", None), "browser")
        self.assertEqual(self.via("instagram", " API "), "api")

    def test_una_strada_di_unaltra_piattaforma_ferma(self):
        # Scritta bene ma nel posto sbagliato: la clip uscirebbe dal browser
        # con l'etichetta di una strada che non ha preso.
        for piattaforma, valore in (("instagram", "buffer"), ("tiktok", "api"), ("youtube", "api"), ("youtube", "buffer")):
            with self.assertRaises(SystemExit, msg=(piattaforma, valore)):
                self.via(piattaforma, valore)


class Analitiche(unittest.TestCase):
    """Dagli insight grezzi ai campi comuni: secondi, quote fra 0 e 1, e mai uno zero inventato."""

    VALORI = {
        "views": 151,
        "reach": 120,
        "ig_reels_avg_watch_time": 4862,
        "ig_reels_video_view_total_time": 734162,
        "reels_skip_rate": 46.5,
        "likes": 3,
        "comments": 0,
        "saved": 1,
        "shares": 2,
        "reposts": 0,
        "total_interactions": 6,
    }

    def test_i_numeri_principali(self):
        a = la.analitiche_instagram(self.VALORI)
        self.assertEqual((a["views"], a["spettatori_unici"]), (151, 120))
        self.assertEqual((a["like"], a["commenti"], a["salvataggi"], a["condivisioni"]), (3, 0, 1, 2))

    def test_millisecondi_in_secondi(self):
        a = la.analitiche_instagram(self.VALORI)
        self.assertEqual((a["tempo_medio_s"], a["tempo_totale_s"]), (4.86, 734.16))

    def test_un_tempo_che_in_millisecondi_non_sta_in_piedi_resta_vuoto(self):
        # 8 sarebbe otto millesimi di secondo: oppure e' in secondi, e allora
        # dividerlo lo falserebbe. In tutti e due i casi non si scrive, e il
        # totale — stessa unita' — nemmeno.
        a = la.analitiche_instagram({**self.VALORI, "ig_reels_avg_watch_time": 8})
        self.assertEqual((a["tempo_medio_s"], a["tempo_totale_s"]), (None, None))
        # E nemmeno un tempo medio piu' lungo del reel piu' lungo possibile.
        a = la.analitiche_instagram({**self.VALORI, "ig_reels_avg_watch_time": 2_000_000})
        self.assertIsNone(a["tempo_medio_s"])

    def test_lo_skip_rate_sopra_uno_e_una_percentuale(self):
        self.assertEqual(la.analitiche_instagram(self.VALORI)["scorre_via_3s"], 0.465)

    def test_lo_skip_rate_ambiguo_resta_vuoto_se_la_scala_non_e_detta(self):
        # 0,46 puo' essere il 46% o lo 0,46%; 1 puo' essere il 100% o l'1%.
        for ambiguo in (0.46, 1, 1.0):
            a = la.analitiche_instagram({**self.VALORI, "reels_skip_rate": ambiguo}, scala=None)
            self.assertIsNone(a["scorre_via_3s"], ambiguo)
        senza = la.analitiche_instagram({**self.VALORI, "reels_skip_rate": 0}, scala=None)
        self.assertEqual(senza["scorre_via_3s"], 0.0)

    # Un reel letto davvero il 3 ottobre 2026 (Dd90YPsxeoC): e' la risposta che
    # ha detto le due unita' che la documentazione non scrive.
    VERO = {
        "views": 55,
        "reach": 47,
        "ig_reels_avg_watch_time": 4681,
        "ig_reels_video_view_total_time": 234052,
        "reels_skip_rate": 68.1,
        "likes": 1,
        "comments": 0,
        "saved": 0,
        "shares": 0,
        "total_interactions": 1,
    }

    def test_la_scala_misurata_e_la_percentuale(self):
        self.assertEqual(la.SCALA_SCORRIMENTO, "percento")
        a = la.analitiche_instagram(self.VERO)
        self.assertEqual(a["scorre_via_3s"], 0.681)
        # Con la scala detta anche un valore piccolo si converte: e' lo 0,46%.
        self.assertEqual(la.analitiche_instagram({"reels_skip_rate": 0.46})["scorre_via_3s"], 0.0046)

    def test_i_millisecondi_tornano_su_una_risposta_vera(self):
        # Tempo totale diviso views ≈ tempo medio: lo stesso numero, in secondi,
        # farebbe 71 minuti a testa per una clip da un minuto.
        a = la.analitiche_instagram(self.VERO)
        self.assertEqual((a["tempo_medio_s"], a["tempo_totale_s"]), (4.68, 234.05))
        self.assertLess(abs(a["tempo_totale_s"] / a["views"] - a["tempo_medio_s"]) / a["tempo_medio_s"], 0.2)

    def test_con_la_scala_detta_si_converte_tutto(self):
        quota = la.analitiche_instagram({**self.VALORI, "reels_skip_rate": 0.46}, scala="quota")
        percento = la.analitiche_instagram({**self.VALORI, "reels_skip_rate": 0.46}, scala="percento")
        self.assertEqual((quota["scorre_via_3s"], percento["scorre_via_3s"]), (0.46, 0.0046))

    def test_cio_che_meta_non_da_resta_vuoto_non_zero(self):
        a = la.analitiche_instagram({"views": 12})
        self.assertEqual(a["views"], 12)
        for campo in ("spettatori_unici", "tempo_medio_s", "scorre_via_3s", "salvataggi", "like"):
            self.assertIsNone(a[campo], campo)

    def test_like_e_commenti_dallelenco_se_gli_insight_non_li_hanno(self):
        a = la.analitiche_instagram({"views": 12}, {"like_count": 4, "comments_count": 1})
        self.assertEqual((a["like"], a["commenti"]), (4, 1))
        a = la.analitiche_instagram({"views": 12, "likes": 0}, {"like_count": 4})
        self.assertEqual(a["like"], 0)

    def test_la_tenuta_nel_pannello_e_chi_non_scorre_via(self):
        riga = {"post_id": "P", "piattaforma": "instagram", "dati": la.analitiche_instagram(self.VALORI), "guasto": None}
        s = analitiche.sintesi(riga)
        self.assertEqual(s["tenuta"], ["oltre i 3 s", 0.535])
        self.assertEqual(s["tempo_medio_s"], 4.86)
        # Dal web lo skip rate non c'e': la tenuta resta vuota, non 100%.
        dal_web = {"post_id": "P", "piattaforma": "instagram", "dati": {"views": 5}, "guasto": None}
        self.assertIsNone(analitiche.sintesi(dal_web)["tenuta"])

    def test_la_riga_in_piu_si_scrive_solo_se_i_numeri_vengono_dallapi(self):
        base = {"post_id": "P", "cartella": None, "clip_id": None, "piattaforma": "instagram", "guasto": None}
        api = "\n".join(analitiche.descrivi({**base, "dati": la.analitiche_instagram(self.VALORI)}))
        web = "\n".join(analitiche.descrivi({**base, "dati": {"views": 5, "like": 1, "commenti": 0}}))
        self.assertIn("scorre via nei primi 3 s 46%", api)
        self.assertNotIn("scorre via", web)


def reel(codice, valori, rifiutate=None):
    return {
        "id": codice,
        "url": f"https://www.instagram.com/reel/{codice}/",
        "media": {"id": "m_" + codice, "like_count": 2},
        "valori": valori,
        "rifiutate": rifiutate or {},
    }


class Righe(unittest.TestCase):
    def test_un_reel_senza_numeri_resta_col_motivo_di_meta(self):
        righe, _ = la.righe_instagram_api(
            [reel("nuovo", {"views": 40}), reel("vecchio", {}, {"views": "posted before conversion"})]
        )
        self.assertEqual(righe[0]["analitiche"]["views"], 40)
        self.assertIsNone(righe[1]["analitiche"])
        self.assertIn("before conversion", righe[1]["guasto"])
        self.assertEqual(righe[1]["grezzo"]["rifiutate"], {"views": "posted before conversion"})

    def test_un_valore_lasciato_vuoto_per_lunita_si_dichiara(self):
        # 8 millisecondi di tempo medio non stanno in piedi: resta vuoto, e lo si dice.
        _, limiti = la.righe_instagram_api([reel("a", {"views": 40, "ig_reels_avg_watch_time": 8})])
        self.assertTrue(any("unita'" in x for x in limiti), limiti)
        _, limiti = la.righe_instagram_api([reel("a", {"views": 40, "ig_reels_avg_watch_time": 4681})])
        self.assertFalse(any("unita'" in x for x in limiti), limiti)

    def test_il_web_riempie_solo_chi_e_rimasto_vuoto(self):
        righe, _ = la.righe_instagram_api([reel("nuovo", {"views": 40}), reel("vecchio", {})])
        web = [
            {"id": "nuovo", "views": 999, "like": 9, "commenti": 9},
            {"id": "vecchio", "views": 151, "like": 3, "commenti": 0},
        ]
        self.assertEqual(la.completa_dal_web(righe, web), 1)
        self.assertEqual(righe[0]["analitiche"]["views"], 40)
        self.assertEqual(righe[1]["analitiche"], {"views": 151, "like": 3, "commenti": 0})
        self.assertIsNone(righe[1]["guasto"])


class Leggi(unittest.TestCase):
    """Il chiamante: col token si legge dall'API, e il browser si apre solo se serve."""

    def leggi(self, token, reel_api, web=None):
        self.aperture = 0

        def dal_web(*_a, **_k):
            self.aperture += 1
            if isinstance(web, BaseException):
                raise web
            return web or []

        @contextmanager
        def presa(*_a, **_k):
            yield

        with mock.patch.object(la.instagram_api, "ha_token", lambda _g: token), mock.patch.object(
            la.instagram_api, "reel_con_numeri", lambda *_a: reel_api
        ), mock.patch.object(la.leggi_account, "leggi_instagram", dal_web), mock.patch.object(
            la.registro, "profilo_obbligatorio", lambda *_: Path("profilo")
        ), mock.patch.object(la.registro, "handle", lambda *_: "canale_business"), mock.patch.object(
            la.serratura, "presa", presa
        ):
            return la.leggi("business", "instagram", set(), True)

    def test_col_token_i_numeri_vengono_dallapi_e_il_browser_resta_chiuso(self):
        righe, limiti = self.leggi(True, [reel("a", {"views": 40, "reach": 30})])
        self.assertEqual((righe[0]["analitiche"]["spettatori_unici"], self.aperture), (30, 0))
        self.assertIn(la.LIMITE_API_INSTAGRAM, limiti)

    def test_un_reel_senza_numeri_fa_aprire_il_web_per_quello(self):
        web = [{"id": "vecchio", "views": 151, "like": 3, "commenti": 0}]
        righe, _ = self.leggi(True, [reel("a", {"views": 40}), reel("vecchio", {})], web)
        self.assertEqual(self.aperture, 1)
        self.assertEqual(righe[1]["analitiche"]["views"], 151)

    def test_se_il_web_non_si_apre_la_lettura_dallapi_resta(self):
        righe, limiti = self.leggi(True, [reel("a", {"views": 40}), reel("vecchio", {})], SystemExit("sessione scaduta"))
        self.assertEqual(righe[0]["analitiche"]["views"], 40)
        self.assertIsNone(righe[1]["analitiche"])
        self.assertTrue(any("sessione scaduta" in x for x in limiti), limiti)

    def test_senza_token_resta_la_lettura_dal_web_col_suo_limite(self):
        web = [{"id": "a", "url": "u", "views": 40, "like": 1, "commenti": 0}]
        righe, limiti = self.leggi(False, [], web)
        self.assertEqual((self.aperture, righe[0]["analitiche"]["views"]), (1, 40))
        self.assertEqual(limiti, [la.LIMITE_INSTAGRAM])


if __name__ == "__main__":
    unittest.main()
