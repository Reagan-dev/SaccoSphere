"""Tests for SACCO-specific payment configuration and credential isolation."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from accounts.models import Sacco, SaccoPaymentConfig, User
from payments.integrations.mpesa.daraja import DarajaClient
from payments.models import MpesaTransaction, PaymentProvider, Transaction
from payments.tasks import (
    SaccoPaymentConfigUnavailable,
    get_daraja_client_for_sacco,
    get_daraja_client_for_transaction,
    reconcile_stale_mpesa_transactions,
)
from saccomembership.models import Membership
from services.models import Saving, SavingsType


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


class ReconciliationDarajaClientScopingTestCase(TestCase):
    """Stale-STK reconciliation must query Daraja with the initiating
    SACCO's own credentials, resolved at query time - mirrors
    DarajaClientCredentialIsolationTestCase for the reconciliation path.
    """

    def setUp(self):
        cache.clear()
        self.provider = PaymentProvider.objects.create(
            name='M-Pesa',
            provider_type=PaymentProvider.ProviderType.MPESA,
            is_active=True,
        )
        self.user = User.objects.create_user(
            email='recon-scope-member@example.com',
            phone_number='254712600001',
            password='StrongPass1',
        )

        self.sacco_a, self.config_a, self.mpesa_a = self._make_sacco(
            slug='A',
            shortcode='111111',
            consumer_key='ck_a',
            passkey='pk_a',
        )
        self.sacco_b, self.config_b, self.mpesa_b = self._make_sacco(
            slug='B',
            shortcode='222222',
            consumer_key='ck_b',
            passkey='pk_b',
        )

        # Age both M-Pesa rows past the reconciliation threshold (5 min).
        stale = timezone.now() - timedelta(minutes=10)
        MpesaTransaction.objects.filter(
            pk__in=[self.mpesa_a.pk, self.mpesa_b.pk],
        ).update(created_at=stale)

    def _make_sacco(self, *, slug, shortcode, consumer_key, passkey):
        sacco = Sacco.objects.create(
            name=f'Recon Scope SACCO {slug}',
            registration_number=f'RSC-{slug}',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            is_active=True,
        )
        config = SaccoPaymentConfig.objects.create(
            sacco=sacco,
            shortcode_type=SaccoPaymentConfig.ShortcodeType.PAYBILL,
            shortcode=shortcode,
            stk_passkey=passkey,
            daraja_consumer_key=consumer_key,
            daraja_consumer_secret=f'secret_{slug.lower()}',
            environment=SaccoPaymentConfig.Environment.SANDBOX,
            is_active=True,
        )
        membership = Membership.objects.create(
            user=self.user,
            sacco=sacco,
            status=Membership.Status.APPROVED,
            member_number=f'RSC-{slug}-M-001',
        )
        savings_type = SavingsType.objects.create(
            sacco=sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        saving = Saving.objects.create(
            membership=membership,
            savings_type=savings_type,
            amount=Decimal('100.00'),
            total_contributions=Decimal('100.00'),
            status=Saving.Status.ACTIVE,
        )
        transaction = Transaction.objects.create(
            provider=self.provider,
            user=self.user,
            reference=f'SS-RSC-{slug}',
            transaction_type=Transaction.TransactionType.DEPOSIT,
            amount=Decimal('100.00'),
            sacco=sacco,
            status=Transaction.Status.PENDING,
            description='Reconciliation scoping test',
        )
        mpesa = MpesaTransaction.objects.create(
            transaction=transaction,
            phone_number='254712600001',
            transaction_type=MpesaTransaction.TransactionType.STK_PUSH,
            checkout_request_id=f'ws_CO_RSC_{slug}',
            related_saving=saving,
        )
        return sacco, config, mpesa

    def _patch_query(self):
        """Patch DarajaClient.query_stk_status, capturing the credentials
        the resolved client carries, keyed by checkout_request_id. Returns
        an empty dict so _process_daraja_status_response is a no-op (no
        crediting side effects)."""
        captured = {}

        def _capture(client, checkout_request_id):
            captured[checkout_request_id] = {
                'consumer_key': client.consumer_key,
                'consumer_secret': client.consumer_secret,
                'shortcode': client.shortcode,
                'passkey': client.passkey,
                'environment': client.environment,
            }
            return {}

        patcher = patch.object(
            DarajaClient,
            'query_stk_status',
            autospec=True,
            side_effect=_capture,
        )
        return captured, patcher

    def test_get_daraja_client_for_transaction_uses_each_saccos_config(self):
        client_a = get_daraja_client_for_transaction(self.mpesa_a)
        client_b = get_daraja_client_for_transaction(self.mpesa_b)

        self.assertEqual(client_a.consumer_key, 'ck_a')
        self.assertEqual(client_a.shortcode, '111111')
        self.assertEqual(client_a.passkey, 'pk_a')

        self.assertEqual(client_b.consumer_key, 'ck_b')
        self.assertEqual(client_b.shortcode, '222222')
        self.assertEqual(client_b.passkey, 'pk_b')

    def test_batch_resolves_each_transaction_with_its_own_credentials(self):
        captured, patcher = self._patch_query()
        with patcher:
            reconcile_stale_mpesa_transactions()

        self.assertEqual(
            captured['ws_CO_RSC_A'],
            {
                'consumer_key': 'ck_a',
                'consumer_secret': 'secret_a',
                'shortcode': '111111',
                'passkey': 'pk_a',
                'environment': SaccoPaymentConfig.Environment.SANDBOX,
            },
        )
        self.assertEqual(
            captured['ws_CO_RSC_B'],
            {
                'consumer_key': 'ck_b',
                'consumer_secret': 'secret_b',
                'shortcode': '222222',
                'passkey': 'pk_b',
                'environment': SaccoPaymentConfig.Environment.SANDBOX,
            },
        )

    def test_inactive_config_fails_only_that_transaction_not_the_batch(self):
        self.config_b.is_active = False
        self.config_b.save(update_fields=['is_active'])

        captured, patcher = self._patch_query()
        with patcher:
            result = reconcile_stale_mpesa_transactions()

        # SACCO A still reconciled with its own credentials.
        self.assertIn('ws_CO_RSC_A', captured)
        self.assertEqual(captured['ws_CO_RSC_A']['consumer_key'], 'ck_a')

        # SACCO B never reached Daraja and did not crash the run.
        self.assertNotIn('ws_CO_RSC_B', captured)
        self.assertEqual(result['failed'], 1)

        self.mpesa_b.transaction.refresh_from_db()
        meta_b = self.mpesa_b.transaction.metadata
        self.assertEqual(meta_b['reconciliation_attempts'], 1)
        self.assertIn('inactive', meta_b['last_reconciliation_error'].lower())

        # SACCO B's transaction is still non-terminal - it drops into the
        # manual-review path once it exhausts its reconciliation attempts.
        self.assertEqual(
            self.mpesa_b.transaction.status,
            Transaction.Status.PENDING,
        )

    def test_missing_config_raises_sacco_payment_config_unavailable(self):
        sacco_c = Sacco.objects.create(
            name='Recon Scope SACCO C',
            registration_number='RSC-C',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            is_active=True,
        )
        with self.assertRaises(SaccoPaymentConfigUnavailable):
            get_daraja_client_for_sacco(sacco_c)

    def test_inactive_config_raises_sacco_payment_config_unavailable(self):
        self.config_a.is_active = False
        self.config_a.save(update_fields=['is_active'])
        with self.assertRaises(SaccoPaymentConfigUnavailable):
            get_daraja_client_for_sacco(self.sacco_a)
