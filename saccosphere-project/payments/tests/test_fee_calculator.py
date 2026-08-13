from decimal import Decimal

from django.test import TestCase

from payments.fee_calculator import SaccoInvoiceFeeCalculator


class InflowFeeTests(TestCase):
    def setUp(self):
        self.calc = SaccoInvoiceFeeCalculator()

    def test_deposit_fee_is_added_on_top(self):
        result = self.calc.calculate('deposit', Decimal('1000'))

        self.assertEqual(result['net_amount'], Decimal('1000'))
        self.assertEqual(result['platform_fee'], Decimal('10.00'))
        self.assertEqual(result['gross_amount'], Decimal('1010.00'))
        self.assertEqual(result['direction'], 'inflow')

    def test_repayment_fee_is_added_on_top(self):
        result = self.calc.calculate('repayment', Decimal('1000'))

        self.assertEqual(result['platform_fee'], Decimal('5.00'))
        self.assertEqual(result['gross_amount'], Decimal('1005.00'))

    def test_inflow_gross_always_exceeds_net_by_fee(self):
        result = self.calc.calculate('deposit', Decimal('2500'))

        self.assertEqual(
            result['gross_amount'] - result['net_amount'],
            result['platform_fee'],
        )


class OutflowFeeTests(TestCase):
    def setUp(self):
        self.calc = SaccoInvoiceFeeCalculator()

    def test_disbursement_tier_boundaries(self):
        cases = [
            (Decimal('500'), Decimal('50')),
            (Decimal('10000'), Decimal('50')),
            (Decimal('10001'), Decimal('100')),
            (Decimal('100000'), Decimal('350')),
            (Decimal('500000'), Decimal('750')),
        ]

        for gross, expected_fee in cases:
            result = self.calc.calculate('disbursement', gross)
            self.assertEqual(result['platform_fee'], expected_fee)
            self.assertEqual(result['net_amount'], gross - expected_fee)

    def test_disbursement_matches_prompt1_worked_example(self):
        result = self.calc.calculate('disbursement', Decimal('100000'))

        self.assertEqual(result['net_amount'], Decimal('99650'))
        self.assertEqual(result['platform_fee'], Decimal('350'))

    def test_withdrawal_tier_boundaries(self):
        cases = [
            (Decimal('1500'), Decimal('15')),
            (Decimal('2000'), Decimal('15')),
            (Decimal('2001'), Decimal('25')),
            (Decimal('25000'), Decimal('100')),
        ]

        for gross, expected_fee in cases:
            result = self.calc.calculate('withdrawal', gross)
            self.assertEqual(result['platform_fee'], expected_fee)
            self.assertEqual(result['net_amount'], gross - expected_fee)

    def test_outflow_gross_always_exceeds_net_by_fee(self):
        result = self.calc.calculate('withdrawal', Decimal('7000'))

        self.assertEqual(
            result['gross_amount'] - result['net_amount'],
            result['platform_fee'],
        )


class FeePreviewContractTests(TestCase):
    """Guard against silently breaking FeePreviewView."""

    def test_output_has_keys_feepreview_depends_on(self):
        calc = SaccoInvoiceFeeCalculator()

        for tx_type, amount in [
            ('deposit', Decimal('1000')),
            ('repayment', Decimal('1000')),
            ('disbursement', Decimal('100000')),
            ('withdrawal', Decimal('5000')),
        ]:
            result = calc.calculate(tx_type, amount)
            for key in ('gross_amount', 'net_amount', 'platform_fee'):
                self.assertIn(key, result)

    def test_unknown_type_raises(self):
        calc = SaccoInvoiceFeeCalculator()

        with self.assertRaises(ValueError):
            calc.calculate('bogus_type', Decimal('100'))
