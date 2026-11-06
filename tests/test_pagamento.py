"""`stato.pagamento`: a che punto e' il pagamento di un invio, dedotto da views e soglia.

Il 15 settembre 2026 i tre invii YouTube di Contoso erano Pending su Whop con 13, 2 e
1.076 views contro una soglia di 1.750, e il pannello non sapeva dirlo: un Pending
atteso sembrava un guasto. Le regole da non rompere: sotto soglia e sopra soglia sono
due etichette diverse, «non si sa» (soglia o views mancanti) non e' «sotto soglia»,
un invio che non c'e' non ha niente da dire, e il chiamante usa la soglia della
piattaforma giusta.
"""

from __future__ import annotations

import unittest

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import stato


class Pagamento(unittest.TestCase):
    def etichetta(self, whop, views, soglia):
        esito = stato.pagamento(whop, views, soglia)
        return esito and esito["etichetta"]

    def test_sotto_e_sopra_soglia(self):
        self.assertEqual(self.etichetta("inviato", 1076, 1750.0), "sotto soglia")
        self.assertEqual(self.etichetta("inviato", 1750, 1750.0), "sopra soglia")

    def test_la_frase_dice_quante_ne_mancano(self):
        self.assertIn("674", stato.pagamento("inviato", 1076, 1750.0)["frase"])

    def test_non_inviato_non_dice_niente(self):
        for whop in ("in attesa", "non confermato", "non registrato", None):
            with self.subTest(whop=whop):
                self.assertIsNone(stato.pagamento(whop, 13, 1750.0))

    def test_inviato_a_mano_conta_come_inviato(self):
        self.assertEqual(self.etichetta("inviato a mano", 13, 1750.0), "sotto soglia")

    def test_non_sapere_non_e_sotto_soglia(self):
        self.assertEqual(self.etichetta("inviato", 13, None), "soglia ignota")
        self.assertEqual(self.etichetta("inviato", None, 1750.0), "views non lette")

    def test_soglia_zero_paga_da_subito(self):
        self.assertEqual(self.etichetta("inviato", 0, 0.0), "nessun minimo")


class ACampagnaFinita(unittest.TestCase):
    """Sotto soglia a campagna finita non e' un'attesa: non paghera'."""

    FINITA = "il budget della campagna è esaurito"

    def test_sotto_soglia_diventa_non_paghera(self):
        self.assertEqual(
            stato.pagamento("inviato", 1076, 1750.0, self.FINITA)["etichetta"], "non pagherà"
        )

    def test_sopra_soglia_resta_sopra(self):
        # Puo' essere gia' stato pagato: la fine della campagna non lo cambia.
        self.assertEqual(
            stato.pagamento("inviato", 2000, 1750.0, self.FINITA)["etichetta"], "sopra soglia"
        )

    def test_senza_soglia_dice_finita(self):
        self.assertEqual(
            stato.pagamento("inviato", 18, None, self.FINITA)["etichetta"], "campagna finita"
        )

    def test_non_inviato_resta_muto(self):
        self.assertIsNone(stato.pagamento("in attesa", 18, 1750.0, self.FINITA))


class PagamentiChiusi(unittest.TestCase):
    """Solo i fatti della campagna chiudono i pagamenti, non la nostra chiusura a mano."""

    CAMP = {"whop_campaign_uuid": "79261cab"}
    VIVA = {"stato": "active", "residuo": 645.14}

    def test_viva_paga(self):
        self.assertIsNone(stato.pagamenti_chiusi(self.CAMP, self.VIVA, True))

    def test_fuori_elenco_e_finita(self):
        self.assertIn("elenco", stato.pagamenti_chiusi(self.CAMP, None, True))

    def test_senza_radar_l_assenza_non_dice_niente(self):
        self.assertIsNone(stato.pagamenti_chiusi(self.CAMP, None, False))

    def test_stato_e_budget(self):
        self.assertIn(
            "ended", stato.pagamenti_chiusi(self.CAMP, {**self.VIVA, "stato": "ended"}, True)
        )
        self.assertIn(
            "budget", stato.pagamenti_chiusi(self.CAMP, {**self.VIVA, "residuo": 0}, True)
        )

    def test_senza_whop_niente(self):
        self.assertIsNone(stato.pagamenti_chiusi({}, None, True))


class NelChiamante(unittest.TestCase):
    """`clip_di_campagna` prende la soglia della piattaforma della clip."""

    def campagna(self, uuid="79261cab"):
        clip = [
            {
                "id": "01",
                "titolo": "t",
                "url": f"https://{p}/1",
                "piattaforma": p,
                "stato_pubblicazione": "pubblicato",
                "stato_whop": "inviato",
                "pubblicato_il": None,
                "quando": None,
            }
            for p in ("youtube", "tiktok")
        ]
        return {
            "cartella": "x",
            "whop_campaign_uuid": uuid,
            "clip": clip,
            "break_even_per_piattaforma": {"youtube": 1750.0, "tiktok": 1250.0},
        }

    def righe(self, camp):
        misure = {"https://youtube/1": {"views": 1500}, "https://tiktok/1": {"views": 1500}}
        return stato.clip_di_campagna(camp, [], misure)[0]["piattaforme"]

    def test_ogni_piattaforma_la_sua_soglia(self):
        p = self.righe(self.campagna())
        self.assertEqual(p["youtube"]["pagamento"]["etichetta"], "sotto soglia")
        self.assertEqual(p["tiktok"]["pagamento"]["etichetta"], "sopra soglia")

    def test_la_fine_della_campagna_arriva_alla_riga(self):
        camp = self.campagna()
        camp["pagamenti_chiusi"] = "il budget della campagna è esaurito"
        p = self.righe(camp)
        self.assertEqual(p["youtube"]["pagamento"]["etichetta"], "non pagherà")
        self.assertEqual(p["tiktok"]["pagamento"]["etichetta"], "sopra soglia")

    def test_senza_whop_niente_pagamento(self):
        # Una campagna che non passa da Whop ha «in attesa» di default: non e' un invio.
        p = self.righe(self.campagna(uuid=None))
        self.assertIsNone(p["youtube"]["pagamento"])


if __name__ == "__main__":
    unittest.main()
