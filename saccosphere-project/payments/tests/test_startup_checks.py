"""payments.E001 startup check: MPESA_CALLBACK_TOKEN must be set in prod.

When MPESA_CALLBACK_TOKEN is blank the path-token guard on the M-Pesa
callback endpoints is silently skipped (each call site does ``if token:``
first), leaving them protected only by the Safaricom IP allowlist. The
system check turns that into a hard failure of ``manage.py check`` /
runserver / migrate when DEBUG is False, while staying out of the way in
local development (DEBUG=True).
"""

from django.test import SimpleTestCase, override_settings

from payments.checks import check_mpesa_callback_token


class MpesaCallbackTokenCheckTest(SimpleTestCase):

    @override_settings(DEBUG=False, MPESA_CALLBACK_TOKEN='')
    def test_error_when_token_blank_and_debug_false(self):
        errors = check_mpesa_callback_token(None)

        self.assertEqual([e.id for e in errors], ['payments.E001'])

    @override_settings(DEBUG=False, MPESA_CALLBACK_TOKEN='a-long-unguessable-token')
    def test_no_error_when_token_set_and_debug_false(self):
        self.assertEqual(check_mpesa_callback_token(None), [])

    def test_never_errors_when_debug_true(self):
        for token in ('', 'a-long-unguessable-token'):
            with self.subTest(token=token), override_settings(
                DEBUG=True, MPESA_CALLBACK_TOKEN=token,
            ):
                self.assertEqual(check_mpesa_callback_token(None), [])
