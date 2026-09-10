"""Phase 8: NPL sweep - flag clearing, query bound, ComplianceFlag, HB."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import Sacco, User
from health.models import JobHeartbeat
from saccomanagement.models import ComplianceFlag, Role
from saccomembership.models import Membership
from services.engines.npl_monitor import resolve_cleared_npl_flags
from services.models import Loan, NPLFlag, RepaymentSchedule
from services.tasks import flag_npl_arrears


class _NplFixture(TestCase):
    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Sweep SACCO',
            registration_number='SWP-001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.admin = User.objects.create_user(
            email='sweep-admin@example.com',
            password='StrongPass1',
        )
        Role.objects.create(
            user=self.admin, sacco=self.sacco, name=Role.SACCO_ADMIN,
        )
        self._member_seq = 0

    def _member(self):
        self._member_seq += 1
        user = User.objects.create_user(
            email=f'sweep-m{self._member_seq}@example.com',
            phone_number=f'25472000{self._member_seq:04d}',
            password='StrongPass1',
        )
        return Membership.objects.create(
            user=user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number=f'SWP-M-{self._member_seq:03d}',
            approved_date=timezone.now(),
        )

    def _loan(self, status=Loan.Status.ACTIVE, amount=Decimal('12000.00')):
        return Loan.objects.create(
            membership=self._member(),
            amount=amount,
            interest_rate=Decimal('12.00'),
            term_months=12,
            outstanding_balance=amount,
            status=status,
        )

    def _instalment(self, loan, number, due_offset_days, status):
        """due_offset_days: negative = past due, positive = future."""
        return RepaymentSchedule.objects.create(
            loan=loan,
            instalment_number=number,
            due_date=timezone.localdate() + timedelta(days=due_offset_days),
            amount=Decimal('1000.00'),
            principal=Decimal('900.00'),
            interest=Decimal('100.00'),
            balance_after=Decimal('11000.00'),
            status=status,
        )


class MultiInstalmentFlagClearsOnArrearsTest(_NplFixture):
    def test_resolve_ignores_future_pending_instalments(self):
        loan = self._loan()
        # arrears, now paid
        self._instalment(loan, 1, -95, RepaymentSchedule.Status.PAID)
        # future instalment, still PENDING - must NOT keep the flag open
        self._instalment(loan, 2, 25, RepaymentSchedule.Status.PENDING)
        NPLFlag.objects.create(
            loan=loan, threshold_days=NPLFlag.ThresholdDays.NINETY,
        )

        resolved = resolve_cleared_npl_flags(loan)

        self.assertEqual(resolved, 1)
        self.assertTrue(NPLFlag.objects.get(loan=loan).resolved)

    @patch('services.tasks.send_sms_notification')
    def test_sweep_clears_flag_when_arrears_paid_before_full_payoff(
        self, _sms,
    ):
        loan = self._loan()
        past_1 = self._instalment(
            loan, 1, -95, RepaymentSchedule.Status.PENDING,
        )
        past_2 = self._instalment(
            loan, 2, -35, RepaymentSchedule.Status.PENDING,
        )
        # a real future instalment the member has NOT paid yet
        self._instalment(loan, 3, 30, RepaymentSchedule.Status.PENDING)

        first = flag_npl_arrears()
        self.assertEqual(first['flags_created'], 1)
        self.assertFalse(NPLFlag.objects.get(loan=loan).resolved)

        # Member clears the two past-due instalments only.
        for inst in (past_1, past_2):
            inst.status = RepaymentSchedule.Status.PAID
            inst.paid_amount = inst.amount
            inst.paid_date = timezone.localdate()
            inst.save(
                update_fields=['status', 'paid_amount', 'paid_date'],
            )

        second = flag_npl_arrears()

        self.assertEqual(second['flags_resolved'], 1)
        self.assertTrue(NPLFlag.objects.get(loan=loan).resolved)
        # loan still has an outstanding (future) instalment
        self.assertTrue(
            RepaymentSchedule.objects.filter(
                loan=loan, status=RepaymentSchedule.Status.PENDING,
            ).exists()
        )


class NplSweepQueryBoundTest(_NplFixture):
    @patch('services.tasks.send_sms_notification')
    def test_query_count_is_independent_of_healthy_portfolio_size(self, _sms):
        # One delinquent loan, flag already staged so the measured runs
        # do no per-new-flag notification work.
        bad_loan = self._loan()
        self._instalment(loan=bad_loan, number=1, due_offset_days=-95,
                         status=RepaymentSchedule.Status.PENDING)
        flag_npl_arrears()  # stage the flag + ComplianceFlag

        def healthy_loan():
            loan = self._loan()
            self._instalment(loan, 1, 30, RepaymentSchedule.Status.PENDING)
            self._instalment(loan, 2, 60, RepaymentSchedule.Status.PENDING)

        for _ in range(3):
            healthy_loan()
        with CaptureQueriesContext(connection) as small:
            flag_npl_arrears()

        for _ in range(25):
            healthy_loan()
        with CaptureQueriesContext(connection) as large:
            flag_npl_arrears()

        self.assertEqual(
            len(large.captured_queries),
            len(small.captured_queries),
            msg=(
                'NPL sweep query count grew with healthy-loan count: '
                f'{len(small.captured_queries)} -> '
                f'{len(large.captured_queries)}'
            ),
        )


class SevereArrearsComplianceFlagTest(_NplFixture):
    @patch('services.tasks.send_sms_notification')
    def test_ninety_day_arrears_emits_npl_compliance_flag(self, _sms):
        loan = self._loan(amount=Decimal('20000.00'))
        self._instalment(loan, 1, -95, RepaymentSchedule.Status.PENDING)

        result = flag_npl_arrears()

        self.assertEqual(result['compliance_flags'], 1)
        flag = ComplianceFlag.objects.get(
            sacco=self.sacco,
            flag_type=ComplianceFlag.FlagType.NPL,
        )
        self.assertEqual(flag.status, ComplianceFlag.Status.OPEN)
        self.assertEqual(flag.severity, ComplianceFlag.Severity.HIGH)
        self.assertIn('90+ days', flag.description)
        self.assertEqual(flag.metadata['severe_loan_count'], 1)

    @patch('services.tasks.send_sms_notification')
    def test_sixty_day_arrears_does_not_emit_compliance_flag(self, _sms):
        loan = self._loan()
        self._instalment(loan, 1, -65, RepaymentSchedule.Status.PENDING)

        result = flag_npl_arrears()

        self.assertEqual(result['compliance_flags'], 0)
        self.assertFalse(
            ComplianceFlag.objects.filter(
                flag_type=ComplianceFlag.FlagType.NPL,
            ).exists()
        )
        # early-warning NPLFlag still fires
        self.assertTrue(NPLFlag.objects.filter(loan=loan).exists())

    @patch('services.tasks.send_sms_notification')
    def test_repeat_sweep_updates_one_flag_not_many(self, _sms):
        loan = self._loan()
        self._instalment(loan, 1, -100, RepaymentSchedule.Status.PENDING)

        flag_npl_arrears()
        flag_npl_arrears()

        flags = ComplianceFlag.objects.filter(
            flag_type=ComplianceFlag.FlagType.NPL,
        )
        self.assertEqual(flags.count(), 1)
        self.assertEqual(flags.first().metadata['occurrence_count'], 2)


class NplSweepHeartbeatTest(_NplFixture):
    @patch('services.tasks.send_sms_notification')
    def test_sweep_writes_heartbeat(self, _sms):
        self.assertFalse(
            JobHeartbeat.objects.filter(job_name='flag_npl_arrears').exists()
        )

        flag_npl_arrears()

        hb = JobHeartbeat.objects.get(job_name='flag_npl_arrears')
        self.assertEqual(hb.last_status, JobHeartbeat.Status.OK)
        self.assertIn('checked', hb.detail)

    @patch(
        'services.tasks._run_npl_arrears_sweep',
        side_effect=RuntimeError('boom'),
    )
    def test_sweep_failure_records_error_heartbeat(self, _run):
        with self.assertRaises(Exception):
            flag_npl_arrears()

        hb = JobHeartbeat.objects.get(job_name='flag_npl_arrears')
        self.assertEqual(hb.last_status, JobHeartbeat.Status.ERROR)
        self.assertEqual(hb.detail.get('error'), 'boom')


class JobHealthEndpointTest(TestCase):
    def test_missing_heartbeat_returns_503(self):
        response = self.client.get('/health/jobs/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()['jobs']['flag_npl_arrears']['status'], 'missing',
        )

    def test_fresh_heartbeat_returns_200(self):
        from health.monitored_jobs import MONITORED_JOBS

        for job_name in MONITORED_JOBS:
            JobHeartbeat.record(job_name)
        response = self.client.get('/health/jobs/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')

    def test_stale_heartbeat_returns_503(self):
        hb = JobHeartbeat.record('flag_npl_arrears')
        JobHeartbeat.objects.filter(pk=hb.pk).update(
            last_run_at=timezone.now() - timedelta(days=3),
        )
        response = self.client.get('/health/jobs/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()['jobs']['flag_npl_arrears']['status'], 'stale',
        )

    def test_errored_heartbeat_returns_503(self):
        JobHeartbeat.record(
            'flag_npl_arrears', status=JobHeartbeat.Status.ERROR,
        )
        response = self.client.get('/health/jobs/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()['jobs']['flag_npl_arrears']['status'], 'errored',
        )
