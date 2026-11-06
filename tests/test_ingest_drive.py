"""Una cartella di Drive si ferma, e dice cosa contiene.

I brief delle campagne consegnano il materiale come cartella — sei episodi piu'
le sottocartelle degli extra — e scegliere quale lavorare e' una decisione
editoriale. Quello che lo script deve garantire e' che la scelta mancante non
sembri un guasto: lasciato a `yt-dlp`, un indirizzo di cartella muore con
`TypeError: expected string or bytes-like object, got 'bool'`, che non nomina
ne' Drive ne' la cartella e manda a cercare il difetto altrove.

Qui si fissano quattro cose: quali indirizzi sono una cartella, che un link a un
file passa **senza toccare la rete**, che l'elenco distingue file e
sottocartelle, e che il rifiuto resta un rifiuto anche quando l'elenco non si
riesce a leggere.
"""

from __future__ import annotations

import pathlib
import unittest

import percorsi  # noqa: F401  (mette le cartelle dei moduli su sys.path)

import ingest

# Un pezzo di `embeddedfolderview` ridotto all'osso: una sottocartella e un
# file. Le due entrate si distinguono solo per l'indirizzo dell'ancora.
HTML = (
    '<div class="flip-entries">'
    '<div class="flip-entry" id="entry-CARTELLA1" tabindex="0" role="link">'
    '<div class="flip-entry-info"><a href="https://drive.google.com/drive/folders/CARTELLA1"'
    ' target="_blank"><div class="flip-entry-icon"><div aria-label="Folder"></div></div>'
    '<div class="flip-entry-title">Additional Shortforms</div></a></div></div>'
    '<div class="flip-entry" id="entry-FILE1" tabindex="0" role="link">'
    '<div class="flip-entry-info"><a href="https://drive.google.com/file/d/FILE1/view"'
    ' target="_blank"><div class="flip-entry-icon"><div aria-label="Video"></div></div>'
    '<div class="flip-entry-title">Northwind Podcast_E101.mp4</div></a></div></div>'
    "</div>"
)


class RiconoscereUnaCartella(unittest.TestCase):
    """Le grafie che i brief usano davvero, e quelle che non sono cartelle."""

    def test_le_tre_grafie_sono_cartelle(self):
        for url in (
            "https://drive.google.com/drive/folders/1sZ3QYOjUfLpzo8IxPZLW2VWrmUqXNSTo",
            "https://drive.google.com/drive/u/0/folders/1sZ3QYOjUfLpzo8IxPZLW2VWrmUqXNSTo",
            "https://drive.google.com/drive/folders/1igqfZMD1Is0NCmEupQpfMT7Ot70r5Zq0?usp=sharing",
        ):
            with self.subTest(url=url):
                m = ingest.CARTELLA_DRIVE.search(url)
                self.assertIsNotNone(m, "questa e' una cartella e non e' stata riconosciuta")

    def test_un_file_e_un_video_non_sono_cartelle(self):
        for url in (
            "https://drive.google.com/file/d/14iOqE7DIniMYN3me21vmovHd6s60A65m/view",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://docs.google.com/document/d/1_7walPrnsma6KXyIn9hRYvE4BAkwiqy9/edit",
        ):
            with self.subTest(url=url):
                self.assertIsNone(ingest.CARTELLA_DRIVE.search(url))


class LeggereLElenco(unittest.TestCase):
    """Dall'HTML piatto escono tipo, id e nome, appaiati."""

    def test_distingue_sottocartelle_e_file(self):
        self.assertEqual(
            ingest.voci_da_html(HTML),
            [
                ("cartella", "CARTELLA1", "Additional Shortforms"),
                ("file", "FILE1", "Northwind Podcast_E101.mp4"),
            ],
        )


