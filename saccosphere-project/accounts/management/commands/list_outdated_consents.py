"""Django management command to list users whose consent is outdated.

Read-only reporting for compliance/ops to drive a re-consent campaign.
Deliberately does not send anything (email, SMS, notifications) - wiring
this output into a messaging system is a separate concern.
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from accounts.models import User, UserConsent
from accounts.services.consent import get_consent_status


class Command(BaseCommand):
    help = (
        'List users whose most recent consent for a given type predates '
        'the current CONSENT_POLICY_VERSIONS entry for that type, so '
        'compliance/ops can drive a re-consent campaign. Read-only: this '
        'command sends nothing and notifies nobody itself.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--consent-type',
            type=str,
            choices=UserConsent.ConsentType.values,
            help=(
                'Restrict the report to one consent type (e.g. MARKETING). '
                'If omitted, reports on every consent type.'
            ),
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help=(
                'List each outdated user (id, email, recorded version) '
                'instead of just a per-type count summary.'
            ),
        )

    def handle(self, *args, **options):
        consent_type_filter = options.get('consent_type')
        verbose = options.get('verbose', False)

        consent_types = (
            [consent_type_filter]
            if consent_type_filter
            else list(UserConsent.ConsentType.values)
        )

        self.stdout.write(self.style.SUCCESS('Outdated consent report'))
        self.stdout.write('=' * 70)

        total_outdated = 0
        for consent_type in consent_types:
            current_version = settings.CONSENT_POLICY_VERSIONS.get(
                consent_type,
            )
            if not current_version:
                self.stdout.write(
                    f'\n{consent_type}: skipped - no current policy '
                    'version configured in CONSENT_POLICY_VERSIONS.'
                )
                continue

            outdated = self._find_outdated_users(consent_type)
            total_outdated += len(outdated)

            self.stdout.write(
                f'\n{consent_type} (current version: {current_version}): '
                f'{len(outdated)} user(s) outdated'
            )

            if verbose and outdated:
                self.stdout.write('-' * 70)
                for entry in outdated:
                    self.stdout.write(
                        f'  user_id={entry["user_id"]} '
                        f'email={entry["email"]} '
                        f'recorded_version={entry["recorded_version"]}'
                    )

        self.stdout.write('\n' + '=' * 70)
        self.stdout.write(
            self.style.WARNING(
                'Total outdated consent record(s) across reported '
                f'type(s): {total_outdated}. This command sends no '
                'notifications - use its output to drive a separate '
                're-consent campaign.'
            )
        )

    @staticmethod
    def _find_outdated_users(consent_type):
        """
        Return a list of {'user_id', 'email', 'recorded_version'} for every
        user whose most recent UserConsent record for consent_type is
        'outdated' per accounts.services.consent.get_consent_status - i.e.
        a previously-accepted, not-withdrawn consent recorded against a
        version older than the current CONSENT_POLICY_VERSIONS entry.

        Reuses get_consent_status rather than re-deriving the version
        comparison, so this command's notion of "outdated" can never drift
        from what GET /consents/ and has_active_consent report.
        """
        candidate_user_ids = (
            UserConsent.objects.filter(consent_type=consent_type)
            .values_list('user_id', flat=True)
            .distinct()
        )

        outdated = []
        for user in User.objects.filter(id__in=candidate_user_ids):
            if get_consent_status(user, consent_type) != 'outdated':
                continue
            latest = (
                UserConsent.objects.filter(
                    user=user, consent_type=consent_type,
                )
                .order_by('-timestamp')
                .first()
            )
            outdated.append({
                'user_id': str(user.id),
                'email': user.email,
                'recorded_version': latest.version if latest else None,
            })

        return outdated
