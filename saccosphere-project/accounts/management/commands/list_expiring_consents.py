"""Django management command to list users whose consent is expiring soon.

Read-only reporting for compliance/ops to drive a proactive re-consent
campaign before expiry, mirroring list_outdated_consents.py. Deliberately
does not send anything (email, SMS, notifications) - wiring this output
into a messaging system is a separate concern.
"""

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import User, UserConsent


class Command(BaseCommand):
    help = (
        'List users whose most recent consent for a given type will '
        'expire within N days, so compliance/ops can drive a proactive '
        're-consent campaign before it lapses. Only consent_types with a '
        'configured duration in settings.CONSENT_EXPIRY_DURATIONS can ever '
        'expire, so this reports nothing for the rest. Already-expired '
        'consent is reported by list_outdated_consents, not here. '
        'Read-only: this command sends nothing and notifies nobody itself.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--consent-type',
            type=str,
            choices=UserConsent.ConsentType.values,
            help=(
                'Restrict the report to one consent type (e.g. MARKETING). '
                'If omitted, reports on every consent type that has a '
                'configured expiry duration.'
            ),
        )
        parser.add_argument(
            '--within-days',
            type=int,
            default=30,
            help=(
                'Report consents expiring between now and this many days '
                'from now (default: 30).'
            ),
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help=(
                'List each expiring user (id, email, expires_at) instead '
                'of just a per-type count summary.'
            ),
        )

    def handle(self, *args, **options):
        consent_type_filter = options.get('consent_type')
        within_days = options['within_days']
        verbose = options.get('verbose', False)

        if within_days <= 0:
            self.stderr.write(
                self.style.ERROR('--within-days must be a positive integer.')
            )
            return

        consent_types = (
            [consent_type_filter]
            if consent_type_filter
            else list(UserConsent.ConsentType.values)
        )

        self.stdout.write(
            self.style.SUCCESS(
                f'Consents expiring within {within_days} day(s)'
            )
        )
        self.stdout.write('=' * 70)

        now = timezone.now()
        window_end = now + timezone.timedelta(days=within_days)
        total_expiring = 0

        for consent_type in consent_types:
            duration = settings.CONSENT_EXPIRY_DURATIONS.get(consent_type)
            if not duration:
                self.stdout.write(
                    f'\n{consent_type}: skipped - no expiry duration '
                    'configured in CONSENT_EXPIRY_DURATIONS.'
                )
                continue

            expiring = self._find_expiring_users(
                consent_type, now, window_end,
            )
            total_expiring += len(expiring)

            self.stdout.write(
                f'\n{consent_type} (expiry duration: {duration}): '
                f'{len(expiring)} user(s) expiring within {within_days} '
                'day(s)'
            )

            if verbose and expiring:
                self.stdout.write('-' * 70)
                for entry in expiring:
                    self.stdout.write(
                        f'  user_id={entry["user_id"]} '
                        f'email={entry["email"]} '
                        f'expires_at={entry["expires_at"].isoformat()}'
                    )

        self.stdout.write('\n' + '=' * 70)
        self.stdout.write(
            self.style.WARNING(
                f'Total user(s) with consent expiring within '
                f'{within_days} day(s): {total_expiring}. This command '
                'sends no notifications - use its output to drive a '
                'separate re-consent campaign.'
            )
        )

    @staticmethod
    def _find_expiring_users(consent_type, now, window_end):
        """
        Return [{'user_id', 'email', 'expires_at'}] for every user whose
        most recent, still-consented, not-withdrawn UserConsent record for
        consent_type has expires_at strictly between now and window_end.

        Already-expired consent (expires_at <= now) is deliberately
        excluded here - that is list_outdated_consents' job, once
        get_consent_status treats it as "outdated".
        """
        candidate_user_ids = (
            UserConsent.objects.filter(
                consent_type=consent_type,
                expires_at__isnull=False,
            )
            .values_list('user_id', flat=True)
            .distinct()
        )

        expiring = []
        for user in User.objects.filter(id__in=candidate_user_ids):
            latest = (
                UserConsent.objects.filter(
                    user=user, consent_type=consent_type,
                )
                .order_by('-timestamp')
                .first()
            )
            if latest is None:
                continue
            if not latest.consented or latest.withdrawn_at is not None:
                continue
            if latest.expires_at is None:
                continue
            if not (now < latest.expires_at <= window_end):
                continue

            expiring.append({
                'user_id': str(user.id),
                'email': user.email,
                'expires_at': latest.expires_at,
            })

        return expiring
