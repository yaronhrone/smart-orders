from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.users.models import User
from core.phone import normalize_il_phone

PAYLOAD = {
    "first_name": "דנה",
    "last_name": "כהן",
    "email": "dana@example.com",
    "phone": "050-123-4567",
    "company_name": "מסעדת הים",
    "company_address": "הנמל 1",
    "region": "haifa",
    "suppliers": [
        {"name": "ירקות הגליל", "phone": "052-111-2222"},
        {"name": "עוף טרי", "phone": "+972 53 333 4444"},
    ],
}


class NormalizePhoneTests(APITestCase):
    def test_formats(self):
        self.assertEqual(normalize_il_phone("050-123-4567"), "0501234567")
        self.assertEqual(normalize_il_phone("+972501234567"), "0501234567")
        self.assertEqual(normalize_il_phone(""), "")


@override_settings(ADMIN_EMAIL="admin@example.com", ADMIN_WHATSAPP_NUMBER="+972500000001")
class SignupRequestTests(APITestCase):
    def setUp(self):
        cache.clear()  # the signup throttle counts in the cache
        self.url = reverse("signup")

    @patch("apps.orders.whatsapp.send_whatsapp_message")
    def test_sends_admin_email_and_whatsapp_and_stores_nothing(self, wa):
        res = self.client.post(self.url, PAYLOAD, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["admin@example.com"])
        body = mail.outbox[0].body
        for part in ("דנה כהן", "0501234567", "ירקות הגליל", "0521112222", "0533334444"):
            self.assertIn(part, body)
        wa.assert_called_once()
        self.assertEqual(wa.call_args[0][0], "+972500000001")
        self.assertFalse(User.objects.filter(email="dana@example.com").exists())

    @patch("apps.orders.whatsapp.send_whatsapp_message")
    def test_works_without_suppliers(self, wa):
        data = {k: v for k, v in PAYLOAD.items() if k != "suppliers"}
        self.assertEqual(self.client.post(self.url, data, format="json").status_code, 201)
        self.assertIn("לא צוינו", mail.outbox[0].body)

    @patch("apps.orders.whatsapp.send_whatsapp_message", side_effect=RuntimeError("twilio down"))
    def test_whatsapp_failure_still_succeeds_and_emails(self, wa):
        self.assertEqual(self.client.post(self.url, PAYLOAD, format="json").status_code, 201)
        self.assertEqual(len(mail.outbox), 1)

    @patch("apps.orders.whatsapp.send_whatsapp_message")
    def test_honeypot_sends_nothing(self, wa):
        res = self.client.post(self.url, {**PAYLOAD, "website": "http://spam"}, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(len(mail.outbox), 0)
        wa.assert_not_called()

    def test_validation(self):
        bad = {**PAYLOAD, "email": "nope"}
        self.assertEqual(self.client.post(self.url, bad, format="json").status_code, 400)
        bad = {**PAYLOAD, "phone": "123"}
        self.assertEqual(self.client.post(self.url, bad, format="json").status_code, 400)
        bad = {**PAYLOAD, "suppliers": [{"name": "x", "phone": "052-111-2222"}] * 11}
        self.assertEqual(self.client.post(self.url, bad, format="json").status_code, 400)
        self.assertEqual(len(mail.outbox), 0)

    @patch("apps.orders.whatsapp.send_whatsapp_message")
    def test_throttled(self, wa):
        for _ in range(5):
            self.assertEqual(self.client.post(self.url, PAYLOAD, format="json").status_code, 201)
        self.assertEqual(self.client.post(self.url, PAYLOAD, format="json").status_code, 429)


class CustomerConfirmationTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(email="boss@example.com", password="Passw0rd!x", is_staff=True)
        self.client.force_authenticate(self.admin)
        self.data = {
            "email": "new@example.com", "password": "Abcdef1!x", "password2": "Abcdef1!x",
            "first_name": "דנה", "last_name": "כהן", "company_name": "מסעדה",
            "company_phone": "", "company_address": "", "phone": "050-123-4567", "region": "haifa",
        }

    @override_settings(SIGNUP_CUSTOMER_WHATSAPP=True, SITE_URL="https://site.test")
    @patch("apps.orders.whatsapp.send_whatsapp_message")
    def test_creating_a_customer_sends_email_and_whatsapp(self, wa):
        res = self.client.post(reverse("register"), self.data, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["new@example.com"])
        self.assertIn("https://site.test/login", mail.outbox[0].body)
        self.assertNotIn("Abcdef1!x", mail.outbox[0].body)
        wa.assert_called_once()
        self.assertEqual(wa.call_args[0][0], "+972501234567")

    def test_email_failure_does_not_fail_creation(self):
        with patch("apps.users.signup.send_mail", side_effect=RuntimeError("smtp down")):
            res = self.client.post(reverse("register"), self.data, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(User.objects.filter(email="new@example.com").exists())
