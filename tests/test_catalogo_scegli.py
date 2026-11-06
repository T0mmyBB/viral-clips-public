"""Dove «scegli» non c'e', la casella del catalogo dice perche'.

Il 17 settembre 2026 il pannello mostrava 174 righe lavorabili e 11 senza
pulsante: 9 a pagina `marca`/`esclusiva`, 2 gia' scelte. Il pulsante manca
apposta — una campagna che pretende una pagina intestata a se' e fuori dal
piano, e un pulsante dietro una conferma sarebbe un divieto cliccabile — ma
l'unica spiegazione stava in una legenda in fondo alla pagina, che delle cinque
ragioni possibili ne elencava due. Da fuori, una regola applicata togliendo un
comando e indistinguibile da un guasto.

Quello che questi test fissano non e' il disegno, e' l'invariante che lo regge:
**etichetta e frase sono vuote esattamente dove la campagna e' proponibile**,
cioe' non esiste una casella muta ne' una riga proponibile che si giustifica
per niente. E le somme non si spostano: «da prendere» continua a contare solo
le righe senza riserve.
"""

from __future__ import annotations

import unittest

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import stato


def riga(id_: str, lavorabile: str = "si", pagina: str | None = None, **extra) -> dict:
    return {
        "id": id_,
        "titolo": f"campagna {id_}",
        "lavorabile": lavorabile,
        "pagina": pagina,
        "campione_ok": True,
        "mesi": 3.0,
        "dollari_post": 10.0,
        **extra,
    }


def catalogo(righe: list[dict], scelte: dict | None = None) -> dict:
    nicchia = {"nome": "prova", "sottonicchie": [{"nome": "sotto", "campagne": righe}]}
    return stato.catalogo_di_nicchia(nicchia, {}, scelte or {})


class RiservaScegli(unittest.TestCase):
    def test_la_proponibile_non_ha_riserve(self):
        self.assertEqual(stato.riserva_scegli(riga("a"), None), ("", ""))

    def test_pagina_marca_dice_del_creator(self):
        perche = stato.riserva_scegli(riga("a", pagina="marca"), None)[1]
        self.assertIn("intestata", perche)

    def test_pagina_esclusiva_dice_del_divieto(self):
        perche = stato.riserva_scegli(riga("a", pagina="esclusiva"), None)[1]
        self.assertIn("vieta", perche)

    def test_lingua_e_forma_sono_ragioni_diverse(self):
        lingua = stato.riserva_scegli(riga("a", lavorabile="lingua"), None)[1]
        forma = stato.riserva_scegli(riga("a", lavorabile="no"), None)[1]
        self.assertIn("lingua" if "lingua" in lingua else "parlato", lingua)
        self.assertNotEqual(lingua, forma)

    def test_non_ancora_letta_non_e_scartata(self):
        # `?` e' lo stato che il progetto tiene separato da `no` da sempre: la
        # prima aspetta un giudizio, la seconda ne ha ricevuto uno. Le due
        # frasi non possono coincidere.
        # E vale per l'etichetta come per la frase: e' l'etichetta quella che
        # si legge in tabella, e due stati diversi con la stessa parola
        # direbbero che una campagna arrivata stanotte e' stata scartata.
        attesa = stato.riserva_scegli(riga("a", lavorabile="?"), None)
        scartata = stato.riserva_scegli(riga("a", lavorabile="no"), None)
        self.assertIn("nessuno ha ancora letto", attesa[1])
        self.assertNotEqual(attesa[0], scartata[0])
        self.assertNotEqual(attesa[1], scartata[1])

    def test_le_sei_riserve_si_leggono_diverse(self):
        # In tabella si legge l'etichetta, non la frase: due riserve con la
        # stessa parola sono una riserva sola, e la differenza che conta —
        # «aspetta un giudizio» contro «giudicata e scartata», «fuori dal
        # piano» contro «la pipeline non la lavora» — sparisce senza un errore.
        casi = [
            stato.riserva_scegli(riga("a", pagina="marca"), None),
            stato.riserva_scegli(riga("a", pagina="esclusiva"), None),
            stato.riserva_scegli(riga("a", lavorabile="lingua"), None),
            stato.riserva_scegli(riga("a", lavorabile="no"), None),
            stato.riserva_scegli(riga("a", lavorabile="?"), None),
            stato.riserva_scegli(riga("a"), {"campagna_id": "a"}),
        ]
        etichette = [e for e, _ in casi]
        self.assertEqual(len(set(etichette)), len(etichette), etichette)
        self.assertTrue(all(e and f for e, f in casi), casi)

    def test_la_gia_scelta_vince_su_ogni_altra_ragione(self):
        # Una campagna gia' scelta e' quello, prima di tutto il resto: se
        # vincesse la pagina, la riga direbbe «fuori dal piano» di una campagna
        # che si sta lavorando.
        perche = stato.riserva_scegli(
            riga("a", pagina="marca"),
            {"campagna_id": "a", "stato": "in-corso", "scelta_il": "2026-09-13T10:00:00"},
        )[1]
        self.assertIn("già scelta", perche)
        self.assertIn("2026-09-13", perche)


class RigheDelCatalogo(unittest.TestCase):
    def righe_di_prova(self) -> list[dict]:
        cat = catalogo(
            [
                riga("a"),
                riga("b", pagina="marca"),
                riga("c", pagina="esclusiva"),
                riga("d", lavorabile="lingua"),
                riga("e", lavorabile="no"),
                riga("f", lavorabile="?"),
                riga("g"),
            ],
            {"g": {"campagna_id": "g", "stato": "da-lanciare", "scelta_il": "2026-09-17T09:00:00"}},
        )
        return cat["sottonicchie"][0]["campagne"]

    def test_nessuna_casella_muta_e_nessun_avviso_per_niente(self):
        for r in self.righe_di_prova():
            with self.subTest(campagna=r["id"]):
                self.assertEqual(bool(r["perche_non_proponibile"]), not r["proponibile"])

    def test_dove_manca_il_pulsante_c_e_sempre_l_etichetta(self):
        # L'etichetta e' quello che si legge al posto del pulsante: se fosse
        # vuota la casella tornerebbe muta, con la frase nascosta in un titolo
        # che nessuno apre.
        for r in self.righe_di_prova():
            with self.subTest(campagna=r["id"]):
                self.assertEqual(
                    bool(r["etichetta_non_proponibile"]), bool(r["perche_non_proponibile"])
                )

    def test_le_riserve_non_entrano_nel_conto_da_prendere(self):
        cat = catalogo([riga("a"), riga("b", pagina="marca"), riga("c", lavorabile="lingua")])
        self.assertEqual(cat["proponibili"], 1)
        self.assertEqual(cat["totale"], 3)


if __name__ == "__main__":
    unittest.main()
