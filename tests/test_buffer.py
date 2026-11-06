"""La pubblicazione che passa da Buffer: le regole che non danno errore se rotte.

Buffer e' la quarta porta da cui una clip puo' uscire, e i modi di sbagliarla
sono quelli gia' pagati sulle altre tre, piu' uno suo:

- **l'account**: il canale Buffer e' di un'altra pagina, o di un'altra
  piattaforma, o non c'e' piu'. Il caricamento riesce comunque.
- **il doppione**: un post rimasto «in corso» al giro prima puo' essere uscito
  nel frattempo. Crearne un secondo mette la stessa clip online due volte.
- **l'esito**: uno stato che non conosciamo non e' un'uscita. E un post uscito
  senza indirizzo non e' un post da rifare.
- **il suo**: l'indirizzo del tunnel viene annunciato prima che il nome esista,
  e chiederlo troppo presto lo rende irraggiungibile da qui per minuti.
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

import buffer_agent
import ospita
import registro

CANALI = [
    {"id": "canale_tiktok", "name": "canale_business", "service": "tiktok"},
    {"id": "canale_insta", "name": "canale_business", "service": "instagram"},
    {"id": "canale_altro", "name": "canale_lab", "service": "tiktok"},
]


class CanaleAtteso(unittest.TestCase):
    def ferma(self, canale_id, handle="canale_business", canali=CANALI):
        with self.assertRaises(registro.AccountSbagliato):
            buffer_agent.canale_atteso(canali, canale_id, "tiktok", handle)

    def test_il_canale_giusto_passa(self):
        c = buffer_agent.canale_atteso(CANALI, "canale_tiktok", "tiktok", "canale_business")
        self.assertEqual(c["id"], "canale_tiktok")

    def test_il_registro_non_dice_quale_canale(self):
        # Si fermerebbe comunque («il canale non c'e'»), ma mandando a cercare
        # un guasto dentro Buffer: quello che manca e' una riga del registro.
        for vuoto in (None, ""):
            with self.assertRaises(registro.AccountSbagliato) as presa:
                buffer_agent.canale_atteso(CANALI, vuoto, "tiktok", "canale_business")
            self.assertIn("buffer_canale", str(presa.exception))

    def test_il_canale_e_stato_scollegato(self):
        self.ferma("canale_sparito")

    def test_il_canale_e_di_unaltra_piattaforma(self):
        # Stesso handle, piattaforma diversa: la clip uscirebbe su Instagram
        # con la didascalia e lo stato di TikTok.
        self.ferma("canale_insta")

    def test_il_canale_e_di_un_altro_account(self):
        self.ferma("canale_altro")

    def test_il_canale_risulta_scollegato(self):
        scollegato = [{**CANALI[0], "isDisconnected": True}]
        self.ferma("canale_tiktok", canali=scollegato)

    def test_buffer_non_dice_di_chi_e_il_canale(self):
        self.ferma("senza_nome", canali=[{"id": "senza_nome", "name": None, "service": "tiktok"}])


class EsitoPost(unittest.TestCase):
    def test_uscito_con_indirizzo(self):
        post = {"status": "sent", "externalLink": "https://www.tiktok.com/@x/video/1"}
        self.assertEqual(
            buffer_agent.esito_post(post), ("uscito", "https://www.tiktok.com/@x/video/1", None)
        )

    def test_uscito_senza_indirizzo_resta_uscito(self):
        # Online e senza link non e' «in corso»: chi lo confonde ripubblica.
        self.assertEqual(
            buffer_agent.esito_post({"status": "sent", "externalLink": None}),
            ("uscito", None, None),
        )

    def test_fallito_porta_il_motivo(self):
        esito, url, errore = buffer_agent.esito_post(
            {"status": "error", "error": {"message": "channel disconnected"}}
        )
        self.assertEqual((esito, url), ("fallito", None))
        self.assertIn("disconnected", errore)

    def test_uno_stato_mai_visto_non_e_unuscita(self):
        for stato in ("scheduled", "sending", "needs_approval", "draft", "mai_visto", "", None):
            self.assertEqual(buffer_agent.esito_post({"status": stato})[0], "in corso", stato)
        self.assertEqual(buffer_agent.esito_post(None)[0], "in corso")


class DifettiTesto(unittest.TestCase):
    def test_cinque_hashtag_passano_sei_no(self):
        self.assertEqual(buffer_agent.difetti_testo("testo #a #b #c #d #e"), [])
        self.assertTrue(buffer_agent.difetti_testo("testo #a #b #c #d #e #f"))

    def test_un_cancelletto_dentro_una_parola_non_e_un_hashtag(self):
        self.assertEqual(buffer_agent.difetti_testo("c#a c#b c#c c#d c#e c#f #uno"), [])

    def test_troppo_lunga_e_vuota(self):
        self.assertTrue(buffer_agent.difetti_testo("x" * (buffer_agent.MAX_CARATTERI + 1)))
        self.assertEqual(buffer_agent.difetti_testo("x" * buffer_agent.MAX_CARATTERI), [])
        self.assertTrue(buffer_agent.difetti_testo("   "))


class Schema(unittest.TestCase):
    def test_tutto_come_atteso(self):
        veri = {tipo: set(campi) | {"altro"} for tipo, campi in buffer_agent.ATTESO.items()}
        self.assertEqual(buffer_agent.differenze_schema(veri), [])

    def test_un_campo_sparito_si_dice(self):
        veri = {tipo: set(campi) for tipo, campi in buffer_agent.ATTESO.items()}
        veri["Post"].discard("externalLink")
        self.assertEqual(
            buffer_agent.differenze_schema(veri), ["Post.externalLink: il campo non esiste"]
        )

    def test_un_tipo_sparito_si_dice(self):
        veri = {tipo: set(campi) for tipo, campi in buffer_agent.ATTESO.items()}
        del veri["Channel"]
        self.assertEqual(buffer_agent.differenze_schema(veri), ["Channel: il tipo non esiste"])


class Segui(unittest.TestCase):
    def segui(self, risposte, attese=(1, 1, 1)):
        letture = iter(risposte)

        def leggi(_id):
            r = next(letture)
            if isinstance(r, Exception):
                raise r
            return r

        with mock.patch.object(buffer_agent, "leggi_post", leggi), mock.patch.object(
            buffer_agent, "log", lambda *_: None
        ):
            return buffer_agent.segui("post_1", attese=attese, dormi=lambda _s: None)

    def test_si_ferma_quando_esce_con_lindirizzo(self):
        fuori = self.segui(
            [{"status": "buffer"}, {"status": "sent", "externalLink": "https://t/1"}, AssertionError()]
        )
        self.assertEqual((fuori["esito"], fuori["url"]), ("uscito", "https://t/1"))

    def test_a_tempo_scaduto_resta_in_corso(self):
        fuori = self.segui([{"status": "buffer"}] * 3)
        self.assertEqual((fuori["esito"], fuori["post"]), ("in corso", "post_1"))

    def test_una_lettura_mancata_non_cambia_quel_che_si_sa(self):
        fuori = self.segui(
            [{"status": "sent", "externalLink": None}, buffer_agent.ErroreBuffer("rete"), {"status": "sent"}]
        )
        self.assertEqual(fuori["esito"], "uscito")

    def test_uscito_senza_indirizzo_continua_a_chiederlo(self):
        fuori = self.segui(
            [{"status": "sent"}, {"status": "sent"}, {"status": "sent", "externalLink": "https://t/2"}]
        )
        self.assertEqual(fuori["url"], "https://t/2")


    def test_uscito_senza_indirizzo_non_aspetta_per_sempre(self):
        # Da quando e' online corre la mezz'ora di Whop: dopo un paio di
        # richieste si esce, e l'indirizzo lo si legge da Studio.
        letture = buffer_agent.PAZIENZA_LINK + 1
        fuori = self.segui([{"status": "sent"}] * letture + [AssertionError()], attese=(1,) * 9)
        self.assertEqual((fuori["esito"], fuori["url"]), ("uscito", None))


class Indirizzo(unittest.TestCase):
    """Cosa va a Whop: l'indirizzo di *quel* video, o niente."""

    VIDEO = "https://www.tiktok.com/@canale_business/video/7600000000000000101"

    def test_solo_lindirizzo_di_un_video_vale(self):
        self.assertEqual(buffer_agent.link_tiktok(self.VIDEO), self.VIDEO)
        for altro in (
            None,
            "",
            "https://www.tiktok.com/@canale_business",
            "https://vm.tiktok.com/ZMabc123/",
            "https://publish.buffer.com/post/abc",
            "https://www.tiktok.com.finto.example/@x/video/1",
        ):
            self.assertIsNone(buffer_agent.link_tiktok(altro), altro)

    # Tre video: uno di ieri, uno gia' nello stato, uno nato dopo `da`.
    DA = datetime(2026, 10, 1, 21, 0, 0)

    @staticmethod
    def video(quando: datetime) -> dict:
        pid = str(int(quando.timestamp()) << 32 | 12345)
        return {"id": pid, "url": f"https://www.tiktok.com/@canale_business/video/{pid}"}

    def test_il_video_nato_dopo_e_non_ancora_noto(self):
        vecchio = self.video(datetime(2026, 9, 30, 12, 0))
        noto = self.video(datetime(2026, 10, 1, 21, 1))
        nuovo = self.video(datetime(2026, 10, 1, 21, 4))
        url = buffer_agent.nuovo_tiktok([vecchio, noto, nuovo], self.DA, {noto["id"]})
        self.assertEqual(url, nuovo["url"])

    def test_due_nuovi_non_si_sceglie(self):
        # Il link di un'altra clip mandato a Whop risulta «inviato»: peggio
        # che non mandarne nessuno.
        a = self.video(datetime(2026, 10, 1, 21, 2))
        b = self.video(datetime(2026, 10, 1, 21, 5))
        self.assertIsNone(buffer_agent.nuovo_tiktok([a, b], self.DA, set()))

    def test_nessun_nuovo_niente_indirizzo(self):
        vecchio = self.video(datetime(2026, 10, 1, 20, 59))
        self.assertIsNone(buffer_agent.nuovo_tiktok([vecchio], self.DA, set()))
        self.assertIsNone(buffer_agent.nuovo_tiktok([], self.DA, set()))
        self.assertIsNone(buffer_agent.nuovo_tiktok([{"id": "non-un-numero"}], self.DA, set()))


