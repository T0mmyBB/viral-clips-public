"""Il cancello su `decisions.json`, l'unico file della pipeline scritto da un
modello invece che da codice deterministico.

Un campo mancante o un bordo invertito, senza questo controllo, si scoprirebbero
a meta' di un render da diversi minuti — o peggio, guardando le clip quando sono
gia' state renderizzate tutte.

Qui si fissano due cose che si potrebbero "semplificare" senza accorgersi:
il minimo di 20 secondi (non e' un vincolo di formato, e' il tempo sotto cui non
ci sta premessa-sviluppo-chiusura) e il fatto che la sovrapposizione si misura
**sulla clip piu' corta**.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import validate


def clip(**campi) -> dict:
    """Una clip valida, su cui i test cambiano un campo per volta."""
    base = {
        "slug": "prova",
        "start": 100.0,
        "end": 140.0,
        "hook": "Il *momento* che regge",
        "why": "si chiude da sola",
        "scores": {d: "solido" for d in validate.DIMENSIONS},
    }
    base.update(campi)
    return base


class Tagli(unittest.TestCase):
    def test_tagli_accetta_solo_un_booleano(self):
        self.assertEqual(validate.check_clip(clip(tagli=False), 0, None), [])
        self.assertTrue(validate.check_clip(clip(tagli="false"), 0, None))


class OverlapRatio(unittest.TestCase):
    def test_clip_lontane_non_si_sovrappongono(self):
        self.assertEqual(validate.overlap_ratio(clip(start=0, end=30), clip(start=60, end=90)), 0.0)

    def test_bordi_che_si_toccano_non_sono_sovrapposizione(self):
        # end == start: una finisce dove l'altra comincia, e non e' lo stesso
        # momento. Con un `<` al posto del `<=` qui uscirebbe una divisione per
        # zero, non uno zero.
        self.assertEqual(validate.overlap_ratio(clip(start=0, end=30), clip(start=30, end=60)), 0.0)

    def test_si_misura_sulla_piu_corta(self):
        # **La parte che conta.** Dieci secondi dentro una clip da cento sono
        # il 100% della corta e il 10% della lunga: la prima e' interamente
        # contenuta nell'altra, quindi e' lo stesso momento detto due volte.
        # Misurandolo sulla lunga, o sull'unione, questo caso passerebbe.
        lunga, corta = clip(start=0, end=100), clip(start=40, end=50)
        self.assertEqual(validate.overlap_ratio(lunga, corta), 1.0)

    def test_e_simmetrica(self):
        a, b = clip(start=0, end=100), clip(start=40, end=50)
        self.assertEqual(validate.overlap_ratio(a, b), validate.overlap_ratio(b, a))

    def test_clip_identiche(self):
        self.assertEqual(
            validate.overlap_ratio(clip(start=10, end=50), clip(start=10, end=50)), 1.0
        )

    def test_meta_della_corta(self):
        # 20-30 in comune, la corta e' 20-40: dieci su venti.
        self.assertAlmostEqual(
            validate.overlap_ratio(clip(start=0, end=30), clip(start=20, end=40)), 0.5
        )

    def test_sopra_la_soglia_e_lo_stesso_momento(self):
        # La soglia decisa e' 0.35: qui si fissa il confronto, non il numero,
        # cosi' cambiarla resta una scelta e non un effetto collaterale.
        molto = validate.overlap_ratio(clip(start=0, end=30), clip(start=5, end=35))
        self.assertGreater(molto, validate.OVERLAP_THRESHOLD)

    def test_durata_zero_non_divide_per_zero(self):
        self.assertEqual(validate.overlap_ratio(clip(start=10, end=10), clip(start=0, end=30)), 0.0)


class CheckClip(unittest.TestCase):
    def test_una_clip_buona_non_da_errori(self):
        self.assertEqual(validate.check_clip(clip(), 0, 600.0), [])

    def test_manca_un_campo_obbligatorio(self):
        for campo in validate.REQUIRED:
            with self.subTest(campo=campo):
                senza = clip()
                del senza[campo]
                errori = validate.check_clip(senza, 0, 600.0)
                self.assertTrue(any(f"'{campo}'" in e for e in errori), errori)

    def test_campo_vuoto_vale_come_mancante(self):
        errori = validate.check_clip(clip(hook=""), 0, 600.0)
        self.assertTrue(any("'hook'" in e for e in errori))

    def test_sotto_il_minimo_di_venti_secondi(self):
        errori = validate.check_clip(clip(start=100, end=115), 0, 600.0)
        self.assertTrue(any("sotto il minimo" in e for e in errori), errori)

    def test_esattamente_venti_secondi_passa(self):
        # Il minimo e' un minimo, non una soglia da superare.
        self.assertEqual(validate.check_clip(clip(start=100, end=120), 0, 600.0), [])

    def test_oltre_il_massimo(self):
        errori = validate.check_clip(clip(start=0, end=200), 0, 600.0)
        self.assertTrue(any("oltre i" in e for e in errori), errori)

    def test_il_tetto_della_campagna_si_spiega_in_modo_diverso(self):
        # Sopra i 180 s e' quasi sempre un bordo sbagliato; sopra il tetto di un
        # brief e' una clip che *quel committente* rifiuta. Due cose diverse, e
        # chi legge l'errore deve capire quale delle due gli e' capitata.
        proprio = validate.check_clip(clip(start=0, end=200), 0, 600.0)[0]
        campagna = validate.check_clip(clip(start=0, end=40), 0, 600.0, massimo=30.0)[0]
        self.assertIn("bordo", proprio)
        self.assertIn("questa campagna", campagna)

    def test_i_limiti_passati_vincono_su_quelli_del_file(self):
        # `check_clip` non sa e non deve sapere se un limite venga da qui o dal
        # brief: li riceve gia' intersecati da `campagna.limiti()`.
        self.assertEqual(
            validate.check_clip(clip(start=0, end=25), 0, 600.0, minimo=25.0, massimo=30.0), []
        )

    def test_end_prima_di_start_si_ferma_subito(self):
        # Con i bordi invertiti ogni altro controllo direbbe cose senza senso:
        # si torna un errore solo, quello vero.
        errori = validate.check_clip(clip(start=140, end=100), 0, 600.0)
        self.assertEqual(len(errori), 1)
        self.assertIn("non e dopo start", errori[0])

    def test_bordi_non_numerici_si_fermano_subito(self):
        errori = validate.check_clip(clip(start="00:01:40", end=140), 0, 600.0)
        self.assertEqual(len(errori), 1)
        self.assertIn("devono essere numeri", errori[0])

    def test_finisce_oltre_la_fine_del_video(self):
        errori = validate.check_clip(clip(start=100, end=140), 0, 120.0)
        self.assertTrue(any("ma il video dura" in e for e in errori), errori)

    def test_start_negativo(self):
        errori = validate.check_clip(clip(start=-5, end=30), 0, 600.0)
        self.assertTrue(any("start negativo" in e for e in errori), errori)

    def test_senza_durata_nota_non_si_inventa_il_controllo(self):
        # `duration=None` capita quando il video non e' ancora stato sondato.
        self.assertEqual(validate.check_clip(clip(start=100, end=140), 0, None), [])

    def test_punteggio_mancante(self):
        punteggi = {d: "solido" for d in validate.DIMENSIONS if d != "attrito"}
        errori = validate.check_clip(clip(scores=punteggi), 0, 600.0)
        self.assertTrue(any("attrito" in e for e in errori), errori)

    def test_livello_inventato(self):
        punteggi = {d: "solido" for d in validate.DIMENSIONS} | {"hook": "ottimo"}
        errori = validate.check_clip(clip(scores=punteggi), 0, 600.0)
        self.assertTrue(any("ottimo" in e for e in errori), errori)

    def test_dimensione_sconosciuta(self):
        # La rubrica ne prevede sei: una settima e' un punteggio inventato, e va
        # detto invece che ignorato.
        punteggi = {d: "solido" for d in validate.DIMENSIONS} | {"viralita": "forte"}
        errori = validate.check_clip(clip(scores=punteggi), 0, 600.0)
        self.assertTrue(any("viralita" in e for e in errori), errori)

    def test_scores_non_e_un_oggetto(self):
        errori = validate.check_clip(clip(scores=["solido"]), 0, 600.0)
        self.assertTrue(any("scores" in e for e in errori), errori)

    def test_l_etichetta_ripiega_sul_numero_quando_manca_lo_slug(self):
        senza = clip()
        del senza["slug"]
        errori = validate.check_clip(senza, 4, 600.0)
        self.assertTrue(all(e.startswith("clip #5") for e in errori), errori)


class AvvisiDiDurata(unittest.TestCase):
    """Sopra il minuto non e' un errore: e' un rischio da verificare."""

    def test_sotto_il_minuto_nessun_avviso(self):
        self.assertEqual(validate.duration_warnings(clip(start=0, end=59)), [])

    def test_sopra_il_minuto_avvisa_del_copyright(self):
        avvisi = validate.duration_warnings(clip(start=0, end=90))
        self.assertEqual(len(avvisi), 1)
        self.assertIn("musica di terzi", avvisi[0])

    def test_resta_un_avviso_non_un_errore(self):
        # Se diventasse bloccante, il tetto editoriale sarebbe 60 s — e non lo
        # e': Shorts accetta 3 minuti dal 15 ottobre 2024.
        self.assertEqual(validate.check_clip(clip(start=0, end=90), 0, 600.0), [])


class Consegna(unittest.TestCase):
    """La meta scritta a mano della consegna: `clips.md` c'e e cita ogni mp4."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        self._tmp = tempfile.TemporaryDirectory()
        self.cartella = Path(self._tmp.name)
        for nome in ("01-il-rospo.mp4", "02-la-piscina.mp4"):
            (self.cartella / nome).write_bytes(b"\0" * validate.MIN_BYTES_CLIP)
        # ffprobe non gira nei test: la sonda risponde come su una clip sana.
        self.letto = {"width": 1080, "height": 1920, "has_audio": True, "duration": 34.2}
        self.sonda = lambda f: dict(self.letto)

    def tearDown(self):
        self._tmp.cleanup()

    def scrivi(self, testo: str):
        (self.cartella / "clips.md").write_text(testo, encoding="utf-8")

    def test_senza_clips_md_non_e_consegnata(self):
        problemi = validate.check_consegna(self.cartella, self.sonda)
        self.assertEqual(len(problemi), 1)
        self.assertIn("clips.md", problemi[0])

    def test_una_clip_non_citata_e_un_errore(self):
        self.scrivi("## 01 — il rospo\n`01-il-rospo.mp4`\n")
        problemi = validate.check_consegna(self.cartella, self.sonda)
        self.assertEqual(len(problemi), 1)
        self.assertIn("02-la-piscina.mp4", problemi[0])

    def test_tutte_citate_passa(self):
        self.scrivi("`01-il-rospo.mp4`\n\n`02-la-piscina.mp4`\n")
        self.assertEqual(validate.check_consegna(self.cartella, self.sonda), [])

    def test_cartella_senza_clip(self):
        for f in self.cartella.glob("*.mp4"):
            f.unlink()
        self.assertTrue(validate.check_consegna(self.cartella, self.sonda))

    # I file, non solo i nomi (osservazione del 22 settembre 2026: sette mp4
    # da zero byte hanno passato il cancello perche clips.md li citava).

    def citate(self):
        self.scrivi("`01-il-rospo.mp4`\n\n`02-la-piscina.mp4`\n")

    def test_file_vuoto_citato_non_passa(self):
        self.citate()
        (self.cartella / "02-la-piscina.mp4").write_bytes(b"")
        problemi = validate.check_consegna(self.cartella, self.sonda)
        self.assertEqual(len(problemi), 1)
        self.assertIn("02-la-piscina.mp4", problemi[0])

    def test_il_peso_si_guarda_prima_della_sonda(self):
        # Su un file vuoto la sonda non va nemmeno chiamata.
        self.citate()
        (self.cartella / "01-il-rospo.mp4").write_bytes(b"")
        chiamati = []
        self.assertTrue(
            validate.check_consegna(
                self.cartella, lambda f: chiamati.append(f.name) or dict(self.letto)
            )
        )
        self.assertEqual(chiamati, ["02-la-piscina.mp4"])

    def test_file_illeggibile_non_passa(self):
        self.citate()

        def rotta(f):
            raise RuntimeError("moov atom not found")

        problemi = validate.check_consegna(self.cartella, rotta)
        self.assertEqual(len(problemi), 2)
        self.assertIn("illeggibile", problemi[0])

    def test_formato_audio_e_durata_sono_le_regole_base(self):
        self.citate()
        for campo, valore in (
            ("width", 1920),
            ("height", 1080),
            ("has_audio", False),
            ("duration", 12.0),
        ):
            with self.subTest(campo=campo):
                self.letto = {
                    "width": 1080,
                    "height": 1920,
                    "has_audio": True,
                    "duration": 34.2,
                    campo: valore,
                }
                self.assertEqual(len(validate.check_consegna(self.cartella, self.sonda)), 2)

    def test_un_file_troncato_dopo_la_scrittura_non_passa(self):
        # Con +faststart l'indice sta in testa: la sonda legge la durata piena,
        # ma dichiara byte che il file non ha. Misurato: 14 volte il peso vero.
        self.citate()
        self.letto["stream_bytes"] = validate.MIN_BYTES_CLIP * 14
        problemi = validate.check_consegna(self.cartella, self.sonda)
        self.assertEqual(len(problemi), 2)
        self.assertIn("troncato", problemi[0])

    def test_i_byte_dichiarati_sani_o_assenti_passano(self):
        # 1,001 e il massimo misurato sulle 111 clip vere; None e una traccia
        # che non dichiara il suo bit_rate, e il silenzio non e un difetto.
        self.citate()
        for dichiarati in (validate.MIN_BYTES_CLIP * 1.001, None):
            with self.subTest(dichiarati=dichiarati):
                self.letto["stream_bytes"] = dichiarati
                self.assertEqual(validate.check_consegna(self.cartella, self.sonda), [])

    def test_la_durata_ha_mezzo_secondo_di_tolleranza(self):
        # Una clip a 19,6 s e il minimo sulla griglia dei fotogrammi, non un difetto.
        self.citate()
        self.letto["duration"] = validate.MIN_DURATION - 0.4
        self.assertEqual(validate.check_consegna(self.cartella, self.sonda), [])
        self.letto["duration"] = validate.MIN_DURATION - 0.6
        self.assertTrue(validate.check_consegna(self.cartella, self.sonda))

    def test_senza_clips_md_si_elencano_anche_i_file_rotti(self):
        (self.cartella / "01-il-rospo.mp4").write_bytes(b"")
        problemi = validate.check_consegna(self.cartella, self.sonda)
        self.assertEqual(len(problemi), 2)
        self.assertIn("01-il-rospo.mp4", problemi[0])
        self.assertIn("clips.md", problemi[1])


