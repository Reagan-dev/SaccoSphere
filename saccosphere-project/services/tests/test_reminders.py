"""Tests for repayment reminder utilities and management command."""

from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from accounts.models import Sacco, User
from notifications.models import Notification
from saccomembership.models import Membership
from services.models import Loan, ReminderLog, RepaymentSchedule
from services.reminder_utils import (
    get_upcoming_instalments,
    send_repayment_reminder,
)


class ReminderTestMixin:
    """Create common SACCO loan reminder test data."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='reminder@example.com',
            first_name='Reminder',
            last_name='Member',
            password='testpass123',
        )
        self.sacco = Sacco.objects.create(
            name='Reminder SACCO',
            registration_number='REM001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='REM001-M001',
        )
        self.loan = Loan.objects.create(
            membership=self.membership,
            amount=Decimal('12000.00'),
            interest_rate=Decimal('12.00'),
            term_months=12,
            outstanding_balance=Decimal('12000.00'),
            status=Loan.Status.ACTIVE,
        )

    def create_schedule_item(
        self,
        instalment_number,
        due_date,
        status=RepaymentSchedule.Status.PENDING,
    ):
        return RepaymentSchedule.objects.create(
            loan=self.loan,
            instalment_number=instalment_number,
            due_date=due_date,
            amount=Decimal('1000.00'),
            principal=Decimal('900.00'),
            interest=Decimal('100.00'),
            balance_after=Decimal('11000.00'),
            status=status,
        )


class ReminderUtilsTest(ReminderTestMixin, TestCase):
    """Test repayment reminder helper functions."""

    def test_get_upcoming_instalments_returns_correct_records(self):
        target_due_date = timezone.localdate() + timezone.timedelta(days=3)
        other_due_date = timezone.localdate() + timezone.timedelta(days=2)
        target_item = self.create_schedule_item(1, target_due_date)
        self.create_schedule_item(2, other_due_date)
        self.create_schedule_item(
            3,
            target_due_date,
            status=RepaymentSchedule.Status.PAID,
        )

        queryset = get_upcoming_instalments(days_ahead=3)

        self.assertQuerySetEqual(queryset, [target_item])

    def test_send_reminder_creates_notification(self):
        due_date = timezone.localdate() + timezone.timedelta(days=3)
        schedule_item = self.create_schedule_item(1, due_date)

        result = send_repayment_reminder(schedule_item)

        self.assertTrue(result)
        notification = Notification.objects.get()
        self.assertEqual(notification.user, self.user)
        self.assertEqual(notification.category, Notification.Category.LOAN)
        self.assertEqual(
            notification.title,
            'Loan Repayment Due in 3 Days',
        )
        self.assertEqual(
            notification.action_url,
            f'/loans/{self.loan.id}/schedule/',
        )

    def test_duplicate_reminder_not_sent(self):
        due_date = timezone.localdate() + timezone.timedelta(days=3)
        schedule_item = self.create_schedule_item(1, due_date)

        first_result = send_repayment_reminder(schedule_item)
        second_result = send_repayment_reminder(schedule_item)

        self.assertTrue(first_result)
        self.assertFalse(second_result)
        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(ReminderLog.objects.count(), 1)


class ManagementCommandTest(ReminderTestMixin, TestCase):
    """Test the repayment reminder management command."""

    def test_dry_run_does_not_create_notifications(self):
        due_date = timezone.localdate() + timezone.timedelta(days=3)
        self.create_schedule_item(1, due_date)
        out = StringIO()

        call_command('send_repayment_reminders', '--dry-run', stdout=out)

        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(ReminderLog.objects.count(), 0)
        self.assertIn(
            'Sent 0 reminders, 0 overdue alerts, 0 errors',
            out.getvalue(),
        )
