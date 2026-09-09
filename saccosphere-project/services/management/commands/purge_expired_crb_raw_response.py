"""Purge raw CRB provider payloads past their retention period.

Mirrors accounts/management/commands/cleanup_expired_kyc.py: the decision
facts on each CRBCheck (score, band, listed_negative, reference,
checked_by, checked_at) are kept for the loan audit trail; only
raw_response - the bulk of the third-party PII - is cleared once
raw_response_purge_at has passed.
"""

import logging

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from saccomanagement.audit_logger import log_audit
from services.models import CRBCheck


logger = logging.getLogger('services.crb_retention')


class Command(BaseCommand):
    help = (
        'Clear CRBCheck.raw_response for records past '
        'raw_response_purge_at. Keeps the score/band/reference audit '
        'trail; removes only the raw bureau payload.'
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
            default=200,
            help='Number of records to process per batch.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        batch_size = options['batch_size']

        retention_days = getattr(
            settings,
            'CRB_RAW_RESPONSE_RETENTION_DAYS',
            None,
        )
        if not retention_days:
            self.stdout.write(
                self.style.WARNING(
                    'CRB_RAW_RESPONSE_RETENTION_DAYS is not configured. '
                    'No purge will be performed.'
                )
            )
            return

        now = timezone.now()
        expired = CRBCheck.objects.filter(
            raw_response_purge_at__isnull=False,
            raw_response_purge_at__lt=now,
            raw_response__isnull=False,
        ).select_related('loan')

        total = expired.count()
        if total == 0:
            self.stdout.write(
                self.style.SUCCESS('No CRB raw responses to purge.')
            )
            return

        self.stdout.write(
            f'Found {total} CRB raw response(s) past retention '
            f'({retention_days} days).'
        )
        if dry_run:
            self.stdout.write(
                self.style.WARNING('Dry run - no changes will be made.')
            )
            for crb in expired[:10]:
                self.stdout.write(
                    f'  - CRBCheck {crb.id} (loan {crb.loan_id}, '
                    f'purge_at {crb.raw_response_purge_at})'
                )
            if total > 10:
                self.stdout.write(f'  ... and {total - 10} more')
            return

        purged = 0
        for crb in expired.iterator(chunk_size=batch_size):
            try:
                crb.raw_response = None
                # save() also nulls raw_response_purge_at.
                crb.save(update_fields=[
                    'raw_response',
                    'raw_response_purge_at',
                ])
                log_audit(
                    user=None,
                    action='CRB_RAW_RESPONSE_PURGE',
                    resource_type='CRBCheck',
                    resource_id=str(crb.id),
                    old_values={'raw_response': 'present'},
                    new_values={'raw_response': None},
                )
                purged += 1
                if purged % 100 == 0:
                    self.stdout.write(f'Purged {purged}/{total}...')
            except Exception as exc:
                logger.error(
                    'Failed to purge CRB raw response for CRBCheck %s',
                    crb.id,
                    exc_info=True,
                )
                self.stdout.write(
                    self.style.ERROR(
                        f'Failed to purge CRBCheck {crb.id}: {exc}'
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                f'Purge complete. Cleared {purged}/{total} raw responses.'
            )
        )
