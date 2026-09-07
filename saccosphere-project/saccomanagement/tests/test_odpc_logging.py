"""Tests for the ODPC data-access audit log write path (odpc_logging.py).

Covers: bounded retry on transient database errors, immediate failure on
non-transient errors, ConsentLogWriteError surfacing once retries are
exhausted (never a silent None), the structured metric log lines, and that
the two production call sites (DataAccessMixin, ledger statement access)
swallow ConsentLogWriteError rather than letting an audit-log failure break
the caller's primary operation.
"""

from unittest.mock import patch

from django.db.utils import OperationalError
from django.test import TestCase

from accounts.models import User
from saccomanagement.models import DataConsentLog
from saccomanagement.odpc_logging import (
    MAX_WRITE_ATTEMPTS,
    ConsentLogWriteError,
    DataAccessMixin,
    create_data_consent_log,
)


class CreateDataConsentLogRetryTestCase(TestCase):
    """Test retry/failure behavior of create_data_consent_log."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='odpc-user@example.com',
            phone_number='+254700000010',
            password='testpass123',
        )
        self.admin = User.objects.create_user(
            email='odpc-admin@example.com',
            phone_number='+254700000011',
            password='testpass123',
            is_staff=True,
        )

    def test_transient_failure_retries_then_succeeds(self):
        """A transient DB error is retried and can still succeed."""
        real_create = DataConsentLog.objects.create
        calls = {'count': 0}

        def flaky_create(**kwargs):
            calls['count'] += 1
            if calls['count'] < MAX_WRITE_ATTEMPTS:
                raise OperationalError('connection lost')
            return real_create(**kwargs)

        with patch('saccomanagement.odpc_logging.time.sleep'):
            with patch.object(
                DataConsentLog.objects, 'create', side_effect=flaky_create,
            ):
                log = create_data_consent_log(
                    user=self.user,
                    accessed_by=self.admin,
                    data_type='MEMBER_PROFILE',
                    reason='Account review',
                )

        self.assertIsInstance(log, DataConsentLog)
        self.assertEqual(calls['count'], MAX_WRITE_ATTEMPTS)
        self.assertTrue(
            DataConsentLog.objects.filter(id=log.id).exists()
        )

    def test_persistent_transient_failure_raises_after_max_attempts(self):
        """Exhausting all retries raises ConsentLogWriteError, not None."""
        with patch('saccomanagement.odpc_logging.time.sleep'):
            with patch.object(
                DataConsentLog.objects,
                'create',
                side_effect=OperationalError('connection lost'),
            ) as mock_create:
                with self.assertRaises(ConsentLogWriteError) as cm:
                    create_data_consent_log(
                        user=self.user,
                        accessed_by=self.admin,
                        data_type='MEMBER_PROFILE',
                        reason='Account review',
                    )

        self.assertEqual(mock_create.call_count, MAX_WRITE_ATTEMPTS)
        self.assertEqual(cm.exception.data_type, 'MEMBER_PROFILE')
        self.assertEqual(cm.exception.user_id, self.user.id)
        self.assertEqual(DataConsentLog.objects.count(), 0)

    def test_non_transient_failure_raises_immediately_without_retry(self):
        """A non-transient error is not retried - it wastes no retry budget."""
        with patch.object(
            DataConsentLog.objects,
            'create',
            side_effect=TypeError('unexpected shape'),
        ) as mock_create:
            with self.assertRaises(ConsentLogWriteError):
                create_data_consent_log(
                    user=self.user,
                    accessed_by=self.admin,
                    data_type='MEMBER_PROFILE',
                    reason='Account review',
                )

        self.assertEqual(mock_create.call_count, 1)

    def test_success_emits_metric(self):
        """A successful write emits a consent_audit_log_write_success metric."""
        with self.assertLogs('saccosphere.metrics', level='INFO') as cm:
            create_data_consent_log(
                user=self.user,
                accessed_by=self.admin,
                data_type='MEMBER_PROFILE',
                reason='Account review',
            )

        self.assertTrue(
            any('consent_audit_log_write_success' in line for line in cm.output)
        )

    def test_failure_emits_metric(self):
        """An exhausted-retry write emits a consent_audit_log_write_failure metric."""
        with patch('saccomanagement.odpc_logging.time.sleep'):
            with patch.object(
                DataConsentLog.objects,
                'create',
                side_effect=OperationalError('connection lost'),
            ):
                with self.assertLogs('saccosphere.metrics', level='INFO') as cm:
                    with self.assertRaises(ConsentLogWriteError):
                        create_data_consent_log(
                            user=self.user,
                            accessed_by=self.admin,
                            data_type='MEMBER_PROFILE',
                            reason='Account review',
                        )

        self.assertTrue(
            any('consent_audit_log_write_failure' in line for line in cm.output)
        )


class DataAccessMixinConsentFailureTestCase(TestCase):
    """Test that DataAccessMixin does not propagate ConsentLogWriteError."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='mixin-member@example.com',
            phone_number='+254700000012',
            password='testpass123',
        )
        self.admin = User.objects.create_user(
            email='mixin-admin@example.com',
            phone_number='+254700000013',
            password='testpass123',
            is_staff=True,
        )

    def test_log_object_access_swallows_write_failure(self):
        """An exhausted-retry audit-log write does not raise out of the mixin."""
        mixin = DataAccessMixin()
        mixin.data_access_type = 'MEMBER_PROFILE'
        mixin.data_access_reason = 'Account review'

        request = type('_Request', (), {'user': self.admin})()
        obj = type('_Obj', (), {'user': self.user})()

        with patch(
            'saccomanagement.odpc_logging.create_data_consent_log',
            side_effect=ConsentLogWriteError(
                'boom', data_type='MEMBER_PROFILE', user_id=self.user.id,
            ),
        ):
            # Must not raise.
            mixin._log_object_access(obj, request)


class StatementAccessLoggingFailureTestCase(TestCase):
    """Test that a statement download survives an audit-log write failure."""

    def test_record_statement_access_swallows_write_failure(self):
        from accounts.models import Sacco
        from ledger.engines.statement_builder import _record_statement_access
        from saccomembership.models import Membership

        user = User.objects.create_user(
            email='statement-member@example.com',
            phone_number='+254700000014',
            password='testpass123',
        )
        sacco = Sacco.objects.create(
            name='ODPC Test SACCO',
            registration_number='ODPC001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        membership = Membership.objects.create(
            user=user,
            sacco=sacco,
            status=Membership.Status.APPROVED,
            member_number='ODPC-M001',
        )

        with patch('saccomanagement.odpc_logging.time.sleep'):
            with patch.object(
                DataConsentLog.objects,
                'create',
                side_effect=OperationalError('connection lost'),
            ):
                # Must not raise even though every retry attempt fails.
                _record_statement_access(membership)

        self.assertEqual(DataConsentLog.objects.count(), 0)
