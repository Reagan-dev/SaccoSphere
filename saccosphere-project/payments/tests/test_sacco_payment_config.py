"""Tests for SACCO-specific payment configuration and credential isolation."""

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from accounts.models import Sacco, SaccoPaymentConfig
from payments.integrations.mpesa.daraja import DarajaClient


class SaccoPaymentConfigTestCase(TestCase):
    """Test cases for SACCO payment configuration model."""

    def setUp(self):
        """Set up test SACCOs and payment configurations."""
        self.sacco_a = Sacco.objects.create(
            name='SACCO A',
            registration_number='SA001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            is_active=True,
        )
        
        self.sacco_b = Sacco.objects.create(
            name='SACCO B',
            registration_number='SA002',
            sector=Sacco.Sector.AGRICULTURE,
            county='Kisumu',
            is_active=True,
        )
        
        self.config_a = SaccoPaymentConfig.objects.create(
            sacco=self.sacco_a,
            shortcode_type=SaccoPaymentConfig.ShortcodeType.PAYBILL,
            shortcode='123456',
            stk_passkey='test_passkey_a',
            daraja_consumer_key='consumer_key_a',
            daraja_consumer_secret='consumer_secret_a',
            environment=SaccoPaymentConfig.Environment.SANDBOX,
            b2c_initiator_name='initiator_a',
            b2c_security_credential='security_credential_a',
            is_active=True,
        )
        
        self.config_b = SaccoPaymentConfig.objects.create(
            sacco=self.sacco_b,
            shortcode_type=SaccoPaymentConfig.ShortcodeType.PAYBILL,
            shortcode='789012',
            stk_passkey='test_passkey_b',
            daraja_consumer_key='consumer_key_b',
            daraja_consumer_secret='consumer_secret_b',
            environment=SaccoPaymentConfig.Environment.SANDBOX,
            b2c_initiator_name='initiator_b',
            b2c_security_credential='security_credential_b',
            is_active=True,
        )

    def test_sacco_has_payment_config(self):
        """Test that SACCO has payment configuration relation."""
        self.assertEqual(self.sacco_a.payment_config, self.config_a)
        self.assertEqual(self.sacco_b.payment_config, self.config_b)

    def test_has_b2c_config(self):
        """Test B2C configuration check."""
        self.assertTrue(self.config_a.has_b2c_config())
        self.assertTrue(self.config_b.has_b2c_config())
        
        # Test without B2C config
        config_no_b2c = SaccoPaymentConfig.objects.create(
            sacco=Sacco.objects.create(
                name='SACCO C',
                registration_number='SA003',
                sector=Sacco.Sector.EDUCATION,
                county='Mombasa',
                is_active=True,
            ),
            shortcode_type=SaccoPaymentConfig.ShortcodeType.PAYBILL,
            shortcode='345678',
            stk_passkey='test_passkey_c',
            daraja_consumer_key='consumer_key_c',
            daraja_consumer_secret='consumer_secret_c',
            environment=SaccoPaymentConfig.Environment.SANDBOX,
            is_active=True,
        )
        self.assertFalse(config_no_b2c.has_b2c_config())


