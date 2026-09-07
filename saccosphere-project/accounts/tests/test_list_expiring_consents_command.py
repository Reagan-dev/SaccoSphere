"""Tests for the list_expiring_consents management command."""

from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User, UserConsent


# The actual duration value doesn't matter for this command's filtering -
# it operates on each fixture's own expires_at timestamp. This override
# only needs to be truthy so --consent-type=MARKETING isn't skipped as
# "no expiry duration configured".
_MARKETING_EXPIRY_OVERRIDE = {'CONSENT_EXPIRY_DURATIONS': {'MARKETING': timedelta(days=365)}}


@override_settings(**_MARKETING_EXPIRY_OVERRIDE)
class ListExpiringConsentsCommandTestCase(TestCase):
    """Test the command against a fixture of users with mixed expiry timing."""

    def setUp(self):
        now = timezone.now()

        self.expiring_soon_user = User.objects.create_user(
            email='expiring-soon@example.com',
            phone_number='+254700000060',
            password='testpass123',
        )
        UserConsent.objects.create(
            user=self.expiring_soon_user,
            consent_type=UserConsent.ConsentType.MARKETING,
            version='v1.0',
            consented=True,
            expires_at=now + timedelta(days=5),
        )

        self.expiring_later_user = User.objects.create_user(
            email='expiring-later@example.com',
            phone_number='+254700000061',
            password='testpass123',
        )
        UserConsent.objects.create(
            user=self.expiring_later_user,
            consent_type=UserConsent.ConsentType.MARKETING,
            version='v1.0',
            consented=True,
            expires_at=now + timedelta(days=60),
        )

        self.already_expired_user = User.objects.create_user(
            email='already-expired@example.com',
            phone_number='+254700000062',
            password='testpass123',
        )
        UserConsent.objects.create(
            user=self.already_expired_user,
            consent_type=UserConsent.ConsentType.MARKETING,
            version='v1.0',
            consented=True,
            expires_at=now - timedelta(days=1),
        )

        self.no_expiry_user = User.objects.create_user(
            email='no-expiry@example.com',
            phone_number='+254700000063',
            password='testpass123',
        )
        UserConsent.objects.create(
            user=self.no_expiry_user,
            consent_type=UserConsent.ConsentType.MARKETING,
            version='v1.0',
            consented=True,
            expires_at=None,
        )

        self.withdrawn_soon_user = User.objects.create_user(
            email='withdrawn-soon@example.com',
            phone_number='+254700000064',
            password='testpass123',
        )
        withdrawn_consent = UserConsent.objects.create(
            user=self.withdrawn_soon_user,
            consent_type=UserConsent.ConsentType.MARKETING,
            version='v1.0',
            consented=True,
            expires_at=now + timedelta(days=5),
        )
        withdrawn_consent.withdrawn_at = now
        withdrawn_consent.save(update_fields=['withdrawn_at'])

    def _run_command(self, *args):
        out = StringIO()
        call_command('list_expiring_consents', *args, stdout=out)
        return out.getvalue()

    def test_within_days_window_includes_only_soon_expiring(self):
        """A 7-day window includes only the user expiring in 5 days."""
        output = self._run_command(
            '--consent-type=MARKETING', '--within-days=7', '--verbose',
        )

        self.assertIn(f'user_id={self.expiring_soon_user.id}', output)
        self.assertNotIn(str(self.expiring_later_user.id), output)
        self.assertNotIn(str(self.already_expired_user.id), output)
        self.assertNotIn(str(self.no_expiry_user.id), output)
        self.assertNotIn(str(self.withdrawn_soon_user.id), output)

    def test_wider_window_includes_both_expiring_users(self):
        """A 90-day window includes both the 5-day and 60-day users."""
        output = self._run_command(
            '--consent-type=MARKETING', '--within-days=90', '--verbose',
        )

        self.assertIn(f'user_id={self.expiring_soon_user.id}', output)
        self.assertIn(f'user_id={self.expiring_later_user.id}', output)
        self.assertNotIn(str(self.already_expired_user.id), output)

    def test_default_output_is_count_summary_only(self):
        """Without --verbose, only the per-type count is shown."""
        output = self._run_command('--consent-type=MARKETING', '--within-days=7')

        self.assertIn('1 user(s) expiring within 7 day(s)', output)
        self.assertNotIn(self.expiring_soon_user.email, output)

    def test_type_with_no_configured_duration_is_skipped(self):
        """A consent_type absent from CONSENT_EXPIRY_DURATIONS is reported as skipped."""
        output = self._run_command('--consent-type=TERMS', '--within-days=30')

        self.assertIn('TERMS: skipped - no expiry duration configured', output)
