"""Tests for shared utility functions."""

from django.test import TestCase

from config.utils import InvalidPhoneNumberError, normalize_phone_number
from payments.integrations.mpesa.daraja import format_phone_for_daraja


class NormalizePhoneNumberTestCase(TestCase):
    """Test cases for normalize_phone_number utility."""

    def test_normalizes_07_format(self):
        """Test that 07XXXXXXXX format normalizes to E.164."""
        result = normalize_phone_number('0712345678')
        self.assertEqual(result, '+254712345678')

    def test_normalizes_011_format(self):
        """Test that 011XXXXXXXX format normalizes to E.164."""
        result = normalize_phone_number('0112345678')
        self.assertEqual(result, '+254112345678')

    def test_normalizes_7_format(self):
        """Test that 7XXXXXXXX format normalizes to E.164."""
        result = normalize_phone_number('712345678')
        self.assertEqual(result, '+254712345678')

    def test_normalizes_1_format(self):
        """Test that 1XXXXXXXX format normalizes to E.164."""
        result = normalize_phone_number('112345678')
        self.assertEqual(result, '+254112345678')

    def test_normalizes_254_format(self):
        """Test that 254XXXXXXXXX format normalizes to E.164."""
        result = normalize_phone_number('254712345678')
        self.assertEqual(result, '+254712345678')

    def test_normalizes_plus_254_format(self):
        """Test that +254XXXXXXXXX format is preserved."""
        result = normalize_phone_number('+254712345678')
        self.assertEqual(result, '+254712345678')

    def test_normalizes_with_spaces(self):
        """Test that numbers with spaces normalize correctly."""
        result = normalize_phone_number('0712 345 678')
        self.assertEqual(result, '+254712345678')

    def test_normalizes_with_dashes(self):
        """Test that numbers with dashes normalize correctly."""
        result = normalize_phone_number('0712-345-678')
        self.assertEqual(result, '+254712345678')

    def test_normalizes_with_mixed_separators(self):
        """Test that numbers with mixed separators normalize correctly."""
        result = normalize_phone_number('0712 345-678')
        self.assertEqual(result, '+254712345678')

    def test_rejects_empty_string(self):
        """Test that empty string raises InvalidPhoneNumberError."""
        with self.assertRaises(InvalidPhoneNumberError) as cm:
            normalize_phone_number('')
        self.assertIn('non-empty string', str(cm.exception))

    def test_rejects_none(self):
        """Test that None raises InvalidPhoneNumberError."""
        with self.assertRaises(InvalidPhoneNumberError) as cm:
            normalize_phone_number(None)
        self.assertIn('non-empty string', str(cm.exception))

    def test_rejects_invalid_prefix(self):
        """Test that invalid prefix raises InvalidPhoneNumberError."""
        with self.assertRaises(InvalidPhoneNumberError) as cm:
            normalize_phone_number('0812345678')
        self.assertIn('Invalid Kenyan phone number', str(cm.exception))

    def test_rejects_wrong_length(self):
        """Test that wrong length raises InvalidPhoneNumberError."""
        with self.assertRaises(InvalidPhoneNumberError) as cm:
            normalize_phone_number('12345678')
        self.assertIn('Invalid Kenyan phone number', str(cm.exception))

    def test_rejects_international_number(self):
        """Test that non-Kenyan international number raises error."""
        with self.assertRaises(InvalidPhoneNumberError) as cm:
            normalize_phone_number('+1234567890')
        self.assertIn('Invalid Kenyan phone number', str(cm.exception))

    def test_rejects_letters(self):
        """Test that numbers with letters raise InvalidPhoneNumberError."""
        with self.assertRaises(InvalidPhoneNumberError) as cm:
            normalize_phone_number('0712abc678')
        self.assertIn('Invalid Kenyan phone number', str(cm.exception))


class FormatPhoneForDarajaTestCase(TestCase):
    """Test cases for format_phone_for_daraja utility."""

    def test_strips_plus_from_e164(self):
        """Test that leading + is stripped from E.164 format."""
        result = format_phone_for_daraja('+254712345678')
        self.assertEqual(result, '254712345678')

    def test_rejects_non_e164_format(self):
        """Test that non-E.164 format raises ValueError."""
        with self.assertRaises(ValueError) as cm:
            format_phone_for_daraja('254712345678')
        self.assertIn('Expected E.164 format', str(cm.exception))

    def test_rejects_empty_string(self):
        """Test that empty string raises ValueError."""
        with self.assertRaises(ValueError) as cm:
            format_phone_for_daraja('')
        self.assertIn('non-empty string', str(cm.exception))

    def test_rejects_none(self):
        """Test that None raises ValueError."""
        with self.assertRaises(ValueError) as cm:
            format_phone_for_daraja(None)
        self.assertIn('non-empty string', str(cm.exception))

    def test_rejects_wrong_country_code(self):
        """Test that wrong country code raises ValueError."""
        with self.assertRaises(ValueError) as cm:
            format_phone_for_daraja('+1234567890')
        self.assertIn('Expected E.164 format', str(cm.exception))


class PhoneNormalizationIntegrationTestCase(TestCase):
    """Integration test for phone normalization across boundaries."""

    def test_same_source_produces_correct_formats_for_both_boundaries(self):
        """Test that one source produces correct formats for both Daraja and Africa's Talking."""
        # Source phone number in various formats
        source_numbers = [
            '0712345678',
            '712345678',
            '+254712345678',
            '254712345678',
            '0712-345-678',
        ]

        for source in source_numbers:
            with self.subTest(source=source):
                # Normalize to canonical E.164 format
                e164 = normalize_phone_number(source)
                
                # For Africa's Talking: use E.164 directly
                africas_talking_format = e164
                self.assertEqual(africas_talking_format, '+254712345678')
                
                # For Daraja: strip the leading +
                daraja_format = format_phone_for_daraja(e164)
                self.assertEqual(daraja_format, '254712345678')
                
                # Verify they're different as expected
                self.assertNotEqual(africas_talking_format, daraja_format)
                
                # Verify Daraja format is just E.164 without +
                self.assertEqual(daraja_format, africas_talking_format[1:])
