"""
Voice messages on WhatsApp (apps/orders/whatsapp/voice.py, core/stt.py).

Mocked: Twilio (send_whatsapp_message and the media download), the speech-to-text call, and the
AI order parser. Everything between them is real: the webhook, the checks that turn a voice message
away, the worker's function, and the same handlers a typed message reaches.
"""
import struct
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from apps.catalog.models import SupplierProduct
from apps.orders.tests.test_webhook import (
    LOCMEM_CACHE, _flush_draft, make_product, make_supplier, make_user_with_profile,
)
from apps.orders.whatsapp import voice
from apps.orders.whatsapp.cache import save_cancel_offer, save_supplier_pending_order
from core import stt
from core.models import AIUsage

PHONE = "+972501234567"
SEND = "apps.orders.whatsapp.validators.send_whatsapp_message"
DELAY = "apps.orders.tasks.process_voice_message_task.delay"
MEDIA = "https://api.twilio.com/2010-04-01/Accounts/AC1/Messages/MM1/Media/ME1"


def fake_ogg(seconds: float) -> bytes:
    """Just enough ogg for the length reader: a page header whose granule position is `seconds` of 48 kHz audio."""
    return b"OggS" + b"\x00\x04" + struct.pack("<q", int(seconds * 48000)) + b"\x00" * 20


def sent_texts(mock_send):
    return [call.args[1] for call in mock_send.call_args_list]


# ─────────────────────── pure helpers ───────────────────────

class VoiceMediaTests(SimpleTestCase):

    def test_finds_the_audio_attachment(self):
        post = {"NumMedia": "2", "MediaContentType0": "image/jpeg", "MediaUrl0": "u0",
                "MediaContentType1": "audio/ogg", "MediaUrl1": "u1"}
        self.assertEqual(voice.voice_media(post), ("u1", "audio/ogg"))

    def test_text_message_and_non_audio_media_are_not_voice(self):
        self.assertIsNone(voice.voice_media({"Body": "5 עגבניות"}))
        self.assertIsNone(voice.voice_media({"NumMedia": "1", "MediaContentType0": "image/png", "MediaUrl0": "u"}))
        self.assertIsNone(voice.voice_media({"NumMedia": "x"}))


class AudioLengthTests(SimpleTestCase):

    def test_reads_ogg_opus_length(self):
        self.assertAlmostEqual(stt.ogg_opus_seconds(fake_ogg(95)), 95, places=2)

    def test_unreadable_audio_has_no_length(self):
        self.assertIsNone(stt.ogg_opus_seconds(b"ID3 not ogg"))
        self.assertIsNone(stt.ogg_opus_seconds(b"OggS\x00"))

    def test_hint_names_the_products_and_is_capped(self):
        self.assertIn("עגבניה", stt.build_hint(["עגבניה", "גזר"]))
        self.assertEqual(stt.build_hint([]), "")
        hint = stt.build_hint([f"מוצר מספר {i}" for i in range(500)])
        self.assertLess(len(hint), stt.HINT_MAX_CHARS + 120)

    def test_unknown_provider_is_refused(self):
        with patch.dict("os.environ", {"STT_PROVIDER": "nope"}):
            with self.assertRaises(ValueError):
                stt.provider()

    def test_max_seconds_default_and_override(self):
        self.assertEqual(stt.max_seconds(), 180)
        with patch.dict("os.environ", {"STT_MAX_SECONDS": "120"}):
            self.assertEqual(stt.max_seconds(), 120)
        with patch.dict("os.environ", {"STT_MAX_SECONDS": "oops"}):
            self.assertEqual(stt.max_seconds(), 180)


@override_settings(CACHES=LOCMEM_CACHE)
class HintNamesTests(TestCase):
    """The transcriber's hint: the whole catalog, the most-ordered products first."""

    def setUp(self):
        cache.clear()

    def test_most_ordered_products_come_first_then_alphabetical(self):
        from apps.orders.models import OrderRequestProduct
        from apps.orders.tests.factories import make_order
        supplier = make_supplier("ספק")
        user = make_user_with_profile(phone="+972509990001")
        rare, common, other = make_product("אבוקדו"), make_product("עגבניה"), make_product("גזר")
        order = make_order(user, supplier)
        for product, times in ((common, 3), (rare, 1)):
            for _ in range(times):
                OrderRequestProduct.objects.create(
                    order_request=order, product=product, supplier=supplier, quantity=1, unit_price=1,
                )
        self.assertEqual(voice.hint_names(), ["עגבניה", "אבוקדו", "גזר"])

    def test_the_list_is_kept_for_a_few_minutes(self):
        make_product("עגבניה")
        self.assertEqual(voice.hint_names(), ["עגבניה"])
        make_product("גזר")
        self.assertEqual(voice.hint_names(), ["עגבניה"])  # still the kept one
        cache.clear()
        self.assertEqual(voice.hint_names(), ["גזר", "עגבניה"])

    def test_a_whole_catalog_fits_in_the_hint(self):
        names = [f"מוצר {chr(1488 + i % 26)}{i}" for i in range(150)]
        hint = stt.build_hint(names)
        self.assertTrue(all(name in hint for name in names))
        self.assertLessEqual(len(hint), stt.HINT_MAX_CHARS + 120)

    def test_a_catalog_too_big_for_the_hint_drops_the_last_names(self):
        names = [f"מוצר מספר {i}" for i in range(1000)]
        hint = stt.build_hint(names)
        self.assertIn(names[0], hint)
        self.assertNotIn(names[-1], hint)


