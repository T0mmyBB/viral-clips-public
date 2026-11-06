"""La cabina di regia del pannello: roadmap, cancello, cose da fare, invii a mano, Whop.

Il 28 settembre 2026 il pannello e' diventato il posto da cui si guida il piano
di docs/roadmap.md. Le regole che qui si fissano sono quelle che, sbagliate, non
danno errore e dicono il falso:

- **«non so» non e' zero**: un pagamento con un importo in unita' ignota, un
  conto Whop non letto, un post mai misurato — nessuno diventa un numero;
- **la roadmap si legge dal file**, fasi scadenze e cancello, senza una copia;
- **«da fare» contiene solo cio' che chiede un gesto**, in ordine di urgenza;
- **«inviato a mano» tocca un record solo**, e rifiuta lo schema vecchio che
  farebbe sparire gli altri.

Niente rete e niente browser: il pannello Whop e' sostituito dai suoi testi.
"""

from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from datetime import date, datetime
from pathlib import Path
from typing import ClassVar
from unittest import mock

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import metriche
import stato


def whop_agent():
    """Importato senza Scrapling, che non e' nel venv del progetto."""
    if "scrapling.fetchers" not in sys.modules:
        finto = types.ModuleType("scrapling.fetchers")
        finto.DynamicSession = object
        sys.modules.setdefault("scrapling", types.ModuleType("scrapling"))
        sys.modules["scrapling.fetchers"] = finto
    import whop_agent as modulo

    return modulo


# Il testo vero della pagina «Submissions», 28 settembre 2026, accorciato.
SUBMISSIONS = """Discover
Home
My work
Submissions
Earnings
Your submissions
Content Rewards
canale_business
Clips
12
Retainer
0
Approved
0
Pending
12
Rejected
0
History
0
Contoso with Jane Doe - September Episode Clippin
1,220 views
"""

# La prima voce di pagamento vista, sul conto del laboratorio.
PAGAMENTO_STORNATO = {
    "amount": "0",
    "netAmount": "0",
    "creatorFeeAmount": "0",
    "cpmTerms": {"maxPayoutCents": "25000", "minPayoutCents": "125"},
    "status": "reversed",
    "reversedAt": "2026-09-07T18:42:04.000Z",
    "reversedReason": "pending_release",
}


class IlPannelloWhop(unittest.TestCase):
    def test_i_contatori_degli_invii(self):
        c = whop_agent().contatori_invii(SUBMISSIONS)
        self.assertEqual(
            c,
            {"clips": 12, "retainer": 0, "approved": 0, "pending": 12, "rejected": 0, "history": 0},
        )

    def test_una_pagina_cambiata_non_e_zero_approvate(self):
        self.assertIsNone(whop_agent().contatori_invii("Clips\n12\nApproved\n0\n"))

    def test_lo_storno_non_e_un_incasso_e_zero_e_zero(self):
        esito = whop_agent().somma_pagamenti({"data": [PAGAMENTO_STORNATO]})
        self.assertEqual((esito["n"], esito["totale"]), (1, 0.0))
        self.assertEqual(esito["stati"], {"reversed": 1})
        # Uno storno con un importo vero: e' denaro che non e' arrivato.
        stornato = {"netAmountCents": "500", "status": "reversed"}
        pagato = {"netAmountCents": "300", "status": "paid"}
        esito = whop_agent().somma_pagamenti({"data": [stornato, pagato]})
        self.assertEqual(esito["totale"], 3.0)

    def test_i_centesimi_dichiarati_si_sommano_netti(self):
        voce = {"netAmountCents": "1250", "amountCents": "1400", "status": "paid"}
        esito = whop_agent().somma_pagamenti({"data": [voce, voce]})
        self.assertEqual(esito["totale"], 25.0)

    def test_un_importo_senza_unita_non_si_indovina(self):
        # `amount: "12"` puo' essere 12 dollari o 12 centesimi: scommettere vuol
        # dire scrivere un incasso cento volte sbagliato.
        esito = whop_agent().somma_pagamenti({"data": [{"amount": "12", "status": "paid"}]})
        self.assertIsNone(esito["totale"])
        self.assertIn("amount", esito["chiavi"])

    def test_nessun_pagamento_e_zero_una_risposta_strana_no(self):
        wa = whop_agent()
        self.assertEqual(wa.somma_pagamenti({"data": []})["totale"], 0.0)
        self.assertIsNone(wa.somma_pagamenti({"errore": "x"})["totale"])

    def test_la_riga_dell_agente(self):
        uscita = 'log\nguadagni: {"invii": {"clips": 1}}\nguadagni: {"invii": {"clips": 2}}\n'
        self.assertEqual(metriche.leggi_riga_guadagni(uscita), {"invii": {"clips": 2}})
        self.assertIsNone(metriche.leggi_riga_guadagni("guadagni: {rotto"))
        self.assertIsNone(metriche.leggi_riga_guadagni("niente"))