class AltezzaSconosciuta(unittest.TestCase):
    """Un'altezza che manca non e' un'altezza piccola.

    Su Drive il master del committente e' il formato `source`, e di quello Drive
    non dichiara la risoluzione; i transcodificati accanto si'. Letto come zero,
    `ingest_url` concludeva «sorgente gia' piccolo» e saltava il proxy: da li' in
    poi l'analisi girava sul 4K, che e' il lavoro che il proxy esiste per non
    fare.
    """

    FORMATI_DRIVE = {
        "formats": [
            {"format_id": "140", "height": None, "acodec": "mp4a.40.2", "vcodec": "none"},
            {"format_id": "134", "height": 360, "vcodec": "avc1.42C01E", "acodec": "none"},
            {"format_id": "136", "height": 720, "vcodec": "avc1.64001F", "acodec": "none"},
            {"format_id": "137", "height": 1080, "vcodec": "avc1.640028", "acodec": "none"},
            {"format_id": "source", "height": None, "vcodec": None, "acodec": None},
        ]
    }

    def test_prende_la_piu_alta_fra_quelle_dichiarate(self):
        self.assertEqual(ingest.altezza_piu_alta(self.FORMATI_DRIVE), 1080)

    def test_supera_la_soglia_del_proxy(self):
        # La conseguenza che conta: `ingest_url` scarica il proxy solo se
        # l'altezza sta sopra PROXY_HEIGHT.
        self.assertGreater(ingest.altezza_piu_alta(self.FORMATI_DRIVE), ingest.PROXY_HEIGHT)

    def test_senza_formati_resta_zero(self):
        self.assertEqual(ingest.altezza_piu_alta({}), 0)
        self.assertEqual(ingest.altezza_piu_alta({"formats": [{"height": None}]}), 0)


class SilenzioNonEAssenza(unittest.TestCase):
    """«Non lo dice» e «non ce l'ha» non sono la stessa cosa.

    YouTube separa le tracce e lo **dichiara**: una traccia video porta
    `acodec: "none"`. Il master caricato su Drive non dichiara niente — ne'
    codec ne' risoluzione — e leggere quel silenzio come una dichiarazione
    fermava l'ingest con «nessuna traccia video selezionabile» proprio sul file
    che la campagna consegna.
    """

    SOLO_VIDEO = {"format_id": "137", "vcodec": "avc1.640028", "acodec": "none"}
    SOLO_AUDIO = {"format_id": "140", "vcodec": "none", "acodec": "mp4a.40.2"}
    MASTER_DRIVE = {"format_id": "source", "ext": "mp4"}  # non dice nient'altro

    def test_il_master_muto_e_video_e_audio(self):
        self.assertFalse(ingest.traccia_assente(self.MASTER_DRIVE, "vcodec"))
        self.assertFalse(ingest.traccia_assente(self.MASTER_DRIVE, "acodec"))
        video, audio = ingest.selected_streams({"formats": [], **self.MASTER_DRIVE})
        self.assertEqual(video["format_id"], "source")
        self.assertIsNotNone(audio)

    def test_una_dichiarazione_resta_una_dichiarazione(self):
        self.assertTrue(ingest.traccia_assente(self.SOLO_VIDEO, "acodec"))
        self.assertTrue(ingest.traccia_assente(self.SOLO_AUDIO, "vcodec"))

    def test_su_due_tracce_separate_prende_quella_giusta_per_parte(self):
        video, audio = ingest.selected_streams(
            {"requested_formats": [self.SOLO_VIDEO, self.SOLO_AUDIO]}
        )
        self.assertEqual(video["format_id"], "137")
        self.assertEqual(audio["format_id"], "140")