class DownloadGuardTests(SimpleTestCase):

    def test_only_twilio_https_addresses_are_fetched(self):
        for url in ("http://api.twilio.com/x", "https://evil.example.com/x", "https://twilio.com.evil.io/x",
                    "file:///etc/passwd"):
            with patch("apps.orders.whatsapp.voice.httpx.stream") as stream:
                with self.assertRaises(ValueError):
                    voice.download_media(url)
                stream.assert_not_called()


@override_settings(CACHES=LOCMEM_CACHE)
class TranscribeTests(TestCase):

    def setUp(self):
        cache.clear()

    def test_openai_call_gets_hebrew_hint_and_usage_is_recorded(self):
        client = MagicMock()
        client.audio.transcriptions.create.return_value = SimpleNamespace(text="  חמישה קילו עגבניות ")
        with patch("openai.OpenAI", return_value=client):
            text = stt.transcribe(fake_ogg(30), hint="רמז")
        self.assertEqual(text, "חמישה קילו עגבניות")
        kwargs = client.audio.transcriptions.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-4o-transcribe")
        self.assertEqual(kwargs["language"], "he")
        self.assertEqual(kwargs["prompt"], "רמז")
        row = AIUsage.objects.get()
        self.assertEqual((row.kind, row.provider, row.ok), ("voice_transcribe", "openai", True))
        self.assertAlmostEqual(float(row.cost_usd), 0.003, places=6)
        self.assertEqual(row.message, "")  # never the text

    def test_a_failing_provider_becomes_transcription_error(self):
        client = MagicMock()
        client.audio.transcriptions.create.side_effect = RuntimeError("boom")
        with patch("openai.OpenAI", return_value=client):
            with self.assertRaises(stt.TranscriptionError):
                stt.transcribe(fake_ogg(5))
        self.assertFalse(AIUsage.objects.get().ok)

    def test_model_can_be_changed_in_env(self):
        client = MagicMock()
        client.audio.transcriptions.create.return_value = SimpleNamespace(text="x")
        with patch("openai.OpenAI", return_value=client), patch.dict("os.environ", {"STT_MODEL": "whisper-1"}):
            stt.transcribe(fake_ogg(5))
        self.assertEqual(client.audio.transcriptions.create.call_args.kwargs["model"], "whisper-1")


# ─────────────────────── the webhook: who may send voice ───────────────────────

