"""Chi chiama `claude -p` scrive il modello per intero, mai l'alias.

Il 22 settembre 2026 `claude update` ha portato il CLI da 2.1.251 a 2.1.280, e con
lui l'alias `opus` e' passato da Opus 5 a Opus 5.5: la scelta delle clip, le
didascalie e le nicchie hanno cambiato modello senza una riga di codice cambiata e
senza una misura. E' la stessa specie di difetto che le costanti `MODELLO` ed
`EFFORT` esistono per chiudere — la decisione presa altrove, in silenzio — solo
che stavolta la decideva la versione del CLI invece di `settings.json`.

Con l'id pieno il passaggio al modello dopo e' una riga di codice, e un CLI troppo
vecchio si ferma con un errore che dice quale versione serve invece di ripiegare
sul modello di prima.

Le costanti si leggono dal sorgente con `ast`, senza importare i moduli:
`conversa.py` e `automatico.py` si portano dietro il pannello e la skill, e qui
serve solo sapere cosa c'e' scritto.
"""

from __future__ import annotations

import ast
import unittest

import percorsi

CHIAMANTI = (
    percorsi.RADICE / ".claude" / "skills" / "viral-clips" / "scripts" / "automatico.py",
    percorsi.RADICE / "publish" / "prepara.py",
    percorsi.RADICE / "dashboard" / "conversa.py",
    percorsi.RADICE / "radar" / "classifica.py",
)

ALIAS = {"opus", "sonnet", "haiku", "fable", "default", "best", "opusplan"}
EFFORT = {"low", "medium", "high", "xhigh", "max"}


def costanti(percorso) -> dict[str, str]:
    albero = ast.parse(percorso.read_text(encoding="utf-8"))
    trovate = {}
    for nodo in albero.body:
        if (
            isinstance(nodo, ast.Assign)
            and len(nodo.targets) == 1
            and isinstance(nodo.targets[0], ast.Name)
            and isinstance(nodo.value, ast.Constant)
        ):
            trovate[nodo.targets[0].id] = nodo.value.value
    return trovate


class ModelloScritto(unittest.TestCase):
    def test_id_pieno_non_alias(self):
        for percorso in CHIAMANTI:
            with self.subTest(file=percorso.name):
                modello = costanti(percorso).get("MODELLO")
                self.assertIsInstance(modello, str)
                self.assertNotIn(modello.lower(), ALIAS)
                self.assertTrue(modello.startswith("claude-"), modello)

    def test_effort_valido(self):
        for percorso in CHIAMANTI:
            with self.subTest(file=percorso.name):
                self.assertIn(costanti(percorso).get("EFFORT"), EFFORT)

    def test_le_costanti_arrivano_alla_riga_di_comando(self):
        # Una costante giusta che nessuno passa a `claude` non decide niente.
        for percorso in CHIAMANTI:
            with self.subTest(file=percorso.name):
                testo = percorso.read_text(encoding="utf-8")
                self.assertRegex(testo, r'"--model",\s*(MODELLO|modello)')
                self.assertRegex(testo, r'"--effort",\s*EFFORT')


if __name__ == "__main__":
    unittest.main()
