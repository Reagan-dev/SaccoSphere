"""Strict X-Sacco-ID enforcement on write / money-moving endpoints.

The ``SaccoScopedMixin`` fallback ("no header -> use the admin's first
SACCO_ADMIN role") silently sends a write to an arbitrary tenant for a
multi-SACCO admin. Views that opt in with ``require_sacco_header = True``
must instead return a clean 400 for an unsafe method from such an admin,
while safe methods and single-SACCO admins keep working unchanged.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from saccomanagement.models import Role
from saccomembership.models import Membership
from services.models import DividendDeclaration, Saving, SavingsType


HEADER_DETAIL = 'X-Sacco-ID header is required for this operation.'


class StrictSaccoHeaderTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.sacco_a = Sacco.objects.create(
            name='Strict A',
            registration_number='STRHDR-A',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.sacco_b = Sacco.objects.create(
            name='Strict B',
            registration_number='STRHDR-B',
            sector=Sacco.Sector.FINANCE,
            county='Kiambu',
        )

        self.multi_admin = User.objects.create_user(
            email='strhdr-multi@example.com', password='StrongPass1',
        )
        Role.objects.create(
            user=self.multi_admin, sacco=self.sacco_a,
            name=Role.SACCO_ADMIN,
        )
        Role.objects.create(
            user=self.multi_admin, sacco=self.sacco_b,
            name=Role.SACCO_ADMIN,
        )

        self.single_admin = User.objects.create_user(
            email='strhdr-single@example.com', password='StrongPass1',
        )
        Role.objects.create(
            user=self.single_admin, sacco=self.sacco_a,
            name=Role.SACCO_ADMIN,
        )

        self.bosa_a = SavingsType.objects.create(
            sacco=self.sacco_a,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        member_user = User.objects.create_user(
            email='strhdr-member@example.com', password='StrongPass1',
        )
        self.membership_a = Membership.objects.create(
            user=member_user,
            sacco=self.sacco_a,
            status=Membership.Status.APPROVED,
            member_number='STRHDR-M1',
        )

    # --- helpers -------------------------------------------------------

    def _approved_declaration(self, financial_year='2025/2026'):
        return DividendDeclaration.objects.create(
            sacco=self.sacco_a,
            savings_type=self.bosa_a,
            financial_year=financial_year,
            declared_rate=Decimal('10.00'),
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            status=DividendDeclaration.Status.APPROVED,
        )

    def _open_body(self):
        return {
            'membership_id': str(self.membership_a.id),
            'savings_type_id': str(self.bosa_a.id),
        }

    # --- provisioning endpoint (POST /services/savings/admin/) --------

    def test_provisioning_multi_admin_without_header_is_400(self):
        self.client.force_authenticate(self.multi_admin)

        response = self.client.post(
            '/api/v1/services/savings/admin/',
            self._open_body(),
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['errors']['detail'], HEADER_DETAIL)
        # Nothing was created against any tenant.
        self.assertFalse(
            Saving.objects.filter(membership=self.membership_a).exists()
        )

    def test_provisioning_single_admin_without_header_still_works(self):
        self.client.force_authenticate(self.single_admin)

        response = self.client.post(
            '/api/v1/services/savings/admin/',
            self._open_body(),
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            Saving.objects.filter(
                membership=self.membership_a, savings_type=self.bosa_a,
            ).exists()
        )

    def test_provisioning_multi_admin_with_header_works(self):
        self.client.force_authenticate(self.multi_admin)

        response = self.client.post(
            '/api/v1/services/savings/admin/',
            self._open_body(),
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco_a.id),
        )

        self.assertEqual(response.status_code, 201)

    # --- dividend disburse (the ticket's headline case) --------------

    def test_disburse_multi_admin_without_header_is_400_no_state_change(self):
        declaration = self._approved_declaration()
        self.client.force_authenticate(self.multi_admin)

        response = self.client.post(
            f'/api/v1/services/dividends/declarations/'
            f'{declaration.id}/disburse/',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['errors']['detail'], HEADER_DETAIL)
        declaration.refresh_from_db()
        # Not silently moved to DISBURSING against an arbitrary SACCO.
        self.assertEqual(
            declaration.status, DividendDeclaration.Status.APPROVED,
        )

    @patch('services.tasks.disburse_dividends_for_declaration_task.delay')
    def test_disburse_single_admin_without_header_proceeds(self, _delay):
        declaration = self._approved_declaration()
        self.client.force_authenticate(self.single_admin)

        response = self.client.post(
            f'/api/v1/services/dividends/declarations/'
            f'{declaration.id}/disburse/',
        )

        self.assertEqual(response.status_code, 202)
        declaration.refresh_from_db()
        self.assertEqual(
            declaration.status, DividendDeclaration.Status.DISBURSING,
        )

    # --- dividend calculate -----------------------------------------

    def test_calculate_multi_admin_without_header_is_400(self):
        declaration = self._approved_declaration(financial_year='2026/2027')
        declaration.status = DividendDeclaration.Status.DRAFT
        declaration.save(update_fields=['status'])
        self.client.force_authenticate(self.multi_admin)

        response = self.client.post(
            f'/api/v1/services/dividends/declarations/'
            f'{declaration.id}/calculate/',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['errors']['detail'], HEADER_DETAIL)
        declaration.refresh_from_db()
        self.assertEqual(
            declaration.status, DividendDeclaration.Status.DRAFT,
        )

    # --- dividend declaration create + list -------------------------

    def test_declaration_create_multi_admin_without_header_is_400(self):
        self.client.force_authenticate(self.multi_admin)

        response = self.client.post(
            '/api/v1/services/dividends/declarations/',
            {
                'savings_type': str(self.bosa_a.id),
                'financial_year': '2028/2029',
                'declared_rate': '10.00',
                'period_start': '2028-01-01',
                'period_end': '2028-12-31',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['errors']['detail'], HEADER_DETAIL)
        self.assertFalse(
            DividendDeclaration.objects.filter(
                financial_year='2028/2029',
            ).exists()
        )

    def test_declaration_list_get_multi_admin_without_header_still_ok(self):
        # GET is a safe method: strict mode does not apply, the fallback
        # to the admin's first SACCO_ADMIN role is intentionally kept.
        self.client.force_authenticate(self.multi_admin)

        response = self.client.get(
            '/api/v1/services/dividends/declarations/',
        )

        self.assertEqual(response.status_code, 200)

    # --- loan status change (drives disbursement) ------------------

    def test_loan_status_multi_admin_without_header_is_400(self):
        # initial() raises before the loan is even looked up, so a bogus
        # id is fine for this assertion.
        self.client.force_authenticate(self.multi_admin)

        response = self.client.patch(
            '/api/v1/management/loans/'
            '00000000-0000-0000-0000-000000000000/status/',
            {'status': 'UNDER_REVIEW'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['errors']['detail'], HEADER_DETAIL)

    def test_loan_status_single_admin_without_header_passes_the_gate(self):
        # Single-SACCO admin: no ambiguity, strict mode lets it through
        # to the normal 404 for a non-existent loan (not a 400).
        self.client.force_authenticate(self.single_admin)

        response = self.client.patch(
            '/api/v1/management/loans/'
            '00000000-0000-0000-0000-000000000000/status/',
            {'status': 'UNDER_REVIEW'},
            format='json',
        )

        self.assertNotEqual(response.status_code, 400)
        self.assertEqual(response.status_code, 404)
