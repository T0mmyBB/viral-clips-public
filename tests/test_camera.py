"""La regia della camera virtuale: quando sta ferma, come si muove, quanto stringe.

Il difetto che questi test fissano non si vede in un fotogramma: si vede solo
guardando la clip scorrere, ed e il modo in cui era sfuggito. Misurato sul moto
esatto del crop di quattro clip reali, la camera di prima faceva **da 85 a 262
micro-salti isolati al minuto**: pixel arrotondati separatamente su larghezza e
altezza, e un crop che cambiava dieci volte al secondo invece che a ogni
fotogramma. Le regole qui sotto sono quelle che li hanno tolti; cambiarne una per
sbaglio non rompe niente di visibile, e rimette il tremolio.
"""

from __future__ import annotations

import unittest

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import camera

BASE_W, BASE_H = 608, 1080
SRC_W, SRC_H = 1920, 1080
DEAD = camera.DEAD_ZONE_FRAC * BASE_W
CUT = camera.CUT_INSTEAD_OF_PAN_FRAC * BASE_W
SPEED = camera.MOVE_SPEED_FRAC * BASE_W


def campioni(durata: float, valore, fps: float = 10.0):
    times = [round(k / fps, 3) for k in range(int(durata * fps))]
    return times, [valore(t) for t in times]


class Pose(unittest.TestCase):
    def test_soggetto_nella_zona_morta_camera_ferma_per_tutta_l_inquadratura(self):
        # Un podcast: la persona oscilla di 40 px attorno a 900.
        times, xs = campioni(20, lambda t: 900 + (40 if int(t * 3) % 2 else -40))
        piano = camera.plan_axis(times, xs, [], SRC_W / 2, DEAD, CUT, SPEED, 20.0)
        posizioni = {round(camera.position(piano, t / 30), 3) for t in range(600)}
        self.assertEqual(len(posizioni), 1, "dentro la zona morta la camera non si muove mai")

    def test_la_posa_sta_dove_il_soggetto_passa_l_inquadratura_non_dove_la_comincia(self):
        # Il primo secondo a 850, poi seduto a 950: dentro la zona morta, quindi
        # nessun movimento — ma la camera deve stare sulla mediana dell'intera
        # inquadratura, non sulla posizione dei primi istanti.
        times, xs = campioni(20, lambda t: 850 if t < 1.0 else 950)
        piano = camera.plan_axis(times, xs, [], SRC_W / 2, DEAD, CUT, SPEED, 20.0)
        self.assertAlmostEqual(camera.position(piano, 10.0), 950)

    def test_un_campione_anomalo_non_muove_la_camera(self):
        # Un falso positivo per un decimo di secondo, lontanissimo.
        times, xs = campioni(10, lambda t: 1500 if abs(t - 5.0) < 0.05 else 900)
        piano = camera.plan_axis(times, xs, [], SRC_W / 2, DEAD, CUT, SPEED, 10.0)
        self.assertTrue(all(tr[2] == tr[3] for tr in piano))

    def test_buco_del_rilevamento_usa_la_posizione_nota_non_il_centro(self):
        times, xs = campioni(10, lambda t: None if 3 < t < 6 else 400)
        piano = camera.plan_axis(times, xs, [], SRC_W / 2, DEAD, CUT, SPEED, 10.0)
        self.assertAlmostEqual(camera.position(piano, 4.5), 400)

    def test_inquadratura_senza_volti_va_al_centro(self):
        times, xs = campioni(10, lambda t: None if t >= 5 else 400)
        piano = camera.plan_axis(times, xs, [5.0], SRC_W / 2, DEAD, CUT, SPEED, 10.0)
        self.assertAlmostEqual(camera.position(piano, 7.0), SRC_W / 2)


