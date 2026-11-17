import re

from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APITestCase

from apps.users.models import User


@override_settings(SITE_URL="https://site.test")
class PasswordResetTests(APITestCase):
    def setUp(self):
        cache.clear()  # the throttles count in the cache
        self.user = User.objects.create_user(email="c@example.com", password="Oldpass!1")
        self.request_url = reverse("password-reset")
        self.confirm_url = reverse("password-reset-confirm")

    def _link_params(self):
        body = mail.outbox[0].body
        match = re.search(r"https://site\.test/reset-password\?uid=([^&\s]+)&token=([^\s]+)", body)
        self.assertIsNotNone(match, body)
        return match.group(1), match.group(2)

    def test_request_sends_link_for_known_email(self):
        res = self.client.post(self.request_url, {"email": "C@example.com"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(mail.outbox[0].to, ["c@example.com"])
        self._link_params()

    def test_unknown_email_gets_the_same_answer_and_no_mail(self):
        known = self.client.post(self.request_url, {"email": "c@example.com"})
        mail.outbox.clear()
        unknown = self.client.post(self.request_url, {"email": "nobody@example.com"})
        self.assertEqual(unknown.status_code, 200)
        self.assertEqual(unknown.data, known.data)
        self.assertEqual(len(mail.outbox), 0)

    def test_full_flow_sets_new_password_and_link_works_once(self):
        self.client.post(self.request_url, {"email": "c@example.com"})
        uid, token = self._link_params()
        data = {"uid": uid, "token": token, "new_password": "Newpass!2", "new_password2": "Newpass!2"}
        self.assertEqual(self.client.post(self.confirm_url, data).status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Newpass!2"))
        # The password changed, so the same link is dead.
        self.assertEqual(self.client.post(self.confirm_url, {**data, "new_password": "Other!pass3", "new_password2": "Other!pass3"}).status_code, 400)

    def test_bad_token_or_uid(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        data = {"uid": uid, "token": "nope", "new_password": "Newpass!2", "new_password2": "Newpass!2"}
        self.assertEqual(self.client.post(self.confirm_url, data).status_code, 400)
        data = {"uid": "###", "token": default_token_generator.make_token(self.user),
                "new_password": "Newpass!2", "new_password2": "Newpass!2"}
        self.assertEqual(self.client.post(self.confirm_url, data).status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Oldpass!1"))

    def test_weak_or_mismatched_password_keeps_old_one(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        for new, new2 in (("weak", "weak"), ("Newpass!2", "Different!3")):
            res = self.client.post(self.confirm_url, {"uid": uid, "token": token, "new_password": new, "new_password2": new2})
            self.assertEqual(res.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Oldpass!1"))

    def test_request_is_throttled(self):
        for _ in range(5):
            self.assertEqual(self.client.post(self.request_url, {"email": "x@example.com"}).status_code, 200)
        self.assertEqual(self.client.post(self.request_url, {"email": "x@example.com"}).status_code, 429)
