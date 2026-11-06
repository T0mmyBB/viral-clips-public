"""Il controllo del moto della camera: come rigioca il piano, e cosa chiama tic.

**Perche questa merita una prova.** `check_camera.py` e l'unica misura del crop che
salta, e i suoi due modi di sbagliare costano in due direzioni opposte. Se rigioca
`sendcmd` un fotogramma in ritardo, ogni cambio di finestra sembra spostato e le
misure non corrispondono al video. Se chiama tic la finestra che cambia apposta su
uno stacco, grida al lupo su ogni montaggio veloce — cioe proprio sulle clip dove la
posizione per inquadratura serve — e un controllo che grida al lupo si smette di
leggere.
"""

from __future__ import annotations

import unittest

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import camera
import check_camera as cc

BASE = (608, 1080)
FPS = 30.0


def fermo(n: int, x: int = 700) -> list[tuple[int, int, int, int]]:
    return [(608, 1080, x, 0)] * n


class RigiocaIlPiano(unittest.TestCase):
    def test_legge_le_righe_scritte_dal_render(self):
        righe = camera.sendcmd_lines([(608, 1080, 700, 0), (608, 1080, 701, 0)], FPS)
        self.assertEqual(
            cc.leggi_sendcmd("\n".join(righe)),
            [(0.0, 608, 1080, 700, 0), (round(0.5 / FPS, 4), 608, 1080, 701, 0)],
        )

    def test_il_comando_vale_dal_fotogramma_per_cui_e_scritto(self):
        # Il render scrive il comando mezzo fotogramma prima: deve applicarsi al
        # fotogramma 5, non al 4 ne al 6.
        attese = fermo(5, 700) + fermo(5, 740)
        righe = camera.sendcmd_lines(attese, FPS)
        rigiocate = cc.finestre(cc.leggi_sendcmd("\n".join(righe)), FPS, len(attese))
        self.assertEqual(rigiocate, attese)

    def test_pari_allinea_la_posizione_come_il_filtro_senza_exact(self):
        comandi = [(0.0, 608, 1080, 733, 3)]
        self.assertEqual(cc.finestre(comandi, FPS, 1, pari=True), [(608, 1080, 732, 2)])


class CosaEUnTic(unittest.TestCase):
    def test_un_pixel_fra_due_fermi_e_un_micro_salto(self):
        fin = fermo(30, 700) + fermo(30, 701)
        esito = cc.misura(fin, FPS, BASE, bordi=[])
        self.assertEqual(esito["micro_salti"], 1)

    def test_lo_stesso_salto_su_uno_stacco_non_e_un_tic(self):
        fin = fermo(30, 700) + fermo(30, 701)
        esito = cc.misura(fin, FPS, BASE, bordi=[30 / FPS])
        self.assertEqual(esito["micro_salti"], 0)
        self.assertEqual(esito["cambi_sugli_stacchi"], 1)

    def test_senza_bordi_un_salto_grande_e_un_cambio_di_finestra(self):
        fin = fermo(30, 400) + fermo(30, 900)
        esito = cc.misura(fin, FPS, BASE, bordi=None)
        self.assertEqual(esito["micro_salti"], 0)
        self.assertEqual(esito["cambi_di_finestra_altrove"], 1)

    def test_una_panoramica_liscia_non_ha_tic_ne_inciampi(self):
        piano = [(0.0, 1.0, 700.0, 700.0), (1.0, 2.0, 700.0, 900.0), (2.0, 3.0, 900.0, 900.0)]
        fin = [(608, 1080, round(camera.position(piano, k / FPS)), 0) for k in range(int(3 * FPS))]
        esito = cc.misura(fin, FPS, BASE, bordi=[])
        self.assertEqual(esito["micro_salti"], 0)
        self.assertEqual(esito["inciampi"], 0)

    def test_una_panoramica_a_gradini_e_fatta_di_micro_salti(self):
        # Un comando ogni tre fotogrammi: il difetto dei piani a 10 Hz.
        fin = []
        for k in range(90):
            fin.append((608, 1080, 700 + 10 * (k // 3), 0))
        esito = cc.misura(fin, FPS, BASE, bordi=[])
        self.assertGreater(esito["micro_salti"], 20)

    def test_altezza_arrotondata_per_conto_suo_e_tremolio_d_aspetto(self):
        fin = [(606, 1078, 733, 0)] * 10
        self.assertEqual(cc.misura(fin, FPS, BASE, bordi=[])["tremolio_aspetto"], 10)
        giuste = [(606, 1076, 733, 0)] * 10
        self.assertEqual(cc.misura(giuste, FPS, BASE, bordi=[])["tremolio_aspetto"], 0)


if __name__ == "__main__":
    unittest.main()
