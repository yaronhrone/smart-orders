"""manage.py check_transcription: the accuracy report on real recordings (the provider is mocked here)."""
import os
import struct
import tempfile
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from apps.catalog.models import Product, Unit
from core import stt
from core.management.commands.check_transcription import word_error_rate, words


def ogg(seconds):
    return b"OggS" + b"\x00\x04" + struct.pack("<q", int(seconds * 48000)) + b"\x00" * 20


class WordErrorRateTests(SimpleTestCase):

    def test_identical_texts_have_no_errors(self):
        self.assertEqual(word_error_rate("חמש עגבניות וגזר", "חמש עגבניות וגזר"), 0)

    def test_punctuation_and_case_do_not_count(self):
        self.assertEqual(words("5 קילו, עגבניות!"), ["5", "קילו", "עגבניות"])
        self.assertEqual(word_error_rate("Tomato 5", "tomato, 5."), 0)

    def test_replaced_dropped_and_added_words(self):
        self.assertAlmostEqual(word_error_rate("חמש עגבניות וגזר", "חמש עגבנית וגזר"), 1 / 3)
        self.assertAlmostEqual(word_error_rate("חמש עגבניות וגזר", "חמש עגבניות"), 1 / 3)
        self.assertAlmostEqual(word_error_rate("חמש עגבניות", "חמש עגבניות וגזר"), 1 / 2)

    def test_empty_cases(self):
        self.assertEqual(word_error_rate("", ""), 0)
        self.assertEqual(word_error_rate("", "משהו"), 1)
        self.assertEqual(word_error_rate("משהו", ""), 1)


class CheckTranscriptionCommandTests(TestCase):

    def setUp(self):
        Product.objects.create(name="עגבניה", unit=Unit.KG)
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "01.ogg").write_bytes(ogg(30))
        (self.dir / "01.txt").write_text("5 עגבניה", encoding="utf-8")
        (self.dir / "02.ogg").write_bytes(ogg(60))      # no .txt: accuracy not measured
        (self.dir / "notes.docx").write_bytes(b"x")     # not a recording

    def _run(self, *args):
        out = StringIO()
        call_command("check_transcription", str(self.dir), *args, stdout=out, stderr=StringIO())
        return out.getvalue()

    def test_reports_word_errors_and_leaves_other_files_alone(self):
        with patch("core.stt.transcribe", side_effect=["5 עגבנית", "משהו"]) as transcribe:
            out = self._run()
        self.assertEqual(transcribe.call_count, 2)  # the .docx was not touched
        self.assertIn("01.ogg", out)
        self.assertIn("word errors: 50%", out)
        self.assertIn("accuracy not measured", out)
        self.assertIn("average word errors: 50%", out)
        self.assertIn("audio: 1.5 min", out)

    def test_the_hint_carries_the_catalog_unless_switched_off(self):
        with patch("core.stt.transcribe", return_value="x") as transcribe:
            self._run()
        self.assertIn("עגבניה", transcribe.call_args.kwargs["hint"])
        with patch("core.stt.transcribe", return_value="x") as transcribe:
            self._run("--no-hint")
        self.assertEqual(transcribe.call_args.kwargs["hint"], "")

    def test_order_check_says_whether_the_transcript_gives_the_same_order(self):
        def parse(text, names):
            return [{"product_name": "עגבניה", "quantity": Decimal("5")}] if "5" in text or "חמש" in text else []
        with patch("core.stt.transcribe", side_effect=["חמש עגבניה", "אממ"]), \
                patch("apps.orders.order_parser.parse_customer_order", side_effect=parse):
            out = self._run("--order")
        self.assertIn("SAME order", out)
        self.assertIn("same order as the true text: 1/1", out)

    def test_order_check_flags_a_different_order(self):
        def parse(text, names):
            qty = Decimal("50") if "50" in text else Decimal("5")
            return [{"product_name": "עגבניה", "quantity": qty}]
        with patch("core.stt.transcribe", side_effect=["50 עגבניה", "x"]), \
                patch("apps.orders.order_parser.parse_customer_order", side_effect=parse):
            out = self._run("--order")
        self.assertIn("DIFFERENT", out)
        self.assertIn("עגבניה x50", out)
        self.assertIn("same order as the true text: 0/1", out)

    def test_models_are_compared_and_the_setting_is_restored(self):
        seen = []
        def fake(*a, **k):
            seen.append(stt.model())
            return "5 עגבניה"
        with patch("core.stt.transcribe", side_effect=fake):
            out = self._run("--models", "gpt-4o-transcribe,whisper-1")
        self.assertEqual(seen, ["gpt-4o-transcribe", "gpt-4o-transcribe", "whisper-1", "whisper-1"])
        self.assertIn("=== whisper-1 ===", out)
        self.assertNotIn("STT_MODEL", os.environ)

    def test_a_failed_recording_is_reported_and_the_rest_go_on(self):
        with patch("core.stt.transcribe", side_effect=[stt.TranscriptionError("boom"), "5 עגבניה"]):
            out = self._run()
        self.assertIn("FAILED: boom", out)
        self.assertIn("transcribed 1/2, 1 failed", out)

    def test_a_recording_over_the_limit_is_flagged(self):
        (self.dir / "03.ogg").write_bytes(ogg(200))
        with patch("core.stt.transcribe", return_value="x"):
            out = self._run()
        self.assertIn("longer than the 180s limit", out)

    def test_missing_path_and_empty_folder_are_clear_errors(self):
        with self.assertRaises(CommandError):
            call_command("check_transcription", str(self.dir / "nope"))
        empty = tempfile.mkdtemp()
        with self.assertRaises(CommandError):
            call_command("check_transcription", empty)
