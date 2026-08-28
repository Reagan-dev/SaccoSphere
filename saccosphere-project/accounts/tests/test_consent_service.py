"""Unit tests for consent service functions."""

from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from django.core.exceptions import PermissionDenied

from accounts.models import User, UserConsent
from accounts.services.consent import (
    has_active_consent,
    get_consent_status,
    require_consent,
    ConsentRequiredError,
)


class HasActiveConsentTestCase(TestCase):
    """Test has_active_consent function."""

    def setUp(self):
        """Create test user."""
        self.user = User.objects.create_user(
            email='consent-service@example.com',
            phone_number='+254700000001',
            password='testpass123',
        )

    def test_returns_true_for_active_consent(self):
        """Returns True when user has active consent for current version."""
        UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.MARKETING,
            version='v1.0',
            consented=True,
        )

        result = has_active_consent(self.user, UserConsent.ConsentType.MARKETING)
        self.assertTrue(result)

    def test_returns_false_for_no_consent(self):
        """Returns False when user has no consent record."""
        result = has_active_consent(self.user, UserConsent.ConsentType.MARKETING)
        self.assertFalse(result)

    def test_returns_false_for_withdrawn_consent(self):
        """Returns False when consent has been withdrawn."""
        consent = UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.MARKETING,
            version='v1.0',
            consented=True,
        )
        consent.withdrawn_at = '2024-01-01T00:00:00Z'
        consent.save()

        result = has_active_consent(self.user, UserConsent.ConsentType.MARKETING)
        self.assertFalse(result)

    def test_returns_false_for_outdated_version(self):
        """Returns False when consent is for an outdated version."""
        UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.MARKETING,
            version='v0.9',  # Outdated version
            consented=True,
        )

        result = has_active_consent(self.user, UserConsent.ConsentType.MARKETING)
        self.assertFalse(result)

    def test_returns_false_for_false_consent(self):
        """Returns False when consented=False."""
        UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.MARKETING,
            version='v1.0',
            consented=False,
        )

        result = has_active_consent(self.user, UserConsent.ConsentType.MARKETING)
        self.assertFalse(result)

    def test_returns_false_for_unauthenticated_user(self):
        """Returns False for unauthenticated user."""
        anonymous_user = AnonymousUser()
        result = has_active_consent(anonymous_user, UserConsent.ConsentType.MARKETING)
        self.assertFalse(result)

    def test_returns_false_for_none_user(self):
        """Returns False for None user."""
        result = has_active_consent(None, UserConsent.ConsentType.MARKETING)
        self.assertFalse(result)


class GetConsentStatusTestCase(TestCase):
    """Test get_consent_status function."""

    def setUp(self):
        """Create test user."""
        self.user = User.objects.create_user(
            email='status-service@example.com',
            phone_number='+254700000002',
            password='testpass123',
        )

    def test_returns_active_for_current_version(self):
        """Returns 'active' for consent matching current version."""
        UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.TERMS,
            version='v1.0',
            consented=True,
        )

        status = get_consent_status(self.user, UserConsent.ConsentType.TERMS)
        self.assertEqual(status, 'active')

    def test_returns_outdated_for_old_version(self):
        """Returns 'outdated' for consent with old version."""
        UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.TERMS,
            version='v0.9',
            consented=True,
        )

        status = get_consent_status(self.user, UserConsent.ConsentType.TERMS)
        self.assertEqual(status, 'outdated')

    def test_returns_withdrawn_for_withdrawn_consent(self):
        """Returns 'withdrawn' when consent has been withdrawn."""
        consent = UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.TERMS,
            version='v1.0',
            consented=True,
        )
        consent.withdrawn_at = '2024-01-01T00:00:00Z'
        consent.save()

        status = get_consent_status(self.user, UserConsent.ConsentType.TERMS)
        self.assertEqual(status, 'withdrawn')

    def test_returns_never_given_for_no_record(self):
        """Returns 'never_given' when no consent record exists."""
        status = get_consent_status(self.user, UserConsent.ConsentType.TERMS)
        self.assertEqual(status, 'never_given')

    def test_returns_never_given_for_false_consent(self):
        """Returns 'never_given' when consented=False."""
        UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.TERMS,
            version='v1.0',
            consented=False,
        )

        status = get_consent_status(self.user, UserConsent.ConsentType.TERMS)
        self.assertEqual(status, 'never_given')

    def test_returns_never_given_for_unauthenticated_user(self):
        """Returns 'never_given' for unauthenticated user."""
        anonymous_user = AnonymousUser()
        status = get_consent_status(anonymous_user, UserConsent.ConsentType.TERMS)
        self.assertEqual(status, 'never_given')

    def test_returns_never_given_for_none_user(self):
        """Returns 'never_given' for None user."""
        status = get_consent_status(None, UserConsent.ConsentType.TERMS)
        self.assertEqual(status, 'never_given')

    def test_uses_most_recent_consent(self):
        """Uses the most recent consent when multiple exist."""
        # Create old consent
        UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.PRIVACY,
            version='v0.9',
            consented=True,
        )

        # Create new consent
        UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.PRIVACY,
            version='v1.0',
            consented=True,
        )

        status = get_consent_status(self.user, UserConsent.ConsentType.PRIVACY)
        self.assertEqual(status, 'active')