class AvvisoRitaglio(unittest.TestCase):
    """Il brief dice di non perdere un marchio e il 9:16 butta i lati: si dice, non si finge.

    Osservazione 131: su 17 clip su 17 il watermark e la fascia con gli handle sono
    spariti, e `--consegna` e' passato verde. E' un avviso e non un errore, perche'
    nessuno script sa dove sta il marchio.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.work = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def prepara(self, larghezza, altezza, vincoli):
        (self.work / "meta.json").write_text(
            json.dumps({"width": larghezza, "height": altezza, "duration": 3600}),
            encoding="utf-8",
        )
        (self.work / "campagna.json").write_text(
            json.dumps({"titolo": "t", "regole": {"letto_il": "2026-09-30", "vincoli_render": vincoli}}),
            encoding="utf-8",
        )

    def test_orizzontale_con_watermark_avvisa_e_dice_la_quota(self):
        self.prepara(1920, 1080, ["il watermark deve restare visibile"])
        avviso = validate.avviso_ritaglio(self.work)
        self.assertIn("32%", avviso)
        self.assertIn("watermark", avviso)

    def test_sorgente_verticale_non_ha_niente_da_perdere(self):
        self.prepara(1080, 1920, ["il watermark deve restare visibile"])
        self.assertIsNone(validate.avviso_ritaglio(self.work))

    def test_un_vincolo_che_non_nomina_un_marchio_non_avvisa(self):
        self.prepara(1920, 1080, ["il video deve durare meno di trenta secondi"])
        self.assertIsNone(validate.avviso_ritaglio(self.work))

    def test_senza_dimensioni_non_si_indovina(self):
        self.prepara(0, 0, ["il watermark deve restare visibile"])
        self.assertIsNone(validate.avviso_ritaglio(self.work))

    def test_il_chiamante_lo_porta_fra_gli_avvisi(self):
        self.prepara(1920, 1080, ["il watermark deve restare visibile"])
        (self.work / "decisions.json").write_text(json.dumps({"clips": [clip()]}), encoding="utf-8")
        emesso = []
        with mock.patch.object(validate, "emit", emesso.append), mock.patch.object(
            validate, "log"
        ), mock.patch.object(sys, "argv", ["validate.py", str(self.work)]):
            validate.main()
        self.assertTrue(any("watermark" in w for w in emesso[0]["warnings"]))


if __name__ == "__main__":
    unittest.main()