class DarajaClientCredentialIsolationTestCase(TestCase):
    """Test cases for DarajaClient credential isolation between SACCOs."""

    def setUp(self):
        """Set up test SACCOs with different credentials."""
        self.sacco_a = Sacco.objects.create(
            name='SACCO A',
            registration_number='SA001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            is_active=True,
        )
        
        self.sacco_b = Sacco.objects.create(
            name='SACCO B',
            registration_number='SA002',
            sector=Sacco.Sector.AGRICULTURE,
            county='Kisumu',
            is_active=True,
        )
        
        self.config_a = SaccoPaymentConfig.objects.create(
            sacco=self.sacco_a,
            shortcode_type=SaccoPaymentConfig.ShortcodeType.PAYBILL,
            shortcode='123456',
            stk_passkey='test_passkey_a',
            daraja_consumer_key='consumer_key_a',
            daraja_consumer_secret='consumer_secret_a',
            environment=SaccoPaymentConfig.Environment.SANDBOX,
            is_active=True,
        )
        
        self.config_b = SaccoPaymentConfig.objects.create(
            sacco=self.sacco_b,
            shortcode_type=SaccoPaymentConfig.ShortcodeType.PAYBILL,
            shortcode='789012',
            stk_passkey='test_passkey_b',
            daraja_consumer_key='consumer_key_b',
            daraja_consumer_secret='consumer_secret_b',
            environment=SaccoPaymentConfig.Environment.SANDBOX,
            is_active=True,
        )
        
        # Clear cache before each test
        cache.clear()

    def test_client_uses_sacco_specific_credentials(self):
        """Test that DarajaClient uses SACCO-specific credentials."""
        client_a = DarajaClient(
            consumer_key=self.config_a.daraja_consumer_key,
            consumer_secret=self.config_a.daraja_consumer_secret,
            shortcode=self.config_a.shortcode,
            passkey=self.config_a.stk_passkey,
            environment=self.config_a.environment,
        )
        
        client_b = DarajaClient(
            consumer_key=self.config_b.daraja_consumer_key,
            consumer_secret=self.config_b.daraja_consumer_secret,
            shortcode=self.config_b.shortcode,
            passkey=self.config_b.stk_passkey,
            environment=self.config_b.environment,
        )
        
        self.assertEqual(client_a.consumer_key, 'consumer_key_a')
        self.assertEqual(client_a.shortcode, '123456')
        self.assertEqual(client_a.passkey, 'test_passkey_a')
        
        self.assertEqual(client_b.consumer_key, 'consumer_key_b')
        self.assertEqual(client_b.shortcode, '789012')
        self.assertEqual(client_b.passkey, 'test_passkey_b')

    def test_oauth_token_cache_is_per_credential(self):
        """Test that OAuth tokens are cached per credential set."""
        client_a = DarajaClient(
            consumer_key=self.config_a.daraja_consumer_key,
            consumer_secret=self.config_a.daraja_consumer_secret,
            shortcode=self.config_a.shortcode,
            environment=self.config_a.environment,
        )
        
        client_b = DarajaClient(
            consumer_key=self.config_b.daraja_consumer_key,
            consumer_secret=self.config_b.daraja_consumer_secret,
            shortcode=self.config_b.shortcode,
            environment=self.config_b.environment,
        )
        
        # Verify cache keys are different
        self.assertNotEqual(client_a._cache_key_prefix, client_b._cache_key_prefix)
        self.assertIn('consumer_key_a', client_a._cache_key_prefix)
        self.assertIn('consumer_key_b', client_b._cache_key_prefix)

    @patch('payments.integrations.mpesa.daraja.requests.get')
    def test_token_for_sacco_a_not_reused_for_sacco_b(self, mock_get):
        """Test that a token for SACCO A is never reused for SACCO B."""
        # Mock successful token response
        mock_response_a = type('MockResponse', (), {
            'json': lambda: {'access_token': 'token_a'},
            'raise_for_status': lambda: None,
        })()
        mock_response_b = type('MockResponse', (), {
            'json': lambda: {'access_token': 'token_b'},
            'raise_for_status': lambda: None,
        })()
        
        # First call for SACCO A
        mock_get.return_value = mock_response_a
        client_a = DarajaClient(
            consumer_key=self.config_a.daraja_consumer_key,
            consumer_secret=self.config_a.daraja_consumer_secret,
            shortcode=self.config_a.shortcode,
            environment=self.config_a.environment,
        )
        token_a = client_a.get_access_token()
        
        # Verify token_a is cached
        self.assertEqual(token_a, 'token_a')
        self.assertEqual(cache.get(client_a._cache_key_prefix), 'token_a')
        
        # Call for SACCO B - should get a different token
        mock_get.return_value = mock_response_b
        client_b = DarajaClient(
            consumer_key=self.config_b.daraja_consumer_key,
            consumer_secret=self.config_b.daraja_consumer_secret,
            shortcode=self.config_b.shortcode,
            environment=self.config_b.environment,
        )
        token_b = client_b.get_access_token()
        
        # Verify token_b is different and cached separately
        self.assertEqual(token_b, 'token_b')
        self.assertEqual(cache.get(client_b._cache_key_prefix), 'token_b')
        self.assertNotEqual(token_a, token_b)
        
        # Verify SACCO A's token is still cached correctly
        self.assertEqual(cache.get(client_a._cache_key_prefix), 'token_a')

    def test_shortcode_isolation(self):
        """Test that shortcodes are isolated between SACCOs."""
        self.assertNotEqual(self.config_a.shortcode, self.config_b.shortcode)
        self.assertEqual(self.config_a.shortcode, '123456')
        self.assertEqual(self.config_b.shortcode, '789012')

    def test_b2c_credentials_isolation(self):
        """Test that B2C credentials are isolated between SACCOs."""
        self.assertNotEqual(
            self.config_a.b2c_initiator_name,
            self.config_b.b2c_initiator_name
        )
        self.assertNotEqual(
            self.config_a.b2c_security_credential,
            self.config_b.b2c_security_credential
        )
        
        self.assertEqual(self.config_a.b2c_initiator_name, 'initiator_a')
        self.assertEqual(self.config_b.b2c_initiator_name, 'initiator_b')