class DaBufferAWhop(unittest.TestCase):
    """
    Il giro intero dell'orchestratore: uscita da Buffer, stato, invio a Whop.

    E' il punto che decide se la clip incassa: Whop accetta l'invio per trenta
    minuti dalla pubblicazione, e l'indirizzo che riceve deve essere quello
    del video uscito adesso.
    """

    VIDEO = "https://www.tiktok.com/@canale_business/video/7691900000000000001"
    STUDIO = "https://www.tiktok.com/@canale_business/video/7691900000000000002"

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
                    "handles": {"TikTok": "@acmeshowtv"},
                    "items": [{"id": "07", "file": "07.mp4", "title": "t", "caption": "c @acmeshowtv"}],
                }
            ),
            encoding="utf-8",
        )
        self.whop = []
        self.avvisi = []
        self.buffer = {"post": "post_1", "esito": "uscito", "url": self.VIDEO, "errore": None}
        self.studio = None

        def run(cmd, **_kw):
            self.whop.append(cmd)
            return SimpleNamespace(stdout="esito: inviato — ok\n", stderr="", returncode=0)

        @contextmanager
        def presa(*_a, **_k):
            yield

        toppe = [
            mock.patch.object(orchestrator, "log", lambda *_: None),
            mock.patch.object(orchestrator, "print", lambda *_a, **_k: None, create=True),
            mock.patch.object(orchestrator, "cadenza", lambda *_: None),
            mock.patch.object(orchestrator, "interprete", lambda *_: "python"),
            mock.patch.object(orchestrator, "profilo_pubblicazione", lambda *_: self.cartella),
            mock.patch.object(orchestrator, "indirizzo_da_studio", lambda *_: self.studio),
            mock.patch.object(orchestrator.subprocess, "run", run),
            mock.patch.object(orchestrator.serratura, "presa", presa),
            mock.patch.object(orchestrator.notifica, "avvisa", lambda t, x: self.avvisi.append(t)),
            mock.patch.object(registro, "via", lambda *_: "buffer"),
            # Il registro vero puo' tenere ferma la piattaforma: la prova non
            # deve cambiare esito quando qualcuno la sospende o la riapre.
            mock.patch.object(registro, "sospesa", lambda *_: None),
            mock.patch.object(registro, "whop_profilo", lambda *_: self.cartella),
            mock.patch.object(buffer_agent, "pubblica", lambda *_a, **_k: self.buffer),
        ]
        for t in toppe:
            t.start()
            self.addCleanup(t.stop)

    def pubblica(self) -> dict:
        self.o.cmd_pubblica(
            SimpleNamespace(folder=str(self.cartella), id="07", platform="tiktok", dry_run=False, forza=False)
        )
        stato = json.loads((self.cartella / "publish_state.json").read_text(encoding="utf-8"))
        return stato["items"]["07@tiktok"]

    def inviato_a_whop(self) -> str | None:
        if not self.whop:
            return None
        cmd = self.whop[0]
        return cmd[cmd.index("--post") + 1]

    def test_lindirizzo_di_buffer_va_a_whop(self):
        r = self.pubblica()
        self.assertEqual(self.inviato_a_whop(), self.VIDEO)
        self.assertEqual((r["status"], r["url"], r["via"], r["whop_status"]),
                         ("pubblicato", self.VIDEO, "buffer", "inviato"))
        self.assertIn("uuid-1", self.whop[0])

    def test_senza_indirizzo_da_buffer_va_quello_di_studio(self):
        self.buffer["url"] = None
        self.studio = self.STUDIO
        r = self.pubblica()
        self.assertEqual(self.inviato_a_whop(), self.STUDIO)
        self.assertEqual((r["url"], r["whop_status"]), (self.STUDIO, "inviato"))

    def test_un_indirizzo_che_non_e_un_video_non_va_a_whop(self):
        self.buffer["url"] = "https://www.tiktok.com/@canale_business"
        self.studio = self.STUDIO
        self.pubblica()
        self.assertEqual(self.inviato_a_whop(), self.STUDIO)

    def test_senza_nessun_indirizzo_resta_pubblicato_e_da_inviare(self):
        # «Pubblicato» e non `errore_url`: altrimenti il giro dopo la rimette
        # online. E qualcuno va avvisato, perche' i trenta minuti corrono.
        self.buffer["url"] = None
        r = self.pubblica()
        self.assertIsNone(self.inviato_a_whop())
        self.assertEqual((r["status"], r["url"], r["whop_status"], r["buffer_post"]),
                         ("pubblicato", None, "da inviare", "post_1"))
        self.assertEqual(len(self.avvisi), 1)

    def test_un_post_ancora_in_corso_non_va_a_whop_e_tiene_il_suo_id(self):
        self.buffer.update(esito="in corso", url=None)
        r = self.pubblica()
        self.assertIsNone(self.inviato_a_whop())
        self.assertEqual((r["status"], r["buffer_post"]), ("buffer_in_corso", "post_1"))

    def test_un_post_ancora_in_corso_non_e_un_guasto(self):
        # Esce con zero: il post vive dentro Buffer, e l'attivita' pianificata
        # non deve tingersi di rosso per un'attesa.
        self.buffer.update(esito="in corso", url=None)
        self.pubblica()  # un SystemExit qui farebbe fallire la prova

    def test_un_post_fallito_non_risulta_pubblicato_e_non_esce_con_zero(self):
        # `esegui.py` legge il codice zero come «fatto»: un post fallito
        # finiva fra le clip pubblicate del giro.
        self.buffer.update(esito="fallito", url=None, errore="canale scollegato")
        with self.assertRaises(SystemExit) as presa:
            self.pubblica()
        self.assertNotIn(presa.exception.code, (0, None))
        self.assertIn("canale scollegato", str(presa.exception.code))
        stato = json.loads((self.cartella / "publish_state.json").read_text(encoding="utf-8"))
        r = stato["items"]["07@tiktok"]
        self.assertIsNone(self.inviato_a_whop())
        self.assertEqual((r["status"], r["errore"]), ("buffer_fallito", "canale scollegato"))


