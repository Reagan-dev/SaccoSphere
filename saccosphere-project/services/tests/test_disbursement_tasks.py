"""Tests for disbursement-related Celery tasks."""

from decimal import Decimal
from unittest.mock import patch, MagicMock, PropertyMock

from django.test import TestCase
from django.utils import timezone

from accounts.models import Sacco, User
from payments.models import Callback, PaymentProvider, Transaction
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


class OnDisbursementB2CCallbackCallbackBookkeepingTests(TestCase):
    """on_disbursement_b2c_callback and payments.tasks.process_b2c_callback_task
    both act on a Callback row payments.views persists before dispatching
    either one - this locks in that the disbursement path also marks its
    Callback processed, instead of leaving it stuck at processed=False
    forever (the two paths previously drifted on this)."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='disb-callback@example.com',
            phone_number='254700000444',
            password='testpass123',
        )
        self.sacco = Sacco.objects.create(
            name='Disbursement Callback SACCO',
            registration_number='DISBCB01',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
            payment_ready=True,
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='DISBCB-M-001',
            approved_date=timezone.now(),
        )
        self.loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Callback Test Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=12,
            min_amount=Decimal('100.00'),
        )
        self.provider = PaymentProvider.objects.create(
            name='M-Pesa',
            provider_type=PaymentProvider.ProviderType.MPESA,
            is_active=True,
        )
        self.transaction = Transaction.objects.create(
            provider=self.provider,
            user=self.user,
            sacco=self.sacco,
            reference='DISBCB-TXN-001',
            transaction_type=Transaction.TransactionType.LOAN_DISBURSEMENT,
            amount=Decimal('950.00'),
            gross_amount=Decimal('1000.00'),
            platform_fee=Decimal('50.00'),
            status=Transaction.Status.SENT,
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
            disbursement_transaction=self.transaction,
            mpesa_conversation_id='DISBCB-CONV-001',
        )
        self.callback = Callback.objects.create(
            transaction=self.transaction,
            provider=self.provider,
            raw_payload={'placeholder': True},
            processed=False,
        )

    @patch('services.tasks.send_disbursement_confirmation_request.delay')
    def test_success_marks_callback_processed(self, _delay):
        from services.tasks import on_disbursement_b2c_callback

        on_disbursement_b2c_callback(
            str(self.loan.id),
            {
                'ResultCode': 0,
                'TransactionID': 'MPESA-RECEIPT-001',
            },
            callback_id=str(self.callback.id),
        )

        self.callback.refresh_from_db()
        self.assertTrue(self.callback.processed)
        self.assertIsNotNone(self.callback.processed_at)

    @patch('services.tasks.notify_user_task.delay')
    def test_failure_also_marks_callback_processed(self, _delay):
        from services.tasks import on_disbursement_b2c_callback

        on_disbursement_b2c_callback(
            str(self.loan.id),
            {
                'ResultCode': 1,
                'ResultDesc': 'Insufficient funds in the utility account.',
            },
            callback_id=str(self.callback.id),
        )

        self.callback.refresh_from_db()
        self.assertTrue(self.callback.processed)

    @patch('services.tasks.send_disbursement_confirmation_request.delay')
    def test_already_disbursed_short_circuit_marks_callback_processed(
        self, _delay,
    ):
        from services.tasks import on_disbursement_b2c_callback

        self.loan.disbursement_status = Loan.DisbursementStatus.DISBURSED
        self.loan.save(update_fields=['disbursement_status'])

        on_disbursement_b2c_callback(
            str(self.loan.id),
            {'ResultCode': 0, 'TransactionID': 'MPESA-RECEIPT-002'},
            callback_id=str(self.callback.id),
        )

        self.callback.refresh_from_db()
        self.assertTrue(self.callback.processed)

    def test_missing_callback_id_does_not_raise(self):
        """callback_id is optional - omitting it must not break the
        actual disbursement processing, only skip the bookkeeping."""
        from services.tasks import on_disbursement_b2c_callback

        with patch(
            'services.tasks.send_disbursement_confirmation_request.delay',
        ):
            result = on_disbursement_b2c_callback(
                str(self.loan.id),
                {'ResultCode': 0, 'TransactionID': 'MPESA-RECEIPT-003'},
            )

        self.assertTrue(result)
        self.loan.refresh_from_db()
        self.assertEqual(
            self.loan.disbursement_status, Loan.DisbursementStatus.DISBURSED,
        )
