"""open_savings_account - the one shared savings-account creation path.

Covers the service function directly and the admin-only HTTP entry
point: happy path, duplicate rejection, cross-tenant rejection, and the
guarantee that an opening balance becomes a real ledger entry rather
than a bare ``Saving.amount`` write.
"""

from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from ledger.models import LedgerEntry
from ledger.utils import savings_ledger_balance
from saccomanagement.models import Role, SystemAuditLog
from saccomembership.models import Membership
from services.engines.savings_provisioning import (
    SavingsAccountError,
    open_savings_account,
)
from services.models import Saving, SavingsType


def _sacco(name, registration_number, county='Nairobi'):
    return Sacco.objects.create(
        name=name,
        registration_number=registration_number,
        sector=Sacco.Sector.FINANCE,
        county=county,
    )


def _member(sacco, email, member_number):
    user = User.objects.create_user(email=email, password='StrongPass1')
    return Membership.objects.create(
        user=user,
        sacco=sacco,
        status=Membership.Status.APPROVED,
        member_number=member_number,
    )


def _bosa(sacco):
    return SavingsType.objects.create(
        sacco=sacco,
        name=SavingsType.Name.BOSA,
        minimum_contribution=Decimal('100.00'),
    )


class OpenSavingsAccountServiceTests(TestCase):
    def setUp(self):
        self.sacco = _sacco('Prov SACCO', 'PROV-1')
        self.membership = _member(
            self.sacco, 'prov-m1@example.com', 'PROV-M1',
        )
        self.bosa = _bosa(self.sacco)

    def test_opens_account_with_no_opening_balance_and_no_ledger_row(self):
        saving = open_savings_account(self.membership, self.bosa)

        self.assertEqual(saving.amount, Decimal('0.00'))
        self.assertEqual(saving.status, Saving.Status.ACTIVE)
        self.assertEqual(saving.membership_id, self.membership.id)
        self.assertFalse(
            LedgerEntry.objects.filter(
                membership=self.membership,
            ).exists()
        )

    def test_opening_balance_produces_a_ledger_entry(self):
        saving = open_savings_account(
            self.membership, self.bosa, opening_balance=Decimal('2500.00'),
        )
        saving.refresh_from_db()

        entry = LedgerEntry.objects.get(
            membership=self.membership,
            category=LedgerEntry.Category.SAVING_DEPOSIT,
        )
        self.assertEqual(entry.entry_type, LedgerEntry.EntryType.CREDIT)
        self.assertEqual(entry.amount, Decimal('2500.00'))
        self.assertEqual(entry.reference, f'SAV-OPEN-{saving.id}')
        # Balance moved through the ledger, not a bare field write.
        self.assertEqual(saving.amount, Decimal('2500.00'))
        self.assertEqual(saving.total_contributions, Decimal('2500.00'))
        self.assertEqual(
            savings_ledger_balance(self.membership), Decimal('2500.00'),
        )

    def test_duplicate_account_is_rejected(self):
        open_savings_account(self.membership, self.bosa)

        with self.assertRaises(SavingsAccountError):
            open_savings_account(self.membership, self.bosa)

        self.assertEqual(
            Saving.objects.filter(
                membership=self.membership, savings_type=self.bosa,
            ).count(),
            1,
        )

    def test_second_account_allowed_when_type_permits_multiple(self):
        self.bosa.allows_multiple_accounts = True
        self.bosa.save(update_fields=['allows_multiple_accounts'])

        open_savings_account(self.membership, self.bosa)
        open_savings_account(self.membership, self.bosa)

        self.assertEqual(
            Saving.objects.filter(
                membership=self.membership, savings_type=self.bosa,
            ).count(),
            2,
        )

    def test_cross_tenant_membership_and_type_is_rejected(self):
        other_sacco = _sacco('Other SACCO', 'PROV-2', county='Kiambu')
        other_type = _bosa(other_sacco)

        with self.assertRaises(SavingsAccountError):
            open_savings_account(self.membership, other_type)

        self.assertFalse(
            Saving.objects.filter(membership=self.membership).exists()
        )

    def test_negative_opening_balance_is_rejected(self):
        with self.assertRaises(SavingsAccountError):
            open_savings_account(
                self.membership,
                self.bosa,
                opening_balance=Decimal('-5.00'),
            )

        self.assertFalse(
            Saving.objects.filter(membership=self.membership).exists()
        )