class Movimenti(unittest.TestCase):
    def test_spostamento_vero_diventa_un_movimento_con_partenza_e_arrivo_dolci(self):
        # La persona si sposta di 150 px (oltre la zona morta, sotto lo stacco).
        times, xs = campioni(12, lambda t: 800 if t < 5 else 950)
        piano = camera.plan_axis(times, xs, [], SRC_W / 2, DEAD, CUT, SPEED, 12.0)
        mosse = [tr for tr in piano if tr[2] != tr[3]]
        self.assertEqual(len(mosse), 1)
        t0, t1, v0, v1 = mosse[0]
        self.assertGreaterEqual(t1 - t0, camera.MOVE_MIN_S - 1e-9)
        self.assertLessEqual(t1 - t0, camera.MOVE_MAX_S + 1e-9)
        # Velocita nulla ai capi: il primo passo e minuscolo rispetto a quello centrale.
        fps = 30.0
        primo = abs(camera.position(piano, t0 + 1 / fps) - camera.position(piano, t0))
        mezzo = abs(
            camera.position(piano, (t0 + t1) / 2 + 1 / fps) - camera.position(piano, (t0 + t1) / 2)
        )
        self.assertLess(primo, mezzo / 10)

    def test_nessun_pixel_isolato_alla_fine_di_un_movimento(self):
        # Il vlog del Mondiale: la camera ferma a 335,6 per qualche fotogramma, poi
        # l'arrotondamento la porta a 335 da sola — uno scatto di un pixel a camera ferma.
        piano = [(0.0, 1.0, 800.0, 800.0), (1.0, 1.9, 800.0, 335.45), (1.9, 5.0, 335.45, 335.45)]
        fps = 59.94
        xs = [round(camera.position(piano, k / fps)) for k in range(int(5 * fps))]
        passi = [abs(b - a) for a, b in zip(xs, xs[1:])]
        for i in range(1, len(passi) - 1):
            if passi[i] and not passi[i - 1] and not passi[i + 1]:
                self.fail(f"scatto isolato di {passi[i]} px al fotogramma {i + 1}")

    def test_spostamento_oltre_il_30_percento_e_uno_stacco(self):
        times, xs = campioni(12, lambda t: 500 if t < 5 else 1400)
        piano = camera.plan_axis(times, xs, [], SRC_W / 2, DEAD, CUT, SPEED, 12.0)
        self.assertFalse([tr for tr in piano if tr[2] != tr[3]], "nessuna panoramica")
        self.assertAlmostEqual(camera.position(piano, 11.0), 1400)

    def test_agli_stacchi_del_girato_non_si_insegue(self):
        times, xs = campioni(10, lambda t: 600 if t < 5 else 760)
        piano = camera.plan_axis(times, xs, [5.0], SRC_W / 2, DEAD, CUT, SPEED, 10.0)
        self.assertFalse([tr for tr in piano if tr[2] != tr[3]])
        self.assertAlmostEqual(camera.position(piano, 5.05), 760)

    def test_dopo_un_movimento_la_posa_dura_almeno_un_secondo(self):
        times, xs = campioni(20, lambda t: 800 + 130 * int(t / 1.2))
        piano = camera.plan_axis(times, xs, [], SRC_W / 2, DEAD, CUT, SPEED, 20.0)
        pose = [tr for tr in piano[1:-1] if tr[2] == tr[3]]
        for t0, t1, _, _ in pose:
            self.assertGreaterEqual(t1 - t0, camera.MIN_HOLD_S - 0.11)


class Finestra(unittest.TestCase):
    def test_le_proporzioni_restano_quelle_della_finestra_base_a_ogni_zoom(self):
        # Il tic di prima: larghezza e altezza arrotondate separatamente.
        for z in [1.0 + k * 0.0007 for k in range(300)]:
            w, h, _, _ = camera.window(960, 540, z, BASE_W, BASE_H, SRC_W, SRC_H)
            self.assertLessEqual(abs(h - w * BASE_H / BASE_W), 0.5 + 1e-9)

    def test_la_finestra_resta_dentro_il_fotogramma(self):
        for cx in (0, 300, 1919):
            for z in (1.0, 1.2):
                w, h, x, y = camera.window(cx, 100, z, BASE_W, BASE_H, SRC_W, SRC_H)
                self.assertGreaterEqual(x, 0)
                self.assertLessEqual(x + w, SRC_W)
                self.assertGreaterEqual(y, 0)
                self.assertLessEqual(y + h, SRC_H)

    def test_niente_allineamento_a_pixel_pari(self):
        xs = {camera.window(961 + k, 540, 1.0, BASE_W, BASE_H, SRC_W, SRC_H)[2] for k in range(4)}
        self.assertEqual(len(xs), 4, "un pixel di spostamento e un pixel, non due")

    def test_un_comando_per_ogni_fotogramma_che_cambia_e_nessuno_per_quelli_fermi(self):
        fermi = [(608, 1080, 600, 0)] * 30
        mossi = [(608, 1080, 600 + k, 0) for k in range(30)]
        self.assertEqual(len(camera.sendcmd_lines(fermi, 30.0)), 1)
        self.assertEqual(len(camera.sendcmd_lines(fermi + mossi, 30.0)), 30)


