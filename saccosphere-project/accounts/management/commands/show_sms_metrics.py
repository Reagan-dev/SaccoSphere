"""Django management command to display SMS delivery metrics from Redis."""

from datetime import datetime, timedelta

from django.core.management.base import BaseCommand

from accounts.otp_backends import get_sms_metrics, SMS_METRICS_KEY_PREFIX


class Command(BaseCommand):
    help = 'Display SMS delivery metrics from Redis cache'

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            help='Date in YYYYMMDD format. Defaults to today.',
        )
        parser.add_argument(
            '--days',
            type=int,
            default=1,
            help='Number of days to show (default: 1). Use with --date to show range.',
        )
        parser.add_argument(
            '--purpose',
            type=str,
            help='Filter by OTP purpose (PHONE_VERIFY, PASSWORD_RESET, LOGIN).',
        )

    def handle(self, *args, **options):
        date_str = options.get('date')
        days = options.get('days', 1)
        purpose = options.get('purpose')

        if date_str:
            try:
                start_date = datetime.strptime(date_str, '%Y%m%d')
            except ValueError:
                self.stderr.write(
                    self.style.ERROR('Invalid date format. Use YYYYMMDD.')
                )
                return
        else:
            start_date = datetime.now()

        self.stdout.write(self.style.SUCCESS('SMS Delivery Metrics'))
        self.stdout.write('=' * 60)

        for i in range(days):
            current_date = start_date - timedelta(days=i)
            date_str = current_date.strftime('%Y%m%d')
            display_date = current_date.strftime('%Y-%m-%d')

            metrics = get_sms_metrics(date_str, purpose)

            if metrics['total'] == 0:
                self.stdout.write(
                    f'\n{display_date}: No data available'
                )
                continue

            success_rate = (
                (metrics['success'] / metrics['total'] * 100)
                if metrics['total'] > 0
                else 0
            )

            self.stdout.write(f'\n{display_date}:')
            if purpose:
                self.stdout.write(f'  Purpose: {purpose}')
            self.stdout.write(f'  Success: {metrics["success"]}')
            self.stdout.write(f'  Failure: {metrics["failure"]}')
            self.stdout.write(f'  Total: {metrics["total"]}')
            self.stdout.write(
                f'  Success Rate: {success_rate:.2f}%'
            )

        self.stdout.write('\n' + '=' * 60)
