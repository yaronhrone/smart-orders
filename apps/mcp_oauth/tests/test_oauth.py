import base64
import hashlib
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.mcp_oauth.models import OAuthCode, OAuthRefreshToken
from apps.users.models import ApiToken, Profile

User = get_user_model()

REDIRECT = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "v" * 50
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
OPEN_URL = reverse("orders-open")


class OAuthFlowTests(APITestCase):

    def setUp(self):
        self.user = User.objects.create_user(email="c@test.com", password="pass1234")
        Profile.objects.create(user=self.user)

    def register(self, uris=None):
        res = self.client.post(
            reverse("oauth-register"), {"client_name": "Claude", "redirect_uris": uris if uris is not None else [REDIRECT]}, format="json",
        )
        return res

    def approve(self, client_id, redirect=REDIRECT, challenge=CHALLENGE, user=None):
        self.client.force_authenticate(user or self.user)
        res = self.client.post(reverse("oauth-authorize"), {
            "client_id": client_id, "redirect_uri": redirect, "code_challenge": challenge,
            "code_challenge_method": "S256", "state": "xyz",
        }, format="json")
        self.client.force_authenticate(None)
        return res

    def code_from(self, res):
        query = parse_qs(urlparse(res.data["redirect_to"]).query)
        self.assertEqual(query["state"], ["xyz"])
        return query["code"][0]

    def exchange(self, client_id, code, verifier=VERIFIER, redirect=REDIRECT):
        return self.client.post(reverse("oauth-token"), {
            "grant_type": "authorization_code", "client_id": client_id, "code": code,
            "redirect_uri": redirect, "code_verifier": verifier,
        })

    def full_login(self):
        client_id = self.register().data["client_id"]
        code = self.code_from(self.approve(client_id))
        return client_id, self.exchange(client_id, code)

    def test_metadata_points_at_our_endpoints(self):
        res = self.client.get(reverse("oauth-metadata"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["code_challenge_methods_supported"], ["S256"])
        self.assertTrue(res.data["token_endpoint"].endswith("/api/oauth/token/"))

    def test_full_flow_yields_a_working_token(self):
        _, res = self.full_login()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["token_type"], "Bearer")
        got = self.client.get(OPEN_URL, HTTP_AUTHORIZATION=f"Bearer {res.data['access_token']}")
        self.assertEqual(got.status_code, 200)
        self.assertTrue(ApiToken.objects.get(user=self.user).via_oauth)

    def test_register_rejects_bad_redirects(self):
        for bad in (["http://evil.example/cb"], ["javascript:alert(1)"], [], ["https://a.com/cb#frag"]):
            self.assertEqual(self.register(bad).status_code, 400, bad)
        self.assertEqual(self.register(["http://localhost:5555/callback"]).status_code, 201)

    def test_authorize_needs_website_login_and_known_redirect(self):
        client_id = self.register().data["client_id"]
        anonymous = self.client.post(reverse("oauth-authorize"), {}, format="json")
        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(self.approve(client_id, redirect="https://evil.example/cb").status_code, 400)

    def test_api_token_cannot_approve(self):
        client_id = self.register().data["client_id"]
        _, raw = ApiToken.issue(self.user)
        res = self.client.post(reverse("oauth-authorize"), {
            "client_id": client_id, "redirect_uri": REDIRECT, "code_challenge": CHALLENGE,
            "code_challenge_method": "S256",
        }, format="json", HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(res.status_code, 401)

    def test_plain_pkce_method_rejected(self):
        client_id = self.register().data["client_id"]
        self.client.force_authenticate(self.user)
        res = self.client.post(reverse("oauth-authorize"), {
            "client_id": client_id, "redirect_uri": REDIRECT, "code_challenge": "abc",
            "code_challenge_method": "plain",
        }, format="json")
        self.assertEqual(res.status_code, 400)

    def test_wrong_verifier_rejected(self):
        client_id = self.register().data["client_id"]
        code = self.code_from(self.approve(client_id))
        res = self.exchange(client_id, code, verifier="w" * 50)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["error"], "invalid_grant")

    def test_code_is_single_use(self):
        client_id = self.register().data["client_id"]
        code = self.code_from(self.approve(client_id))
        self.assertEqual(self.exchange(client_id, code).status_code, 200)
        self.assertEqual(self.exchange(client_id, code).status_code, 400)

    def test_expired_code_rejected(self):
        client_id = self.register().data["client_id"]
        code = self.code_from(self.approve(client_id))
        OAuthCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.exchange(client_id, code).status_code, 400)

    def test_code_for_another_client_rejected(self):
        a = self.register().data["client_id"]
        b = self.register().data["client_id"]
        code = self.code_from(self.approve(a))
        self.assertEqual(self.exchange(b, code).status_code, 400)

    def test_redirect_must_match_at_exchange(self):
        client_id = self.register(["https://claude.ai/a", "https://claude.ai/b"]).data["client_id"]
        code = self.code_from(self.approve(client_id, redirect="https://claude.ai/a"))
        self.assertEqual(self.exchange(client_id, code, redirect="https://claude.ai/b").status_code, 400)

    def test_refresh_rotates_and_old_tokens_die(self):
        client_id, first = self.full_login()
        res = self.client.post(reverse("oauth-token"), {
            "grant_type": "refresh_token", "client_id": client_id, "refresh_token": first.data["refresh_token"],
        })
        self.assertEqual(res.status_code, 200)
        old = self.client.get(OPEN_URL, HTTP_AUTHORIZATION=f"Bearer {first.data['access_token']}")
        new = self.client.get(OPEN_URL, HTTP_AUTHORIZATION=f"Bearer {res.data['access_token']}")
        self.assertEqual((old.status_code, new.status_code), (401, 200))
        again = self.client.post(reverse("oauth-token"), {
            "grant_type": "refresh_token", "client_id": client_id, "refresh_token": first.data["refresh_token"],
        })
        self.assertEqual(again.status_code, 400)

    def test_revoking_the_connection_stops_refresh(self):
        client_id, first = self.full_login()
        ApiToken.objects.filter(user=self.user).update(revoked=True)
        res = self.client.post(reverse("oauth-token"), {
            "grant_type": "refresh_token", "client_id": client_id, "refresh_token": first.data["refresh_token"],
        })
        self.assertEqual(res.status_code, 400)

    def test_expired_refresh_rejected(self):
        client_id, first = self.full_login()
        OAuthRefreshToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        res = self.client.post(reverse("oauth-token"), {
            "grant_type": "refresh_token", "client_id": client_id, "refresh_token": first.data["refresh_token"],
        })
        self.assertEqual(res.status_code, 400)

    def test_unknown_client_and_grant(self):
        self.assertEqual(self.client.post(reverse("oauth-token"), {"grant_type": "authorization_code",
                                                                    "client_id": "nope"}).status_code, 401)
        client_id = self.register().data["client_id"]
        res = self.client.post(reverse("oauth-token"), {"grant_type": "password", "client_id": client_id})
        self.assertEqual(res.data["error"], "unsupported_grant_type")

    def test_oauth_connections_do_not_use_up_the_manual_limit(self):
        self.full_login()
        self.client.force_authenticate(self.user)
        for _ in range(3):
            self.assertEqual(self.client.post(reverse("api-tokens"), {}).status_code, 201)
