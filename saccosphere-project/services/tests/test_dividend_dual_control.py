"""Four-eyes control on dividend approve / disburse.

The admin who created a declaration must not approve it; the admin who
approved it must not disburse it. Per-SACCO via
``SaccoSettings.enforce_dividend_dual_control`` (default on).
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Sacco, SaccoSettings, User
from saccomanagement.models import Role
from services.models import DividendDeclaration, SavingsType


APPROVE_403 = (
    'A dividend declaration must be approved by a different admin than '
    'the one who created it.'
)
DISBURSE_403 = (
    'A dividend declaration must be disbursed by a different admin than '
    'the one who approved it.'
)


def _sacco(name, registration_number, county='Nairobi'):
    return Sacco.objects.create(
        name=name,
        registration_number=registration_number,
        sector=Sacco.Sector.FINANCE,
        county=county,
    )


def _admin(sacco, email):
    user = User.objects.create_user(email=email, password='StrongPass1')
    Role.objects.create(user=user, sacco=sacco, name=Role.SACCO_ADMIN)
    return user


class DividendDualControlTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.sacco = _sacco('Dual A', 'DUAL-A')
        self.savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        self.creator = _admin(self.sacco, 'dual-creator@example.com')
        self.approver = _admin(self.sacco, 'dual-approver@example.com')
        self.disburser = _admin(self.sacco, 'dual-disburser@example.com')

    # helpers -------------------------------------------------------

    def _create_declaration(self, actor, financial_year='2025/2026'):
        self.client.force_authenticate(actor)
        response = self.client.post(
            '/api/v1/services/dividends/declarations/',
            {
                'savings_type': str(self.savings_type.id),
                'financial_year': financial_year,
                'declared_rate': '10.00',
                'period_start': '2025-01-01',
                'period_end': '2025-12-31',
            },
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )
        self.assertEqual(response.status_code, 201, response.content)
        return DividendDeclaration.objects.get(id=response.json()['id'])

    def _set_status(self, declaration, status_value):
        DividendDeclaration.objects.filter(pk=declaration.pk).update(
            status=status_value,
        )

    def _approve(self, actor, declaration, sacco_header=None):
        self.client.force_authenticate(actor)
        return self.client.post(
            f'/api/v1/services/dividends/declarations/'
            f'{declaration.id}/approve/',
            HTTP_X_SACCO_ID=str(sacco_header or self.sacco.id),
        )

    def _disburse(self, actor, declaration):
        self.client.force_authenticate(actor)
        return self.client.post(
            f'/api/v1/services/dividends/declarations/'
            f'{declaration.id}/disburse/',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

    # tests -------------------------------------------------------

    def test_creator_cannot_approve_own_declaration(self):
        declaration = self._create_declaration(self.creator)
        self._set_status(declaration, DividendDeclaration.Status.CALCULATED)

        response = self._approve(self.creator, declaration)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['detail'], APPROVE_403)
        declaration.refresh_from_db()
        self.assertEqual(
            declaration.status, DividendDeclaration.Status.CALCULATED,
        )
        self.assertIsNone(declaration.approved_by)

    def test_a_different_admin_can_approve(self):
        declaration = self._create_declaration(self.creator)
        self._set_status(declaration, DividendDeclaration.Status.CALCULATED)

        response = self._approve(self.approver, declaration)

        self.assertEqual(response.status_code, 200)
        declaration.refresh_from_db()
        self.assertEqual(
            declaration.status, DividendDeclaration.Status.APPROVED,
        )
        self.assertEqual(declaration.approved_by, self.approver)

    def test_approver_cannot_disburse(self):
        declaration = self._create_declaration(self.creator)
        self._set_status(declaration, DividendDeclaration.Status.CALCULATED)
        self.assertEqual(
            self._approve(self.approver, declaration).status_code, 200,
        )

        response = self._disburse(self.approver, declaration)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['detail'], DISBURSE_403)
        declaration.refresh_from_db()
        self.assertEqual(
            declaration.status, DividendDeclaration.Status.APPROVED,
        )

    @patch('services.tasks.disburse_dividends_for_declaration_task.delay')
    def test_full_three_admin_flow_succeeds(self, _delay):
        declaration = self._create_declaration(self.creator)
        self._set_status(declaration, DividendDeclaration.Status.CALCULATED)
        self.assertEqual(
            self._approve(self.approver, declaration).status_code, 200,
        )

        response = self._disburse(self.disburser, declaration)

        self.assertEqual(response.status_code, 202)
        declaration.refresh_from_db()
        self.assertEqual(
            declaration.status, DividendDeclaration.Status.DISBURSING,
        )

    @patch('services.tasks.disburse_dividends_for_declaration_task.delay')
    def test_creator_who_did_not_approve_may_disburse(self, _delay):
        # The rule is creator != approver and approver != disburser;
        # creator == disburser is deliberately allowed.
        declaration = self._create_declaration(self.creator)
        self._set_status(declaration, DividendDeclaration.Status.CALCULATED)
        self._approve(self.approver, declaration)

        response = self._disburse(self.creator, declaration)

        self.assertEqual(response.status_code, 202)

    def test_switching_x_sacco_id_does_not_route_around_the_check(self):
        other_sacco = _sacco('Dual B', 'DUAL-B', county='Kiambu')
        Role.objects.create(
            user=self.creator, sacco=other_sacco, name=Role.SACCO_ADMIN,
        )
        declaration = self._create_declaration(self.creator)
        self._set_status(declaration, DividendDeclaration.Status.CALCULATED)

        # Approve while naming the *other* tenant: the declaration is not
        # in that tenant's scope -> 404, never an approval.
        wrong = self._approve(
            self.creator, declaration, sacco_header=other_sacco.id,
        )
        self.assertEqual(wrong.status_code, 404)

        # Correct tenant header: still the same authenticated user -> 403.
        right = self._approve(self.creator, declaration)
        self.assertEqual(right.status_code, 403)
        declaration.refresh_from_db()
        self.assertEqual(
            declaration.status, DividendDeclaration.Status.CALCULATED,
        )
        self.assertIsNone(declaration.approved_by)

    def test_sacco_can_disable_dual_control(self):
        SaccoSettings.objects.create(
            sacco=self.sacco, enforce_dividend_dual_control=False,
        )
        declaration = self._create_declaration(self.creator)
        self._set_status(declaration, DividendDeclaration.Status.CALCULATED)

        response = self._approve(self.creator, declaration)

        self.assertEqual(response.status_code, 200)
        declaration.refresh_from_db()
        self.assertEqual(
            declaration.status, DividendDeclaration.Status.APPROVED,
        )

    def test_declaration_without_a_recorded_creator_can_be_approved(self):
        # ORM-created (legacy) rows have created_by = NULL; the check can
        # only assert sameness, so it fails open here.
        declaration = DividendDeclaration.objects.create(
            sacco=self.sacco,
            savings_type=self.savings_type,
            financial_year='2027/2028',
            declared_rate=Decimal('10.00'),
            period_start=date(2027, 1, 1),
            period_end=date(2027, 12, 31),
            status=DividendDeclaration.Status.CALCULATED,
        )

        response = self._approve(self.creator, declaration)

        self.assertEqual(response.status_code, 200)
