"""Tests for disbursement-related Celery tasks."""

from decimal import Decimal
from unittest.mock import patch, MagicMock, PropertyMock

from django.test import TestCase
from django.utils import timezone

from accounts.models import Sacco, User
from saccomembership.models import Membership
from services.models import DisbursementAuditLog, Loan, LoanType


class DisbursementTaskTests(TestCase):
    """Validate disbursement auto-resolution and escalation behavior."""

    def setUp(self):
        """Create test fixtures for disbursement escalation tests."""
        self.user = User.objects.create_user(
            email='disbursement.test@example.com',
            phone_number='254700000333',
            password='testpass123',
        )
        self.sacco = Sacco.objects.create(
            name='Disbursement Test SACCO',
            registration_number='DISB001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
            payment_ready=True,
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='DISB-M-001',
            approved_date=timezone.now(),
        )
        self.loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Test Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=12,
            min_amount=Decimal('100.00'),
        )
        self.loan = Loan.objects.create(
            membership=self.membership,
            loan_type=self.loan_type,
            amount=Decimal('1000.00'),
            interest_rate=Decimal('12.00'),
            term_months=6,
            outstanding_balance=Decimal('0.00'),
            status=Loan.Status.APPROVED,
            disbursement_status=Loan.DisbursementStatus.INITIATED,
            mpesa_conversation_id='CONV-TEST-123',
            disbursement_initiated_at=timezone.now(),
        )

    @patch('services.tasks._notify_superadmins')
    @patch.object(Sacco, 'payment_config', new_callable=PropertyMock)
    def test_auto_resolve_sends_sentry_alert_on_escalation(
        self,
        mock_payment_config,
        mock_notify,
    ):
        """Confirm escalation triggers Sentry capture_message with context."""
        # Mock sentry_sdk
        mock_sentry = MagicMock()
        mock_sentry.set_context = MagicMock()
        mock_sentry.capture_message = MagicMock()

        # Mock payment_config
        mock_config = MagicMock()
        mock_config.is_active = True
        mock_config.has_b2c_config.return_value = True
        mock_payment_config.return_value = mock_config

        import sys
        sys.modules['sentry_sdk'] = mock_sentry

        try:
            # Call the task
            from services.tasks import auto_resolve_disbursement
            auto_resolve_disbursement(str(self.loan.id))

            # Verify Sentry was called
            mock_sentry.set_context.assert_called_once()
            context_arg = mock_sentry.set_context.call_args[0][0]
            context_data = mock_sentry.set_context.call_args[0][1]

            self.assertEqual(context_arg, 'disbursement_escalation')
            self.assertIn('sacco_id', context_data)
            self.assertIn('sacco_name', context_data)
            self.assertIn('loan_id', context_data)
            self.assertIn('conversation_id', context_data)
            self.assertIn('reason', context_data)

            mock_sentry.capture_message.assert_called_once()
            message_arg = mock_sentry.capture_message.call_args[0][0]
            level_arg = mock_sentry.capture_message.call_args[1]['level']

            self.assertIn('Escalated to Review', message_arg)
            self.assertIn(self.sacco.name, message_arg)
            self.assertIn(str(self.loan.id), message_arg)
            self.assertEqual(level_arg, 'warning')
        finally:
            sys.modules.pop('sentry_sdk', None)

    @patch('services.tasks._notify_superadmins')
    @patch.object(Sacco, 'payment_config', new_callable=PropertyMock)
    def test_auto_resolve_gracefully_handles_missing_sentry(
        self,
        mock_payment_config,
        mock_notify,
    ):
        """Confirm task continues if Sentry is not installed."""
        # Mock payment_config
        mock_config = MagicMock()
        mock_config.is_active = True
        mock_config.has_b2c_config.return_value = True
        mock_payment_config.return_value = mock_config

        import sys
        sentry_backup = sys.modules.pop('sentry_sdk', None)

        try:
            # Call the task
            from services.tasks import auto_resolve_disbursement
            auto_resolve_disbursement(str(self.loan.id))

            # Verify loan was still escalated
            self.loan.refresh_from_db()
            self.assertEqual(
                self.loan.disbursement_status,
                Loan.DisbursementStatus.UNDER_REVIEW,
            )
        finally:
            if sentry_backup is not None:
                sys.modules['sentry_sdk'] = sentry_backup

    @patch('services.tasks._notify_superadmins')
    @patch.object(Sacco, 'payment_config', new_callable=PropertyMock)
    def test_auto_resolve_sends_sentry_context_with_correct_fields(
        self,
        mock_payment_config,
        mock_notify,
    ):
        """Verify Sentry context includes all required fields for triage."""
        mock_sentry = MagicMock()
        mock_sentry.set_context = MagicMock()
        mock_sentry.capture_message = MagicMock()

        # Mock payment_config
        mock_config = MagicMock()
        mock_config.is_active = True
        mock_config.has_b2c_config.return_value = True
        mock_payment_config.return_value = mock_config

        import sys
        sys.modules['sentry_sdk'] = mock_sentry

        try:
            # Call the task
            from services.tasks import auto_resolve_disbursement
            auto_resolve_disbursement(str(self.loan.id))

            context_data = mock_sentry.set_context.call_args[0][1]

            # Verify all expected fields are present
            self.assertEqual(context_data['sacco_id'], str(self.sacco.id))
            self.assertEqual(context_data['sacco_name'], self.sacco.name)
            self.assertEqual(context_data['loan_id'], str(self.loan.id))
            self.assertEqual(
                context_data['conversation_id'],
                self.loan.mpesa_conversation_id,
            )
            self.assertEqual(
                context_data['disbursement_status'],
                self.loan.disbursement_status,
            )
            self.assertIn('24hr timeout', context_data['reason'])
        finally:
            sys.modules.pop('sentry_sdk', None)