ROADMAP = """# Roadmap, decisa il 28 settembre 2026

## Fase 1 — sblocco (28 settembre - 5 ottobre)

1. **29 settembre: scegliere la campagna.** Requisiti.
2. **Risolutore, passo 1:** il giro a secco.
   *Fatto il 28 settembre.*

## Fase 2 — la distribuzione funziona? (6 - 26 ottobre)

- testo

## Fase 3 — crescita (novembre)

## Fase 4 — autonomia (da dicembre)

## Fase 5 — oltre (gennaio)

## Scadenze

| Data | Cosa | Esito |
|---|---|---|
| 2026-09-29 | Scegliere la campagna | |
| 2026-09-30 | Test TikTok | fatto |
| 2026-10-26 | Cancello della fase 2 | |

## Cancello

| Condizione | Chiave | Soglia |
|---|---|---|
| Views mediane | `views_mediane` | 1000 |
| Primo incasso | `incassato` | 0,01 |
"""


class LaRoadmap(unittest.TestCase):
    def rm(self, oggi=date(2026, 9, 28)):
        return stato.roadmap(ROADMAP, oggi)

    def test_le_fasi_e_i_loro_periodi(self):
        fasi = {f["numero"]: (f["inizio"], f["fine"]) for f in self.rm()["fasi"]}
        self.assertEqual(fasi[1], ("2026-09-28", "2026-10-05"))
        self.assertEqual(fasi[2], ("2026-10-06", "2026-10-26"))
        self.assertEqual(fasi[3], ("2026-11-01", "2026-11-30"))
        self.assertEqual(fasi[4], ("2026-12-01", None))
        # Dopo dicembre viene gennaio dell'anno dopo, non di quello prima.
        self.assertEqual(fasi[5][0], "2027-01-01")

    def test_la_fase_di_oggi(self):
        self.assertEqual(self.rm(date(2026, 9, 28))["corrente"], 1)
        self.assertEqual(self.rm(date(2026, 10, 10))["corrente"], 2)
        self.assertEqual(self.rm(date(2027, 3, 1))["corrente"], 4, "la fase aperta resta")

    def test_le_voci_fatte(self):
        voci = self.rm()["fasi"][0]["voci"]
        self.assertEqual([(v["numero"], v["fatto"]) for v in voci], [(1, False), (2, True)])

    def test_scadenze_e_cancello(self):
        rm = self.rm()
        self.assertEqual([s["giorni"] for s in rm["scadenze"]], [1, 2, 28])
        self.assertEqual(rm["scadenze"][1]["esito"], "fatto")
        self.assertEqual(rm["data_cancello"], "2026-10-26")
        self.assertEqual(
            [(c["chiave"], c["soglia"]) for c in rm["cancello"]],
            [("views_mediane", 1000.0), ("incassato", 0.01)],
        )


class IlCancello(unittest.TestCase):
    RM: ClassVar[dict] = {
        "cancello": [
            {"condizione": "v", "chiave": "views_mediane", "soglia": 1000.0},
            {"condizione": "i", "chiave": "incassato", "soglia": 0.01},
        ]
    }

    def test_basta_una_piattaforma_e_non_so_resta_non_so(self):
        numeri = {
            "piattaforme": [{"mediana": 18}, {"mediana": 1200}, {"mediana": None}],
            "incassato": None,
        }
        v, i = stato.stato_cancello(self.RM, numeri)
        self.assertEqual((v["valore"], v["raggiunta"]), (1200, True))
        self.assertIsNone(i["raggiunta"], "un incasso non letto non e' «non raggiunto»")

    def test_zero_incassato_e_non_raggiunto(self):
        _, i = stato.stato_cancello(self.RM, {"piattaforme": [], "incassato": 0.0})
        self.assertIs(i["raggiunta"], False)


