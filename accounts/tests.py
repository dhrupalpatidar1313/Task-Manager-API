from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

User = get_user_model()

class AuthAPITestCase(APITestCase):
    def test_register_creates_user_and_returns_id(self):
        res = self.client.post("/api/auth/register/", {
            "username": "carol",
            "password": "Str0ng!Pass",
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIn("id", res.data)
        self.assertNotIn("password", res.data)
        self.assertTrue(User.objects.filter(username="carol").exists())

    def test_register_duplicate_username_fails(self):
        User.objects.create_user(username="carol", password="Str0ng!Pass")
        res = self.client.post("/api/auth/register/", {
            "username": "carol",
            "password": "Str0ng!Pass",
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_register_weak_password_fails(self):
        res = self.client.post("/api/auth/register/", {
            "username": "carol",
            "password": "password",
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_register_short_password_fails(self):
        res = self.client.post("/api/auth/register/", {
            "username": "carol",
            "password": "short",
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_token_obtain_and_refresh(self):
        User.objects.create_user(username="carol", password="Str0ng!Pass")
        res = self.client.post("/api/auth/token/", {
            "username": "carol",
            "password": "Str0ng!Pass",
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("access", res.data)
        self.assertIn("refresh", res.data)

        res = self.client.post("/api/auth/token/refresh/", {
            "refresh": res.data["refresh"],
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("access", res.data)

    def test_token_obtain_bad_credentials_fails(self):
        User.objects.create_user(username="carol", password="Str0ng!Pass")
        res = self.client.post("/api/auth/token/", {
            "username": "carol",
            "password": "wrong",
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
