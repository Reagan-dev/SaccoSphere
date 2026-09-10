"""Sacco-scoped admin API for savings status + dividend eligibility.

- POST /savings/<id>/status/  {action, reason}
- POST /savings/<id>/dividend-eligibility/  {eligible, reason}

Legal transitions succeed and are audited; illegal ones 400; CLOSED is
terminal; a cross-tenant id 404s; the member is notified on freeze/close.
"""

from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from notifications.models import Notification
from saccomanagement.models import Role, SystemAuditLog
from saccomembership.models import Membership
from services.models import Saving, SavingsType


REASON = 'Account under fraud review, ticket OPS-4471.'


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


def _member_saving(sacco, email, member_number, *, status=Saving.Status.ACTIVE,
                   dividend_eligible=True):
    user = User.objects.create_user(email=email, password='StrongPass1')
    membership = Membership.objects.create(
        user=user,
        sacco=sacco,
        status=Membership.Status.APPROVED,
        member_number=member_number,
    )
    stype, _ = SavingsType.objects.get_or_create(
        sacco=sacco,
        name=SavingsType.Name.BOSA,
        defaults={'minimum_contribution': Decimal('100.00')},
    )
    saving = Saving.objects.create(
        membership=membership,
        savings_type=stype,
        amount=Decimal('1000.00'),
        status=status,
        dividend_eligible=dividend_eligible,
    )
    return membership, saving


class _Fixture(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.sacco = _sacco('Status A', 'SSTA-A')
        self.admin = _admin(self.sacco, 'ssta-admin@example.com')
        self.client.force_authenticate(self.admin)
        self.membership, self.saving = _member_saving(
            self.sacco, 'ssta-m1@example.com', 'SSTA-M1',
        )

    def _status_url(self, saving):
        return f'/api/v1/services/savings/{saving.id}/status/'

    def _elig_url(self, saving):
        return f'/api/v1/services/savings/{saving.id}/dividend-eligibility/'

    def _post_status(self, saving, action, reason=REASON):
        return self.client.post(
            self._status_url(saving),
            {'action': action, 'reason': reason},
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

    def _post_eligibility(self, saving, eligible, reason=REASON):
        return self.client.post(
            self._elig_url(saving),
            {'eligible': eligible, 'reason': reason},
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )


class SavingsStatusActionTests(_Fixture):
    def test_freeze_active_account_succeeds_audits_and_notifies(self):
        response = self._post_status(self.saving, 'freeze')

        self.assertEqual(response.status_code, 200)
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.status, Saving.Status.FROZEN)

        audit = SystemAuditLog.objects.get(
            action='SAVINGS_STATUS_CHANGED',
            resource_type='Saving',
            resource_id=str(self.saving.id),
        )
        self.assertEqual(audit.user, self.admin)
        self.assertEqual(audit.old_values['status'], Saving.Status.ACTIVE)
        self.assertEqual(audit.new_values['status'], Saving.Status.FROZEN)
        self.assertEqual(audit.new_values['reason'], REASON)

        note = Notification.objects.get(user=self.membership.user)
        self.assertEqual(note.category, Notification.Category.ALERT)
        self.assertIn('frozen', note.title.lower())
        self.assertEqual(note.related_object_id, str(self.saving.id))

    def test_close_from_active_and_from_frozen_both_legal(self):
        _mb, other = _member_saving(
            self.sacco, 'ssta-m2@example.com', 'SSTA-M2',
        )
        self.assertEqual(
            self._post_status(other, 'close').status_code, 200,
        )
        other.refresh_from_db()
        self.assertEqual(other.status, Saving.Status.CLOSED)

        self._post_status(self.saving, 'freeze')
        self.assertEqual(
            self._post_status(self.saving, 'close').status_code, 200,
        )
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.status, Saving.Status.CLOSED)

    def test_reactivate_frozen_succeeds_without_a_new_notification(self):
        self._post_status(self.saving, 'freeze')
        notes_after_freeze = Notification.objects.filter(
            user=self.membership.user,
        ).count()

        response = self._post_status(self.saving, 'reactivate')

        self.assertEqual(response.status_code, 200)
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.status, Saving.Status.ACTIVE)
        self.assertEqual(
            Notification.objects.filter(
                user=self.membership.user,
            ).count(),
            notes_after_freeze,
        )

    def test_reactivating_an_active_account_is_rejected(self):
        response = self._post_status(self.saving, 'reactivate')

        self.assertEqual(response.status_code, 400)
        self.assertIn('already active', response.json()['detail'].lower())
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.status, Saving.Status.ACTIVE)
        self.assertFalse(
            SystemAuditLog.objects.filter(
                action='SAVINGS_STATUS_CHANGED',
            ).exists()
        )

    def test_closed_account_is_terminal(self):
        self._post_status(self.saving, 'close')

        for action in ('freeze', 'reactivate', 'close'):
            with self.subTest(action=action):
                response = self._post_status(self.saving, action)
                self.assertEqual(response.status_code, 400)
                self.assertIn(
                    'closed', response.json()['detail'].lower(),
                )

        self.saving.refresh_from_db()
        self.assertEqual(self.saving.status, Saving.Status.CLOSED)

    def test_reason_must_be_present_and_substantial(self):
        missing = self.client.post(
            self._status_url(self.saving),
            {'action': 'freeze'},
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )
        self.assertEqual(missing.status_code, 400)

        too_short = self._post_status(self.saving, 'freeze', reason='no')
        self.assertEqual(too_short.status_code, 400)
        self.assertIn('reason', too_short.json()['detail'].lower())

        self.saving.refresh_from_db()
        self.assertEqual(self.saving.status, Saving.Status.ACTIVE)

    def test_cross_tenant_target_404s(self):
        sacco_b = _sacco('Status B', 'SSTA-B', county='Kiambu')
        _mb_b, saving_b = _member_saving(
            sacco_b, 'ssta-b1@example.com', 'SSTA-B1',
        )

        response = self.client.post(
            f'/api/v1/services/savings/{saving_b.id}/status/',
            {'action': 'freeze', 'reason': REASON},
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, 404)
        saving_b.refresh_from_db()
        self.assertEqual(saving_b.status, Saving.Status.ACTIVE)

    def test_non_admin_member_is_forbidden(self):
        self.client.force_authenticate(self.membership.user)
        response = self._post_status(self.saving, 'freeze')
        self.assertIn(response.status_code, (401, 403))
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.status, Saving.Status.ACTIVE)