class RequireConsentDecoratorTestCase(TestCase):
    """Test require_consent decorator."""

    def setUp(self):
        """Create test user."""
        self.user = User.objects.create_user(
            email='decorator-service@example.com',
            phone_number='+254700000003',
            password='testpass123',
        )

    def test_allows_execution_with_active_consent(self):
        """Allows function execution when user has active consent."""
        UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.MARKETING,
            version='v1.0',
            consented=True,
        )

        @require_consent(UserConsent.ConsentType.MARKETING)
        def send_marketing_email(user):
            return 'email sent'

        result = send_marketing_email(user=self.user)
        self.assertEqual(result, 'email sent')

    def test_raises_error_without_consent(self):
        """Raises ConsentRequiredError when user lacks active consent."""
        @require_consent(UserConsent.ConsentType.MARKETING)
        def send_marketing_email(user):
            return 'email sent'

        with self.assertRaises(ConsentRequiredError) as cm:
            send_marketing_email(user=self.user)

        self.assertIn('MARKETING', str(cm.exception))

    def test_raises_error_with_withdrawn_consent(self):
        """Raises ConsentPermissionError when consent is withdrawn."""
        consent = UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.MARKETING,
            version='v1.0',
            consented=True,
        )
        consent.withdrawn_at = '2024-01-01T00:00:00Z'
        consent.save()

        @require_consent(UserConsent.ConsentType.MARKETING)
        def send_marketing_email(user):
            return 'email sent'

        with self.assertRaises(ConsentRequiredError):
            send_marketing_email(user=self.user)

    def test_raises_error_with_outdated_consent(self):
        """Raises ConsentRequiredError when consent is outdated."""
        UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.MARKETING,
            version='v0.9',
            consented=True,
        )

        @require_consent(UserConsent.ConsentType.MARKETING)
        def send_marketing_email(user):
            return 'email sent'

        with self.assertRaises(ConsentRequiredError):
            send_marketing_email(user=self.user)

    def test_works_with_user_in_kwargs(self):
        """Works when user is passed as keyword argument."""
        UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.MARKETING,
            version='v1.0',
            consented=True,
        )

        @require_consent(UserConsent.ConsentType.MARKETING)
        def send_marketing_email(**kwargs):
            return 'email sent'

        result = send_marketing_email(user=self.user)
        self.assertEqual(result, 'email sent')

    def test_works_with_request_object(self):
        """Works when request object is passed."""
        from django.test import RequestFactory

        UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.MARKETING,
            version='v1.0',
            consented=True,
        )

        factory = RequestFactory()
        request = factory.get('/')
        request.user = self.user

        @require_consent(UserConsent.ConsentType.MARKETING)
        def send_marketing_email(request):
            return 'email sent'

        result = send_marketing_email(request=request)
        self.assertEqual(result, 'email sent')


class ConsentRequiredErrorTestCase(TestCase):
    """Test ConsentRequiredError exception."""

    def test_error_message_includes_consent_type(self):
        """Error message includes the consent type."""
        error = ConsentRequiredError('MARKETING')
        self.assertIn('MARKETING', str(error))
        self.assertIn('consent', str(error).lower())

    def test_is_permission_denied_subclass(self):
        """ConsentRequiredError is a PermissionDenied subclass."""
        error = ConsentRequiredError('MARKETING')
        self.assertIsInstance(error, PermissionDenied)
