"""
Tests for SaccoAdmin's registration_fee/loan_multiplier readonly guard.

Note on scope: like accounts/tests/test_consent_admin.py, this test
environment has no staticfiles manifest, so a full HTTP GET/POST against a
successfully-rendered admin change page is not reliable here. This test
checks the ModelAdmin's form/readonly_fields directly instead - the actual
mechanism that makes the fields non-editable.
"""

from decimal import Decimal

from django.contrib import admin
from django.test import TestCase

from accounts.models import Sacco


class SaccoAdminReadonlyFieldsTestCase(TestCase):
    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Readonly Fields SACCO',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            registration_fee=Decimal('500.00'),
            loan_multiplier=Decimal('3.00'),
        )
        self.model_admin = admin.site._registry[Sacco]

    def test_registration_fee_and_loan_multiplier_are_readonly(self):
        request = type('_Req', (), {'user': None})()

        readonly_fields = self.model_admin.get_readonly_fields(
            request, self.sacco,
        )

        self.assertIn('registration_fee', readonly_fields)
        self.assertIn('loan_multiplier', readonly_fields)

    def test_change_form_excludes_readonly_fields_from_editable_fields(self):
        """
        The actual guarantee: these fields are excluded from the form
        entirely, so no submitted value for them can ever be applied.
        """
        request = type('_Req', (), {'user': None})()
        form_class = self.model_admin.get_form(request, self.sacco)
        form = form_class(instance=self.sacco)

        self.assertNotIn('registration_fee', form.fields)
        self.assertNotIn('loan_multiplier', form.fields)

    def test_direct_field_assignment_via_admin_form_is_ignored(self):
        """
        Simulate a submission attempting to change a readonly field - since
        it isn't a form field at all, the submitted value has no effect.
        """
        request = type('_Req', (), {'user': None})()
        form_class = self.model_admin.get_form(request, self.sacco)
        form = form_class(
            data={
                'name': self.sacco.name,
                'sector': self.sacco.sector,
                'county': self.sacco.county,
                'membership_type': self.sacco.membership_type,
                'default_interest_rate': self.sacco.default_interest_rate,
                'min_loan_months': self.sacco.min_loan_months,
                'next_member_number_seq': self.sacco.next_member_number_seq,
                # Attempted change to a readonly field - must be ignored.
                'registration_fee': '999999.00',
            },
            instance=self.sacco,
        )

        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()

        self.assertEqual(saved.registration_fee, Decimal('500.00'))