class Pubblica(unittest.TestCase):
    """Il chiamante: una regola giusta invocata nel posto sbagliato non si vede."""

    def setUp(self):
        self.esposti = []
        self.creati = []

        @contextmanager
        def espositore(video):
            self.esposti.append(video)
            yield ospita.Esposizione(url="https://finto.trycloudflare.com/gettone/clip.mp4")

        self.espositore = espositore
        self.toppe = [
            mock.patch.object(buffer_agent, "log", lambda *_: None),
            mock.patch.object(buffer_agent, "canali", lambda: CANALI),
            mock.patch.object(registro, "handle", lambda *_: "canale_business"),
            mock.patch.object(registro, "buffer_canale", lambda *_: "canale_tiktok"),
            mock.patch.object(buffer_agent, "crea_post", self.crea),
            mock.patch.object(
                buffer_agent, "segui", lambda pid: {"post": pid, "esito": "uscito", "url": "https://t/9", "errore": None}
            ),
        ]
        for t in self.toppe:
            t.start()
            self.addCleanup(t.stop)

    def crea(self, canale_id, testo, url):
        self.creati.append((canale_id, testo, url))
        return "post_nuovo"

    def pubblica(self, **kw):
        return buffer_agent.pubblica(
            Path("clip.mp4"), "una didascalia #uno", "business", "tiktok",
            espositore=self.espositore, **kw,
        )

    def test_davvero_crea_un_post_sul_canale_del_registro(self):
        fuori = self.pubblica(davvero=True)
        self.assertEqual(fuori["url"], "https://t/9")
        self.assertEqual(
            self.creati,
            [("canale_tiktok", "una didascalia #uno", "https://finto.trycloudflare.com/gettone/clip.mp4")],
        )

    def test_la_prova_non_crea_niente(self):
        fuori = self.pubblica(davvero=False)
        self.assertEqual((fuori["esito"], self.creati), ("prova", []))
        self.assertEqual(len(self.esposti), 1)

    def test_un_post_ancora_in_corso_non_se_ne_crea_un_altro(self):
        with mock.patch.object(buffer_agent, "leggi_post", lambda _id: {"status": "buffer"}):
            fuori = self.pubblica(davvero=True, post_precedente="post_vecchio")
        self.assertEqual((fuori["esito"], fuori["post"]), ("in corso", "post_vecchio"))
        self.assertEqual((self.creati, self.esposti), ([], []))

    def test_un_post_uscito_nel_frattempo_non_si_rifa(self):
        uscito = {"status": "sent", "externalLink": "https://t/vecchio"}
        with mock.patch.object(buffer_agent, "leggi_post", lambda _id: uscito):
            fuori = self.pubblica(davvero=True, post_precedente="post_vecchio")
        self.assertEqual((fuori["esito"], fuori["url"]), ("uscito", "https://t/vecchio"))
        self.assertEqual(self.creati, [])

    def test_un_post_fallito_si_rifa(self):
        with mock.patch.object(buffer_agent, "leggi_post", lambda _id: {"status": "error"}):
            fuori = self.pubblica(davvero=True, post_precedente="post_vecchio")
        self.assertEqual((fuori["post"], len(self.creati)), ("post_nuovo", 1))

    def test_laccount_sbagliato_ferma_prima_di_esporre_il_file(self):
        with mock.patch.object(registro, "buffer_canale", lambda *_: "canale_altro"):
            with self.assertRaises(registro.AccountSbagliato):
                self.pubblica(davvero=True)
        self.assertEqual((self.creati, self.esposti), ([], []))

    def test_una_didascalia_che_buffer_rifiuta_ferma_prima(self):
        with self.assertRaises(buffer_agent.ErroreBuffer):
            buffer_agent.pubblica(
                Path("clip.mp4"), "#a #b #c #d #e #f", "business", "tiktok",
                davvero=True, espositore=self.espositore,
            )
        self.assertEqual((self.creati, self.esposti), ([], []))


