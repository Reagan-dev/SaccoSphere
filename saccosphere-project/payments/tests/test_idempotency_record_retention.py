"""Retention sweep for MpesaIdempotencyRecord / SavingsWithdrawalIdempotencyKey.

Both tables exist only to make a callback or withdrawal-initiation
retry idempotent - one row per successfully processed request, forever,
unless swept. Neither previously had a retention sweep at all, so the
tables grew unbounded; this sweep (mirroring purge_expired_callbacks)
closes that gap.
"""

from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from accounts.models import Sacco, User
from payments.models import MpesaIdempotencyRecord, SavingsWithdrawalIdempotencyKey
from payments.tasks import purge_expired_idempotency_records as purge_task
from saccomembership.models import Membership
from services.models import Saving, SavingsType


class IdempotencyRecordRetentionSweepTests(TestCase):
    def setUp(self):
        sacco = Sacco.objects.create(
            name='Idempotency Retention SACCO',
            registration_number='IDEMRET-001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        user = User.objects.create_user(
            email='idem-retention-member@example.com',
            password='StrongPass1',
        )
        membership = Membership.objects.create(
            user=user,
            sacco=sacco,
            status=Membership.Status.APPROVED,
            member_number='IDEMRET-M1',
        )
        savings_type = SavingsType.objects.create(
            sacco=sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        self.saving = Saving.objects.create(
            membership=membership,
            savings_type=savings_type,
            amount=Decimal('500.00'),
            total_contributions=Decimal('500.00'),
            status=Saving.Status.ACTIVE,
        )
        self.membership = membership

    def _mpesa_record(self, *, processed_days_ago, external_reference_id):
        record = MpesaIdempotencyRecord.objects.create(
            kind=MpesaIdempotencyRecord.Kind.STK,
            external_reference_id=external_reference_id,
        )
        MpesaIdempotencyRecord.objects.filter(pk=record.pk).update(
            processed_at=(
                timezone.now() - timezone.timedelta(days=processed_days_ago)
            ),
        )
        record.refresh_from_db()
        return record

    def _withdrawal_key(self, *, created_days_ago, key):
        record = SavingsWithdrawalIdempotencyKey.objects.create(
            key=key,
            membership=self.membership,
            saving=self.saving,
            amount=Decimal('100.00'),
        )
        SavingsWithdrawalIdempotencyKey.objects.filter(pk=record.pk).update(
            created_at=(
                timezone.now() - timezone.timedelta(days=created_days_ago)
            ),
        )
        record.refresh_from_db()
        return record

    def test_sweep_removes_expired_leaves_fresh_untouched(self):
        expired_mpesa = self._mpesa_record(
            processed_days_ago=181, external_reference_id='ws_CO_EXPIRED',
        )
        fresh_mpesa = self._mpesa_record(
            processed_days_ago=1, external_reference_id='ws_CO_FRESH',
        )
        expired_withdrawal = self._withdrawal_key(
            created_days_ago=181, key='expired-key',
        )
        fresh_withdrawal = self._withdrawal_key(
            created_days_ago=1, key='fresh-key',
        )

        with self.settings(MPESA_IDEMPOTENCY_RETENTION_DAYS=180):
            call_command('purge_expired_idempotency_records')

        self.assertFalse(
            MpesaIdempotencyRecord.objects.filter(
                pk=expired_mpesa.pk,
            ).exists()
        )
        self.assertTrue(
            MpesaIdempotencyRecord.objects.filter(pk=fresh_mpesa.pk).exists()
        )
        self.assertFalse(
            SavingsWithdrawalIdempotencyKey.objects.filter(
                pk=expired_withdrawal.pk,
            ).exists()
        )
        self.assertTrue(
            SavingsWithdrawalIdempotencyKey.objects.filter(
                pk=fresh_withdrawal.pk,
            ).exists()
        )

    def test_dry_run_makes_no_changes(self):
        expired = self._mpesa_record(
            processed_days_ago=181, external_reference_id='ws_CO_DRYRUN',
        )

        output = StringIO()
        with self.settings(MPESA_IDEMPOTENCY_RETENTION_DAYS=180):
            call_command(
                'purge_expired_idempotency_records',
                '--dry-run',
                stdout=output,
            )

        self.assertTrue(
            MpesaIdempotencyRecord.objects.filter(pk=expired.pk).exists()
        )
        self.assertIn('Dry run', output.getvalue())

    def test_sweep_is_a_noop_when_retention_unconfigured(self):
        expired = self._mpesa_record(
            processed_days_ago=9999, external_reference_id='ws_CO_NOCONFIG',
        )

        with self.settings(MPESA_IDEMPOTENCY_RETENTION_DAYS=None):
            call_command('purge_expired_idempotency_records')

        self.assertTrue(
            MpesaIdempotencyRecord.objects.filter(pk=expired.pk).exists()
        )

    def test_task_wrapper_invokes_the_command(self):
        expired = self._mpesa_record(
            processed_days_ago=181, external_reference_id='ws_CO_TASK',
        )

        with self.settings(MPESA_IDEMPOTENCY_RETENTION_DAYS=180):
            result = purge_task()

        self.assertFalse(
            MpesaIdempotencyRecord.objects.filter(pk=expired.pk).exists()
        )
        self.assertIn('Deleted 1', result)