def zero(campagna, piattaforma, giorni):
    return {
        "livello": "avviso",
        "campagna": campagna,
        "clip": "01",
        "messaggio": f"{piattaforma}: 0 views a {giorni} giorni dall'uscita",
    }


class GliAvvisiRaggruppati(unittest.TestCase):
    def test_sei_zeri_sono_una_riga(self):
        trovati = [zero("contoso", "tiktok", g) for g in (12, 13, 15)] + [
            zero("contoso", "instagram", 15),
            {"livello": "errore", "campagna": "x", "clip": "02", "messaggio": "senza URL"},
        ]
        righe = stato.avvisi_raggruppati(trovati)
        testi = [r["messaggio"] for r in righe]
        self.assertIn("tiktok: 3 post a 0 views, da 12-15 giorni", testi)
        self.assertIn("instagram: 1 post a 0 views, da 15 giorni", testi)
        self.assertIn("senza URL", testi, "gli altri avvisi passano come sono")
        self.assertEqual(len(righe), 3)


def invio(minuti, finita=None, in_finestra=None):
    return {
        "clip": "01",
        "piattaforma": "tiktok",
        "minuti": minuti,
        "in_finestra": minuti <= 30 if in_finestra is None else in_finestra,
        "finita": finita,
    }


class LeCoseDaFare(unittest.TestCase):
    ORA = datetime(2026, 9, 28, 12, 0)

    def fare(self, mano=(), scadenze=(), giro=None, cal=(), lav=(), trovati=()):
        return stato.da_fare(
            list(cal),
            list(mano),
            list(trovati),
            giro or {"girato_il": "2026-09-28T02:00:00"},
            {"scadenze": list(scadenze)},
            list(lav),
            ora=self.ORA,
        )

    def test_un_invio_in_finestra_e_la_prima_cosa(self):
        voci = self.fare(
            mano=[invio(10), invio(50)],
            scadenze=[{"data": "", "cosa": "domani", "esito": "", "giorni": 1}],
        )
        self.assertEqual(voci[0]["livello"], "adesso")
        self.assertIn("finestra aperta", voci[0]["titolo"])
        self.assertIn("20 min rimasti", voci[0]["dettaglio"])
        self.assertEqual([v["livello"] for v in voci], ["adesso", "oggi", "presto"])

    def test_a_campagna_finita_l_invio_non_e_da_fare(self):
        self.assertEqual(self.fare(mano=[invio(10, finita="budget esaurito")]), [])

    def test_le_scadenze(self):
        voci = self.fare(
            scadenze=[
                {"data": "", "cosa": "scaduta", "esito": "", "giorni": -2},
                {"data": "", "cosa": "oggi", "esito": "", "giorni": 0},
                {"data": "", "cosa": "lontana", "esito": "", "giorni": 9},
                {"data": "", "cosa": "chiusa", "esito": "fatto", "giorni": -1},
            ]
        )
        self.assertEqual(
            [(v["titolo"], v["livello"]) for v in voci], [("scaduta", "adesso"), ("oggi", "oggi")]
        )

    def test_un_radar_vecchio_chiede_aggiorna_radar(self):
        # il catalogo vecchio si cura col tasto del radar, non con quello dei social
        voci = self.fare(giro={"girato_il": "2026-09-25T09:00:00"})
        self.assertEqual(voci[0]["vai"], "aggiorna-radar")

    def test_l_ordine_e_l_urgenza_non_l_arrivo(self):
        # La scadenza si aggiunge prima dell'errore, ma l'errore e' di oggi e la
        # scadenza fra due giorni: l'elenco va per urgenza.
        voci = self.fare(
            scadenze=[{"data": "", "cosa": "fra due giorni", "esito": "", "giorni": 2}],
            trovati=[{"livello": "errore", "campagna": "c", "clip": "01", "messaggio": "guasto"}],
        )
        self.assertEqual([v["livello"] for v in voci], ["oggi", "presto"])


def cartella_pubblicata(
    whop="non registrato", stato_pub="pubblicato", url="https://x/1", uuid="u1"
):
    return {
        "cartella": "c1",
        "campagna": "Campagna",
        "whop_campaign_uuid": uuid,
        "whop_app": "https://whop.com/p/exp/app/",
        "clip": [
            {
                "id": "01",
                "titolo": "t",
                "piattaforma": "tiktok",
                "url": url,
                "stato_pubblicazione": stato_pub,
                "stato_whop": whop,
                "pubblicato_il": "2026-09-28T11:50:00",
            }
        ],
    }


