"""Le grafiche animate: cosa si accetta, quando partono, dove stanno nel render.

Gli errori che contano qui non danno errore. Una grafica partita mezzo secondo
prima della parola che illustra, una scritta che esce dal quadro, i sottotitoli
che restano sotto la grafica a dire la stessa cosa in piccolo, il logo coperto:
la clip esce e sembra fatta. Il render di HyperFrames non si prova qui (vuole
Node e Chrome), si provano le decisioni attorno.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import grafiche
import make_subs
import render_clip
import validate


def parola(word: str, start: float, end: float | None = None) -> dict:
    return {"word": word, "start": start, "end": end if end is not None else start + 0.3}


class Difetti(unittest.TestCase):
    def test_una_grafica_completa_passa(self):
        g = {"modello": "numero", "at": 10.0, "numero": "$7M", "etichetta": "raised"}
        self.assertEqual(grafiche.difetti(g), [])

    def test_modello_sconosciuto(self):
        self.assertIn("sconosciuto", grafiche.difetti({"modello": "esplosione", "at": 1})[0])

    def test_campo_mancante_e_at_mancante(self):
        errori = grafiche.difetti({"modello": "prima_dopo", "prima": "$0"})
        self.assertTrue(any("'at'" in e for e in errori))
        self.assertTrue(any("'dopo'" in e for e in errori))

    def test_testo_troppo_lungo_si_ferma(self):
        errori = grafiche.difetti({"modello": "parola", "at": 1, "testo": "x" * 19})
        self.assertTrue(any("caratteri" in e for e in errori))

    def test_gli_asterischi_non_contano_nella_lunghezza(self):
        g = {"modello": "parola", "at": 1, "testo": "*" + "x" * 18 + "*"}
        self.assertEqual(grafiche.difetti(g), [])

    def test_frase_con_due_accenti(self):
        g = {"modello": "frase", "at": 1, "testo": "a *b* c *d*"}
        self.assertTrue(any("al massimo una" in e for e in grafiche.difetti(g)))

    def test_elenco_vuole_da_due_a_tre_voci(self):
        g = {"modello": "elenco", "at": 1, "voci": ["a", "b", "c", "d"]}
        self.assertTrue(any("da 2 a 3" in e for e in grafiche.difetti(g)))
        g["voci"] = ["a", "b"]
        self.assertEqual(grafiche.difetti(g), [])

    def test_durata_fuori_misura(self):
        g = {"modello": "parola", "at": 1, "testo": "boom", "durata": 9}
        self.assertTrue(any("durata" in e for e in grafiche.difetti(g)))

    def test_bocciature_vuole_da_due_a_otto_tentativi(self):
        g = {"modello": "bocciature", "at": 1, "titolo": "YC applications", "testo": "denied",
             "quante": "6"}
        self.assertEqual(grafiche.difetti(g), [])
        for sbagliato in ("1", "9", "x"):
            g["quante"] = sbagliato
            self.assertTrue(
                any("da 2 a 8" in e for e in grafiche.difetti(g)), f"quante={sbagliato!r}"
            )

    def test_giorni_vuole_un_intero_e_nient_altro(self):
        g = {"modello": "giorni", "at": 1, "numero": "505"}
        self.assertEqual(grafiche.difetti(g), [])
        for sbagliato in ("505 days", "2,5", "$7M"):
            g["numero"] = sbagliato
            self.assertTrue(
                any("intero" in e for e in grafiche.difetti(g)), f"numero={sbagliato!r}"
            )


class Istante(unittest.TestCase):
    WORDS = [
        parola("fifty", 100.0),
        parola("twenty", 103.2),
        parola("twenty", 105.5),
        parola("twenty,", 109.4),
    ]

    def test_senza_parola_vale_at(self):
        self.assertEqual(grafiche.istante({"at": 104.0}, self.WORDS), 104.0)

    def test_la_parola_piu_vicina_ad_at_vince(self):
        # Piu «twenty» entro tre secondi: si prende quella vicina ad `at`, non la
        # prima che si incontra, e la punteggiatura attaccata non la nasconde.
        self.assertEqual(grafiche.istante({"at": 108.6, "parola": "Twenty"}, self.WORDS), 109.4)
        self.assertEqual(grafiche.istante({"at": 105.0, "parola": "twenty"}, self.WORDS), 105.5)
        self.assertEqual(grafiche.istante({"at": 103.5, "parola": "twenty"}, self.WORDS), 103.2)

    def test_parola_non_detta_e_un_errore(self):
        with self.assertRaises(ValueError):
            grafiche.istante({"at": 100.0, "parola": "hundred"}, self.WORDS)


class Piano(unittest.TestCase):
    def g(self, at: float, **extra) -> dict:
        return {"modello": "parola", "at": at, "testo": "boom", **extra}

    def test_istante_nel_tempo_della_clip_montata(self):
        # Clip 100-130, tolto il tratto 5-8: una grafica a 110 (10 nel sorgente
        # della clip) parte a 7 nella clip montata.
        voci, problemi = grafiche.piano([self.g(110)], [], 100, 130, [(0, 5), (8, 30)])
        self.assertEqual(problemi, [])
        self.assertAlmostEqual(voci[0]["t"], 7.0)

    def test_dentro_un_silenzio_tolto_parte_al_tratto_dopo(self):
        voci, _ = grafiche.piano([self.g(106)], [], 100, 130, [(0, 5), (8, 30)])
        self.assertAlmostEqual(voci[0]["t"], 5.0)

    def test_troppo_vicine_si_tiene_la_prima(self):
        voci, problemi = grafiche.piano([self.g(112), self.g(110)], [], 100, 130, [])
        self.assertEqual([v["indice"] for v in voci], [1])
        self.assertEqual(len(problemi), 1)

    def test_distanti_abbastanza_restano_tutte_e_due(self):
        dopo = 110 + 1.6 + grafiche.STACCO_MIN
        voci, problemi = grafiche.piano([self.g(110), self.g(dopo)], [], 100, 130, [])
        self.assertEqual(len(voci), 2)
        self.assertEqual(problemi, [])

    def test_in_fondo_si_accorcia_o_si_salta(self):
        voci, _ = grafiche.piano([self.g(128.6)], [], 100, 130, [])
        self.assertAlmostEqual(voci[0]["durata"], 1.3, places=2)
        voci, problemi = grafiche.piano([self.g(129.5)], [], 100, 130, [])
        self.assertEqual(voci, [])
        self.assertEqual(len(problemi), 1)


class Layout(unittest.TestCase):
    def test_la_parola_lunga_si_stringe_e_sta_nella_fascia(self):
        corta = grafiche._parola({"testo": "boom"})
        lunga = grafiche._parola({"testo": "cinque milioni ora"})
        self.assertEqual(corta["size"], 150)
        self.assertLess(lunga["size"], 150)
        self.assertLessEqual(grafiche.larghezza(lunga["testo"], lunga["size"]), 820 + 1)

    def test_la_frase_lunga_va_su_piu_righe_e_ci_sta(self):
        d = grafiche._frase({"testo": "Nobody pays you to *wait* for permission"})
        self.assertGreaterEqual(len(d["righe"]), 2)
        accese = [w["t"] for r in d["righe"] for w in r if w["a"]]
        self.assertEqual(accese, ["WAIT"])
        for r in d["righe"]:
            riga = " ".join(w["t"] for w in r)
            self.assertLessEqual(grafiche.larghezza(riga, d["size"]), 940 + 1)

    def test_oltre_quattro_parole_mai_una_riga_sola(self):
        # Cinque parole cortissime starebbero in una riga: si va a capo lo stesso,
        # perche una riga da cinque parole esce in corpo piccolo.
        self.assertTrue(grafiche.larghezza("A B C D E", 96) < 940)
        self.assertGreaterEqual(len(grafiche._frase({"testo": "a b c d e"})["righe"]), 2)
        self.assertEqual(len(grafiche._frase({"testo": "a b c d"})["righe"]), 1)

    def test_il_numero_si_legge_per_contarlo(self):
        d = grafiche._numero({"numero": "$7M"})
        self.assertEqual((d["prefisso"], d["valore"], d["suffisso"]), ("$", 7.0, "M"))
        d = grafiche._numero({"numero": "2,5x"})
        self.assertEqual((d["valore"], d["decimali"], d["separatore"]), (2.5, 1, ","))

    def test_le_bocciature_stanno_su_tre_colonne_al_massimo(self):
        d = grafiche._bocciature({"titolo": "yc", "testo": "denied", "quante": "6"})
        self.assertEqual((d["quante"], d["colonne"]), (6, 3))
        self.assertEqual(grafiche._bocciature({"titolo": "yc", "testo": "no", "quante": "2"})["colonne"], 2)
        # Tre colonne da 290 px e due vuoti da 24 stanno nei 940 della fascia.
        self.assertLessEqual(3 * 290 + 2 * 24, 940)
        # Il timbro sta dentro il riquadro anche con la parola piu lunga permessa.
        d = grafiche._bocciature({"titolo": "yc", "testo": "x" * 12, "quante": "3"})
        self.assertLessEqual(grafiche.larghezza(d["testo"], d["size_timbro"]), 230 + 1)

    def test_i_giorni_contano_fino_al_valore_detto(self):
        d = grafiche._giorni({"numero": " 505 ", "etichetta": "days"})
        self.assertEqual((d["valore"], d["testo"], d["etichetta"]), (505, "505", "DAYS"))

    def test_un_numero_che_non_si_legge_entra_intero(self):
        self.assertFalse(grafiche._numero({"numero": "1.000.000"})["conta"])

    def test_misura_css_e_non_quella_di_libass(self):
        # L'em di CSS e unitsPerEm, quello di libass winAscent+winDescent: per
        # Archivo Black la misura di CSS e piu larga, e usare l'altra fa uscire
        # la scritta dal quadro.
        css = grafiche.larghezza("MILIONI", 100)
        ass = make_subs.text_width("MILIONI", "Archivo Black", 100)
        self.assertGreater(css, ass)


class Sottotitoli(unittest.TestCase):
    def test_le_parole_sotto_la_grafica_spariscono(self):
        ws = [parola("a", 0.0), parola("b", 1.0), parola("c", 2.0), parola("d", 3.0)]
        out = make_subs.sospendi(ws, [(0.9, 2.5)])
        self.assertEqual([w["word"] for w in out], ["a", "d"])

    def test_nessuna_riga_attraversa_la_grafica(self):
        # Parole fitte: tolta quella sotto la grafica, il buco e piu corto di una
        # pausa vera, e senza il confine la riga lo scavalcherebbe.
        ws = [parola(w, 0.25 * i, 0.25 * i + 0.22) for i, w in enumerate("one two three four five".split())]
        ass = make_subs.build_ass(ws, 0.0, 3.0, pause=[(0.45, 0.72)])
        self.assertIn("FOUR", ass)
        righe = [l for l in ass.splitlines() if l.startswith("Dialogue") and ",Main," in l]
        for r in righe:
            inizio, fine = (self._secondi(x) for x in r.split(",")[1:3])
            self.assertFalse(inizio < 0.72 and fine > 0.45, r)
        self.assertNotIn("THREE", ass)

    @staticmethod
    def _secondi(t: str) -> float:
        h, m, s = t.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)


class Filtergraph(unittest.TestCase):
    def graph(self, logo: bool) -> str:
        staged = {"fontsdir": "fonts", "subs": "subs.ass"}
        if logo:
            staged["logo"] = "logo.png"
        return render_clip.build_filter_graph(
            (608, 1080, 656, 0),
            staged,
            False,
            "anull",
            [(0.0, 30.0)],
            30.0,
            [{"nome": "grafiche/parola_abc.mov", "t": 4.5}],
        )

    def test_la_grafica_sta_sopra_i_sottotitoli_e_sotto_il_logo(self):
        g = self.graph(logo=True)
        self.assertLess(g.index("ass=f=subs.ass"), g.index("movie=grafiche/parola_abc.mov"))
        self.assertLess(g.index("parola_abc.mov"), g.index("movie=logo.png"))

    def test_parte_al_suo_istante_e_non_resta_ferma_alla_fine(self):
        g = self.graph(logo=False)
        self.assertIn("setpts=PTS-STARTPTS+4.5000/TB", g)
        self.assertIn(f"overlay=0:{grafiche.TELA_Y}:eof_action=pass", g)
        self.assertTrue(g.split(";")[-2].endswith("format=yuv420p[vout]"))

    def test_senza_grafiche_il_filtergraph_e_quello_di_prima(self):
        staged = {"fontsdir": "fonts", "subs": "subs.ass"}
        a = render_clip.build_filter_graph((608, 1080, 656, 0), staged, False, "anull", [(0, 30)], 30)
        b = render_clip.build_filter_graph(
            (608, 1080, 656, 0), staged, False, "anull", [(0, 30)], 30, []
        )
        self.assertEqual(a, b)
        self.assertNotIn("movie=", a)


class Validate(unittest.TestCase):
    CLIP = {
        "slug": "01-x",
        "start": 100.0,
        "end": 130.0,
        "hook": "x",
        "scores": {d: "solido" for d in validate.DIMENSIONS},
    }

    def errori(self, grafiche_clip) -> list[str]:
        clip = {**self.CLIP, "grafiche": grafiche_clip}
        return [e for e in validate.check_clip(clip, 0, None) if "grafic" in e]

    def test_grafica_fuori_dalla_clip(self):
        e = self.errori([{"modello": "parola", "at": 140, "testo": "boom"}])
        self.assertTrue(any("fuori dalla clip" in x for x in e))

    def test_grafica_valida_non_da_errori(self):
        self.assertEqual(self.errori([{"modello": "parola", "at": 110, "testo": "boom"}]), [])

    def test_grafiche_non_elenco(self):
        self.assertTrue(self.errori({"modello": "parola"}))


class Modelli(unittest.TestCase):
    def test_ogni_modello_ha_il_suo_file(self):
        for nome in grafiche.MODELLI:
            html = (grafiche.MODELLI_DIR / f"{nome}.html").read_text(encoding="utf-8")
            self.assertIn("__DURATA__", html, nome)
            self.assertIn('data-width="1080" data-height="760"', html, nome)

    def test_nessun_modello_carica_qualcosa_dalla_rete(self):
        # Un <script src> o un font da un indirizzo vuol dire una richiesta a
        # ogni render: la regola del progetto e tutto in locale.
        cartella = Path(grafiche.GRAFICHE_DIR)
        for f in [*cartella.glob("modelli/*.html"), cartella / "assets" / "base.css"]:
            testo = f.read_text(encoding="utf-8")
            self.assertNotIn("http://", testo, f.name)
            self.assertNotIn("https://", testo, f.name)


if __name__ == "__main__":
    unittest.main()
