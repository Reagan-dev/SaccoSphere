from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from notifications.models import Notification
from saccomanagement.models import Role
from saccomembership.models import Membership
from services.engines.npl_monitor import (
    get_arrears_bucket,
    resolve_cleared_npl_flags,
)
from services.models import Loan, NPLFlag, RepaymentSchedule
from services.tasks import flag_npl_arrears


class NPLMonitorTests(TestCase):
    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='NPL SACCO',
            registration_number='NPL001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.other_sacco = Sacco.objects.create(
            name='Other NPL SACCO',
            registration_number='NPL002',
            sector=Sacco.Sector.FINANCE,
            county='Kiambu',
        )
        self.admin = User.objects.create_user(
            email='npl-admin@example.com',
            password='secret',
            first_name='NPL',
            last_name='Admin',
        )
        Role.objects.create(
            user=self.admin,
            sacco=self.sacco,
            name=Role.SACCO_ADMIN,
        )
        self.member = User.objects.create_user(
            email='npl-member@example.com',
            password='secret',
            first_name='NPL',
            last_name='Member',
            phone_number='254712345678',
        )
        self.membership = Membership.objects.create(
            user=self.member,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='NPL-M001',
        )

    def test_get_arrears_bucket_uses_earliest_unpaid_schedule(self):
        loan = self._loan(Decimal('12000.00'))
        self._schedule(
            loan=loan,
            instalment_number=1,
            days_ago=65,
            status=RepaymentSchedule.Status.PENDING,
        )
        self._schedule(
            loan=loan,
            instalment_number=2,
            days_ago=95,
            status=RepaymentSchedule.Status.PAID,
        )

        self.assertEqual(get_arrears_bucket(loan), 60)

    def test_resolve_cleared_npl_flags_marks_open_flags_resolved(self):
        loan = self._loan(Decimal('12000.00'))
        schedule_item = self._schedule(
            loan=loan,
            instalment_number=1,
            days_ago=45,
            status=RepaymentSchedule.Status.PENDING,
        )
        flag = NPLFlag.objects.create(
            loan=loan,
            threshold_days=NPLFlag.ThresholdDays.THIRTY,
        )

        schedule_item.status = RepaymentSchedule.Status.PAID
        schedule_item.save(update_fields=['status'])
        resolved_count = resolve_cleared_npl_flags(loan)

        flag.refresh_from_db()
        self.assertEqual(resolved_count, 1)
        self.assertTrue(flag.resolved)
        self.assertIsNotNone(flag.resolved_at)

    @patch('services.tasks.send_sms_notification')
    def test_flag_npl_arrears_creates_flag_and_notifications(self, sms_mock):
        loan = self._loan(Decimal('12000.00'))
        self._schedule(
            loan=loan,
            instalment_number=1,
            days_ago=65,
            status=RepaymentSchedule.Status.PENDING,
        )

        result = flag_npl_arrears()

        self.assertEqual(result['checked'], 1)
        self.assertEqual(result['flags_created'], 1)
        flag = NPLFlag.objects.get(loan=loan)
        self.assertEqual(flag.threshold_days, 60)
        self.assertEqual(Notification.objects.count(), 2)
        self.assertTrue(
            Notification.objects.filter(
                user=self.admin,
                category=Notification.Category.NPL_WARNING,
                message__contains='65 days overdue',
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                user=self.member,
                category=Notification.Category.LOAN,
                message__contains='loan agreement',
            ).exists()
        )
        sms_mock.assert_called_once()

    def test_npl_dashboard_returns_sacco_scoped_counts_and_ratio(self):
        flagged_loan = self._loan(Decimal('10000.00'))
        self._loan(Decimal('30000.00'))
        other_membership = self._membership(
            email='other-npl-member@example.com',
            sacco=self.other_sacco,
            member_number='NPL-M002',
        )
        other_loan = self._loan(
            Decimal('50000.00'),
            membership=other_membership,
        )
        NPLFlag.objects.create(
            loan=flagged_loan,
            threshold_days=NPLFlag.ThresholdDays.THIRTY,
        )
        NPLFlag.objects.create(
            loan=other_loan,
            threshold_days=NPLFlag.ThresholdDays.NINETY,
        )

        client = APIClient()
        client.force_authenticate(user=self.admin)
        response = client.get('/api/v1/management/npl/')

        self.assertEqual(response.status_code, 200)
        data = response.json()['data']
        self.assertEqual(data['unresolved_counts'], {
            '30': 1,
            '60': 0,
            '90': 0,
        })
        self.assertEqual(data['npl_outstanding_balance'], '10000.00')
        self.assertEqual(data['active_outstanding_balance'], '40000.00')
        self.assertEqual(data['npl_ratio'], '0.2500')

    @patch('services.tasks.send_sms_notification')
    def test_staged_npl_flow_matches_manual_verification(self, sms_mock):
        loan = self._loan(Decimal('10000.00'))
        current_loan = self._loan(Decimal('30000.00'))
        schedule_item = self._schedule(
            loan=loan,
            instalment_number=1,
            days_ago=35,
            status=RepaymentSchedule.Status.PENDING,
        )

        first_result = flag_npl_arrears()

        self.assertEqual(first_result['flags_created'], 1)
        self.assertTrue(
            NPLFlag.objects.filter(
                loan=loan,
                threshold_days=NPLFlag.ThresholdDays.THIRTY,
                resolved=False,
            ).exists()
        )
        self.assertEqual(NPLFlag.objects.filter(loan=loan).count(), 1)
        self.assertEqual(
            NPLFlag.objects.filter(
                loan=loan,
                threshold_days=NPLFlag.ThresholdDays.THIRTY,
            ).count(),
            1,
        )
        self.assertTrue(
            Notification.objects.filter(
                user=self.admin,
                category=Notification.Category.NPL_WARNING,
                message__contains='35 days overdue',
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                user=self.member,
                category=Notification.Category.LOAN,
                message__contains='falling behind',
            ).exists()
        )

        schedule_item.due_date = (
            timezone.localdate() - timezone.timedelta(days=65)
        )
        schedule_item.save(update_fields=['due_date'])
        second_result = flag_npl_arrears()

        self.assertEqual(second_result['flags_created'], 1)
        self.assertTrue(
            NPLFlag.objects.filter(
                loan=loan,
                threshold_days=NPLFlag.ThresholdDays.SIXTY,
                resolved=False,
            ).exists()
        )
        self.assertEqual(
            NPLFlag.objects.filter(
                loan=loan,
                threshold_days=NPLFlag.ThresholdDays.THIRTY,
            ).count(),
            1,
        )

        client = APIClient()
        client.force_authenticate(user=self.admin)
        response = client.get('/api/v1/management/npl/')

        self.assertEqual(response.status_code, 200)
        data = response.json()['data']
        self.assertEqual(data['unresolved_counts'], {
            '30': 1,
            '60': 1,
            '90': 0,
        })
        self.assertEqual(data['npl_outstanding_balance'], '10000.00')
        self.assertEqual(data['active_outstanding_balance'], '40000.00')
        self.assertEqual(data['npl_ratio'], '0.2500')

        schedule_item.status = RepaymentSchedule.Status.PAID
        schedule_item.save(update_fields=['status'])
        paid_result = flag_npl_arrears()

        self.assertEqual(paid_result['flags_resolved'], 2)
        self.assertEqual(
            NPLFlag.objects.filter(loan=loan, resolved=False).count(),
            0,
        )
        self.assertEqual(
            NPLFlag.objects.filter(loan=loan, resolved=True).count(),
            2,
        )
        self.assertEqual(
            Loan.objects.filter(id=current_loan.id).count(),
            1,
        )
        self.assertEqual(sms_mock.call_count, 2)

    def _membership(self, email, sacco=None, member_number='NPL-M999'):
        user = User.objects.create_user(
            email=email,
            password='secret',
            first_name='Loan',
            last_name='Member',
        )
        return Membership.objects.create(
            user=user,
            sacco=sacco or self.sacco,
            status=Membership.Status.APPROVED,
            member_number=member_number,
        )

    def _loan(self, outstanding_balance, membership=None):
        return Loan.objects.create(
            membership=membership or self.membership,
            amount=outstanding_balance,
            interest_rate=Decimal('12.00'),
            term_months=12,
            outstanding_balance=outstanding_balance,
            status=Loan.Status.ACTIVE,
        )

    def _schedule(self, loan, instalment_number, days_ago, status):
        return RepaymentSchedule.objects.create(
            loan=loan,
            instalment_number=instalment_number,
            due_date=timezone.localdate() - timezone.timedelta(days=days_ago),
            amount=Decimal('1000.00'),
            principal=Decimal('900.00'),
            interest=Decimal('100.00'),
            balance_after=Decimal('11000.00'),
            status=status,
        )