class OpenSavingsAccountAdminEndpointTests(TestCase):
    URL = '/api/v1/services/savings/admin/'

    def setUp(self):
        self.client = APIClient()
        self.sacco = _sacco('Endpoint SACCO', 'EP-1')
        self.other_sacco = _sacco('Endpoint Other', 'EP-2', county='Kiambu')

        self.admin = User.objects.create_user(
            email='ep-admin@example.com', password='StrongPass1',
        )
        Role.objects.create(
            user=self.admin, sacco=self.sacco, name=Role.SACCO_ADMIN,
        )
        self.client.force_authenticate(user=self.admin)

        self.membership = _member(self.sacco, 'ep-m1@example.com', 'EP-M1')
        self.bosa = _bosa(self.sacco)
        self.other_membership = _member(
            self.other_sacco, 'ep-o1@example.com', 'EP-O1',
        )
        self.other_type = _bosa(self.other_sacco)

    def _post(self, body):
        return self.client.post(
            self.URL, body, format='json',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

    def test_admin_opens_account_happy_path(self):
        response = self._post(
            {
                'membership_id': str(self.membership.id),
                'savings_type_id': str(self.bosa.id),
                'opening_balance': '1500.00',
            }
        )

        self.assertEqual(response.status_code, 201)
        saving = Saving.objects.get(
            membership=self.membership, savings_type=self.bosa,
        )
        self.assertEqual(saving.amount, Decimal('1500.00'))
        self.assertTrue(
            LedgerEntry.objects.filter(
                membership=self.membership,
                category=LedgerEntry.Category.SAVING_DEPOSIT,
                amount=Decimal('1500.00'),
            ).exists()
        )
        self.assertTrue(
            SystemAuditLog.objects.filter(
                action='SAVINGS_ACCOUNT_OPENED',
                resource_type='Saving',
                resource_id=str(saving.id),
            ).exists()
        )

    def test_admin_opens_account_without_opening_balance(self):
        response = self._post(
            {
                'membership_id': str(self.membership.id),
                'savings_type_id': str(self.bosa.id),
            }
        )

        self.assertEqual(response.status_code, 201)
        self.assertFalse(
            LedgerEntry.objects.filter(
                membership=self.membership,
            ).exists()
        )

    def test_duplicate_open_returns_409(self):
        first = self._post(
            {
                'membership_id': str(self.membership.id),
                'savings_type_id': str(self.bosa.id),
            }
        )
        self.assertEqual(first.status_code, 201)

        second = self._post(
            {
                'membership_id': str(self.membership.id),
                'savings_type_id': str(self.bosa.id),
            }
        )
        self.assertEqual(second.status_code, 409)
        self.assertEqual(
            Saving.objects.filter(membership=self.membership).count(), 1,
        )

    def test_membership_from_another_sacco_is_404(self):
        response = self._post(
            {
                'membership_id': str(self.other_membership.id),
                'savings_type_id': str(self.bosa.id),
            }
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            Saving.objects.filter(
                membership=self.other_membership,
            ).exists()
        )

    def test_savings_type_from_another_sacco_is_404(self):
        response = self._post(
            {
                'membership_id': str(self.membership.id),
                'savings_type_id': str(self.other_type.id),
            }
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            Saving.objects.filter(membership=self.membership).exists()
        )

    def test_non_admin_member_is_forbidden(self):
        self.client.force_authenticate(user=self.membership.user)

        response = self._post(
            {
                'membership_id': str(self.membership.id),
                'savings_type_id': str(self.bosa.id),
            }
        )

        self.assertIn(response.status_code, (401, 403))
        self.assertFalse(
            Saving.objects.filter(membership=self.membership).exists()
        )
