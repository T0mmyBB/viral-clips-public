"""Il provider di una campagna non si perde perche' il sito ha smesso di dirlo.

Dal 16 settembre 2026 una parte delle schede non espone piu' l'esperienza Whop.
Senza provider una campagna risulta «non iscritto» anche quando lo siamo, e il
collegamento per inviare sparisce: tutto senza un errore, mentre ogni numero
della scheda torna. `riporta_provider` rimette il valore letto prima — ma solo
dove oggi manca, e mai sopra una lettura fallita.
"""

from __future__ import annotations

import unittest

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import dettagli

JOIN_VECCHIO = "https://whop.com/experiences/exp_vecchio/campaigns/a"


class RiportaProvider(unittest.TestCase):
    def test_riprende_il_provider_dove_oggi_manca(self):
        oggi = [{"id": "a", "provider": "", "join": "https://contentrewards.com/discover/a/join"}]
        ieri = [{"id": "a", "provider": "exp_vecchio", "join": JOIN_VECCHIO}]
        self.assertEqual(dettagli.riporta_provider(oggi, ieri), 1)
        self.assertEqual(oggi[0]["provider"], "exp_vecchio")
        self.assertEqual(oggi[0]["join"], JOIN_VECCHIO)

    def test_la_lettura_di_oggi_vince(self):
        """Un'esperienza cambiata e' un fatto nuovo, non un buco da riempire."""
        oggi = [{"id": "a", "provider": "exp_nuovo", "join": "nuovo"}]
        ieri = [{"id": "a", "provider": "exp_vecchio", "join": JOIN_VECCHIO}]
        self.assertEqual(dettagli.riporta_provider(oggi, ieri), 0)
        self.assertEqual(oggi[0]["provider"], "exp_nuovo")

    def test_non_ripara_una_scheda_non_letta(self):
        """Una scheda con errore resta un errore: non diventa mezza scheda."""
        oggi = [{"id": "a", "errore": "timeout"}]
        ieri = [{"id": "a", "provider": "exp_vecchio", "join": JOIN_VECCHIO}]
        self.assertEqual(dettagli.riporta_provider(oggi, ieri), 0)
        self.assertNotIn("provider", oggi[0])

    def test_campagna_mai_vista_resta_senza(self):
        oggi = [{"id": "b", "provider": "", "join": ""}]
        ieri = [{"id": "a", "provider": "exp_vecchio", "join": JOIN_VECCHIO}]
        self.assertEqual(dettagli.riporta_provider(oggi, ieri), 0)
        self.assertEqual(oggi[0]["provider"], "")


class RiportaMateriali(unittest.TestCase):
    """Dal sito nuovo i materiali si vedono solo dopo l'iscrizione: si tiene l'ultima lettura."""

    DETT = [{"url": "https://youtu.be/x", "tipo": "brandAsset", "mezzo": "video"}]

    def test_archivio_e_ripresa(self):
        archivio = {}
        ieri = [{"id": "a", "materiali": ["https://youtu.be/x"], "materiali_dett": self.DETT}]
        self.assertEqual(dettagli.archivia_materiali(ieri, archivio, "2026-10-05"), 1)
        oggi = [{"id": "a", "materiali": [], "materiali_dett": [], "riferimenti": 3}]
        self.assertEqual(dettagli.riporta_materiali(oggi, archivio), 1)
        self.assertEqual(oggi[0]["materiali_dett"], self.DETT)
        self.assertEqual(oggi[0]["materiali_letti_il"], "2026-10-05")

    def test_la_lettura_di_oggi_vince(self):
        archivio = {
            "a": {
                "materiali": ["vecchio"],
                "materiali_dett": [{"url": "vecchio"}],
                "letto_il": "2026-10-05",
            }
        }
        oggi = [{"id": "a", "materiali": ["https://youtu.be/x"], "materiali_dett": self.DETT}]
        self.assertEqual(dettagli.riporta_materiali(oggi, archivio), 0)
        self.assertNotIn("materiali_letti_il", oggi[0])

    def test_non_ripara_una_scheda_non_letta(self):
        archivio = {"a": {"materiali": [], "materiali_dett": self.DETT, "letto_il": "2026-10-05"}}
        oggi = [{"id": "a", "errore": "timeout"}]
        self.assertEqual(dettagli.riporta_materiali(oggi, archivio), 0)

    def test_i_riferimenti_chiusi_si_contano(self):
        s = dettagli.scheda_da_api(
            {
                "id": "a",
                "name": "x",
                "referenceMaterials": [],
                "briefCounts": {"references": 3},
                "metrics": {},
            }
        )
        self.assertEqual((s["materiali_dett"], s["riferimenti"]), ([], 3))


def voce(**campi) -> dict:
    """Una voce dell'API del catalogo, nella forma misurata il 16 settembre 2026."""
    base = {
        "id": "a",
        "name": "Campagna",
        "primaryPayoutCents": 150,
        "budgetCents": 100_000,
        "organizationExperienceId": "exp_Abc123",
        "organizationName": "Org",
        "status": "active",
        "requiresApplication": False,
        "description": "d",
        "payoutType": "cpm",
        "platforms": ["tiktok"],
        "payouts": [
            {"platform": "tiktok", "rateCents": 150, "maxPayoutCents": 5000, "minPayoutCents": 100}
        ],
        "referenceMaterials": [],
        "metrics": {
            "creatorCount": 10,
            "budgetSpentCents": 40_000,
            "approvedSubmissionCount": 30,
            "topEarners": [{"earnedCents": 1234}],
            "chartPoints": [{"totalViews": 2_000_000, "approvedSubmissionCount": 20}],
        },
    }
    return {**base, **campi}


class SchedaDaApi(unittest.TestCase):
    """La stessa scheda della pagina: i campi che l'API mette altrove si ritrovano."""

    def test_i_numeri_dentro_metrics(self):
        s = dettagli.scheda_da_api(voce())
        self.assertEqual(s["clipper"], 10)
        self.assertEqual(s["residuo"], 600.0)
        self.assertEqual(s["primo_incasso"], 12.34)
        self.assertEqual(s["views_30g"], 2_000_000)
        self.assertEqual(s["soldi_mese"], 3000.0)
        self.assertEqual(s["titolo"], "Campagna")

    def test_join_whop_dall_esperienza(self):
        s = dettagli.scheda_da_api(voce())
        self.assertEqual(s["provider"], "exp_Abc123")
        self.assertEqual(s["join"], "https://whop.com/experiences/exp_Abc123/campaigns/a")

    def test_senza_esperienza_il_join_del_sito_e_assoluto(self):
        s = dettagli.scheda_da_api(voce(organizationExperienceId=None))
        self.assertEqual(s["provider"], "")
        self.assertEqual(s["join"], "https://contentrewards.com/discover/a/join")

    def test_materiale_caricato_prende_il_prefisso_del_bucket(self):
        """Senza, il link non si apre e l'impronta della campagna cambia per niente."""
        s = dettagli.scheda_da_api(
            voce(
                referenceMaterials=[
                    {"url": "organizations/o/campaigns/references/images/x.png", "type": "example"},
                    {"url": "https://drive.google.com/x", "type": "brandAsset"},
                ]
            )
        )
        self.assertEqual(
            s["materiali"],
            [
                dettagli.BUCKET + "organizations/o/campaigns/references/images/x.png",
                "https://drive.google.com/x",
            ],
        )


if __name__ == "__main__":
    unittest.main()
