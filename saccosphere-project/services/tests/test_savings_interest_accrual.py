"""Monthly savings-interest accrual.

- a basic run credits the expected simple monthly interest;
- FROZEN / CLOSED accounts and no-rate products accrue nothing;
- the accrual credit lands in SAVINGS_LEDGER_CATEGORIES, so the Prompt-7
  reconciliation still sees Saving.amount == savings_ledger_balance;
- it is idempotent per (saving, month);
- the beat task is per-SACCO opt-in and writes a heartbeat + audit row.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from accounts.models import Sacco, SaccoSettings, User
from health.models import JobHeartbeat
from ledger.models import LedgerEntry
from ledger.utils import (
    apply_ledger_entry,
    expected_savings_balance,
    savings_ledger_balance,
)
from saccomanagement.models import SystemAuditLog
from saccomembership.models import Membership
from services.engines.savings_interest import (
    accrue_savings_interest_for_sacco,
)
from services.models import Saving, SavingsType
from services.tasks import (
    _run_savings_interest_accrual,
    accrue_savings_interest,
)


ACCRUAL_DATE = date(2026, 1, 1)
PERIOD = '2026-01'


class _InterestFixture(TestCase):
    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Interest SACCO',
            registration_number='INT-1',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.settings = SaccoSettings.objects.create(
            sacco=self.sacco,
            savings_interest_accrual_enabled=True,
        )
        # 12% p.a. -> 1% per month.
        self.savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
            interest_rate=Decimal('12.00'),
        )
        self._seq = 0

    def _member_saving(self, opening, *, status=Saving.Status.ACTIVE,
                       savings_type=None):
        self._seq += 1
        user = User.objects.create_user(
            email=f'int-m{self._seq}@example.com', password='StrongPass1',
        )
        membership = Membership.objects.create(
            user=user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number=f'INT-M{self._seq:03d}',
        )
        saving = Saving.objects.create(
            membership=membership,
            savings_type=savings_type or self.savings_type,
            amount=Decimal('0.00'),
            status=status,
        )
        if Decimal(opening) > 0:
            apply_ledger_entry(
                saving=saving,
                amount=Decimal(opening),
                entry_type=LedgerEntry.EntryType.CREDIT,
                category=LedgerEntry.Category.SAVING_DEPOSIT,
                description='opening',
                reference=f'INT-SEED-{self._seq}',
                contribution_delta=Decimal(opening),
            )
        saving.refresh_from_db()
        # status may have been forced after the (ACTIVE-only) seed helper.
        if saving.status != status:
            Saving.objects.filter(pk=saving.pk).update(status=status)
            saving.refresh_from_db()
        return membership, saving


class AccrueForSaccoTests(_InterestFixture):
    def test_basic_run_credits_simple_monthly_interest(self):
        membership_a, saving_a = self._member_saving('10000.00')
        _mb, saving_b = self._member_saving('1234.56')

        result = accrue_savings_interest_for_sacco(
            self.sacco, accrual_date=ACCRUAL_DATE,
        )

        self.assertEqual(result['savings_credited'], 2)
        self.assertEqual(result['skipped_already_accrued'], 0)
        self.assertEqual(result['total_interest'], Decimal('112.35'))
        self.assertEqual(result['period'], PERIOD)

        saving_a.refresh_from_db()
        saving_b.refresh_from_db()
        self.assertEqual(saving_a.amount, Decimal('10100.00'))
        # 1234.56 * 12 / 100 / 12 = 12.3456 -> 12.35 (HALF_UP)
        self.assertEqual(saving_b.amount, Decimal('1234.56')
                         + Decimal('12.35'))

        entry = LedgerEntry.objects.get(
            reference=f'INT-{saving_a.id}-{PERIOD}',
        )
        self.assertEqual(entry.entry_type, LedgerEntry.EntryType.CREDIT)
        self.assertEqual(
            entry.category, LedgerEntry.Category.SAVINGS_INTEREST,
        )
        self.assertEqual(entry.amount, Decimal('100.00'))
        self.assertIn('12.00% p.a.', entry.description)

    def test_frozen_and_closed_accounts_accrue_nothing(self):
        _a, active = self._member_saving('10000.00')
        _f, frozen = self._member_saving(
            '10000.00', status=Saving.Status.FROZEN,
        )
        _c, closed = self._member_saving(
            '10000.00', status=Saving.Status.CLOSED,
        )

        result = accrue_savings_interest_for_sacco(
            self.sacco, accrual_date=ACCRUAL_DATE,
        )

        self.assertEqual(result['savings_credited'], 1)
        frozen.refresh_from_db()
        closed.refresh_from_db()
        self.assertEqual(frozen.amount, Decimal('10000.00'))
        self.assertEqual(closed.amount, Decimal('10000.00'))
        self.assertFalse(
            LedgerEntry.objects.filter(
                category=LedgerEntry.Category.SAVINGS_INTEREST,
                reference__in=[
                    f'INT-{frozen.id}-{PERIOD}',
                    f'INT-{closed.id}-{PERIOD}',
                ],
            ).exists()
        )

    def test_accrual_keeps_the_ledger_reconciliation_green(self):
        membership, saving = self._member_saving('10000.00')

        accrue_savings_interest_for_sacco(
            self.sacco, accrual_date=ACCRUAL_DATE,
        )

        saving.refresh_from_db()
        self.assertEqual(saving.amount, Decimal('10100.00'))
        self.assertEqual(
            expected_savings_balance(membership),
            savings_ledger_balance(membership),
        )
        self.assertEqual(
            savings_ledger_balance(membership), Decimal('10100.00'),
        )

    def test_run_is_idempotent_for_the_same_month(self):
        _mb, saving = self._member_saving('10000.00')

        first = accrue_savings_interest_for_sacco(
            self.sacco, accrual_date=ACCRUAL_DATE,
        )
        second = accrue_savings_interest_for_sacco(
            self.sacco, accrual_date=ACCRUAL_DATE,
        )

        self.assertEqual(first['savings_credited'], 1)
        self.assertEqual(second['savings_credited'], 0)
        self.assertEqual(second['skipped_already_accrued'], 1)
        self.assertEqual(
            LedgerEntry.objects.filter(
                category=LedgerEntry.Category.SAVINGS_INTEREST,
            ).count(),
            1,
        )
        saving.refresh_from_db()
        self.assertEqual(saving.amount, Decimal('10100.00'))

    def test_next_month_accrues_again(self):
        _mb, saving = self._member_saving('10000.00')

        accrue_savings_interest_for_sacco(
            self.sacco, accrual_date=ACCRUAL_DATE,
        )
        accrue_savings_interest_for_sacco(
            self.sacco, accrual_date=date(2026, 2, 1),
        )

        saving.refresh_from_db()
        # Month 1: +100 on 10000. Month 2: 1% of 10100 = 101.00.
        self.assertEqual(saving.amount, Decimal('10201.00'))
        self.assertEqual(
            LedgerEntry.objects.filter(
                category=LedgerEntry.Category.SAVINGS_INTEREST,
            ).count(),
            2,
        )

    def test_no_rate_and_zero_balance_accrue_nothing(self):
        no_rate_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.SHARE_CAPITAL,
            minimum_contribution=Decimal('0.00'),
            interest_rate=None,
        )
        _nr, no_rate = self._member_saving(
            '10000.00', savings_type=no_rate_type,
        )
        _zb, zero_balance = self._member_saving('0.00')

        result = accrue_savings_interest_for_sacco(
            self.sacco, accrual_date=ACCRUAL_DATE,
        )

        self.assertEqual(result['savings_credited'], 0)
        no_rate.refresh_from_db()
        zero_balance.refresh_from_db()
        self.assertEqual(no_rate.amount, Decimal('10000.00'))
        self.assertEqual(zero_balance.amount, Decimal('0.00'))


class AccrualTaskTests(_InterestFixture):
    def test_opted_out_sacco_is_not_processed(self):
        self.settings.savings_interest_accrual_enabled = False
        self.settings.save(update_fields=['savings_interest_accrual_enabled'])
        _mb, saving = self._member_saving('10000.00')

        result = _run_savings_interest_accrual('2026-01-01')

        self.assertEqual(result['saccos_processed'], 0)
        self.assertEqual(result['savings_credited'], 0)
        saving.refresh_from_db()
        self.assertEqual(saving.amount, Decimal('10000.00'))
        self.assertFalse(
            SystemAuditLog.objects.filter(
                action='SAVINGS_INTEREST_ACCRUED',
            ).exists()
        )

    def test_opted_in_run_credits_audits_and_heartbeats(self):
        self._member_saving('10000.00')
        self._member_saving('5000.00')

        result = accrue_savings_interest('2026-01-01')

        self.assertEqual(result['saccos_processed'], 1)
        self.assertEqual(result['savings_credited'], 2)
        self.assertEqual(result['total_interest_credited'], '150.00')
        self.assertEqual(result['period'], PERIOD)
        self.assertIn('duration_ms', result)

        audit = SystemAuditLog.objects.get(
            action='SAVINGS_INTEREST_ACCRUED',
            resource_type='Sacco',
            resource_id=str(self.sacco.id),
        )
        self.assertEqual(audit.new_values['period'], PERIOD)
        self.assertEqual(audit.new_values['savings_credited'], 2)
        self.assertEqual(audit.new_values['total_interest'], '150.00')

        heartbeat = JobHeartbeat.objects.get(
            job_name='accrue_savings_interest',
        )
        self.assertEqual(heartbeat.last_status, JobHeartbeat.Status.OK)
        self.assertEqual(heartbeat.detail['savings_credited'], 2)

    def test_run_is_scoped_to_each_sacco(self):
        # A second, opted-out SACCO with a rich savings book must be
        # untouched even though the task iterates all SACCOs.
        other = Sacco.objects.create(
            name='Other Interest SACCO',
            registration_number='INT-2',
            sector=Sacco.Sector.FINANCE,
            county='Kiambu',
        )
        SaccoSettings.objects.create(
            sacco=other, savings_interest_accrual_enabled=False,
        )
        other_type = SavingsType.objects.create(
            sacco=other,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
            interest_rate=Decimal('12.00'),
        )
        other_user = User.objects.create_user(
            email='int-other@example.com', password='StrongPass1',
        )
        other_membership = Membership.objects.create(
            user=other_user,
            sacco=other,
            status=Membership.Status.APPROVED,
            member_number='INT-O-1',
        )
        other_saving = Saving.objects.create(
            membership=other_membership,
            savings_type=other_type,
            amount=Decimal('99999.00'),
            status=Saving.Status.ACTIVE,
        )
        self._member_saving('10000.00')

        _run_savings_interest_accrual('2026-01-01')

        other_saving.refresh_from_db()
        self.assertEqual(other_saving.amount, Decimal('99999.00'))
        self.assertFalse(
            LedgerEntry.objects.filter(
                membership=other_membership,
            ).exists()
        )
