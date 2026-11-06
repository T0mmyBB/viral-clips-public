"""Quanto vale un nostro post in una campagna: views per post x CPM.

**E' la misura su cui si sceglie che nicchia lavorare**, e quella sbagliata ha
gia' proposto due nicchie sbagliate di fila: il collezionismo per gli $85.091 in
cassa (Initech ha 345 clipper, cioe' $4,34 a post, ultimo del catalogo) e il gaming
per un `$/clipper` alto che veniva da una campagna sola in scadenza.

La differenza fra le tre misure possibili non e' accademica:

- **budget** dice quanto c'e' in cassa, non quanto ne arriva qui;
- **$/clipper** divide anche per la concorrenza, quindi fa sembrare ricca una
  campagna deserta e povera una affollata;
- **$/post** dice quello che a noi cambia: quante views prende **un** post.

Il nostro limite sono i post — due al giorno per piattaforma — non i soldi di
qualcun altro.
"""

from __future__ import annotations

import unittest

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import tassonomia


def scheda(**campi) -> dict:
    """Una campagna misurabile, su cui i test cambiano un campo per volta."""
    base = {"stato": "active", "post_30g": 100, "views_30g": 5_000_000, "cpm": 1.0}
    base.update(campi)
    return base


class DollariPost(unittest.TestCase):
    def test_la_formula(self):
        # 5.000.000 views / 100 post = 50.000 views a post = 50 mille-views,
        # per $1,00 di CPM = $50 a post.
        self.assertAlmostEqual(tassonomia.dollari_post(scheda()), 50.0)

    def test_il_cpm_moltiplica(self):
        self.assertAlmostEqual(tassonomia.dollari_post(scheda(cpm=2.5)), 125.0)

    def test_non_dipende_da_quanti_clipper_ci_sono(self):
        # La campagna affollata e quella deserta valgono uguale **per noi**: e'
        # tutta la differenza fra questa misura e `$/clipper`, ed e' la ragione
        # per cui esiste. Il numero di clipper non entra nel conto.
        self.assertEqual(
            tassonomia.dollari_post(scheda(clipper=345)), tassonomia.dollari_post(scheda(clipper=2))
        )

    def test_non_dipende_dal_budget(self):
        self.assertEqual(
            tassonomia.dollari_post(scheda(budget=85_091)),
            tassonomia.dollari_post(scheda(budget=300)),
        )

    def test_campagna_non_attiva_non_si_misura(self):
        # Non «vale zero»: **non si sa**, e le due cose non vanno confuse in una
        # classifica. Uno zero ordinerebbe, un None resta fuori dalla somma.
        for stato in ("ended", "paused", "draft", None):
            with self.subTest(stato=stato):
                self.assertIsNone(tassonomia.dollari_post(scheda(stato=stato)))

    def test_troppo_pochi_post_non_si_misura(self):
        # Sotto i 20 post in 30 giorni, views/post e' l'aneddoto di due clipper
        # fortunati, non una media della campagna.
        self.assertIsNone(tassonomia.dollari_post(scheda(post_30g=19)))

    def test_la_soglia_e_inclusiva(self):
        self.assertIsNotNone(tassonomia.dollari_post(scheda(post_30g=tassonomia.POST_MINIMI)))

    def test_nessun_post_non_si_misura(self):
        for post in (0, None):
            with self.subTest(post_30g=post):
                self.assertIsNone(tassonomia.dollari_post(scheda(post_30g=post)))

    def test_campi_mancanti_valgono_zero_non_esplodono(self):
        # Il radar legge schede scritte da un sito, non da noi: un campo assente
        # e' la norma, e non deve fermare il giro di 600 campagne.
        self.assertEqual(tassonomia.dollari_post({"stato": "active", "post_30g": 50}), 0.0)
        self.assertEqual(tassonomia.dollari_post(scheda(cpm=None)), 0.0)
        self.assertEqual(tassonomia.dollari_post(scheda(views_30g=None)), 0.0)

    def test_il_caso_initech(self):
        # La campagna che la misura sbagliata aveva messo in cima: budget enorme,
        # $4,34 a post. Qui i numeri sono ricostruiti per dare quel valore — cio'
        # che si fissa e' che un budget grosso non lo sposta di un centesimo.
        initech = scheda(post_30g=345, views_30g=1_497_300, cpm=1.0, budget=85_091)
        self.assertAlmostEqual(tassonomia.dollari_post(initech), 4.34, places=2)
        self.assertLess(tassonomia.dollari_post(initech), tassonomia.BUONA)


