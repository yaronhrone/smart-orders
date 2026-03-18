from django.test import TestCase
from django.contrib.auth import get_user_model



class ModelTests(TestCase):
    def test_create_user_with_email_successful(self):
        """Test creating a new user with an email is successful"""
        email = 'j6x7H@example.com'
        password = 'Testpass123'


        user  = get_user_model().objects.create_user(
            email=email,
            password=password,

            )


        self.assertEqual(user.email, email.lower())
        self.assertTrue(user.check_password(password))


    def test_new_user_email_normalized(self):
        """Test email is normalized"""

        email = "Test@Example.COM"

        user = get_user_model().objects.create_user(
            email=email,
            password="test123"
        )

        self.assertEqual(user.email, email.lower())


    def test_new_user_without_email_raises_error(self):
        """Test creating user without email raises error"""

        with self.assertRaises(ValueError):
            get_user_model().objects.create_user(
                email=None,
                password="test123"
            )


    def test_create_superuser(self):
        """Test creating a superuser"""

        user = get_user_model().objects.create_superuser(
            email="admin@example.com",
            password="admin123"
        )

        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_staff)