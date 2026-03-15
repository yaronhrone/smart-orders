from django.test import TestCase
from django.contrib.auth import get_user_model
from apps.users.models import Profile


class ProfileModelTests(TestCase):

    def test_create_profile(self):

        user = get_user_model().objects.create_user(
            email="test@test.com",
            password="123456",

        )

        profile = Profile.objects.create(
            user=user,
            company_name="Fresh Market"
        )

        self.assertEqual(profile.user, user)
        self.assertEqual(profile.company_name, "Fresh Market")