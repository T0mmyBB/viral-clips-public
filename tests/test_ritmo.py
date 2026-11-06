"""Il ritmo di pubblicazione: rampa, tetto giornaliero, distanza minima.

**Perche' proprio questo modulo per primo.** La regola e' misurata sul campo e
costa cara sbagliarla: @canale_lab ha fatto 218 views col primo video, poi
sei post in nove minuti, e da li' 0-6 views per settimane. E il modo in cui e'
gia' stata violata una volta e' istruttivo — non un errore nel calcolo, ma un
secondo punto d'ingresso che non passava di qui: le clip 01 e 02 sono uscite a
sette minuti l'una dall'altra su un canale nuovo, **senza nessun errore**,
perche' la pubblicazione riesce lo stesso e il danno arriva settimane dopo.

Quindi questi test non guardano se il codice "funziona": fissano i **numeri
decisi**, che e' l'unica cosa che qualcuno potrebbe cambiare per sbaglio
credendo di semplificare.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import ritmo


def istante(giorno: str, ora: str = "12:00") -> datetime:
    return datetime.fromisoformat(f"{giorno}T{ora}")


class TettoGiornaliero(unittest.TestCase):
    """La rampa di un account nuovo: prima si esiste, poi si pubblica piano."""

    def test_senza_data_di_apertura_e_maturo(self):
        # L'assunzione ottimista, e va detta: senza `aperto_il` non si frena.
        quota, motivo = ritmo.tetto_giornaliero(3)
        self.assertEqual(quota, 3)
        self.assertIn("maturo", motivo)

    def test_la_prima_settimana_e_di_sola_presenza(self):
        aperto = "2026-09-05"
        for giorni in range(0, 7):
            oggi = date.fromisoformat(aperto) + timedelta(days=giorni)
            quota, motivo = ritmo.tetto_giornaliero(3, aperto, oggi)
            self.assertEqual(quota, 0, f"giorno {giorni} dovrebbe essere fermo")
            self.assertIn("sola presenza", motivo)

    def test_le_quattro_settimane_della_rampa(self):
        aperto = "2026-09-05"
        atteso = {7: 1, 13: 1, 14: 2, 20: 2, 21: 3, 27: 3, 28: 3}
        for giorni, quota_attesa in atteso.items():
            oggi = date.fromisoformat(aperto) + timedelta(days=giorni)
            quota, _ = ritmo.tetto_giornaliero(3, aperto, oggi)
            self.assertEqual(quota, quota_attesa, f"giorno {giorni}")

    def test_la_rampa_non_alza_mai_il_tetto_dell_account(self):
        # Terza settimana = 3 al giorno, ma se l'account ne ammette 2 restano 2:
        # la rampa e' un freno, non un permesso.
        aperto = "2026-09-05"
        oggi = date.fromisoformat(aperto) + timedelta(days=21)
        self.assertEqual(ritmo.tetto_giornaliero(2, aperto, oggi)[0], 2)

    def test_senza_massimo_vale_la_quota_della_rampa(self):
        aperto = "2026-09-05"
        oggi = date.fromisoformat(aperto) + timedelta(days=14)
        self.assertEqual(ritmo.tetto_giornaliero(0, aperto, oggi)[0], 2)

    def test_data_illeggibile_non_blocca_ma_lo_dice(self):
        # Fermare un account per una data scritta male sarebbe peggio del danno
        # che si vuole evitare: si passa, ma il motivo lo dichiara.
        quota, motivo = ritmo.tetto_giornaliero(3, "ieri", date(2026, 9, 9))
        self.assertEqual(quota, 3)
        self.assertIn("non valida", motivo)


class QuandoSiPuo(unittest.TestCase):
    def test_account_maturo_senza_pubblicazioni(self):
        esito = ritmo.quando_si_puo([], massimo=3, adesso=istante("2026-09-09"))
        self.assertTrue(esito["puo"])
        self.assertEqual(esito["oggi"], 0)
        self.assertEqual(esito["tetto"], 3)
        self.assertIsNone(esito["ultimo"])

    def test_dice_perche_si_puo_non_solo_che_si_puo(self):
        # La plancia deve poter mostrare «tetto 3, oggi 1»: «ok» non e'
        # un'informazione, e chi guarda non saprebbe quanto margine resta.
        esito = ritmo.quando_si_puo(
            [istante("2026-09-09", "09:00")], massimo=3, adesso=istante("2026-09-09", "20:00")
        )
        self.assertTrue(esito["puo"])
        self.assertIn("1/3", esito["motivo"])

    def test_rampa_a_zero_blocca(self):
        esito = ritmo.quando_si_puo(
            [], massimo=3, aperto_il="2026-09-05", adesso=istante("2026-09-08")
        )
        self.assertFalse(esito["puo"])
        self.assertEqual(esito["tetto"], 0)
        self.assertIn("sola presenza", esito["motivo"])

    def test_tetto_giornaliero_raggiunto(self):
        fatte = [istante("2026-09-09", "09:00"), istante("2026-09-09", "13:00")]
        esito = ritmo.quando_si_puo(fatte, massimo=2, adesso=istante("2026-09-09", "20:00"))
        self.assertFalse(esito["puo"])
        self.assertIn("2/2", esito["motivo"])

    def test_conta_solo_i_post_di_oggi(self):
        # Tre ieri non pesano su oggi: il tetto e' giornaliero.
        fatte = [istante("2026-09-08", h) for h in ("09:00", "13:00", "19:00")]
        esito = ritmo.quando_si_puo(fatte, massimo=3, adesso=istante("2026-09-09", "10:00"))
        self.assertTrue(esito["puo"])
        self.assertEqual(esito["oggi"], 0)

    def test_sette_minuti_dopo_l_ultimo_post_e_troppo_presto(self):
        # **La regressione che questo modulo esiste per impedire.** Le clip 01 e
        # 02 sono uscite a sette minuti l'una dall'altra su un canale nuovo, e
        # quell'account e' stato strozzato.
        adesso = istante("2026-09-09", "20:07")
        esito = ritmo.quando_si_puo(
            [istante("2026-09-09", "20:00")], distanza_minima=5400, massimo=3, adesso=adesso
        )
        self.assertFalse(esito["puo"])
        self.assertIn("troppo presto", esito["motivo"])

    def test_dice_a_che_ora_si_potra(self):
        # Un rifiuto senza «quando» costringe a ricalcolarlo a mano ogni volta.
        adesso = istante("2026-09-09", "20:00")
        esito = ritmo.quando_si_puo(
            [istante("2026-09-09", "19:30")], distanza_minima=3600, massimo=3, adesso=adesso
        )
        self.assertFalse(esito["puo"])
        self.assertEqual(datetime.fromisoformat(esito["quando"]), adesso + timedelta(seconds=1800))

    def test_passata_la_distanza_minima_si_puo(self):
        esito = ritmo.quando_si_puo(
            [istante("2026-09-09", "18:00")],
            distanza_minima=5400,
            massimo=3,
            adesso=istante("2026-09-09", "20:00"),
        )
        self.assertTrue(esito["puo"])

    def test_senza_distanza_minima_non_si_guarda_l_ultimo(self):
        esito = ritmo.quando_si_puo(
            [istante("2026-09-09", "19:59")],
            distanza_minima=0,
            massimo=3,
            adesso=istante("2026-09-09", "20:00"),
        )
        self.assertTrue(esito["puo"])

    def test_accetta_stringhe_iso_e_datetime_insieme(self):
        # Lo stato su disco tiene stringhe ISO, il calendario passa datetime:
        # devono contare allo stesso modo o il tetto si conta a meta'.
        fatte = ["2026-09-09T09:00:00", istante("2026-09-09", "13:00")]
        esito = ritmo.quando_si_puo(fatte, massimo=3, adesso=istante("2026-09-09", "20:00"))
        self.assertEqual(esito["oggi"], 2)

    def test_le_date_illeggibili_si_saltano_senza_far_saltare_il_conto(self):
        fatte = ["mai", None, "", istante("2026-09-09", "09:00")]
        esito = ritmo.quando_si_puo(fatte, massimo=3, adesso=istante("2026-09-09", "20:00"))
        self.assertEqual(esito["oggi"], 1)

    def test_l_ultimo_e_il_piu_recente_non_l_ultimo_della_lista(self):
        # I momenti arrivano in qualunque ordine: prendere `[-1]` invece del
        # massimo farebbe passare una raffica ordinata al contrario.
        fatte = [istante("2026-09-09", "20:00"), istante("2026-09-09", "09:00")]
        esito = ritmo.quando_si_puo(
            fatte, distanza_minima=5400, massimo=3, adesso=istante("2026-09-09", "20:05")
        )
        self.assertFalse(esito["puo"])
        self.assertTrue(esito["ultimo"].startswith("2026-09-09T20:00"))


if __name__ == "__main__":
    unittest.main()
