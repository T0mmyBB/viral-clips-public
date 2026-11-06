"""La serratura che mette in fila i processi di pubblicazione.

**Perche' questa merita una prova.** Da quando le tre piattaforme di una clip
escono insieme, due cose non reggono due scrittori: `publish_state.json`, che
si legge e si riscrive intero, e il profilo browser di Whop, che e' uno per
gruppo. Nessuna delle due protesta quando viene calpestata — il record sparisce
o il browser non parte — quindi il difetto si vede giorni dopo, guardando una
clip online che lo stato dice non pubblicata.

Come gli altri test del progetto, questi fissano **decisioni**, non coprono il
codice: che due prese non convivano, che l'attesa finisca invece di durare per
sempre, che una serratura abbandonata da un processo morto non blocchi il resto
della vita del progetto, e che una scrittura non si veda mai a meta'.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import serratura


class UnaAllaVolta(unittest.TestCase):
    """La cosa per cui esiste: due non entrano insieme."""

    def test_la_seconda_presa_aspetta_la_prima(self):
        dentro = []

        def lavoro(etichetta: str, quanto: float) -> None:
            with serratura.presa("prova-una-alla-volta", attesa=5):
                dentro.append(("entra", etichetta))
                time.sleep(quanto)
                dentro.append(("esce", etichetta))

        primo = threading.Thread(target=lavoro, args=("a", 0.20))
        primo.start()
        time.sleep(0.05)  # il tempo che «a» prenda la serratura
        secondo = threading.Thread(target=lavoro, args=("b", 0.01))
        secondo.start()
        primo.join(10)
        secondo.join(10)

        # Se si fossero sovrapposti, fra «entra a» e «esce a» ci sarebbe «entra b».
        self.assertEqual(dentro, [("entra", "a"), ("esce", "a"), ("entra", "b"), ("esce", "b")])

    def test_chi_aspetta_troppo_si_ferma_invece_di_entrare(self):
        # Proseguire lo stesso sarebbe la corsa che la serratura toglie: meglio
        # un errore che chi chiama puo' registrare come «non fatto».
        with serratura.presa("prova-attesa-finita", attesa=5):
            with self.assertRaises(serratura.Occupata):
                with serratura.presa("prova-attesa-finita", attesa=0.15):
                    self.fail("non doveva entrare: la serratura era presa")

    def test_due_nomi_diversi_non_si_ostacolano(self):
        with serratura.presa("prova-nome-uno", attesa=5):
            with serratura.presa("prova-nome-due", attesa=1):
                pass

    def test_si_apre_anche_se_il_lavoro_dentro_va_male(self):
        with self.assertRaises(ValueError):
            with serratura.presa("prova-eccezione", attesa=5):
                raise ValueError("un guasto qualunque")
        # Se il `finally` non togliesse il file, questa seconda presa aspetterebbe.
        with serratura.presa("prova-eccezione", attesa=1):
            pass


class Abbandonata(unittest.TestCase):
    """Un processo ucciso lascia il suo file: non deve bloccare tutti per sempre."""

    def test_una_serratura_vecchia_si_puo_togliere(self):
        percorso = serratura._file("prova-abbandonata")
        serratura.CARTELLA.mkdir(exist_ok=True)
        percorso.write_text("999999 processo morto\n", encoding="utf-8")
        vecchio = time.time() - 600
        os.utime(percorso, (vecchio, vecchio))
        try:
            with serratura.presa("prova-abbandonata", attesa=1, scaduta=300):
                pass
        finally:
            percorso.unlink(missing_ok=True)

    def test_una_serratura_recente_non_si_tocca(self):
        # Il numero va scelto piu' lungo del lavoro protetto: una serratura
        # scavalcata mentre il lavoro e' in corso rimette il difetto.
        with serratura.presa("prova-recente", attesa=5):
            with self.assertRaises(serratura.Occupata):
                with serratura.presa("prova-recente", attesa=0.15, scaduta=300):
                    self.fail("una presa viva non e' abbandonata")


class ScritturaIntera(unittest.TestCase):
    """I lettori non chiedono la serratura: non devono mai vedere mezzo file."""

    def test_il_file_vecchio_resta_intero_fino_alla_sostituzione(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "stato.json"
            f.write_text(json.dumps({"items": {"01@youtube": {}}}), encoding="utf-8")
            serratura.scrivi_atomico(f, json.dumps({"items": {"01@tiktok": {}}}))
            self.assertEqual(
                json.loads(f.read_text(encoding="utf-8")), {"items": {"01@tiktok": {}}}
            )
            # Nessun `.tmp` dimenticato accanto: `os.replace` sposta, non copia.
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["stato.json"])


if __name__ == "__main__":
    unittest.main()