class GliInviiAMano(unittest.TestCase):
    ORA = datetime(2026, 9, 28, 12, 0)

    def mano(self, campagne):
        with mock.patch.object(stato, "leggi", return_value=[]):
            return stato.invii_a_mano(campagne, ora=self.ORA)

    def test_chi_entra_nella_coda(self):
        (r,) = self.mano([cartella_pubblicata()])
        self.assertEqual((r["clip"], r["minuti"], r["in_finestra"]), ("01", 10, True))
        self.assertEqual(r["whop_app"], "https://whop.com/p/exp/app/")
        for fuori in (
            cartella_pubblicata(whop="inviato a mano"),
            cartella_pubblicata(whop="inviato"),
            cartella_pubblicata(stato_pub="in coda"),
            cartella_pubblicata(url=None),
            cartella_pubblicata(uuid=None),
        ):
            with self.subTest(fuori=fuori["clip"][0]):
                self.assertEqual(self.mano([fuori]), [])


class InviatoAMano(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.output = Path(self.tmp.name)
        (self.output / "c1").mkdir()
        self.file = self.output / "c1" / "publish_state.json"
        self.file.write_text(
            json.dumps(
                {
                    "items": {
                        "01@tiktok": {
                            "url": "https://t/1",
                            "status": "pubblicato",
                            "whop_status": "x",
                        },
                        "01@youtube": {
                            "url": "https://y/1",
                            "status": "pubblicato",
                            "whop_status": "inviato",
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        self.patch = mock.patch.object(stato, "OUTPUT_DIR", self.output)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def items(self):
        return json.loads(self.file.read_text(encoding="utf-8"))["items"]

    def test_tocca_solo_il_suo_record(self):
        stato.segna_inviato_a_mano("c1", "01", "tiktok")
        items = self.items()
        self.assertEqual(items["01@tiktok"]["whop_status"], "inviato a mano")
        self.assertEqual(items["01@tiktok"]["url"], "https://t/1", "il resto del record resta")
        self.assertEqual(items["01@youtube"]["whop_status"], "inviato")

    def test_annullare_torna_a_non_so(self):
        stato.segna_inviato_a_mano("c1", "01", "tiktok")
        stato.segna_inviato_a_mano("c1", "01", "tiktok", annulla=True)
        self.assertEqual(self.items()["01@tiktok"]["whop_status"], "non confermato")

    def test_i_rifiuti(self):
        with self.assertRaises(ValueError):
            stato.segna_inviato_a_mano("c1", "09", "tiktok")

    def test_mai_fuori_da_output(self):
        # Un file valido **accanto** a output/: senza il controllo del percorso
        # «../fuori» lo raggiungerebbe e lo riscriverebbe.
        fuori = self.output.parent / (self.output.name + "-fuori")
        fuori.mkdir()
        vittima = fuori / "publish_state.json"
        vittima.write_text(self.file.read_text(encoding="utf-8"), encoding="utf-8")
        prima = vittima.read_text(encoding="utf-8")
        try:
            with self.assertRaises(ValueError):
                stato.segna_inviato_a_mano(f"../{fuori.name}", "01", "tiktok")
            self.assertEqual(vittima.read_text(encoding="utf-8"), prima)
        finally:
            vittima.unlink()
            fuori.rmdir()

    def test_lo_schema_vecchio_si_rifiuta(self):
        # Senza `items`: scriverci aprirebbe un elenco nuovo e i record di prima
        # sparirebbero dalla vista. Il record qui e' valido e trovabile.
        vecchio = {"01": {"url": "https://t/1", "platform": "tiktok", "status": "pubblicato"}}
        self.file.write_text(json.dumps(vecchio), encoding="utf-8")
        with self.assertRaises(ValueError):
            stato.segna_inviato_a_mano("c1", "01", "tiktok")
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), vecchio)


class PubblicatoAMano(InviatoAMano):
    """Il post fatto dal telefono: da qui l'esecutore lo considera uscito."""

    def setUp(self):
        super().setUp()
        (self.output / "c1" / "publish.json").write_text(
            json.dumps({"items": [{"id": "01"}, {"id": "02", "a_mano": {"tiktok": "prova"}}]}),
            encoding="utf-8",
        )

    def test_scrive_il_record_e_non_tocca_gli_altri(self):
        url = "https://www.tiktok.com/@a/video/1"
        stato.segna_pubblicato_a_mano("c1", "02", "tiktok", url, whop_inviato=True)
        items = self.items()
        self.assertEqual(items["02@tiktok"]["status"], "pubblicato")
        self.assertEqual(items["02@tiktok"]["url"], url)
        self.assertEqual(items["02@tiktok"]["whop_status"], "inviato a mano")
        self.assertEqual(items["01@youtube"]["whop_status"], "inviato")

    def test_senza_whop_finisce_fra_gli_invii_da_fare(self):
        stato.segna_pubblicato_a_mano(
            "c1", "02", "tiktok", "https://www.tiktok.com/@a/video/1", whop_inviato=False
        )
        self.assertNotIn(self.items()["02@tiktok"]["whop_status"], stato.WHOP_FATTO)

    def test_il_link_deve_essere_della_piattaforma(self):
        for url in ("https://youtube.com/shorts/x", "https://tiktok.com.evil.io/x", "tiktok.com/x", ""):
            with self.assertRaises(ValueError, msg=url):
                stato.segna_pubblicato_a_mano("c1", "02", "tiktok", url, whop_inviato=True)
        self.assertNotIn("02@tiktok", self.items())

    def test_una_coppia_gia_uscita_non_si_riscrive(self):
        with self.assertRaises(ValueError):
            stato.segna_pubblicato_a_mano(
                "c1", "01", "tiktok", "https://www.tiktok.com/@a/video/2", whop_inviato=True
            )
        self.assertEqual(self.items()["01@tiktok"]["url"], "https://t/1")


class LeSorgentiNelPannello(unittest.TestCase):
    def test_facilita_e_candidati(self):
        with tempfile.TemporaryDirectory() as tmp:
            dati = {
                "esito": "automatico",
                "motivo": "",
                "candidati": [
                    {"url": "e", "titolo": "esempio", "durata_s": 3000, "forse_esempio": True},
                    {"url": "b", "titolo": "secondo", "durata_s": 1800, "preselezionato": False},
                    {"url": "a", "titolo": "primo", "durata_s": 3600, "preselezionato": True},
                ],
            }
            (Path(tmp) / "c1.json").write_text(json.dumps(dati), encoding="utf-8")
            with mock.patch.object(stato, "SORGENTI_DIR", Path(tmp)):
                stato._CACHE_SORGENTI.clear()
                f = stato.facilita_sorgente("c1")
                r = stato.risolutore("c1")
                self.assertIsNone(stato.facilita_sorgente("assente"))
        self.assertEqual((f["etichetta"], f["titolo"], f["durata_min"]), ("pronta", "primo", 60))
        self.assertEqual(
            [c["url"] for c in r["candidati"]], ["a", "b"], "niente esempi, prima i preselezionati"
        )


class INumeri(unittest.TestCase):
    def numeri(self, whop):
        campagne = [
            {
                "clip": [
                    {
                        "url": u,
                        "stato_pubblicazione": "pubblicato",
                        "piattaforma": "tiktok",
                        "identita": "t",
                    }
                    for u in ("a", "b", "c", "d")
                ]
            }
        ]
        misure = {"a": {"views": 10}, "b": {"views": 30}, "c": {"views": 0}}
        finto = types.SimpleNamespace(ultimi_whop=lambda: whop, andamento=lambda giorni: {})
        with (
            mock.patch.object(stato, "misure_per_url", return_value=misure),
            mock.patch.dict(sys.modules, {"metriche": finto}),
        ):
            return stato.numeri(campagne)

    def test_mediana_senza_i_non_misurati(self):
        tt = next(p for p in self.numeri([])["piattaforme"] if p["piattaforma"] == "tiktok")
        self.assertEqual((tt["post"], tt["misurati"], tt["mediana"]), (4, 3, 10))

    def test_un_conto_non_letto_non_fa_un_totale(self):
        self.assertIsNone(self.numeri([{"pagato": 0.0}, {"pagato": None}])["incassato"])
        self.assertEqual(self.numeri([{"pagato": 0.0}, {"pagato": 5.5}])["incassato"], 5.5)
        self.assertIsNone(self.numeri([])["incassato"], "nessuna lettura non e' zero")


if __name__ == "__main__":
    unittest.main()
