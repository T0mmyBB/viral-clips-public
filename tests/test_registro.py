"""Da quale account esce una clip: la guardia che si mette fra profilo e upload.

**E' la regola che costa di piu' sbagliare di tutto il progetto.** Una clip
pubblicata dall'account sbagliato non si ritira: si cancella il post, ma
l'algoritmo di quel canale l'ha gia' vista, e su Instagram nemmeno quello e' un
clic solo. E il modo in cui succede non e' un errore che si legge nei log — e'
una consegna che sembra riuscita: il caricamento va a buon fine, lo stato dice
«pubblicata», e l'URL viene cercato fra i video di un altro account.

I casi sono quattro, e fanno una tabella sola:

    registro dice | browser dice | cosa si fa
    --------------|--------------|--------------------------------------------
    @giusto       | @giusto      | si pubblica
    @giusto       | @altro       | si ferma: profilo browser sbagliato
    @giusto       | non lo sa    | si ferma: sessione scaduta
    non lo sa     | @chiunque    | si pubblica: niente da confrontare
    non lo sa     | non lo sa    | si ferma: non si saprebbe dove e' finita

L'ultima riga e' quella che mancava. Con il registro muto la guardia usciva
subito, e il chiamante ripiegava su un handle cablato — `canale_lab`, il
nome dell'account del laboratorio. Non sbagliava il caricamento, che va dove il
profilo e' collegato: sbagliava la **riga di stato**, cioe' il posto dove si va
a leggere cos'e' uscito e dove.
"""

from __future__ import annotations

import unittest

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import registro


class ControllaHandle(unittest.TestCase):
    def ferma(self, trovato, atteso):
        with self.assertRaises(registro.AccountSbagliato):
            registro.controlla_handle(trovato, atteso, "instagram")

    def passa(self, trovato, atteso):
        registro.controlla_handle(trovato, atteso, "instagram")

    def test_coincidono_si_pubblica(self):
        self.passa("@giusto", "giusto")

    def test_la_chiocciola_e_le_maiuscole_non_contano(self):
        # L'handle arriva dal DOM in una forma e dal registro in un'altra:
        # fermarsi per una maiuscola sarebbe un falso allarme, e un controllo
        # che grida al lupo insegna a scavalcarlo.
        self.passa("@Giusto", "giusto")
        self.passa("GIUSTO", "giusto")

    def test_profilo_browser_di_un_altro_account(self):
        self.ferma("@altro", "giusto")

    def test_sessione_scaduta_non_e_un_permesso(self):
        # Il rilevamento dell'handle fallisce proprio quando la sessione e'
        # scaduta, cioe' quando un caricamento finirebbe dove non deve.
        self.ferma(None, "giusto")
        self.ferma("", "giusto")

    def test_senza_registro_ma_col_browser_che_sa_chi_e(self):
        # Le cartelle nate prima dei gruppi: non c'e' niente da confrontare, e
        # chi lavora a mano su un account solo continua a funzionare.
        self.passa("@chiunque", None)
        self.passa("@chiunque", "")

    def test_ne_il_registro_ne_il_browser_sanno_chi_siamo(self):
        # «Non so chi devo essere» non autorizza «non so nemmeno chi sono».
        self.ferma(None, None)
        self.ferma("", "")

    def test_il_messaggio_dice_cosa_fare(self):
        # Un errore che non dice il rimedio si legge come un guasto del
        # programma, e il rimedio qui e' sempre lo stesso: rifare il login.
        for trovato, atteso in ((None, "giusto"), (None, None)):
            with self.subTest(trovato=trovato, atteso=atteso):
                try:
                    registro.controlla_handle(trovato, atteso, "instagram")
                except registro.AccountSbagliato as errore:
                    self.assertIn("login", str(errore).lower())


if __name__ == "__main__":
    unittest.main()
