"""Un campo che si svuota su tutte le schede e' un guasto, anche se ogni passo esce `ok`.

Il 16 settembre 2026 il sito ha cambiato forma a `joinHref` e `provider` e' uscito
vuoto su 643 schede su 643: nessun errore, nessun codice di uscita, ogni numero al
suo posto. `sorveglia` lo vede confrontando quante schede portano ciascun campo
con il riferimento — e il riferimento non si lascia trascinare giu' dal guasto,
altrimenti l'allarme durerebbe un giro solo.
"""

from __future__ import annotations

import unittest

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import giornaliero


def schede(n: int, **campi) -> list[dict]:
    base = {
        "provider": "exp_x",
        "join": "https://whop.com/x",
        "cpm": 1.0,
        "clipper": 5,
        "residuo": 100.0,
        "tariffe": [{}],
        "piattaforme": ["tiktok"],
        "views_30g": 10,
        "giorni_serie": 30,
        "descrizione": "d",
        "stato": "active",
        "organizzazione": "o",
        "materiali": ["u"],
        "tipo_pagamento": "cpm",
    }
    return [{**base, **campi, "id": str(i)} for i in range(n)]


class Copertura(unittest.TestCase):
    def test_conta_solo_le_schede_lette(self):
        q = giornaliero.copertura(schede(3) + [{"id": "rotta", "errore": "timeout"}])
        self.assertEqual(q["lette"], 0.75)
        self.assertEqual(q["provider"], 1.0)

    def test_nessuna_scheda_e_tutto_a_zero(self):
        q = giornaliero.copertura([])
        self.assertEqual(q["lette"], 0.0)
        self.assertEqual(q["provider"], 0.0)


class Sorveglia(unittest.TestCase):
    def test_il_campo_svuotato_da_allarme(self):
        rif = giornaliero.copertura(schede(10))
        adesso = giornaliero.copertura(schede(10, provider=""))
        allarmi, _ = giornaliero.sorveglia(rif, adesso)
        self.assertEqual([a["campo"] for a in allarmi], ["provider"])

    def test_la_deriva_del_catalogo_non_da_allarme(self):
        """Qualche punto in meno e' il catalogo che cambia, non un guasto."""
        rif = giornaliero.copertura(schede(10))
        adesso = giornaliero.copertura(schede(8) + schede(2, materiali=[]))
        allarmi, nuovo = giornaliero.sorveglia(rif, adesso)
        self.assertEqual(allarmi, [])
        self.assertEqual(nuovo["materiali"], 0.8)

    def test_l_allarme_resta_finche_il_campo_non_torna(self):
        rif = giornaliero.copertura(schede(10))
        rotto = giornaliero.copertura(schede(10, provider=""))
        _, rif_dopo = giornaliero.sorveglia(rif, rotto)
        self.assertEqual(rif_dopo["provider"], 1.0)
        allarmi, _ = giornaliero.sorveglia(rif_dopo, rotto)
        self.assertEqual([a["campo"] for a in allarmi], ["provider"])
        allarmi, _ = giornaliero.sorveglia(rif_dopo, giornaliero.copertura(schede(10)))
        self.assertEqual(allarmi, [])

    def test_primo_giro_senza_riferimento(self):
        adesso = giornaliero.copertura(schede(10, provider=""))
        allarmi, nuovo = giornaliero.sorveglia({}, adesso)
        self.assertEqual(allarmi, [])
        self.assertEqual(nuovo, adesso)

    def test_campo_non_piu_sorvegliato_non_da_allarme(self):
        adesso = giornaliero.copertura(schede(10))
        allarmi, _ = giornaliero.sorveglia({"campo_tolto": 1.0}, adesso)
        self.assertEqual(allarmi, [])


if __name__ == "__main__":
    unittest.main()
