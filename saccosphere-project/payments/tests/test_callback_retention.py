"""Callback.raw_payload encryption at rest + its retention sweep.

raw_payload carries the member's phone number and, for B2C, their name -
personal data under Kenya's DPA 2019 - so it is now routed through the
same Fernet-encrypted-field infrastructure that already protects
id_number and CRB raw responses (accounts.models.EncryptedJSONField),
and is purged by a scheduled retention sweep
(payments.tasks.purge_expired_callbacks / the purge_expired_callbacks
management command), since encryption alone is not a retention policy.
"""

from io import StringIO

from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from payments.models import Callback, PaymentProvider
from payments.tasks import purge_expired_callbacks as purge_task


def _raw_column(table, column):
    """Every value of one column, straight from the DB (no ORM field)."""
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT {column} FROM {table}')
        return [row[0] for row in cursor.fetchall()]


def _fernet_looking(value):
    return isinstance(value, str) and value.startswith('gAAAAA')


class CallbackEncryptionTests(TestCase):
    """Encryption round-trips correctly, and existing code reading
    raw_payload still works through the new accessor - plain attribute
    access, unchanged for every caller in payments.tasks/payments.views.
    """

    def setUp(self):
        self.provider = PaymentProvider.objects.create(
            name='M-Pesa',
            provider_type=PaymentProvider.ProviderType.MPESA,
            is_active=True,
        )

    def test_raw_payload_ciphertext_in_db_dict_via_orm(self):
        payload = {
            'Body': {
                'stkCallback': {
                    'CheckoutRequestID': 'ws_CO_ENC_001',
                    'ResultCode': 0,
                    'CallbackMetadata': {
                        'Item': [
                            {'Name': 'PhoneNumber', 'Value': 254712345678},
                            {'Name': 'Amount', 'Value': 500},
                        ],
                    },
                },
            },
        }
        callback = Callback.objects.create(
            provider=self.provider,
            raw_payload=payload,
        )

        raw = _raw_column('payments_callback', 'raw_payload')[0]
        self.assertTrue(_fernet_looking(raw))
        self.assertNotIn('254712345678', raw)
        self.assertNotIn('ws_CO_ENC_001', raw)

        # ORM round-trips to the original dict.
        reloaded = Callback.objects.get(pk=callback.pk)
        self.assertEqual(reloaded.raw_payload, payload)
        self.assertEqual(
            reloaded.raw_payload['Body']['stkCallback']['ResultCode'], 0,
        )

    def test_encryption_is_non_deterministic(self):
        field = Callback._meta.get_field('raw_payload')
        self.assertNotEqual(
            field.get_prep_value({'a': 1}),
            field.get_prep_value({'a': 1}),
        )

    def test_existing_callers_still_read_via_attribute_access(self):
        """Mirrors payments.tasks' ``callback_body = callback.raw_payload``
        and payments.views' ``Callback.objects.create(raw_payload=...)``
        - a plain dict in, a plain dict out, no call-site changes."""
        callback = Callback.objects.create(
            provider=self.provider,
            raw_payload={'callback_type': 'STK', 'payload': {'x': 1}},
        )

        callback_body = callback.raw_payload
        self.assertEqual(callback_body['callback_type'], 'STK')
        self.assertEqual(callback_body['payload'], {'x': 1})


class CallbackRetentionSweepTests(TestCase):
    def setUp(self):
        self.provider = PaymentProvider.objects.create(
            name='M-Pesa',
            provider_type=PaymentProvider.ProviderType.MPESA,
            is_active=True,
        )

    def _callback(self, *, received_days_ago):
        callback = Callback.objects.create(
            provider=self.provider,
            raw_payload={'CheckoutRequestID': f'ws_CO_{received_days_ago}'},
        )
        Callback.objects.filter(pk=callback.pk).update(
            received_at=(
                timezone.now() - timezone.timedelta(days=received_days_ago)
            ),
        )
        callback.refresh_from_db()
        return callback

    def test_retention_sweep_removes_expired_leaves_fresh_untouched(self):
        expired = self._callback(received_days_ago=91)
        fresh = self._callback(received_days_ago=1)

        with self.settings(CALLBACK_RETENTION_DAYS=90):
            call_command('purge_expired_callbacks')

        self.assertFalse(Callback.objects.filter(pk=expired.pk).exists())
        self.assertTrue(Callback.objects.filter(pk=fresh.pk).exists())

    def test_dry_run_makes_no_changes(self):
        expired = self._callback(received_days_ago=91)

        output = StringIO()
        with self.settings(CALLBACK_RETENTION_DAYS=90):
            call_command(
                'purge_expired_callbacks', '--dry-run', stdout=output,
            )

        self.assertTrue(Callback.objects.filter(pk=expired.pk).exists())
        self.assertIn('Dry run', output.getvalue())

    def test_sweep_is_a_noop_when_retention_unconfigured(self):
        expired = self._callback(received_days_ago=9999)

        with self.settings(CALLBACK_RETENTION_DAYS=None):
            call_command('purge_expired_callbacks')

        self.assertTrue(Callback.objects.filter(pk=expired.pk).exists())

    def test_task_wrapper_invokes_the_command(self):
        expired = self._callback(received_days_ago=91)
        fresh = self._callback(received_days_ago=1)

        with self.settings(CALLBACK_RETENTION_DAYS=90):
            result = purge_task()

        self.assertFalse(Callback.objects.filter(pk=expired.pk).exists())
        self.assertTrue(Callback.objects.filter(pk=fresh.pk).exists())
        self.assertIn('Deleted 1', result)
