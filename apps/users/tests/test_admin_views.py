from django.urls import reverse
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status

from apps.users.models import Profile
from apps.catalog.models import Region

User = get_user_model()

ADMIN_USERS_URL = reverse("admin-user-list")


class AdminUserListPaginationTests(APITestCase):

    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="pass1234")
        self.client.force_authenticate(user=self.admin)

    def test_default_page_shape(self):
        res = self.client.get(ADMIN_USERS_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("results", res.data)
        self.assertIn("has_more", res.data)

    def test_limit_caps_results_and_sets_has_more(self):
        for i in range(3):
            u = User.objects.create_user(email=f"user{i}@test.com", password="pass1234")
            Profile.objects.create(user=u, region=Region.CENTER)

        res = self.client.get(ADMIN_USERS_URL, {"limit": 2})

        self.assertEqual(len(res.data["results"]), 2)
        self.assertTrue(res.data["has_more"])

    def test_non_admin_forbidden(self):
        user = User.objects.create_user(email="plain@test.com", password="pass1234")
        self.client.force_authenticate(user=user)
        res = self.client.get(ADMIN_USERS_URL)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
