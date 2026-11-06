"""Com'e' andato un invio a Whop: la riga che l'orchestratore scrive nello stato.

**Perche' questa merita una prova.** L'esito passa da un processo all'altro
come testo — `whop_agent` stampa `esito: <esito> — <pagina>` e l'orchestratore
lo ritaglia — e un ritaglio sbagliato non da' errore: scrive nello stato una
parola che sembra un esito. Il 14 settembre 2026 «non registrato» e' arrivato
come «non», il pannello ha mostrato un invio in sospeso che nessuno sapeva
come leggere, e l'unica prova che fosse un difetto e' stata guardare Whop a
mano.

Il verso che conta e' l'altro: un'uscita senza la riga non e' un invio. Tutte
le vie d'uscita anticipate di `cmd_invia` escono con codice 0.
"""

from __future__ import annotations

import sys
import unittest

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import orchestrator


def riga(esito: str, testo: str = "Skip to content Discover Home Campaigns") -> str:
    """La riga come la stampa `whop_agent.log()`."""
    return f"[2026-09-14 19:20:03] esito: {esito} — {testo}"


class LeggiEsitoWhop(unittest.TestCase):
    def test_esito_di_una_parola(self):
        self.assertEqual(orchestrator.leggi_esito_whop(riga("inviato")), "inviato")
        self.assertEqual(orchestrator.leggi_esito_whop(riga("rifiutato")), "rifiutato")

    def test_esito_di_due_parole_resta_intero(self):
        self.assertEqual(orchestrator.leggi_esito_whop(riga("non registrato")), "non registrato")
        self.assertEqual(orchestrator.leggi_esito_whop(riga("non verificato")), "non verificato")

    def test_pagina_vuota_dopo_il_trattino(self):
        self.assertEqual(orchestrator.leggi_esito_whop(riga("inviato", "")), "inviato")

    def test_riga_in_mezzo_al_resto_del_log(self):
        uscita = "\n".join(
            [
                "[2026-09-14 19:19:39] link scritto nel modulo: https://www.tiktok.com/@x/video/1",
                "[2026-09-14 19:20:03]   lettura 1/4: il post non compare ancora fra gli invii",
                riga("inviato"),
                "[2026-09-14 19:20:03] registrato in whop_invii.json",
            ]
        )
        self.assertEqual(orchestrator.leggi_esito_whop(uscita), "inviato")

    def test_senza_la_riga_non_e_un_invio(self):
        uscita = (
            "[2026-09-14 19:19:29] non sono arrivato alla scheda della campagna\n"
            "[2026-09-14 19:19:30] diagnostica in logs/x.png"
        )
        self.assertEqual(orchestrator.leggi_esito_whop(uscita), "non confermato")
        self.assertEqual(orchestrator.leggi_esito_whop(""), "non confermato")

    def test_invii_in_pausa_arriva_intero(self):
        """La frase dopo il trattino contiene due punti e virgolette: l'esito no."""
        _, frase = descrivi({"intake": "closed", "intakeClosedReason": "hold"})
        self.assertEqual(
            orchestrator.leggi_esito_whop(riga("invii in pausa", frase)), "invii in pausa"
        )


def descrivi(stato):
    """`whop_agent.descrivi_invii`, importato senza Scrapling (non e' nel venv del progetto)."""
    import types

    if "scrapling.fetchers" not in sys.modules:
        finto = types.ModuleType("scrapling.fetchers")
        finto.DynamicSession = object
        sys.modules.setdefault("scrapling", types.ModuleType("scrapling"))
        sys.modules["scrapling.fetchers"] = finto
    import whop_agent

    return whop_agent.descrivi_invii(stato)


class DescriviInvii(unittest.TestCase):
    """«Submit clip» spento: pausa e chiusura si dicono, e non si confondono.

    Il 16 settembre 2026 il pulsante era `disabled` e l'agente scriveva «non trovo il
    pulsante». Era la campagna che aveva sospeso gli invii (`intake: closed`, `hold`).
    Una pausa riapre e le clip si tengono ferme; una chiusura no.
    """

    def test_hold_e_una_pausa(self):
        self.assertEqual(
            descrivi({"intake": "closed", "intakeClosedReason": "hold"})[0], "invii in pausa"
        )

    def test_altro_motivo_e_una_chiusura(self):
        esito, frase = descrivi({"intake": "closed", "intakeClosedReason": "budget"})
        self.assertEqual(esito, "invii chiusi")
        self.assertIn("budget", frase)
        self.assertEqual(descrivi({"intake": "closed"})[0], "invii chiusi")

    def test_aperti_o_ignoti_non_si_spiegano(self):
        """Li' il pulsante spento e' un guasto nostro, e non va coperto con una scusa."""
        self.assertIsNone(descrivi({"intake": "open", "intakeClosedReason": None}))
        self.assertIsNone(descrivi(None))


if __name__ == "__main__":
    unittest.main()
