"""Il modello Whisper di default: uno solo, `large-v3-turbo`, per italiano e inglese.

Fino al 25 settembre 2026 il default era `small` e l'italiano chiedeva `--model` a mano;
il pannello non lo passava, quindi i video italiani lavorati da li' uscivano con frasi
intere mancanti. Il banco del 25/09 (PROGRESS.md) ha misurato che `small` perde di piu'
anche in inglese senza essere piu' veloce. Un default che torna a `small` non rompe
niente di visibile: si paga in clip scelte su un testo bucato.
"""

from __future__ import annotations

import unittest

import percorsi  # noqa: F401  (mette le cartelle del progetto su sys.path)

import transcribe


class ModelloDiDefault(unittest.TestCase):
    def test_turbo_per_tutte_e_due_le_lingue(self):
        self.assertEqual(transcribe.DEFAULT_MODEL, "large-v3-turbo")

    def test_senza_model_arriva_turbo_su_una_cartella_inglese(self):
        # Il chiamante: `main()` senza --model, su una cartella inglese come quelle del
        # pannello. Si intercetta il modello che arriva davvero a `transcribe()`.
        import json
        import sys
        import tempfile
        from pathlib import Path
        from unittest import mock

        class Fermo(Exception):
            pass

        arrivato = {}

        def finta(audio, modello, *a, **kw):
            arrivato["modello"] = modello
            raise Fermo

        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "meta.json").write_text(json.dumps({"audio_lang": "en"}), encoding="utf-8")
            with (
                mock.patch.object(sys, "argv", ["transcribe.py", d]),
                mock.patch.object(transcribe, "require_audio", return_value=Path(d) / "audio.wav"),
                mock.patch.object(transcribe, "transcribe", side_effect=finta),
            ):
                with self.assertRaises(Fermo):
                    transcribe.main()
        self.assertEqual(arrivato["modello"], "large-v3-turbo")


if __name__ == "__main__":
    unittest.main()