class SavingsDividendEligibilityTests(_Fixture):
    def test_disable_eligibility_succeeds_and_audits(self):
        response = self._post_eligibility(self.saving, False)

        self.assertEqual(response.status_code, 200)
        self.saving.refresh_from_db()
        self.assertFalse(self.saving.dividend_eligible)

        audit = SystemAuditLog.objects.get(
            action='DIVIDEND_ELIGIBILITY_CHANGED',
            resource_type='Saving',
            resource_id=str(self.saving.id),
        )
        self.assertEqual(audit.user, self.admin)
        self.assertEqual(audit.old_values['dividend_eligible'], True)
        self.assertEqual(audit.new_values['dividend_eligible'], False)
        self.assertEqual(audit.new_values['reason'], REASON)
        # Eligibility change does not notify the member.
        self.assertFalse(
            Notification.objects.filter(user=self.membership.user).exists()
        )

    def test_reenable_after_disable(self):
        self._post_eligibility(self.saving, False)
        response = self._post_eligibility(self.saving, True)
        self.assertEqual(response.status_code, 200)
        self.saving.refresh_from_db()
        self.assertTrue(self.saving.dividend_eligible)

    def test_noop_eligibility_change_is_rejected(self):
        response = self._post_eligibility(self.saving, True)  # already True

        self.assertEqual(response.status_code, 400)
        self.assertIn('already', response.json()['detail'].lower())
        self.assertFalse(
            SystemAuditLog.objects.filter(
                action='DIVIDEND_ELIGIBILITY_CHANGED',
            ).exists()
        )

    def test_cross_tenant_target_404s(self):
        sacco_b = _sacco('Elig B', 'SSTE-B', county='Kiambu')
        _mb_b, saving_b = _member_saving(
            sacco_b, 'sste-b1@example.com', 'SSTE-B1',
        )

        response = self.client.post(
            f'/api/v1/services/savings/{saving_b.id}/dividend-eligibility/',
            {'eligible': False, 'reason': REASON},
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, 404)
        saving_b.refresh_from_db()
        self.assertTrue(saving_b.dividend_eligible)