class CpmDelleNostrePiattaforme(unittest.TestCase):
    """
    Il `cpm` della scheda e' `primaryPayoutCents`, cioe' il **massimo** fra le
    piattaforme — misurato: coincide col massimo su 617 campagne su 618, e su
    101 le piattaforme pagano diverso. Prenderlo per «il CPM» gonfia `$/post`,
    che e' la misura con cui si sceglie che campagna lavorare.
    """

    @staticmethod
    def tariffa(piattaforma, cpm):
        return {"piattaforma": piattaforma, "cpm": cpm, "tetto": 100.0, "minimo": 1.0}

    def test_la_media_delle_nostre_tre(self):
        s = scheda(
            cpm=2.0,
            tariffe=[
                self.tariffa("youtube", 2.0),
                self.tariffa("tiktok", 2.0),
                self.tariffa("instagram", 1.5),
            ],
        )
        self.assertAlmostEqual(tassonomia.cpm_nostro(s), (2.0 + 2.0 + 1.5) / 3)

    def test_le_piattaforme_dove_non_pubblichiamo_non_contano(self):
        # Il caso Fabrikam: la scheda dichiara $2,00, che e' il prezzo di X.
        # Sulle nostre tre paga $0,12, cioe' `$/post` vero e' un sedicesimo.
        s = scheda(
            cpm=2.0,
            tariffe=[
                self.tariffa("x", 2.0),
                self.tariffa("youtube", 0.12),
                self.tariffa("tiktok", 0.12),
                self.tariffa("instagram", 0.12),
            ],
        )
        self.assertAlmostEqual(tassonomia.cpm_nostro(s), 0.12)
        self.assertAlmostEqual(tassonomia.dollari_post(s), 6.0)
        self.assertAlmostEqual(tassonomia.dollari_post(scheda(cpm=2.0)), 100.0)

    def test_senza_tariffe_non_si_sa(self):
        # `None`, non zero: uno zero ordinerebbe. Sono 9 campagne su 627, e li'
        # `dollari_post` ripiega sul campo vecchio perche' non c'e' altro.
        self.assertIsNone(tassonomia.cpm_nostro(scheda()))
        self.assertAlmostEqual(tassonomia.dollari_post(scheda(cpm=2.0)), 100.0)

    def test_nessuna_delle_nostre_fra_quelle_pagate(self):
        s = scheda(cpm=2.0, tariffe=[self.tariffa("x", 2.0), self.tariffa("facebook", 2.0)])
        self.assertIsNone(tassonomia.cpm_nostro(s))

    def test_le_nostre_piattaforme_sono_tre(self):
        self.assertEqual(set(tassonomia.NOSTRE_PIATTAFORME), {"youtube", "tiktok", "instagram"})


class BreakEven(unittest.TestCase):
    """
    Quante views servono perche' un post paghi qualcosa. Sotto il minimo una
    clip vale **zero**, non poco, ed e' il vincolo che morde durante la rampa
    di un account nuovo: dei primi cinque post misurati (1.278, 956, 26, 4, 1
    views) uno solo avrebbe pagato.
    """

    @staticmethod
    def tariffa(piattaforma, cpm, minimo):
        return {"piattaforma": piattaforma, "cpm": cpm, "tetto": 500.0, "minimo": minimo}

    def test_minimo_diviso_cpm_per_mille(self):
        # $2,50 di minimo a $2,00 di CPM = 1.250 views prima del primo centesimo.
        s = scheda(tariffe=[self.tariffa("tiktok", 2.0, 2.50)])
        self.assertAlmostEqual(tassonomia.break_even(s)["break_even"], 1250.0)

    def test_il_dettaglio_resta_per_piattaforma(self):
        # Il caso Contoso: tre minimi diversi sulla stessa campagna. Il numero
        # unico e' la mediana e serve a ordinare; quello che si incassa lo
        # decide la piattaforma.
        s = scheda(
            tariffe=[
                self.tariffa("instagram", 1.5, 2.30),
                self.tariffa("youtube", 2.0, 3.50),
                self.tariffa("tiktok", 2.0, 2.50),
            ]
        )
        b = tassonomia.break_even(s)
        self.assertEqual(set(b["break_even_per_piattaforma"]), {"instagram", "youtube", "tiktok"})
        self.assertAlmostEqual(b["break_even_per_piattaforma"]["youtube"], 1750.0)
        self.assertAlmostEqual(b["break_even"], 1533.3333, places=3)

    def test_le_piattaforme_dove_non_usciamo_non_contano(self):
        s = scheda(tariffe=[self.tariffa("x", 2.0, 50.0), self.tariffa("tiktok", 2.0, 2.0)])
        self.assertAlmostEqual(tassonomia.break_even(s)["break_even"], 1000.0)

    def test_senza_minimo_paga_dalla_prima_view(self):
        # Zero e' una risposta vera qui: la campagna paga da subito.
        s = scheda(tariffe=[self.tariffa("tiktok", 2.0, 0.0)])
        self.assertEqual(tassonomia.break_even(s)["break_even"], 0.0)

    def test_non_si_sa_non_e_zero(self):
        # Uno zero direbbe «paga da subito», che e' il contrario di «non si sa».
        for s in (
            scheda(),
            scheda(tariffe=[self.tariffa("tiktok", 0.0, 2.0)]),
            scheda(tariffe=[self.tariffa("x", 2.0, 2.0)]),
        ):
            with self.subTest(tariffe=s.get("tariffe")):
                self.assertIsNone(tassonomia.break_even(s)["break_even"])

    def test_viaggia_coi_paletti(self):
        # Tre lettori — proposte, campagne attive, catalogo — e una lettura sola.
        s = scheda(tariffe=[self.tariffa("tiktok", 2.0, 2.0)])
        self.assertAlmostEqual(tassonomia.paletti(s)["break_even"], 1000.0)


