"""Tests for keyed OTP hashing at rest."""

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import OTPToken, User
from accounts.otp_backends import PhoneOTPBackend
from accounts.otp_utils import (
    OTPError,
    create_otp_token,
    hash_otp_code,
    verify_otp,
)


@override_settings(DEBUG=True, OTP_HASH_KEY='test-otp-hash-key')
class OTPHashingTests(TestCase):
    """Verify OTP codes are hashed in storage and usable in memory."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='otp-hash@example.com',
            first_name='Otp',
            last_name='Hash',
            phone_number='254700000010',
            password='StrongPass1',
        )

    def test_hash_otp_code_uses_keyed_hmac(self):
        first_hash = hash_otp_code('123456')

        with override_settings(OTP_HASH_KEY='different-test-key'):
            second_hash = hash_otp_code('123456')

        self.assertEqual(len(first_hash), 64)
        self.assertNotEqual(first_hash, second_hash)

    def test_create_otp_token_stores_hash_and_keeps_plaintext_in_memory(self):
        token = create_otp_token(
            self.user,
            self.user.phone_number,
            OTPToken.Purpose.PHONE_VERIFY,
        )
        token_from_db = OTPToken.objects.get(id=token.id)

        self.assertRegex(token_from_db.code, r'^[0-9a-f]{64}$')
        self.assertNotRegex(token_from_db.code, r'^\d{6}$')
        self.assertRegex(token.plaintext_code, r'^\d{6}$')
        self.assertEqual(token_from_db.code, hash_otp_code(token.plaintext_code))

    def test_verify_otp_accepts_plaintext_code_for_hashed_token(self):
        token = create_otp_token(
            self.user,
            self.user.phone_number,
            OTPToken.Purpose.PHONE_VERIFY,
        )

        verified_token = verify_otp(
            self.user.phone_number,
            token.plaintext_code,
            OTPToken.Purpose.PHONE_VERIFY,
        )

        self.assertEqual(verified_token.id, token.id)
        verified_token.refresh_from_db()
        self.assertTrue(verified_token.is_used)

    def test_verify_otp_rejects_wrong_code_with_same_error(self):
        create_otp_token(
            self.user,
            self.user.phone_number,
            OTPToken.Purpose.PHONE_VERIFY,
        )

        with self.assertRaisesMessage(OTPError, 'Incorrect code.'):
            verify_otp(
                self.user.phone_number,
                '000000',
                OTPToken.Purpose.PHONE_VERIFY,
            )

    def test_phone_backend_debug_log_does_not_include_plaintext_code(self):
        token = OTPToken(
            user=self.user,
            phone_number=self.user.phone_number,
            code=hash_otp_code('123456'),
            purpose=OTPToken.Purpose.PHONE_VERIFY,
            expires_at=timezone.now(),
        )
        token.plaintext_code = '123456'

        with self.assertLogs('accounts.otp_backends', level='INFO') as logs:
            PhoneOTPBackend().send(token)

        log_output = '\n'.join(logs.output)
        self.assertIn('OTP SMS prepared', log_output)
        self.assertNotIn('123456', log_output)
