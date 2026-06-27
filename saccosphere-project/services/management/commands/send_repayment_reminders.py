"""Send proactive loan repayment reminders and overdue alerts."""

import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from services.models import RepaymentSchedule
from services.reminder_utils import (
    get_upcoming_instalments,
    send_overdue_alert,
    send_repayment_reminder,
)


logger = logging.getLogger('saccosphere.reminders')


class Command(BaseCommand):
    """
    Notify members about loan repayments before due date and after default.

    The command sends in-app and SMS repayment reminders for pending
    instalments due in the configured number of days. It also sends overdue
    alerts for instalments that became overdue today, identified by an
    OVERDUE status and yesterday's due date. ReminderLog makes the command
    safe to run more than once a day.
    """

    help = 'Send loan repayment reminders and overdue alerts.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=3,
            help='How many days ahead to send repayment reminders.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Log what would be sent without creating notifications.',
        )

    def handle(self, *args, **options):
        """Run the repayment reminder workflow."""
        days = options['days']
        dry_run = options['dry_run']
        reminders_sent = 0
        overdue_alerts_sent = 0
        errors = 0

        upcoming_instalments = get_upcoming_instalments(days_ahead=days)
        overdue_instalments = self._get_overdue_instalments()

        if dry_run:
            self._log_dry_run(upcoming_instalments, overdue_instalments)
            self.stdout.write(
                'Sent 0 reminders, 0 overdue alerts, 0 errors'
            )
            return

        for schedule_item in upcoming_instalments:
            try:
                if send_repayment_reminder(schedule_item):
                    reminders_sent += 1
            except Exception:
                errors += 1
                logger.exception(
                    'Failed to send repayment reminder for '
                    'schedule_item_id=%s.',
                    schedule_item.id,
                )

        for schedule_item in overdue_instalments:
            try:
                if send_overdue_alert(schedule_item):
                    overdue_alerts_sent += 1
            except Exception:
                errors += 1
                logger.exception(
                    'Failed to send overdue alert for schedule_item_id=%s.',
                    schedule_item.id,
                )

        self.stdout.write(
            self.style.SUCCESS(
                f'Sent {reminders_sent} reminders, '
                f'{overdue_alerts_sent} overdue alerts, {errors} errors'
            )
        )

    def _get_overdue_instalments(self):
        yesterday = timezone.localdate() - timezone.timedelta(days=1)
        return RepaymentSchedule.objects.filter(
            status=RepaymentSchedule.Status.OVERDUE,
            due_date=yesterday,
        ).select_related(
            'loan',
            'loan__membership',
            'loan__membership__user',
            'loan__membership__sacco',
        )

    def _log_dry_run(self, upcoming_instalments, overdue_instalments):
        for schedule_item in upcoming_instalments:
            logger.info(
                'Dry run: would send repayment reminder for '
                'schedule_item_id=%s.',
                schedule_item.id,
            )

        for schedule_item in overdue_instalments:
            logger.info(
                'Dry run: would send overdue alert for schedule_item_id=%s.',
                schedule_item.id,
            )