class SoglieDecise(unittest.TestCase):
    """I numeri che ordinano report e pannello, fissati perche' sono scelte."""

    def test_le_costanti_non_si_spostano_per_sbaglio(self):
        self.assertEqual(tassonomia.POST_MINIMI, 20)
        self.assertEqual(tassonomia.BUONA, 20.0)
        self.assertEqual(tassonomia.MESI_CHE_REGGE, 1.5)

    def test_le_pagine_escluse_sono_marca_ed_esclusiva(self):
        # `marca` vuole una pagina intestata a quel creator, `esclusiva` vieta
        # altre campagne sulla stessa pagina: nessuna delle due serve un account
        # che deve reggere molte campagne, e infatti non entrano in nessuna
        # somma. `tema` invece e' il nostro modello e ci deve restare dentro.
        self.assertEqual(set(tassonomia.PAGINA_ESCLUSA), {"marca", "esclusiva"})
        self.assertNotIn("tema", tassonomia.PAGINA_ESCLUSA)
        self.assertNotIn("libera", tassonomia.PAGINA_ESCLUSA)


class Riusabili(unittest.TestCase):
    """Quali campagne entrano nelle somme di una nicchia."""

    @staticmethod
    def voce(**campi) -> dict:
        base = {"lavorabile": "si", "pagina": "libera", "m": scheda()}
        base.update(campi)
        return base

    def test_solo_le_lavorabili(self):
        voci = [self.voce(), self.voce(lavorabile="no"), self.voce(lavorabile="lingua")]
        self.assertEqual(len(tassonomia._riusabili(voci)), 1)

    def test_fuori_le_pagine_di_marca_ed_esclusive(self):
        voci = [self.voce(pagina=p) for p in ("libera", "tema", "marca", "esclusiva")]
        rimaste = {v["pagina"] for v in tassonomia._riusabili(voci)}
        self.assertEqual(rimaste, {"libera", "tema"})

    def test_fuori_le_campagne_finite(self):
        voci = [self.voce(), self.voce(m=scheda(stato="ended"))]
        self.assertEqual(len(tassonomia._riusabili(voci)), 1)

    def test_la_mediana_non_la_media(self):
        # Le distribuzioni hanno una coda lunghissima: una campagna a $431 a post
        # accanto a tante sotto i dieci. La media la decide quella, la mediana
        # descrive il caso normale — ed e' per questo che stanno affiancate.
        voci = [self.voce(m=scheda(views_30g=v * 100_000)) for v in (1, 2, 3, 4, 431)]
        self.assertLess(tassonomia._mediana_post(voci), tassonomia._media_post(voci))

    def test_senza_campagne_misurabili_zero(self):
        self.assertEqual(tassonomia._mediana_post([]), 0.0)
        self.assertEqual(tassonomia._media_post([]), 0.0)


if __name__ == "__main__":
    unittest.main()
