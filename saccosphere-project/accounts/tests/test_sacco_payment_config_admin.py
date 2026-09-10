"""
Tests for SaccoPaymentConfigAdmin's secret-field hardening and the
FIELD_ENCRYPTION_KEY system check.

Note on scope: like accounts/tests/test_consent_admin.py, this test
environment has no staticfiles manifest, so a full HTTP GET against a
*successfully rendered* admin change page is not reliable here. Tests that
need to inspect form-render or save behavior for an authorized user exercise
PaymentSecretChangeForm and ModelAdmin.save_model directly instead of going
through the live HTTP changeform_view. The permission-denial case IS tested
over real HTTP too, since PermissionDenied is raised before any template is
touched (same reasoning test_consent_admin.py documents).
"""

from cryptography.fernet import Fernet

from django.contrib import admin
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.admin import PaymentSecretChangeForm
from accounts.checks import check_field_encryption_key
from accounts.models import Sacco, SaccoPaymentConfig, User


class SaccoPaymentConfigAdminFormTestCase(TestCase):
    """PaymentSecretChangeForm must never carry a decrypted secret as
    initial or rendered form data."""

    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Payment Secret SACCO',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.config = SaccoPaymentConfig.objects.create(
            sacco=self.sacco,
            shortcode='123456',
            stk_passkey='super-secret-passkey',
            daraja_consumer_secret='super-secret-consumer-secret',
            b2c_security_credential='super-secret-b2c-credential',
        )

    def test_change_form_never_carries_secret_as_initial_or_rendered_value(self):
        form = PaymentSecretChangeForm(instance=self.config)

        for field_name in (
            'stk_passkey',
            'daraja_consumer_secret',
            'b2c_security_credential',
        ):
            self.assertEqual(form.initial.get(field_name), '')

        rendered = str(form)
        self.assertNotIn('super-secret-passkey', rendered)
        self.assertNotIn('super-secret-consumer-secret', rendered)
        self.assertNotIn('super-secret-b2c-credential', rendered)


class SaccoPaymentConfigAdminSaveTestCase(TestCase):
    """Blank secret fields must leave the stored value untouched; a
    non-blank submission must update it."""

    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Payment Save SACCO',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.config = SaccoPaymentConfig.objects.create(
            sacco=self.sacco,
            shortcode='123456',
            stk_passkey='original-passkey',
            daraja_consumer_secret='original-consumer-secret',
            b2c_security_credential='original-b2c-credential',
        )
        self.model_admin = admin.site._registry[SaccoPaymentConfig]
        self.request = type('_Req', (), {'user': None})()

    def _submit(self, **overrides):
        data = {
            'sacco': str(self.config.sacco_id),
            'shortcode_type': self.config.shortcode_type,
            'shortcode': self.config.shortcode,
            'daraja_consumer_key': self.config.daraja_consumer_key or '',
            'environment': self.config.environment,
            'is_active': 'on' if self.config.is_active else '',
            'stk_passkey': '',
            'daraja_consumer_secret': '',
            'b2c_security_credential': '',
            'b2c_initiator_name': self.config.b2c_initiator_name or '',
        }
        data.update(overrides)
        form = PaymentSecretChangeForm(data=data, instance=self.config)
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save(commit=False)
        self.model_admin.save_model(self.request, obj, form, change=True)
        return obj

    def test_blank_secret_leaves_stored_value_unchanged(self):
        self._submit()

        self.config.refresh_from_db()
        self.assertEqual(self.config.stk_passkey, 'original-passkey')
        self.assertEqual(
            self.config.daraja_consumer_secret,
            'original-consumer-secret',
        )
        self.assertEqual(
            self.config.b2c_security_credential,
            'original-b2c-credential',
        )

    def test_non_blank_secret_updates_stored_value(self):
        self._submit(stk_passkey='rotated-passkey')

        self.config.refresh_from_db()
        self.assertEqual(self.config.stk_passkey, 'rotated-passkey')
        # Fields left blank in this same submission are still untouched.
        self.assertEqual(
            self.config.daraja_consumer_secret,
            'original-consumer-secret',
        )
        self.assertEqual(
            self.config.b2c_security_credential,
            'original-b2c-credential',
        )