class IlProxySiScaricaLoStesso(unittest.TestCase):
    """La conseguenza vera, presa su `ingest_url` e non su una funzione pura.

    Il difetto non stava nel conto dell'altezza — stava in chi lo leggeva: con
    `video_fmt["height"]` a `None`, il ramo del proxy vedeva zero, concludeva
    «sorgente gia' a bassa risoluzione» e scaricava **il master intero** al
    posto del proxy, lasciando l'analisi senza il file per cui e' stata
    disegnata. Qui si sostituiscono le funzioni che toccano la rete e si guarda
    quale delle due viene chiamata.
    """

    INFO = {
        "title": "Northwind Podcast E106",
        "duration": 958.29,
        "formats": AltezzaSconosciuta.FORMATI_DRIVE["formats"],
        # Quello che il selettore sceglie su Drive: il `source`, che non
        # dichiara ne' risoluzione ne' codec.
        "requested_formats": [
            {
                "format_id": "source",
                "height": None,
                "width": None,
                "vcodec": "unknown",
                "acodec": "none",
            },
            {"format_id": "140", "height": None, "acodec": "mp4a.40.2", "vcodec": "none"},
        ],
    }

    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.chiamate = []

        def finge(nome, ritorna):
            def f(*a, **k):
                self.chiamate.append(nome)
                return ritorna

            return f

        finto = {
            "probe": lambda url, lang: self.INFO,
            "download_audio": finge("audio", pathlib.Path("audio_src.m4a")),
            "extract_audio": finge("estrai", pathlib.Path("audio.wav")),
            "download_proxy": finge("proxy", pathlib.Path("proxy.mp4")),
            "download_source": finge("sorgente", pathlib.Path("source.mp4")),
            "attach_source": lambda work, meta, video: meta,
            "video_info": lambda p: {"width": 640, "height": 360},
            # Il master misurato a distanza: 4K vero, non i 1080p dei
            # transcodificati che gli stanno accanto.
            "misure_dal_flusso": finge(
                "misura",
                {
                    "width": 3840,
                    "height": 2160,
                    "fps": 23.976,
                    "video_codec": "h264",
                    "duration": 958.29,
                    "has_audio": True,
                },
            ),
        }
        for nome, f in finto.items():
            vero = getattr(ingest, nome)
            setattr(ingest, nome, f)
            self.addCleanup(setattr, ingest, nome, vero)

    def prepara(self):
        return ingest.ingest_url(
            "https://drive.google.com/file/d/FILE1/view", pathlib.Path(self.tmp.name), None
        )[1]

    def test_con_altezza_sconosciuta_scarica_il_proxy_non_il_master(self):
        self.prepara()
        self.assertIn(
            "proxy", self.chiamate, "senza proxy l'analisi gira sul master a piena risoluzione"
        )
        self.assertNotIn(
            "sorgente", self.chiamate, "il master serve al render, non alla preparazione"
        )

    def test_le_dimensioni_vengono_dal_flusso_non_dai_transcodificati(self):
        meta = self.prepara()
        self.assertIn("misura", self.chiamate, "un formato che tace si misura, non si indovina")
        self.assertEqual((meta["width"], meta["height"]), (3840, 2160))

    def test_un_16_9_non_viene_chiamato_verticale(self):
        # `width` mancante faceva concludere a `crop_fields()` «verticale», che
        # e' la condizione con cui la regia rinuncia a muovere la camera e a
        # ingrandire: un 16:9 lavorato come se fosse gia' verticale.
        meta = self.prepara()
        self.assertEqual(meta["orientation"], "orizzontale")
        self.assertFalse(
            meta["upscale_required"],
            "da un 4K il ritaglio 9:16 e' una riduzione, non un ingrandimento",
        )


class Rifiutare(unittest.TestCase):
    """Il cancello: chi passa, chi si ferma, e cosa si porta dietro il messaggio."""

    def setUp(self):
        self.chiamate = []
        self.vero = ingest.elenca_cartella_drive

        def finto(folder_id):
            self.chiamate.append(folder_id)
            return self.risposta

        self.risposta = []
        ingest.elenca_cartella_drive = finto
        self.addCleanup(setattr, ingest, "elenca_cartella_drive", self.vero)

    def test_un_link_a_un_file_passa_senza_toccare_la_rete(self):
        url = "https://drive.google.com/file/d/14iOqE7DIniMYN3me21vmovHd6s60A65m/view"
        self.assertIsNone(ingest.rifiuta_cartella_drive(url))
        self.assertEqual(self.chiamate, [], "ha provato a elencare un file")

    def test_la_cartella_si_ferma_e_elenca_i_file_con_il_loro_link(self):
        self.risposta = [
            ("cartella", "CARTELLA1", "Additional Shortforms"),
            ("file", "FILE1", "Northwind Podcast_E101.mp4"),
        ]
        with self.assertRaises(SystemExit) as caso:
            ingest.rifiuta_cartella_drive("https://drive.google.com/drive/folders/LACARTELLA")
        testo = str(caso.exception)
        self.assertEqual(self.chiamate, ["LACARTELLA"])
        self.assertIn("cartella di Drive", testo)
        self.assertIn("Northwind Podcast_E101.mp4", testo)
        # Il nome da solo non basta: quello che serve e' l'indirizzo da
        # rilanciare, ed e' diverso per un file e per una sottocartella.
        self.assertIn("https://drive.google.com/file/d/FILE1/view", testo)
        self.assertIn("https://drive.google.com/drive/folders/CARTELLA1", testo)

    def test_se_lelenco_non_si_legge_si_ferma_lo_stesso(self):
        # Cartella privata, rete assente, pagina cambiata: elencarla e' un di
        # piu'. Senza, resterebbe l'errore opaco di yt-dlp, cioe' il difetto
        # che questo cancello esiste per togliere.
        self.risposta = []
        with self.assertRaises(SystemExit) as caso:
            ingest.rifiuta_cartella_drive("https://drive.google.com/drive/folders/LACARTELLA")
        self.assertIn("cartella di Drive", str(caso.exception))


if __name__ == "__main__":
    unittest.main()
