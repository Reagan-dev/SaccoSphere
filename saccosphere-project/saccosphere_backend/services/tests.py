# services/tests.py
from datetime import date, timedelta

from django.urls import reverse
from django.db.models.signals import post_save
from rest_framework import status
from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model

from .models import Service, Saving, Loan, Insurance

# Get the configured user model (custom user uses email as username field)
User = get_user_model()


class ServicesAppTests(APITestCase):
    # ---------------------------
    # Setup / helpers
    # ---------------------------
    def setUp(self):
        # Create normal user
        self.user = User.objects.create_user(
            email="user@example.com", password="testpass123"
        )

        # Create admin user
        self.admin = User.objects.create_user(
            email="admin@example.com", password="adminpass123", is_staff=True
        )

        # Create sample service records to reference in payloads
        self.saving_service = Service.objects.create(
            name="Savings Service", description="Member savings"
        )
        self.loan_service = Service.objects.create(
            name="Loan Service", description="Member loans"
        )
        self.insurance_service = Service.objects.create(
            name="Insurance Service", description="Member insurance"
        )

        # Build URLs from router names (more robust than hard-coded paths)
        # These names come from the router registration basenames:
        # router.register(r'', ServiceViewSet, basename='service')
        # router.register(r'savings', SavingViewSet, basename='saving')
        # router.register(r'loans', LoanViewSet, basename='loan')
        # router.register(r'insurances', InsuranceViewSet, basename='insurance')
        self.service_url = reverse("service-list")
        self.saving_url = reverse("saving-list")
        self.loan_url = reverse("loan-list")
        self.insurance_url = reverse("insurance-list")

        # --- IMPORTANT: avoid triggering the saving->transaction signal in tests.
        # The app's post_save receiver for Saving may create Transaction objects
        # that depend on other apps and cause IntegrityError during unit tests.
        # If that signal is connected, disconnect it here so tests remain isolated.
        try:
            # import the signals module and disconnect the specific receiver if present
            import services.signals as signals_module  # type: ignore
            if hasattr(signals_module, "create_transaction_for_saving"):
                post_save.disconnect(
                    receiver=signals_module.create_transaction_for_saving,
                    sender=Saving,
                )
        except Exception:
            # If signals module or function not present, ignore - tests continue
            pass

    # ---------------------------
    # Service endpoints
    # ---------------------------
    def test_non_admin_cannot_create_service(self):
        # normal user must not be able to create a Service
        self.client.force_authenticate(user=self.user)
        payload = {"name": "New Service", "description": "Not allowed for normal users"}
        res = self.client.post(self.service_url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_create_service(self):
        # admin user should create a Service
        self.client.force_authenticate(user=self.admin)
        payload = {"name": "Shares", "description": "Admin created service"}
        res = self.client.post(self.service_url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        # we already created 3 services in setUp, so now should be 4
        self.assertTrue(Service.objects.filter(name="Shares").exists())

    def test_anyone_can_list_services(self):
        # listing services is open (AllowAny)
        res = self.client.get(self.service_url, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(res.data), 1)

    # ---------------------------
    # Savings endpoints
    # ---------------------------
    def test_user_can_create_saving(self):
        # user can create a saving record for themselves
        self.client.force_authenticate(user=self.user)
        payload = {
            # include member id to satisfy serializers that declare member as writable
            "member": str(self.user.id),
            "amount": "1000.00",
            "service_id": str(self.saving_service.id),
            "transaction_type": "deposit",
        }
        res = self.client.post(self.saving_url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Saving.objects.filter(member=self.user).count(), 1)

    def test_user_sees_only_own_savings(self):
        # create a saving for user via API (authenticated as user)
        self.client.force_authenticate(user=self.user)
        payload_user = {
            "member": str(self.user.id),
            "amount": "500.00",
            "service_id": str(self.saving_service.id),
            "transaction_type": "deposit",
        }
        res1 = self.client.post(self.saving_url, payload_user, format="json")
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)

        # create a saving for admin via API (authenticated as admin)
        self.client.force_authenticate(user=self.admin)
        payload_admin = {
            "member": str(self.admin.id),
            "amount": "2000.00",
            "service_id": str(self.saving_service.id),
            "transaction_type": "deposit",
        }
        res2 = self.client.post(self.saving_url, payload_admin, format="json")
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED)

        # user should only see their own saving
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.saving_url, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        # expect exactly 1 saving belonging to the user
        # (response is a list of serialized savings)
        self.assertEqual(len(res.data), 1)

    # ---------------------------
    # Loan endpoints
    # ---------------------------
    def test_user_can_apply_for_loan(self):
        # user posts a loan application
        self.client.force_authenticate(user=self.user)
        payload = {
            "member": str(self.user.id),
            "amount": "5000.00",
            "interest_rate": "10.00",
            "duration_months": 12,
            "service_id": str(self.loan_service.id),
        }
        res = self.client.post(self.loan_url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Loan.objects.filter(member=self.user).count(), 1)

    def test_admin_can_see_all_loans(self):
        # create a loan record directly (avoid API to keep test simple)
        Loan.objects.create(
            member=self.user,
            service=self.loan_service,
            amount=3000,
            interest_rate=5,
            duration_months=6,
        )

        # admin should be able to list all loans
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(self.loan_url, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        # ensure at least the one we created is returned
        self.assertGreaterEqual(len(res.data), 1)

    # ---------------------------
    # Insurance endpoints
    # ---------------------------
    def test_user_can_create_insurance(self):
        # user creates an insurance policy
        self.client.force_authenticate(user=self.user)
        payload = {
            "member": str(self.user.id),
            "policy_number": "POL12345",
            "coverage_amount": "10000.00",
            "premium": "500.00",
            "start_date": str(date.today()),
            "end_date": str(date.today() + timedelta(days=365)),
            "service_id": str(self.insurance_service.id),
        }
        res = self.client.post(self.insurance_url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Insurance.objects.filter(member=self.user).count(), 1)

    def test_user_can_view_own_insurance(self):
        # create insurance record directly
        Insurance.objects.create(
            member=self.user,
            service=self.insurance_service,
            policy_number="POL654321",
            coverage_amount=100000,
            premium=1000,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=365),
        )

        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.insurance_url, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(res.data), 1)

    def test_insurance_auto_expires(self):
        insurance = Insurance.objects.create(
            member=self.user,
            service=self.insurance_service,
            policy_number="POL00001",
            coverage_amount=10000,
            premium=500,
            start_date=date.today() - timedelta(days=400),
            end_date=date.today() - timedelta(days=1),
        )
        self.assertTrue(insurance.is_expired)
