from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase, APIClient
from django.urls import reverse

from .models import PaymentProvider, Transaction, Callback

User = get_user_model()


class PaymentAppTests(APITestCase):
    def setUp(self):
        # Users
        self.admin = User.objects.create_user(email="admin@example.com", password="admin123", is_staff=True)
        self.user = User.objects.create_user(email="member@example.com", password="member123")

        # Clients
        self.admin_client = APIClient()
        self.user_client = APIClient()
        self.admin_client.force_authenticate(user=self.admin)
        self.user_client.force_authenticate(user=self.user)

        # Base URLs from router
        self.providers_url = reverse("provider-list")
        self.transactions_url = reverse("transaction-list")
        self.callbacks_url = reverse("callback-list")

        # Default Provider
        self.mpesa = PaymentProvider.objects.create(
            name="M-Pesa", provider_code="MPESA", is_active=True
        )
        self.provider = self.mpesa  # for easy reference in tests

    def test_admin_can_create_provider(self):
        payload = {
            "name": "Airtel Money",
            "provider_code": "AIRTEL",
            "api_key": "secret123",
            "api_secret": "secret456",
            "callback_url": "https://example.com/callback/",
            "is_active": True,
        }
        res = self.admin_client.post(self.providers_url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(PaymentProvider.objects.count(), 2)

    def test_user_cannot_create_provider(self):
        payload = {"name": "BankPay", "provider_code": "BANK"}
        res = self.user_client.post(self.providers_url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_user_can_list_providers(self):
        res = self.user_client.get(self.providers_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(res.data), 1)

    def test_user_can_create_transaction(self):
        payload = {
            "provider": self.provider.id,  # provider required
            "amount": "500.00",
            "currency": "KES",
            "reference": "TXN12345",
            "description": "Deposit",
            "user": str(self.user.id),  # user is required
            "provider_reference": "MPESA12345",  # optional
        }
        res = self.user_client.post(self.transactions_url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Transaction.objects.count(), 1)
        self.assertEqual(Transaction.objects.first().user, self.user)
        self.assertEqual(Transaction.objects.first().provider_reference, "MPESA12345")
        self.assertEqual(Transaction.objects.first().provider, self.provider)

    def test_admin_can_view_all_transactions(self):
        Transaction.objects.create(
            user=self.user,
            provider=self.mpesa,
            amount="1000.00",
            currency="KES",
            reference="TXN999",
            description="Loan repayment",
        )
        res = self.admin_client.get(self.transactions_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)

    def test_user_can_only_view_own_transactions(self):
        Transaction.objects.create(
            user=self.admin,
            provider=self.mpesa,
            amount="200.00",
            currency="KES",
            reference="TXN777",
            description="Admin transaction",
        )
        res = self.user_client.get(self.transactions_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 0)  # user should not see admin transactions

    def test_admin_can_mark_transaction_success(self):
        txn = Transaction.objects.create(
            user=self.user,
            provider=self.mpesa,
            amount="100.00",
            currency="KES",
            reference="TXN555",
        )
        url = reverse("transaction-mark-success", args=[txn.id])
        res = self.admin_client.post(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        txn.refresh_from_db()
        self.assertEqual(txn.status, "SUCCESS")

    def test_callback_can_be_created_without_auth(self):
        txn = Transaction.objects.create(
            user=self.user,
            provider=self.mpesa,
            amount="100.00",
            currency="KES",
            reference="TXN222",
        )
        payload = {
            "transaction": str(txn.id),
            "provider": self.mpesa.id,
            "payload": {"message": "Payment received", "amount": "100.00"},
        }
        res = self.client.post(self.callbacks_url, payload, format="json")  # no auth
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Callback.objects.count(), 1)

    def test_admin_can_mark_callback_processed(self):
        txn = Transaction.objects.create(
            user=self.user,
            provider=self.mpesa,
            amount="300.00",
            currency="KES",
            reference="TXN333",
        )
        callback = Callback.objects.create(
            transaction=txn, provider=self.mpesa, payload={"message": "Done"}
        )
        url = reverse("callback-mark-processed", args=[callback.id])
        res = self.admin_client.post(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        callback.refresh_from_db()
        self.assertTrue(callback.processed)