@override_settings(CACHES=LOCMEM_CACHE, DEBUG=True, TWILIO_SKIP_SIGNATURE_VALIDATION=True)
class VoiceWebhookTests(TestCase):

    def setUp(self):
        cache.clear()
        self.tomato = make_product("עגבניה")
        self.supplier = make_supplier("ספק א")
        SupplierProduct.objects.create(supplier=self.supplier, product=self.tomato, price_per_unit="5.00")

    def _voice(self, phone, sid="SM1"):
        return self.client.post("/whatsapp/webhook/", {
            "From": f"whatsapp:{phone}", "Body": "", "MessageSid": sid,
            "NumMedia": "1", "MediaContentType0": "audio/ogg; codecs=opus", "MediaUrl0": MEDIA,
        })

    @patch(SEND)
    def test_a_customers_voice_message_goes_to_a_worker(self, mock_send):
        make_user_with_profile(phone=PHONE)
        with patch(DELAY) as delay:
            res = self._voice(PHONE)
        self.assertEqual(res.status_code, 200)
        delay.assert_called_once_with(PHONE, None, MEDIA, "audio/ogg; codecs=opus")
        mock_send.assert_not_called()

    @patch(SEND)
    def test_a_suppliers_voice_message_goes_to_a_worker_with_the_supplier(self, mock_send):
        with patch(DELAY) as delay:
            self._voice(self.supplier.whatsapp_number)
        delay.assert_called_once_with(self.supplier.whatsapp_number, self.supplier.id, MEDIA, "audio/ogg; codecs=opus")

    @patch(SEND)
    def test_an_unregistered_number_is_not_transcribed(self, mock_send):
        with patch(DELAY) as delay:
            self._voice("+972500000009")
        delay.assert_not_called()
        self.assertIn("לא רשום", sent_texts(mock_send)[0])

    @patch(SEND)
    def test_voice_is_turned_away_while_the_customer_has_a_question_to_answer(self, mock_send):
        make_user_with_profile(phone=PHONE)
        save_cancel_offer(PHONE, 1)
        with patch(DELAY) as delay:
            self._voice(PHONE)
        delay.assert_not_called()
        self.assertEqual(sent_texts(mock_send), [voice.NOT_FOR_VOICE])

    @patch(SEND)
    def test_voice_is_turned_away_while_a_pending_order_waits_for_approval(self, mock_send):
        make_user_with_profile(phone=PHONE)
        cache.set(f"whatsapp_order:{PHONE}", "{}", 3600)
        with patch(DELAY) as delay:
            self._voice(PHONE)
        delay.assert_not_called()
        self.assertEqual(sent_texts(mock_send), [voice.NOT_FOR_VOICE])

    @patch(SEND)
    def test_voice_is_turned_away_while_a_supplier_has_an_order_to_answer(self, mock_send):
        save_supplier_pending_order(self.supplier.whatsapp_number, 1, [])
        with patch(DELAY) as delay:
            self._voice(self.supplier.whatsapp_number)
        delay.assert_not_called()
        self.assertEqual(sent_texts(mock_send), [voice.NOT_FOR_VOICE])

    @patch(SEND)
    def test_too_many_voice_messages_in_an_hour_stop_costing_money(self, mock_send):
        make_user_with_profile(phone=PHONE)
        with patch(DELAY) as delay:
            for i in range(voice.VOICE_PER_HOUR + 3):
                self._voice(PHONE, sid=f"SM{i}")
        self.assertEqual(delay.call_count, voice.VOICE_PER_HOUR)
        self.assertEqual(len(sent_texts(mock_send)), 3)

    @patch(SEND)
    def test_when_the_worker_cannot_be_reached_the_customer_is_told(self, mock_send):
        make_user_with_profile(phone=PHONE)
        with patch(DELAY, side_effect=RuntimeError("broker down")):
            res = self._voice(PHONE)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(sent_texts(mock_send), [voice.TRY_AGAIN])


# ─────────────────────── the worker: from audio to the same handlers as text ───────────────────────

