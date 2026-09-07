"""Tests for client-IP extraction helpers used by consent (and OTP/KYC) throttling.

Both accounts.utils.get_client_ip and accounts.throttles._get_client_ip must trust
the rightmost X-Forwarded-For entry, not the leftmost, since this deployment sits
behind exactly one reverse proxy hop that appends the true client IP last. A client
can freely set its own X-Forwarded-For header, so trusting the leftmost entry would
let a request forge whatever IP it likes for throttling and consent-audit purposes.
"""

from django.test import RequestFactory, TestCase

from accounts.throttles import _get_client_ip
from accounts.utils import get_client_ip


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
