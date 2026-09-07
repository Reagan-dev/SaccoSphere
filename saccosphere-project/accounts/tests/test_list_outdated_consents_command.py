"""Tests for the list_outdated_consents management command."""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from accounts.models import User, UserConsent


class ListOutdatedConsentsCommandTestCase(TestCase):
    """Test the command against a fixture of users at mixed consent versions."""

    def setUp(self):
        self.current_user = User.objects.create_user(
            email='current@example.com',
            phone_number='+254700000040',
            password='testpass123',
        )
        UserConsent.objects.create(
            user=self.current_user,
            consent_type=UserConsent.ConsentType.TERMS,
            version='v1.0',
            consented=True,
        )

        self.outdated_user = User.objects.create_user(
            email='outdated@example.com',
            phone_number='+254700000041',
            password='testpass123',
        )
        UserConsent.objects.create(
            user=self.outdated_user,
            consent_type=UserConsent.ConsentType.TERMS,
            version='v0.9',
            consented=True,
        )

        self.withdrawn_user = User.objects.create_user(
            email='withdrawn@example.com',
            phone_number='+254700000042',
            password='testpass123',
        )
        withdrawn_consent = UserConsent.objects.create(
            user=self.withdrawn_user,
            consent_type=UserConsent.ConsentType.TERMS,
            version='v0.9',
            consented=True,
        )
        withdrawn_consent.withdrawn_at = withdrawn_consent.timestamp
        withdrawn_consent.save(update_fields=['withdrawn_at'])

        self.denied_user = User.objects.create_user(
            email='denied@example.com',
            phone_number='+254700000043',
            password='testpass123',
        )
        UserConsent.objects.create(
            user=self.denied_user,
            consent_type=UserConsent.ConsentType.TERMS,
            version='v0.9',
            consented=False,
        )

        # A never-given user (no UserConsent row at all) is deliberately
        # not created - the command must not report on users it has no
        # record for.

        # A second consent type, kept current, to prove --consent-type
        # scoping doesn't accidentally pull in unrelated types.
        UserConsent.objects.create(
            user=self.outdated_user,
            consent_type=UserConsent.ConsentType.MARKETING,
            version='v1.0',
            consented=True,
        )

    def _run_command(self, *args):
        out = StringIO()
        call_command('list_outdated_consents', *args, stdout=out)
        return out.getvalue()

    def test_default_output_is_count_summary_only(self):
        """Without --verbose, only per-type counts are shown, no identifiers."""
        output = self._run_command('--consent-type=TERMS')

        self.assertIn('TERMS (current version: v1.0): 1 user(s) outdated', output)
        self.assertNotIn(self.outdated_user.email, output)
        self.assertNotIn(str(self.outdated_user.id), output)

    def test_verbose_output_lists_only_the_truly_outdated_user(self):
        """--verbose lists the outdated user, and excludes withdrawn/denied/current."""
        output = self._run_command('--consent-type=TERMS', '--verbose')

        self.assertIn(f'user_id={self.outdated_user.id}', output)
        self.assertIn('email=outdated@example.com', output)
        self.assertIn('recorded_version=v0.9', output)

        # Withdrawn consent, explicit denial, and up-to-date consent must
        # all be excluded - none of these are "outdated" in the
        # get_consent_status sense the command reuses.
        self.assertNotIn(str(self.withdrawn_user.id), output)
        self.assertNotIn(str(self.denied_user.id), output)
        self.assertNotIn(str(self.current_user.id), output)

    def test_consent_type_filter_scopes_the_report(self):
        """--consent-type restricts the report to that type only."""
        output = self._run_command('--consent-type=MARKETING', '--verbose')

        self.assertIn('MARKETING (current version: v1.0): 0 user(s) outdated', output)
        self.assertNotIn('TERMS (current version', output)

    def test_no_filter_reports_on_every_consent_type(self):
        """Without --consent-type, every consent type is reported on."""
        output = self._run_command()

        for consent_type in UserConsent.ConsentType.values:
            self.assertIn(f'{consent_type} (current version:', output)

    def test_total_count_is_summed_across_reported_types(self):
        """The trailing total reflects the sum across all reported types."""
        output = self._run_command()

        self.assertIn('Total outdated consent record(s) across reported type(s): 1', output)
