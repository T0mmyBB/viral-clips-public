"""Quale colore cerca il controllo di sincronia, stile per stile.

**Perche questa merita una prova.** `check_sync.py` e l'unico controllo che
guarda i pixel della clip renderizzata, e il suo modo di sbagliare e un falso
allarme che sembra un difetto: «SFASATO» su una clip in sincrono. E gia
successo con una keyword ciano accanto alla parola accesa. Cercando sempre il
giallo di `bold`, lo stesso sarebbe successo su ogni clip in stile `punch`,
dove il giallo e delle keyword e la parola accesa e rossa — e su `minimal`,
dove la parola accesa non ha colore, avrebbe dato un verdetto su una misura
impossibile.

La decisione fissata: il colore viene dallo stile, e dove i pixel non possono
distinguere la parola accesa il controllo dice «non misurabile».
"""

from __future__ import annotations

import unittest

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import check_sync
from make_subs import STYLES


class ColoreAttivo(unittest.TestCase):
    def test_bold_e_il_giallo_di_sempre(self):
        bgr, _ = check_sync.colore_attivo(STYLES["bold"])
        self.assertEqual(bgr, check_sync.ACTIVE_BGR)

    def test_punch_cerca_il_rosso_non_il_giallo_delle_keyword(self):
        bgr, _ = check_sync.colore_attivo(STYLES["punch"])
        self.assertEqual(bgr, (0x3B, 0x3B, 0xFF))
        self.assertNotEqual(bgr, check_sync.ACTIVE_BGR)

    def test_minimal_non_si_misura_sui_pixel(self):
        bgr, perche = check_sync.colore_attivo(STYLES["minimal"])
        self.assertIsNone(bgr)
        self.assertIn("primary", perche)

    def test_colore_condiviso_con_le_keyword_non_si_misura(self):
        stile = dict(STYLES["bold"], keyword=STYLES["bold"]["active"])
        bgr, perche = check_sync.colore_attivo(stile)
        self.assertIsNone(bgr)
        self.assertIn("keyword", perche)

    def test_ogni_stile_ha_una_risposta(self):
        for nome, stile in STYLES.items():
            with self.subTest(stile=nome):
                bgr, perche = check_sync.colore_attivo(stile)
                self.assertTrue(bgr is not None or perche)


FPS = 23.976  # il caso che ha mostrato il difetto: Northwind Podcast E106


def evento(parola: str, inizio: float, durata: float) -> dict:
    return {
        "word": parola,
        "start": inizio,
        "end": inizio + durata,
        "x": 540.0,
        "y": 1400.0,
        "active": True,
    }


class EventiTroppoBrevi(unittest.TestCase):
    """Un evento da due fotogrammi non si campiona: il suo punto medio e un confine.

    `CAMERAS.` durava 80 ms; il fotogramma preso a 40 ms era quello dopo la fine e
    il controllo scriveva «nessun giallo nella striscia», il messaggio di uno
    sfasamento vero. Un allarme che risulta falso ogni volta insegna a ignorare
    anche quelli veri.
    """

    def test_due_fotogrammi_si_scartano(self):
        brevi = evento("CAMERAS.", 10.0, 0.08)
        lunghi, scartati = check_sync.campionabili([brevi, evento("PICCOLO", 11.0, 0.5)], FPS)
        self.assertNotIn(brevi, lunghi)
        self.assertEqual(scartati, 1)

    def test_la_soglia_e_tre_fotogrammi(self):
        tre = 3 / FPS
        sopra = evento("ABBASTANZA", 1.0, tre + 0.001)
        sotto = evento("TROPPOBREVE", 2.0, tre - 0.001)
        lunghi, scartati = check_sync.campionabili([sopra, sotto], FPS)
        self.assertEqual(lunghi, [sopra])
        self.assertEqual(scartati, 1)

    def test_la_soglia_segue_il_frame_rate(self):
        # 0,1 s sono 6 fotogrammi a 60 fps e 2,4 a 24: stessa durata, verdetto diverso.
        e = evento("PAROLA", 1.0, 0.1)
        self.assertEqual(check_sync.campionabili([e], 60.0)[0], [e])
        self.assertEqual(check_sync.campionabili([e], 24.0)[0], [])

    def test_fra_i_lunghi_si_preferiscono_le_parole_da_quattro_lettere(self):
        corta = evento("CHE", 1.0, 0.5)
        lunga = evento("CAMERA", 2.0, 0.5)
        self.assertEqual(check_sync.campionabili([corta, lunga], FPS)[0], [lunga])

    def test_senza_parole_lunghe_si_ripiega_sulle_corte_ma_non_sulle_brevi(self):
        corta = evento("CHE", 1.0, 0.5)
        breve = evento("CAMERAS.", 2.0, 0.08)
        self.assertEqual(check_sync.campionabili([corta, breve], FPS)[0], [corta])

    def test_tutti_troppo_brevi_non_ce_niente_da_campionare(self):
        self.assertEqual(check_sync.campionabili([evento("CAMERAS.", 1.0, 0.08)], FPS)[0], [])