@override_settings(CACHES=LOCMEM_CACHE, DEBUG=True, TWILIO_SKIP_SIGNATURE_VALIDATION=True)
class ProcessVoiceTests(TestCase):

    def setUp(self):
        cache.clear()
        self.tomato = make_product("עגבניה")
        self.supplier = make_supplier("ספק א")
        SupplierProduct.objects.create(supplier=self.supplier, product=self.tomato, price_per_unit="5.00")
        make_user_with_profile(phone=PHONE)

    def _process(self, audio=None, text="חמישה קילו עגבניות", supplier_id=None, phone=PHONE):
        with patch("apps.orders.whatsapp.voice.download_media", return_value=audio or fake_ogg(20)), \
                patch("core.stt.transcribe", return_value=text) as transcribe:
            voice.process_voice_message(phone, supplier_id, MEDIA, "audio/ogg; codecs=opus")
        return transcribe

    @patch(SEND)
    @patch("apps.orders.order_parser.parse_customer_order")
    def test_a_spoken_order_is_priced_like_a_typed_one(self, mock_parse, mock_send):
        mock_parse.return_value = [{"product_name": "עגבניה", "quantity": Decimal("5")}]
        transcribe = self._process()
        _flush_draft(PHONE)
        mock_parse.assert_called_once_with("חמישה קילו עגבניות", ["עגבניה"])
        self.assertIn("עגבניה", transcribe.call_args.kwargs["hint"])
        self.assertEqual(transcribe.call_args.kwargs["content_type"], "audio/ogg")
        message = sent_texts(mock_send)[-1]
        self.assertIn("עגבניה", message)
        self.assertIn("אישור", message)
        self.assertNotIn("שמעתי", message)  # understood: no "what I heard"

    @patch(SEND)
    @patch("apps.orders.order_parser.parse_customer_order", side_effect=ValueError("no_items"))
    def test_when_the_order_is_not_understood_the_customer_sees_what_was_heard(self, mock_parse, mock_send):
        self._process(text="אממ משהו לא ברור")
        self.assertEqual(len(sent_texts(mock_send)), 1)
        self.assertIn("לא הצלחתי להבין", sent_texts(mock_send)[0])
        self.assertIn('שמעתי: "אממ משהו לא ברור"', sent_texts(mock_send)[0])

    @patch(SEND)
    @patch("apps.orders.order_parser.parse_customer_order", side_effect=ValueError("no_items"))
    def test_a_typed_message_afterwards_forgets_what_was_heard(self, mock_parse, mock_send):
        self._process(text="אממ")
        self.client.post("/whatsapp/webhook/", {"From": f"whatsapp:{PHONE}", "Body": "משהו אחר"})
        self.assertNotIn("שמעתי", sent_texts(mock_send)[-1])

    @patch(SEND)
    @patch("apps.orders.whatsapp.user_flow._handle_cancel_order_request")
    @patch("apps.orders.order_parser.parse_customer_order", side_effect=ValueError("no_items"))
    def test_cancelling_by_voice_does_not_cancel(self, mock_parse, mock_cancel, mock_send):
        self._process(text="בטל את ההזמנה")
        mock_cancel.assert_not_called()

    @patch(SEND)
    def test_a_recording_over_the_limit_is_refused_before_transcription(self, mock_send):
        transcribe = self._process(audio=fake_ogg(181))
        transcribe.assert_not_called()
        self.assertEqual(sent_texts(mock_send), [voice._too_long_text()])
        self.assertIn("3 דקות", voice._too_long_text())

    @patch(SEND)
    def test_a_recording_under_the_limit_is_accepted(self, mock_send):
        with patch("apps.orders.order_parser.parse_customer_order", side_effect=ValueError("no_items")):
            transcribe = self._process(audio=fake_ogg(179))
        transcribe.assert_called_once()

    @patch(SEND)
    def test_silence_gets_a_clear_answer(self, mock_send):
        self._process(text="   ")
        self.assertEqual(sent_texts(mock_send), [voice.NOTHING_HEARD])

    @patch(SEND)
    def test_a_transcription_failure_is_reported_not_swallowed(self, mock_send):
        with patch("apps.orders.whatsapp.voice.download_media", return_value=fake_ogg(5)), \
                patch("core.stt.transcribe", side_effect=stt.TranscriptionError("down")):
            voice.process_voice_message(PHONE, None, MEDIA, "audio/ogg")
        self.assertEqual(sent_texts(mock_send), [voice.TRY_AGAIN])

    @patch(SEND)
    def test_a_failed_download_is_reported(self, mock_send):
        with patch("apps.orders.whatsapp.voice.download_media", side_effect=RuntimeError("404")), \
                patch("core.stt.transcribe") as transcribe:
            voice.process_voice_message(PHONE, None, MEDIA, "audio/ogg")
        transcribe.assert_not_called()
        self.assertEqual(sent_texts(mock_send), [voice.COULD_NOT_HEAR])

    @patch(SEND)
    def test_a_question_asked_while_transcribing_wins(self, mock_send):
        save_cancel_offer(PHONE, 1)
        with patch("apps.orders.whatsapp.user_flow._handle_order_text") as handler:
            self._process()
        handler.assert_not_called()
        self.assertEqual(sent_texts(mock_send), [voice.NOT_FOR_VOICE])

    @patch(SEND)
    def test_a_suppliers_spoken_prices_update_the_prices(self, mock_send):
        with patch("apps.catalog.price_parser.update_prices_from_message") as update:
            update.return_value = {
                "updated": [{"product_name": "עגבניה", "price": "4.50", "unit": 'ק"ג'}], "removed": [], "skipped": [],
            }
            self._process(text="עגבניה ארבע וחצי", supplier_id=self.supplier.id,
                          phone=self.supplier.whatsapp_number)
        update.assert_called_once_with(self.supplier, "עגבניה ארבע וחצי")
        self.assertIn("מחירים עודכנו", sent_texts(mock_send)[-1])
        self.assertNotIn("שמעתי", sent_texts(mock_send)[-1])

    @patch(SEND)
    def test_a_suppliers_unrecognized_prices_show_what_was_heard(self, mock_send):
        with patch("apps.catalog.price_parser.update_prices_from_message") as update:
            update.return_value = {"updated": [], "removed": [], "skipped": []}
            self._process(text="בלה בלה", supplier_id=self.supplier.id, phone=self.supplier.whatsapp_number)
        self.assertIn('שמעתי: "בלה בלה"', sent_texts(mock_send)[-1])

    @patch(SEND)
    def test_a_suppliers_shipping_words_are_not_acted_on(self, mock_send):
        from apps.orders.whatsapp import supplier_flow
        with patch.object(supplier_flow, "_handle_mark_shipped") as shipped, \
                patch("apps.catalog.price_parser.update_prices_from_message") as update:
            update.return_value = {"updated": [], "removed": [], "skipped": []}
            self._process(text="יצא למשלוח", supplier_id=self.supplier.id, phone=self.supplier.whatsapp_number)
        shipped.assert_not_called()
