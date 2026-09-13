"""Purge MpesaIdempotencyRecord / SavingsWithdrawalIdempotencyKey rows.

Both tables exist purely to make a callback or withdrawal-initiation
retry idempotent: one row is written per successfully processed STK/B2C
callback (MpesaIdempotencyRecord) or per withdrawal initiation
(SavingsWithdrawalIdempotencyKey), forever, unless swept. Neither holds
PII beyond opaque identifiers, so - unlike purge_expired_callbacks -
this is a table-growth control, not a DPA one: it deletes rows once
MPESA_IDEMPOTENCY_RETENTION_DAYS has passed since they were written.
"""

import logging

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from payments.models import MpesaIdempotencyRecord, SavingsWithdrawalIdempotencyKey
from saccomanagement.audit_logger import log_audit


logger = logging.getLogger('payments.callback_retention')


class Command(BaseCommand):
    help = (
        'Delete MpesaIdempotencyRecord / SavingsWithdrawalIdempotencyKey '
        'rows older than MPESA_IDEMPOTENCY_RETENTION_DAYS.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be purged without changing anything.',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=500,
            help='Number of rows to delete per batch, per table.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        batch_size = options['batch_size']

        retention_days = getattr(
            settings, 'MPESA_IDEMPOTENCY_RETENTION_DAYS', None,
        )
        if not retention_days:
            self.stdout.write(
                self.style.WARNING(
                    'MPESA_IDEMPOTENCY_RETENTION_DAYS is not configured. '
                    'No purge will be performed.'
                )
            )
            return

        cutoff = timezone.now() - timezone.timedelta(days=retention_days)

        mpesa_purged = self._purge(
            MpesaIdempotencyRecord.objects.filter(processed_at__lt=cutoff),
            'MpesaIdempotencyRecord',
            batch_size,
            dry_run,
        )
        withdrawal_purged = self._purge(
            SavingsWithdrawalIdempotencyKey.objects.filter(
                created_at__lt=cutoff,
            ),
            'SavingsWithdrawalIdempotencyKey',
            batch_size,
            dry_run,
        )

        if dry_run:
            return

        if mpesa_purged or withdrawal_purged:
            log_audit(
                user=None,
                action='IDEMPOTENCY_RECORD_RETENTION_PURGE',
                resource_type='MpesaIdempotencyRecord',
                resource_id='batch',
                new_values={
                    'mpesa_idempotency_purged': mpesa_purged,
                    'savings_withdrawal_idempotency_purged': (
                        withdrawal_purged
                    ),
                    'cutoff': cutoff.isoformat(),
                    'retention_days': retention_days,
                },
            )

        self.stdout.write(
            self.style.SUCCESS(
                f'Purge complete. Deleted {mpesa_purged} '
                f'MpesaIdempotencyRecord and {withdrawal_purged} '
                'SavingsWithdrawalIdempotencyKey row(s).'
            )
        )

    def _purge(self, queryset, label, batch_size, dry_run):
        total = queryset.count()
        if total == 0:
            self.stdout.write(f'No expired {label} rows to purge.')
            return 0

        self.stdout.write(f'Found {total} expired {label} row(s).')
        if dry_run:
            self.stdout.write(
                self.style.WARNING(f'Dry run - {label} rows kept as-is.')
            )
            return 0

        purged = 0
        while True:
            batch_ids = list(
                queryset.order_by('pk').values_list('pk', flat=True)[
                    :batch_size
                ]
            )
            if not batch_ids:
                break

            queryset.model.objects.filter(pk__in=batch_ids).delete()
            purged += len(batch_ids)
            self.stdout.write(f'Purged {purged}/{total} {label}...')

        return purged
