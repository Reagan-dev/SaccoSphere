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


class InflowWholeShillingRoundingTests(TestCase):
    """gross_amount - the value sent to Daraja for an inflow - is always
    a whole KES; the fee absorbs the rounding delta. See
    SaccoInvoiceFeeCalculator's whole-shilling policy docstring."""

    def setUp(self):
        self.calc = SaccoInvoiceFeeCalculator()

    def test_deposit_gross_rounds_up_from_exact_half(self):
        # net=550, fee=5.50 (1%) -> theoretical gross 555.50.
        result = self.calc.calculate('deposit', Decimal('550'))

        self.assertEqual(result['gross_amount'], Decimal('556.00'))
        self.assertEqual(result['platform_fee'], Decimal('6.00'))

    def test_deposit_gross_rounds_up_exact_half_larger_amount(self):
        # net=1250, fee=12.50 -> theoretical gross 1262.50.
        result = self.calc.calculate('deposit', Decimal('1250'))

        self.assertEqual(result['gross_amount'], Decimal('1263.00'))
        self.assertEqual(result['platform_fee'], Decimal('13.00'))

    def test_deposit_gross_rounds_down_below_half(self):
        # net=333, fee=3.33 -> theoretical gross 336.33.
        result = self.calc.calculate('deposit', Decimal('333'))

        self.assertEqual(result['gross_amount'], Decimal('336.00'))
        self.assertEqual(result['platform_fee'], Decimal('3.00'))

    def test_repayment_gross_rounds_up_from_exact_half(self):
        # net=550, fee=2.75 (0.5%) -> theoretical gross 552.75.
        result = self.calc.calculate('repayment', Decimal('550'))

        self.assertEqual(result['gross_amount'], Decimal('553.00'))
        self.assertEqual(result['platform_fee'], Decimal('3.00'))

    def test_repayment_gross_rounds_down_below_half(self):
        # net=1250, fee=6.25 -> theoretical gross 1256.25.
        result = self.calc.calculate('repayment', Decimal('1250'))

        self.assertEqual(result['gross_amount'], Decimal('1256.00'))
        self.assertEqual(result['platform_fee'], Decimal('6.00'))

    def test_gross_is_always_a_whole_shilling(self):
        amounts = (
            Decimal('1'), Decimal('7'), Decimal('99'), Decimal('100'),
            Decimal('333'), Decimal('550'), Decimal('1250'),
            Decimal('12345'), Decimal('299999'),
        )
        for net_amount in amounts:
            for tx_type in ('deposit', 'repayment'):
                with self.subTest(tx_type=tx_type, net_amount=net_amount):
                    result = self.calc.calculate(tx_type, net_amount)
                    gross = result['gross_amount']
                    self.assertEqual(gross, gross.to_integral_value())
                    self.assertEqual(
                        gross - net_amount, result['platform_fee'],
                    )

    def test_whole_net_amount_is_unaffected(self):
        # 1000 * 1% = 10.00 exactly - already whole, rounding is a no-op.
        result = self.calc.calculate('deposit', Decimal('1000'))

        self.assertEqual(result['gross_amount'], Decimal('1010.00'))
        self.assertEqual(result['platform_fee'], Decimal('10.00'))


class OutflowWholeShillingRoundingTests(TestCase):
    """net_amount - the value sent to Daraja's B2C payout for an outflow
    - is always a whole KES, defensively, for the (currently rare) case
    gross_amount itself is fractional (a non-whole loan amount or
    requested withdrawal)."""

    def setUp(self):
        self.calc = SaccoInvoiceFeeCalculator()

    def test_non_whole_loan_amount_still_produces_whole_net(self):
        result = self.calc.calculate('disbursement', Decimal('50000.75'))

        net = result['net_amount']
        self.assertEqual(net, net.to_integral_value())
        self.assertEqual(
            result['gross_amount'] - result['net_amount'],
            result['platform_fee'],
        )

    def test_non_whole_withdrawal_amount_still_produces_whole_net(self):
        result = self.calc.calculate('withdrawal', Decimal('5000.37'))

        net = result['net_amount']
        self.assertEqual(net, net.to_integral_value())
        self.assertEqual(
            result['gross_amount'] - result['net_amount'],
            result['platform_fee'],
        )

    def test_whole_gross_amount_is_unaffected(self):
        result = self.calc.calculate('withdrawal', Decimal('5000.00'))

        self.assertEqual(result['net_amount'], Decimal('4975.00'))
        self.assertEqual(result['platform_fee'], Decimal('25.00'))


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
