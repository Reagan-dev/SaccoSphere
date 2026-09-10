"""Tests for the client-IP extraction helpers used across the project.

All of accounts.utils.get_client_ip, accounts.throttles._get_client_ip and
accounts.oauth_views._get_client_ip now delegate to the one canonical
resolver, config.utils.get_client_ip. It is built for this project's
deploy target (Railway - see README "Railway Deployment Processes"): a
single trusted Envoy edge hop that sets X-Envoy-External-Address to the
real client IP and appends the observed peer to X-Forwarded-For without
stripping client-supplied entries. So it prefers X-Envoy-External-Address,
otherwise trusts the rightmost non-internal X-Forwarded-For entry (never
the leftmost, which a client can forge), otherwise REMOTE_ADDR.
"""

from django.test import RequestFactory, TestCase

from accounts.throttles import _get_client_ip
from accounts.utils import get_client_ip
from config.utils import get_client_ip as canonical_get_client_ip


class GetClientIPTestCase(TestCase):
    """Tests for accounts.utils.get_client_ip."""

    def setUp(self):
        self.factory = RequestFactory()

    def test_single_forwarded_ip_is_used(self):
        """A single X-Forwarded-For entry is returned as-is."""
        request = self.factory.get(
            '/', HTTP_X_FORWARDED_FOR='203.0.113.5',
        )
        self.assertEqual(get_client_ip(request), '203.0.113.5')

    def test_rightmost_forwarded_ip_is_trusted_over_spoofed_entries(self):
        """The last entry (appended by our proxy) is trusted, not a spoofed first one."""
        request = self.factory.get(
            '/',
            HTTP_X_FORWARDED_FOR='198.51.100.1, 203.0.113.5',
        )
        self.assertEqual(get_client_ip(request), '203.0.113.5')

    def test_falls_back_to_remote_addr_without_forwarded_header(self):
        """With no X-Forwarded-For header, REMOTE_ADDR is used."""
        request = self.factory.get('/', REMOTE_ADDR='192.0.2.9')
        self.assertEqual(get_client_ip(request), '192.0.2.9')

    def test_ignores_blank_entries_in_forwarded_chain(self):
        """Trailing empty segments in the header don't break extraction."""
        request = self.factory.get(
            '/',
            HTTP_X_FORWARDED_FOR='198.51.100.1, 203.0.113.5, ',
        )
        self.assertEqual(get_client_ip(request), '203.0.113.5')

    def test_envoy_external_address_wins_over_forwarded_for(self):
        """Railway's X-Envoy-External-Address is authoritative when present."""
        request = self.factory.get(
            '/',
            HTTP_X_ENVOY_EXTERNAL_ADDRESS='203.0.113.7',
            HTTP_X_FORWARDED_FOR='198.51.100.1, 203.0.113.5',
        )
        self.assertEqual(get_client_ip(request), '203.0.113.7')

    def test_trailing_railway_internal_hop_is_skipped(self):
        """A trailing 100.64.0.0/10 CGNAT hop is not the client IP."""
        request = self.factory.get(
            '/',
            HTTP_X_FORWARDED_FOR='203.0.113.5, 100.64.0.9',
        )
        self.assertEqual(get_client_ip(request), '203.0.113.5')

    def test_non_ip_forwarded_entries_are_ignored(self):
        """A garbage rightmost entry is skipped, not returned verbatim."""
        request = self.factory.get(
            '/',
            HTTP_X_FORWARDED_FOR='203.0.113.5, not-an-ip',
        )
        self.assertEqual(get_client_ip(request), '203.0.113.5')

    def test_returns_none_when_nothing_usable(self):
        """The canonical resolver returns None (callers coerce as needed)."""
        request = self.factory.get('/')
        request.META.pop('REMOTE_ADDR', None)
        self.assertIsNone(canonical_get_client_ip(request))


class ThrottleGetClientIPTestCase(TestCase):
    """Tests for accounts.throttles._get_client_ip (shared by OTP/KYC/consent throttles)."""

    def setUp(self):
        self.factory = RequestFactory()

    def test_rightmost_forwarded_ip_is_trusted_over_spoofed_entries(self):
        """A spoofed leading entry must not be treated as the client IP."""
        request = self.factory.get(
            '/',
            HTTP_X_FORWARDED_FOR='198.51.100.1, 203.0.113.5',
        )
        self.assertEqual(_get_client_ip(request), '203.0.113.5')

    def test_falls_back_to_remote_addr_without_forwarded_header(self):
        """With no X-Forwarded-For header, REMOTE_ADDR is used."""
        request = self.factory.get('/', REMOTE_ADDR='192.0.2.9')
        self.assertEqual(_get_client_ip(request), '192.0.2.9')

    def test_coerces_missing_ip_to_unknown_for_cache_keys(self):
        """The throttle wrapper never returns None; cache keys need one."""
        request = self.factory.get('/')
        request.META.pop('REMOTE_ADDR', None)
        self.assertEqual(_get_client_ip(request), 'unknown')