class Via(unittest.TestCase):
    def via(self, valore):
        voce = {"id": "business-tiktok", "gruppo": "business", "piattaforma": "tiktok"}
        if valore is not None:
            voce["pubblica_via"] = valore
        with mock.patch.object(registro, "_leggi", lambda: {"identita": [voce]}):
            return registro.via("business", "tiktok")

    def test_senza_il_campo_e_il_browser(self):
        self.assertEqual(self.via(None), "browser")

    def test_buffer_scritto_in_qualunque_modo(self):
        self.assertEqual(self.via("buffer"), "buffer")
        self.assertEqual(self.via(" Buffer "), "buffer")

    def test_una_strada_scritta_male_ferma(self):
        # Non ripiega sul browser: le due strade non rendono uguale, e la clip
        # uscirebbe lo stesso dalla strada che nessuno ha scelto.
        with self.assertRaises(SystemExit):
            self.via("bufer")


class Tunnel(unittest.TestCase):
    PRIMA = "INF |  https://grande-sharon-font-fewer.trycloudflare.com  |\n"
    DOPO = PRIMA + "INF Registered tunnel connection connIndex=0 location=fco01\n"

    def test_lindirizzo_non_si_consegna_prima_dellaggancio(self):
        # Annunciato ma non ancora esistente: chiederlo adesso lo rende
        # irraggiungibile da qui per minuti.
        self.assertIsNone(ospita.indirizzo_dal_log(self.PRIMA))

    def test_dopo_laggancio_si(self):
        self.assertEqual(
            ospita.indirizzo_dal_log(self.DOPO), "https://grande-sharon-font-fewer.trycloudflare.com"
        )

    def test_niente_log_niente_indirizzo(self):
        self.assertIsNone(ospita.indirizzo_dal_log(""))
        self.assertIsNone(ospita.indirizzo_dal_log(None))


class Intervallo(unittest.TestCase):
    def test_i_pezzi_di_chi_riprende(self):
        self.assertEqual(ospita.intervallo("bytes=0-0", 100), (0, 0))
        self.assertEqual(ospita.intervallo("bytes=10-19", 100), (10, 19))
        self.assertEqual(ospita.intervallo("bytes=90-", 100), (90, 99))
        self.assertEqual(ospita.intervallo("bytes=-10", 100), (90, 99))

    def test_la_fine_oltre_il_file_si_ferma_al_file(self):
        self.assertEqual(ospita.intervallo("bytes=50-999", 100), (50, 99))

    def test_quello_che_non_si_capisce_vale_tutto(self):
        for intestazione in ("", "bytes=-", "bytes=abc", "items=0-1", "bytes=200-300"):
            self.assertIsNone(ospita.intervallo(intestazione, 100), intestazione)


if __name__ == "__main__":
    unittest.main()