class SaccoPaymentConfigAdminPermissionTestCase(TestCase):
    """Only superusers or accounts.manage_payment_secrets holders may view
    or change this admin page - a plain is_staff user must not."""

    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Payment Permission SACCO',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.config = SaccoPaymentConfig.objects.create(
            sacco=self.sacco,
            shortcode='654321',
        )
        self.model_admin = admin.site._registry[SaccoPaymentConfig]
        self.staff_user = User.objects.create_user(
            email='payment-admin-staff@example.com',
            phone_number='+254700000040',
            password='testpass123',
            is_staff=True,
        )

    def test_staff_without_permission_cannot_view_or_change(self):
        request = type('_Req', (), {'user': self.staff_user})()
        self.assertFalse(self.model_admin.has_view_permission(request))
        self.assertFalse(self.model_admin.has_change_permission(request))

    def test_staff_without_permission_gets_403_over_http(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(
            reverse(
                'admin:accounts_saccopaymentconfig_change',
                args=[self.config.id],
            ),
        )
        self.assertEqual(response.status_code, 403)

    def test_user_with_dedicated_permission_can_view_and_change(self):
        content_type = ContentType.objects.get_for_model(SaccoPaymentConfig)
        perm = Permission.objects.get(
            content_type=content_type,
            codename='manage_payment_secrets',
        )
        self.staff_user.user_permissions.add(perm)

        request = type('_Req', (), {'user': self.staff_user})()
        self.assertTrue(self.model_admin.has_view_permission(request))
        self.assertTrue(self.model_admin.has_change_permission(request))

    def test_superuser_can_view_and_change_without_dedicated_permission(self):
        superuser = User.objects.create_superuser(
            email='payment-admin-super@example.com',
            phone_number='+254700000041',
            password='testpass123',
        )
        request = type('_Req', (), {'user': superuser})()
        self.assertTrue(self.model_admin.has_view_permission(request))
        self.assertTrue(self.model_admin.has_change_permission(request))


class FieldEncryptionKeyCheckTestCase(TestCase):
    """The accounts.E001/E002 system check must fail fast on a missing or
    invalid FIELD_ENCRYPTION_KEY, and pass on a valid one."""

    def test_check_fails_when_key_unset(self):
        with override_settings(FIELD_ENCRYPTION_KEY=''):
            errors = check_field_encryption_key(None)

        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, 'accounts.E001')

    def test_check_fails_when_key_invalid(self):
        with override_settings(FIELD_ENCRYPTION_KEY='not-a-valid-fernet-key'):
            errors = check_field_encryption_key(None)

        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, 'accounts.E002')

    def test_check_passes_when_key_valid(self):
        valid_key = Fernet.generate_key().decode()

        with override_settings(FIELD_ENCRYPTION_KEY=valid_key):
            errors = check_field_encryption_key(None)

        self.assertEqual(errors, [])

    def test_check_fails_when_debug_false_and_key_blank(self):
        with override_settings(DEBUG=False, FIELD_ENCRYPTION_KEY=''):
            errors = check_field_encryption_key(None)

        self.assertEqual([e.id for e in errors], ['accounts.E001'])

    def test_check_passes_when_debug_false_and_key_valid(self):
        valid_key = Fernet.generate_key().decode()

        with override_settings(DEBUG=False, FIELD_ENCRYPTION_KEY=valid_key):
            errors = check_field_encryption_key(None)

        self.assertEqual(errors, [])

    def test_check_never_errors_when_debug_true(self):
        valid_key = Fernet.generate_key().decode()

        for key in ('', 'not-a-valid-fernet-key', valid_key):
            with self.subTest(key=key), override_settings(
                DEBUG=True, FIELD_ENCRYPTION_KEY=key,
            ):
                self.assertEqual(check_field_encryption_key(None), [])