class EventiTroppoBreviNelControllo(unittest.TestCase):
    """Il punto che usa la regola: `main` non deve piu campionare l'evento breve.

    La funzione pura giusta, chiamata nel posto sbagliato, e indistinguibile da una
    funzione sbagliata: qui si fa girare `main` con ffmpeg e OpenCV sostituiti e si
    guarda **dove** ha chiesto i fotogrammi.
    """

    def esegui(self, eventi, fps=FPS):
        import sys
        import tempfile
        from pathlib import Path
        from unittest import mock

        istanti, emessi = [], []
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            subs, clip = work / "x.ass", work / "x.mp4"
            subs.write_text("")
            clip.write_bytes(b"")
            dati = {
                "meta.json": {"slug": "x"},
                "decisions.json": {"clips": [{"slug": "c1", "start": 0.0, "end": 60.0}]},
                "transcript.json": {"words": []},
            }

            def grab(_clip, istante, dest):
                istanti.append(round(istante, 3))
                return dest

            argv = ["check_sync", str(work), "--slug", "c1", "--clip", str(clip), "--subs", str(subs)]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(check_sync, "read_json", lambda p: dati[Path(p).name]),
                mock.patch.object(check_sync, "read_events", lambda _p: eventi),
                mock.patch.object(check_sync, "video_info", lambda _p: {"fps": fps}),
                mock.patch.object(check_sync, "grab_frame", grab),
                mock.patch.object(check_sync, "highlight_x", lambda *a, **k: None),
                mock.patch.object(check_sync, "acceso_presente", lambda *a, **k: True),
                mock.patch.object(check_sync, "emit", emessi.append),
            ):
                try:
                    check_sync.main()
                except SystemExit as uscita:
                    return istanti, emessi, uscita
        return istanti, emessi, None

    def test_non_chiede_il_fotogramma_dell_evento_breve(self):
        breve = evento("CAMERAS.", 5.0, 0.08)
        eventi = [evento(f"PAROLA{i}", float(i), 0.5) for i in range(6)]
        eventi.insert(3, breve)  # dove lo pesca il campionamento a tre punti
        istanti, emessi, _ = self.esegui(eventi)
        self.assertTrue(istanti)
        self.assertNotIn(round((breve["start"] + breve["end"]) / 2, 3), istanti)
        self.assertEqual(emessi[0]["troppo_brevi_non_campionati"], 1)

    def test_solo_eventi_brevi_dice_non_verificabile_e_non_sfasato(self):
        _, emessi, uscita = self.esegui([evento("CAMERAS.", 5.0, 0.08)])
        self.assertIn("non verificabile", str(uscita))
        self.assertEqual(emessi, [])


if __name__ == "__main__":
    unittest.main()
