"""Purge M-Pesa Callback rows past their retention period.

Callback.raw_payload holds the full provider callback JSON, including
the member's phone number and, for B2C, their name - personal data
under Kenya's DPA 2019. It is encrypted at rest
(accounts.models.EncryptedJSONField), but encryption is not a retention
policy: this command deletes rows once CALLBACK_RETENTION_DAYS has
passed since they were received.

Unlike CRBCheck/KYCVerification (see
purge_expired_crb_raw_response/cleanup_expired_kyc), a Callback row
carries no separately-valuable decision facts once raw_payload is gone
- processed/processing_error were already surfaced onto the linked
Transaction/MpesaTransaction and the SystemAuditLog trail at process
time - so the whole row is deleted, not anonymized in place.
"""

import logging

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from payments.models import Callback
from saccomanagement.audit_logger import log_audit


logger = logging.getLogger('payments.callback_retention')


class Command(BaseCommand):
    help = (
        'Delete Callback rows received more than CALLBACK_RETENTION_DAYS '
        "ago. raw_payload carries member PII (phone number, and for "
        'B2C their name); this is the retention sweep for it.'
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
            help='Number of rows to delete per batch.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        batch_size = options['batch_size']

        retention_days = getattr(settings, 'CALLBACK_RETENTION_DAYS', None)
        if not retention_days:
            self.stdout.write(
                self.style.WARNING(
                    'CALLBACK_RETENTION_DAYS is not configured. No purge '
                    'will be performed.'
                )
            )
            return

        cutoff = timezone.now() - timezone.timedelta(days=retention_days)
        expired = Callback.objects.filter(received_at__lt=cutoff)

        total = expired.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS('No callbacks to purge.'))
            return

        self.stdout.write(
            f'Found {total} callback(s) received before '
            f'{cutoff.isoformat()} ({retention_days}-day retention).'
        )
        if dry_run:
            self.stdout.write(
                self.style.WARNING('Dry run - no changes will be made.')
            )
            for callback in expired.order_by('received_at')[:10]:
                self.stdout.write(
                    f'  - Callback {callback.id} '
                    f'(received {callback.received_at.isoformat()})'
                )
            if total > 10:
                self.stdout.write(f'  ... and {total - 10} more')
            return

        purged = 0
        while True:
            batch_ids = list(
                expired.order_by('pk').values_list(
                    'pk', flat=True,
                )[:batch_size]
            )
            if not batch_ids:
                break

            Callback.objects.filter(pk__in=batch_ids).delete()
            purged += len(batch_ids)
            self.stdout.write(f'Purged {purged}/{total}...')

        # One summary row, not one per callback: unlike a CRB check (rare,
        # one per loan application), Callback volume scales with every
        # M-Pesa transaction attempt, so per-row audit logging here would
        # itself become a second, unbounded store of purge metadata.
        log_audit(
            user=None,
            action='CALLBACK_RETENTION_PURGE',
            resource_type='Callback',
            resource_id='batch',
            new_values={
                'purged_count': purged,
                'cutoff': cutoff.isoformat(),
                'retention_days': retention_days,
            },
        )

        self.stdout.write(
            self.style.SUCCESS(
                f'Purge complete. Deleted {purged} callback(s).'
            )
        )