class MontaggioVeloce(unittest.TestCase):
    """Sul girato che stacca ogni due secondi: ferma dentro, riposizionata sugli stacchi."""

    SRC_H = 2160
    BASE_W = 1215

    def piano(self, xs_per_shot, hs_per_shot, cuts, durata):
        times, rx, rh = [], [], []
        bordi = [0.0] + cuts + [durata]
        for (a, b), xs, h in zip(zip(bordi, bordi[1:]), xs_per_shot, hs_per_shot):
            k = 0
            t = a
            while t < b - 1e-9:
                times.append(round(t, 3))
                rx.append(xs(k) if callable(xs) else xs)
                rh.append(h)
                k += 1
                t += 0.1
        ry = [900.0 if x is not None else None for x in rx]
        return camera.lock_per_shot(
            times, rx, ry, rh, cuts, durata, self.SRC_H, self.BASE_W, (1920.0, 1080.0)
        ), times

    def test_il_primo_piano_ha_la_sua_posizione_il_passante_resta_sulla_clip(self):
        (xs, _, proprie), times = self.piano([2400.0, 900.0], [500, 60], [2.0], 4.0)
        self.assertEqual(proprie, 1)
        self.assertEqual(xs[0], 2400.0)
        self.assertEqual(xs[-1], 1920.0, "volto di 60 px su 2160: un passante, non si insegue")

    def test_inquadratura_troppo_corta_resta_sulla_clip(self):
        (xs, _, proprie), _ = self.piano([2400.0, 2400.0], [500, 500], [0.5], 3.0)
        self.assertEqual(xs[0], 1920.0)
        self.assertEqual(proprie, 1)

    def test_nessun_movimento_dentro_un_inquadratura(self):
        (xs, _, _), times = self.piano([lambda k: 1500 + 40 * k], [500], [], 2.0)
        self.assertEqual(len(set(xs)), 1)

    def test_chi_attraversa_il_quadro_resta_dentro_dall_inizio_alla_fine(self):
        # Il caso del vlog: fermo a destra per un secondo, poi attraversa il quadro.
        # La mediana sta a destra e alla fine lo perde; il centro del tratto no.
        xs = [2150.0] * 10 + [2100 - 65 * k for k in range(10)]
        x = camera.shot_position(xs, 540, self.BASE_W)
        volto = 0.8 * 540
        for v in sorted(xs)[1:]:
            self.assertLessEqual(abs(v - x) + volto / 2, self.BASE_W / 2 + 1e-9)


class Zoom(unittest.TestCase):
    def test_scatti_distanziati_e_mai_sull_hook(self):
        scelti = camera.pick_punches([0.5, 3.0, 5.0, 19.0, 25.0, 40.0], 45.0)
        self.assertEqual(scelti, [3.0, 19.0, 40.0])

    def test_nessuno_scatto_a_ridosso_di_uno_stacco_o_di_un_taglio(self):
        # La 07 di Contoso: stacco a 8,25, scatto a 8,38, taglio dei silenzi a 8,89.
        self.assertEqual(camera.pick_punches([8.38], 110.0, cuts=[8.25, 8.89]), [])
        # Uno stacco appena dopo la fine dello scatto non lo impedisce.
        fine = (
            20.0
            - camera.PUNCH_LEAD_S
            + camera.PUNCH_RISE_S
            + camera.PUNCH_HOLD_S
            + camera.PUNCH_FALL_S
        )
        self.assertEqual(camera.pick_punches([20.0], 110.0, cuts=[15.0, fine + 0.2]), [20.0])

    def test_lo_scatto_entra_morbido_tiene_ed_esce(self):
        p = 10.0
        attacco = p - camera.PUNCH_LEAD_S
        self.assertEqual(camera.punch_envelope(attacco - 0.01, [p]), 1.0)
        self.assertAlmostEqual(
            camera.punch_envelope(attacco + camera.PUNCH_RISE_S + 0.5, [p]), camera.PUNCH_ZOOM
        )
        salita = [camera.punch_envelope(attacco + k / 30, [p]) for k in range(9)]
        self.assertEqual(salita, sorted(salita))
        self.assertLess(salita[1] - salita[0], salita[4] - salita[3])

    def test_lo_zoom_non_taglia_la_testa(self):
        # Un primo piano stretto: volto alto 500 px su 1080.
        self.assertLess(camera.max_zoom_for_face(1080, 500), 1.25)
        self.assertEqual(camera.max_zoom_for_face(1080, None), camera.ZOOM_MAX)
        self.assertGreaterEqual(camera.max_zoom_for_face(1080, 2000), 1.0)


if __name__ == "__main__":
    unittest.main()
