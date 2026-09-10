"""SavingAdmin can no longer silently rewrite a balance or status.

- amount / total_* / status are readonly on the change form;
- create_savings_adjustment moves the balance via apply_ledger_entry and
  writes a SAVING_BALANCE_ADJUSTED audit row;
- set_saving_status is the shared, audited freeze/close/reactivate path.
"""

from decimal import Decimal

from django.contrib.admin.sites import site
from django.test import TestCase
from django.urls import reverse

from accounts.models import Sacco, User
from ledger.models import LedgerEntry
from ledger.utils import (
    apply_ledger_entry,
    expected_savings_balance,
    savings_ledger_balance,
)
from saccomanagement.models import SystemAuditLog
from saccomembership.models import Membership
from services.engines.savings_admin_ops import (
    SavingsAdminOpError,
    create_savings_adjustment,
    set_saving_status,
)
from services.models import Saving, SavingsType


REASON = 'Correcting a mis-posted branch deposit from 2025-08-14.'


def _fixture():
    sacco = Sacco.objects.create(
        name='Admin Ops SACCO',
        registration_number='ADMOPS-1',
        sector=Sacco.Sector.FINANCE,
        county='Nairobi',
    )
    member = User.objects.create_user(
        email='admops-member@example.com', password='StrongPass1',
    )
    membership = Membership.objects.create(
        user=member,
        sacco=sacco,
        status=Membership.Status.APPROVED,
        member_number='ADMOPS-M1',
    )
    stype = SavingsType.objects.create(
        sacco=sacco,
        name=SavingsType.Name.BOSA,
        minimum_contribution=Decimal('100.00'),
    )
    saving = Saving.objects.create(
        membership=membership,
        savings_type=stype,
        amount=Decimal('0.00'),
        status=Saving.Status.ACTIVE,
    )
    # A fully-ledgered starting balance so reconciliation is green.
    apply_ledger_entry(
        saving=saving,
        amount=Decimal('1000.00'),
        entry_type=LedgerEntry.EntryType.CREDIT,
        category=LedgerEntry.Category.SAVING_DEPOSIT,
        description='seed deposit',
        reference='ADMOPS-SEED',
        contribution_delta=Decimal('1000.00'),
    )
    saving.refresh_from_db()
    return sacco, membership, saving


class SavingAdminReadonlyTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email='admops-staff@example.com',
            password='StrongPass1',
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.staff)
        _sacco, self.membership, self.saving = _fixture()

    def test_amount_is_a_readonly_field(self):
        model_admin = site._registry[Saving]
        readonly = model_admin.get_readonly_fields(
            request=None, obj=self.saving,
        )
        for field in (
            'amount', 'total_contributions', 'total_withdrawals', 'status',
        ):
            self.assertIn(field, readonly)

    def test_editing_amount_via_raw_change_form_has_no_effect(self):
        url = reverse('admin:services_saving_change', args=[self.saving.pk])
        payload = {
            'membership': str(self.membership.pk),
            'savings_type': str(self.saving.savings_type_id),
            'dividend_eligible': 'on',
            # Attempt to hand-edit money and status:
            'amount': '99999.99',
            'total_contributions': '88888.88',
            'total_withdrawals': '77777.77',
            'status': Saving.Status.CLOSED,
        }
        if self.saving.last_transaction_date:
            payload['last_transaction_date'] = (
                self.saving.last_transaction_date.isoformat()
            )
        response = self.client.post(url, payload)
        # 302 = admin saved and redirected; a readonly field on the POST
        # is simply ignored, it does not make the form invalid.
        self.assertEqual(response.status_code, 302)
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('1000.00'))
        self.assertEqual(
            self.saving.total_contributions, Decimal('1000.00'),
        )
        self.assertEqual(self.saving.status, Saving.Status.ACTIVE)

    def test_saving_cannot_be_deleted_from_admin(self):
        model_admin = site._registry[Saving]
        self.assertFalse(
            model_admin.has_delete_permission(request=None, obj=self.saving)
        )


class CreateSavingsAdjustmentTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email='admops-adjuster@example.com',
            password='StrongPass1',
            is_staff=True,
            is_superuser=True,
        )
        _sacco, self.membership, self.saving = _fixture()

    def test_credit_adjustment_moves_balance_via_ledger_and_audits(self):
        before = self.saving.amount

        entry = create_savings_adjustment(
            self.saving,
            amount=Decimal('500.00'),
            direction='CREDIT',
            actor=self.staff,
            reason=REASON,
        )

        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, before + Decimal('500.00'))
        self.assertEqual(entry.entry_type, LedgerEntry.EntryType.CREDIT)
        self.assertEqual(
            entry.category, LedgerEntry.Category.SAVING_DEPOSIT,
        )
        self.assertEqual(entry.amount, Decimal('500.00'))
        self.assertIn('[ADMIN ADJUSTMENT]', entry.description)

        log = SystemAuditLog.objects.get(
            action='SAVING_BALANCE_ADJUSTED',
            resource_type='Saving',
            resource_id=str(self.saving.pk),
        )
        self.assertEqual(log.user, self.staff)
        self.assertEqual(log.old_values['amount'], str(before))
        self.assertEqual(
            log.new_values['amount'], str(before + Decimal('500.00')),
        )
        self.assertEqual(log.new_values['reason'], REASON)
        self.assertEqual(log.new_values['direction'], 'CREDIT')
        self.assertEqual(
            log.new_values['ledger_entry_reference'], entry.reference,
        )

    def test_adjustment_keeps_reconciliation_green(self):
        create_savings_adjustment(
            self.saving,
            amount=Decimal('250.00'),
            direction='DEBIT',
            actor=self.staff,
            reason=REASON,
        )
        self.assertEqual(
            expected_savings_balance(self.membership),
            savings_ledger_balance(self.membership),
        )
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('750.00'))
        # A member correction is not a member contribution/withdrawal.
        self.assertEqual(
            self.saving.total_contributions, Decimal('1000.00'),
        )
        self.assertEqual(
            self.saving.total_withdrawals, Decimal('0.00'),
        )

    def test_short_reason_is_rejected_with_no_side_effects(self):
        with self.assertRaises(SavingsAdminOpError):
            create_savings_adjustment(
                self.saving,
                amount=Decimal('500.00'),
                direction='CREDIT',
                actor=self.staff,
                reason='too short',
            )
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('1000.00'))
        self.assertFalse(
            LedgerEntry.objects.filter(
                membership=self.membership,
                description__startswith='[ADMIN ADJUSTMENT]',
            ).exists()
        )
        self.assertFalse(
            SystemAuditLog.objects.filter(
                action='SAVING_BALANCE_ADJUSTED',
            ).exists()
        )

    def test_debit_adjustment_cannot_exceed_current_balance(self):
        with self.assertRaises(SavingsAdminOpError):
            create_savings_adjustment(
                self.saving,
                amount=Decimal('5000.00'),
                direction='DEBIT',
                actor=self.staff,
                reason=REASON,
            )
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('1000.00'))

    def test_action_through_admin_wires_reason_and_actor(self):
        self.client.force_login(self.staff)
        url = reverse('admin:services_saving_changelist')

        response = self.client.post(
            url,
            {
                'action': 'create_balance_adjustment',
                '_selected_action': [str(self.saving.pk)],
                'select_across': '0',
                'index': '0',
                'reason': REASON,
                'adjustment_amount': '300.00',
                'adjustment_direction': 'CREDIT',
            },
        )

        # The action runs, then redirects back to the changelist.
        self.assertEqual(response.status_code, 302)
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('1300.00'))
        self.assertTrue(
            LedgerEntry.objects.filter(
                membership=self.membership,
                category=LedgerEntry.Category.SAVING_DEPOSIT,
                amount=Decimal('300.00'),
                description__startswith='[ADMIN ADJUSTMENT]',
            ).exists()
        )
        log = SystemAuditLog.objects.get(action='SAVING_BALANCE_ADJUSTED')
        self.assertEqual(log.user, self.staff)
        self.assertEqual(log.new_values['reason'], REASON)

    def test_non_superuser_staff_cannot_run_the_adjustment_action(self):
        from django.contrib.auth.models import Permission

        plain_staff = User.objects.create_user(
            email='admops-plain@example.com',
            password='StrongPass1',
            is_staff=True,
        )
        # Give it enough admin access to reach the action, so the
        # is_superuser guard inside the action is what actually stops it.
        plain_staff.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label='services',
                codename__in=('view_saving', 'change_saving'),
            )
        )
        self.client.force_login(plain_staff)
        url = reverse('admin:services_saving_changelist')

        self.client.post(
            url,
            {
                'action': 'create_balance_adjustment',
                '_selected_action': [str(self.saving.pk)],
                'select_across': '0',
                'index': '0',
                'reason': REASON,
                'adjustment_amount': '300.00',
                'adjustment_direction': 'CREDIT',
            },
        )

        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('1000.00'))
        self.assertFalse(
            SystemAuditLog.objects.filter(
                action='SAVING_BALANCE_ADJUSTED',
            ).exists()
        )


class SetSavingStatusTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email='admops-freezer@example.com',
            password='StrongPass1',
            is_staff=True,
            is_superuser=True,
        )
        _sacco, self.membership, self.saving = _fixture()

    def test_status_change_is_locked_and_audited(self):
        set_saving_status(
            self.saving,
            new_status=Saving.Status.FROZEN,
            actor=self.staff,
            reason='Fraud review opened, ticket OPS-2211.',
        )

        self.saving.refresh_from_db()
        self.assertEqual(self.saving.status, Saving.Status.FROZEN)

        log = SystemAuditLog.objects.get(
            action='SAVINGS_STATUS_CHANGED',
            resource_id=str(self.saving.pk),
        )
        self.assertEqual(log.user, self.staff)
        self.assertEqual(log.old_values['status'], Saving.Status.ACTIVE)
        self.assertEqual(log.new_values['status'], Saving.Status.FROZEN)
        self.assertIn('OPS-2211', log.new_values['reason'])

    def test_noop_transition_writes_no_audit_row(self):
        set_saving_status(
            self.saving,
            new_status=Saving.Status.ACTIVE,
            actor=self.staff,
            reason='no change',
        )
        self.assertFalse(
            SystemAuditLog.objects.filter(
                action='SAVINGS_STATUS_CHANGED',
            ).exists()
        )

    def test_unknown_status_is_rejected(self):
        with self.assertRaises(SavingsAdminOpError):
            set_saving_status(
                self.saving,
                new_status='BANANA',
                actor=self.staff,
                reason='x',
            )
