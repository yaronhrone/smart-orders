from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.users.models import Profile, SignupRequest, User
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
    def test_saves_pending_request_and_notifies_admin(self, wa):
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
        req = SignupRequest.objects.get()
        self.assertEqual(req.status, "pending")
        self.assertEqual(req.phone, "0501234567")
        self.assertEqual(req.suppliers[0], {"name": "ירקות הגליל", "phone": "0521112222"})

    @patch("apps.orders.whatsapp.send_whatsapp_message")
    def test_same_email_twice_is_one_pending_request(self, wa):
        self.client.post(self.url, PAYLOAD, format="json")
        self.client.post(self.url, PAYLOAD, format="json")
        self.assertEqual(SignupRequest.objects.count(), 1)

    @patch("apps.orders.whatsapp.send_whatsapp_message")
    def test_existing_user_email_rejected(self, wa):
        User.objects.create_user(email="dana@example.com", password="Passw0rd!x")
        self.assertEqual(self.client.post(self.url, PAYLOAD, format="json").status_code, 400)
        self.assertEqual(SignupRequest.objects.count(), 0)

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
        self.assertEqual(SignupRequest.objects.count(), 0)
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
        self.assertIn("סיסמה זמנית: Abcdef1!x", mail.outbox[0].body)
        wa.assert_called_once()
        self.assertEqual(wa.call_args[0][0], "+972501234567")

    def test_email_failure_does_not_fail_creation(self):
        with patch("apps.users.signup.send_mail", side_effect=RuntimeError("smtp down")):
            res = self.client.post(reverse("register"), self.data, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(User.objects.filter(email="new@example.com").exists())


@override_settings(SIGNUP_CUSTOMER_WHATSAPP=True, SITE_URL="https://site.test")
class ApproveSignupTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(email="boss@example.com", password="Passw0rd!x", is_staff=True)
        self.signup = SignupRequest.objects.create(
            email="dana@example.com", first_name="דנה", last_name="כהן", phone="0501234567",
            company_name="מסעדת הים", company_address="הנמל 1", region="haifa",
            suppliers=[{"name": "ירקות הגליל", "phone": "0521112222"}],
        )

    def approve(self, pk=None):
        return self.client.post(reverse("admin-signup-approve", args=[pk or self.signup.pk]))

    def test_admin_only(self):
        self.assertEqual(self.client.get(reverse("admin-signup-list")).status_code in (401, 403), True)
        regular = User.objects.create_user(email="u@example.com", password="Passw0rd!x")
        self.client.force_authenticate(regular)
        self.assertEqual(self.approve().status_code, 403)
        self.assertFalse(User.objects.filter(email="dana@example.com").exists())

    def test_list_shows_only_pending(self):
        SignupRequest.objects.create(email="old@example.com", first_name="a", last_name="b", phone="0501111111",
                                     company_name="x", region="haifa", status="rejected")
        self.client.force_authenticate(self.admin)
        res = self.client.get(reverse("admin-signup-list"))
        self.assertEqual([r["email"] for r in res.data["results"]], ["dana@example.com"])

    @patch("apps.orders.whatsapp.send_whatsapp_message")
    def test_approve_creates_customer_and_sends_password(self, wa):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.approve().status_code, 200)
        user = User.objects.get(email="dana@example.com")
        self.assertEqual(user.profile.company_name, "מסעדת הים")
        self.assertEqual(user.profile.phone, "0501234567")
        self.signup.refresh_from_db()
        self.assertEqual(self.signup.status, "approved")
        body = mail.outbox[0].body
        self.assertEqual(mail.outbox[0].to, ["dana@example.com"])
        self.assertIn("https://site.test/login", body)
        password = body.split("סיסמה זמנית: ")[1].splitlines()[0]
        self.assertTrue(user.check_password(password))
        self.assertEqual(wa.call_args[0][0], "+972501234567")

    def test_approve_twice_fails(self):
        self.client.force_authenticate(self.admin)
        self.approve()
        self.assertEqual(self.approve().status_code, 400)
        self.assertEqual(User.objects.filter(email="dana@example.com").count(), 1)

    def test_reject_sends_nothing_and_creates_no_user(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post(reverse("admin-signup-reject", args=[self.signup.pk]))
        self.assertEqual(res.status_code, 200)
        self.signup.refresh_from_db()
        self.assertEqual(self.signup.status, "rejected")
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(User.objects.filter(email="dana@example.com").exists())

    def test_temp_password_always_valid(self):
        from apps.users.serializers import validate_password_hebrew
        from apps.users.signup import make_temp_password
        for _ in range(200):
            validate_password_hebrew(make_temp_password())


class ChangePasswordTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="c@example.com", password="Oldpass!1")
        self.client.force_authenticate(self.user)
        self.url = reverse("change-password")

    def test_changes_password(self):
        res = self.client.post(self.url, {"old_password": "Oldpass!1", "new_password": "Newpass!2", "new_password2": "Newpass!2"})
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Newpass!2"))

    def test_wrong_old_password(self):
        res = self.client.post(self.url, {"old_password": "nope", "new_password": "Newpass!2", "new_password2": "Newpass!2"})
        self.assertEqual(res.status_code, 400)

    def test_mismatch_and_weak(self):
        res = self.client.post(self.url, {"old_password": "Oldpass!1", "new_password": "Newpass!2", "new_password2": "Other!22"})
        self.assertEqual(res.status_code, 400)
        res = self.client.post(self.url, {"old_password": "Oldpass!1", "new_password": "weak", "new_password2": "weak"})
        self.assertEqual(res.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Oldpass!1"))

    def test_requires_login(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post(self.url, {}).status_code, 401)


class FirstLoginPasswordTests(APITestCase):
    def test_approved_and_hand_made_customers_must_change_password(self):
        admin = User.objects.create_user(email="boss2@example.com", password="Passw0rd!x", is_staff=True)
        self.client.force_authenticate(admin)
        signup = SignupRequest.objects.create(email="s@example.com", first_name="a", last_name="b", phone="0501111111",
                                              company_name="x", region="haifa")
        self.client.post(reverse("admin-signup-approve", args=[signup.pk]))
        self.assertTrue(User.objects.get(email="s@example.com").must_change_password)
        data = {"email": "m@example.com", "password": "Abcdef1!x", "password2": "Abcdef1!x", "first_name": "a",
                "last_name": "b", "company_name": "x", "company_phone": "", "company_address": "",
                "phone": "0502222222", "region": "haifa"}
        self.client.post(reverse("register"), data, format="json")
        self.assertTrue(User.objects.get(email="m@example.com").must_change_password)
        self.assertFalse(admin.must_change_password)

    def test_flag_is_in_me_and_clears_without_old_password(self):
        user = User.objects.create_user(email="f@example.com", password="Temp!pass1", must_change_password=True)
        self.client.force_authenticate(user)
        self.assertTrue(self.client.get(reverse("me")).data["must_change_password"])
        res = self.client.post(reverse("change-password"), {"new_password": "Mine!pass2", "new_password2": "Mine!pass2"})
        self.assertEqual(res.status_code, 200)
        user.refresh_from_db()
        self.assertFalse(user.must_change_password)
        self.assertTrue(user.check_password("Mine!pass2"))
        # Once the flag is off the old password is required again.
        res = self.client.post(reverse("change-password"), {"new_password": "Other!pass3", "new_password2": "Other!pass3"})
        self.assertEqual(res.status_code, 400)
