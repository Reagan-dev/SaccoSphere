"""Purge free-text Notification content past its retention period.

Mirrors services/management/commands/purge_expired_crb_raw_response.py:
the audit-trail-shaped fields (category, is_read, created_at,
related_object_type/related_object_id) are kept indefinitely; only the
free-text title/message/action_url - where PII/business data (loan
amounts, KYC outcomes, SACCO names) can end up - is cleared once a
notification is older than NOTIFICATION_CONTENT_RETENTION_DAYS.
"""

import logging

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from notifications.models import Notification


logger = logging.getLogger('saccosphere.notifications')

_PURGED_TITLE = '[removed]'
_PURGED_MESSAGE = (
    'This notification\'s content has been removed per the data '
    'retention policy.'
)


class Command(BaseCommand):
    help = (
        'Clear Notification.title/message/action_url for notifications '
        'older than NOTIFICATION_CONTENT_RETENTION_DAYS. Keeps category, '
        'is_read, created_at, and the related_object_* reference intact.'
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
            help='Number of records to process per batch.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        batch_size = options['batch_size']

        retention_days = settings.NOTIFICATION_CONTENT_RETENTION_DAYS
        if not retention_days:
            self.stdout.write(
                self.style.WARNING(
                    'NOTIFICATION_CONTENT_RETENTION_DAYS is not '
                    'configured. No purge will be performed.'
                )
            )
            return

        cutoff = timezone.now() - timezone.timedelta(days=retention_days)
        expired = Notification.objects.filter(
            created_at__lt=cutoff,
        ).exclude(
            title=_PURGED_TITLE,
        )

        total = expired.count()
        if total == 0:
            self.stdout.write(
                self.style.SUCCESS('No notification content to purge.')
            )
            return

        self.stdout.write(
            f'Found {total} notification(s) past retention '
            f'({retention_days} days).'
        )
        if dry_run:
            self.stdout.write(
                self.style.WARNING('Dry run - no changes will be made.')
            )
            for notification in expired[:10]:
                self.stdout.write(
                    f'  - Notification {notification.id} '
                    f'(created_at {notification.created_at})'
                )
            if total > 10:
                self.stdout.write(f'  ... and {total - 10} more')
            return

        purged = 0
        for notification in expired.iterator(chunk_size=batch_size):
            try:
                notification.title = _PURGED_TITLE
                notification.message = _PURGED_MESSAGE
                notification.action_url = None
                notification.save(update_fields=[
                    'title',
                    'message',
                    'action_url',
                ])
                purged += 1
                if purged % 100 == 0:
                    self.stdout.write(f'Purged {purged}/{total}...')
            except Exception as exc:
                logger.error(
                    'Failed to purge notification content for '
                    'notification %s',
                    notification.id,
                    exc_info=True,
                )
                self.stdout.write(
                    self.style.ERROR(
                        f'Failed to purge notification {notification.id}: '
                        f'{exc}'
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                f'Purge complete. Cleared {purged}/{total} notifications.'
            )
        )
