from decimal import Decimal

from django.test import TestCase, override_settings

from payments.fee_calculator import  SaccoInvoiceFeeCalculator


@override_settings(
    DISBURSEMENT_TIERS=[
        (Decimal("10000"), Decimal("50.00")),
        (Decimal("30000"), Decimal("100.00")),
        (Decimal("70000"), Decimal("200.00")),
        (Decimal("150000"), Decimal("350.00")),
        (Decimal("300000"), Decimal("500.00")),
        (None, Decimal("750.00")),
    ],
    WITHDRAWAL_TIERS=[
        (Decimal("2000"), Decimal("15.00")),
        (Decimal("5000"), Decimal("25.00")),
        (Decimal("10000"), Decimal("40.00")),
        (Decimal("20000"), Decimal("60.00")),
        (None, Decimal("100.00")),
    ],
)
class SaccoInvoiceFeeCalculatorTests(TestCase):
    def setUp(self):
        self.calculator = SaccoInvoiceFeeCalculator()

    def test_deposit_calculation(self):
        result = self.calculator.calculate("deposit", Decimal("1000"))

        self.assertEqual(result["transaction_type"], "deposit")
        self.assertEqual(result["requested_amount"], Decimal("1000.00"))
        self.assertEqual(result["platform_fee"], Decimal("10.00"))
        self.assertEqual(result["member_amount"], Decimal("1010.00"))
        self.assertEqual(result["ledger_amount"], Decimal("1000.00"))
        self.assertEqual(result["fee_model"], "percentage")
        self.assertEqual(result["rate_applied"], "1.00% of requested amount")
        self.assertIsNone(result["tier_applied"])

    def test_repayment_calculation(self):
        result = self.calculator.calculate("repayment", Decimal("1000"))

        self.assertEqual(result["transaction_type"], "repayment")
        self.assertEqual(result["requested_amount"], Decimal("1000.00"))
        self.assertEqual(result["platform_fee"], Decimal("5.00"))
        self.assertEqual(result["member_amount"], Decimal("1005.00"))
        self.assertEqual(result["ledger_amount"], Decimal("1000.00"))
        self.assertEqual(result["fee_model"], "percentage")
        self.assertEqual(result["rate_applied"], "0.50% of requested amount")
        self.assertIsNone(result["tier_applied"])

    def test_disbursement_tier_boundaries(self):
        cases = [
            (Decimal("1"), Decimal("50.00"), "≤ KES 10,000"),
            (Decimal("10000"), Decimal("50.00"), "≤ KES 10,000"),
            (Decimal("10001"), Decimal("100.00"), "≤ KES 30,000"),
            (Decimal("30000"), Decimal("100.00"), "≤ KES 30,000"),
            (Decimal("30001"), Decimal("200.00"), "≤ KES 70,000"),
            (Decimal("70000"), Decimal("200.00"), "≤ KES 70,000"),
            (Decimal("70001"), Decimal("350.00"), "≤ KES 150,000"),
            (Decimal("150000"), Decimal("350.00"), "≤ KES 150,000"),
            (Decimal("150001"), Decimal("500.00"), "≤ KES 300,000"),
            (Decimal("300000"), Decimal("500.00"), "≤ KES 300,000"),
            (
                Decimal("300001"),
                Decimal("750.00"),
                "exceeds all configured tiers",
            ),
            (
                Decimal("500000"),
                Decimal("750.00"),
                "exceeds all configured tiers",
            ),
        ]

        for amount, expected_fee, tier_fragment in cases:
            with self.subTest(amount=amount):
                result = self.calculator.calculate("disbursement", amount)

                self.assertEqual(
                    result["transaction_type"],
                    "disbursement",
                )
                self.assertEqual(
                    result["requested_amount"],
                    amount.quantize(Decimal("0.01")),
                )
                self.assertEqual(result["platform_fee"], expected_fee)
                self.assertEqual(
                    result["member_amount"],
                    (amount - expected_fee).quantize(Decimal("0.01")),
                )
                self.assertEqual(
                    result["ledger_amount"],
                    amount.quantize(Decimal("0.01")),
                )
                self.assertEqual(result["fee_model"], "tiered_flat")
                self.assertEqual(
                    result["rate_applied"],
                    f"Flat KES {expected_fee}",
                )
                self.assertIn(tier_fragment, result["tier_applied"])

    def test_withdrawal_tier_boundaries(self):
        cases = [
            (Decimal("1"), Decimal("15.00"), "≤ KES 2,000"),
            (Decimal("2000"), Decimal("15.00"), "≤ KES 2,000"),
            (Decimal("2001"), Decimal("25.00"), "≤ KES 5,000"),
            (Decimal("5000"), Decimal("25.00"), "≤ KES 5,000"),
            (Decimal("5001"), Decimal("40.00"), "≤ KES 10,000"),
            (Decimal("10000"), Decimal("40.00"), "≤ KES 10,000"),
            (Decimal("10001"), Decimal("60.00"), "≤ KES 20,000"),
            (Decimal("20000"), Decimal("60.00"), "≤ KES 20,000"),
            (
                Decimal("20001"),
                Decimal("100.00"),
                "exceeds all configured tiers",
            ),
            (
                Decimal("500000"),
                Decimal("100.00"),
                "exceeds all configured tiers",
            ),
        ]

        for amount, expected_fee, tier_fragment in cases:
            with self.subTest(amount=amount):
                result = self.calculator.calculate("withdrawal", amount)

                self.assertEqual(
                    result["transaction_type"],
                    "withdrawal",
                )
                self.assertEqual(
                    result["requested_amount"],
                    amount.quantize(Decimal("0.01")),
                )
                self.assertEqual(result["platform_fee"], expected_fee)
                self.assertEqual(
                    result["member_amount"],
                    (amount - expected_fee).quantize(Decimal("0.01")),
                )
                self.assertEqual(
                    result["ledger_amount"],
                    amount.quantize(Decimal("0.01")),
                )
                self.assertEqual(result["fee_model"], "tiered_flat")
                self.assertEqual(
                    result["rate_applied"],
                    f"Flat KES {expected_fee}",
                )
                self.assertIn(tier_fragment, result["tier_applied"])

    def test_transaction_type_is_normalized(self):
        result = self.calculator.calculate("  DePoSiT  ", Decimal("1000"))

        self.assertEqual(result["transaction_type"], "deposit")
        self.assertEqual(result["platform_fee"], Decimal("10.00"))

    def test_unknown_transaction_type_raises_value_error(self):
        with self.assertRaisesMessage(
            ValueError,
            "Unknown transaction type: transfer",
        ):
            self.calculator.calculate("transfer", Decimal("100"))

    def test_transaction_type_required_raises_value_error(self):
        with self.assertRaisesMessage(
            ValueError,
            "Transaction type is required.",
        ):
            self.calculator.calculate("", Decimal("100"))

    def test_amount_must_be_decimal_instance(self):
        with self.assertRaisesMessage(
            TypeError,
            "Amount must be a Decimal instance.",
        ):
            self.calculator.calculate("deposit", "1000")  # type: ignore[arg-type]

    def test_amount_must_be_greater_than_zero(self):
        with self.assertRaisesMessage(
            ValueError,
            "Transaction amount must be greater than zero.",
        ):
            self.calculator.calculate("deposit", Decimal("0"))

        with self.assertRaisesMessage(
            ValueError,
            "Transaction amount must be greater than zero.",
        ):
            self.calculator.calculate("deposit", Decimal("-10"))

    def test_amount_is_normalized_to_two_decimal_places(self):
        result = self.calculator.calculate("deposit", Decimal("1000.1"))

        self.assertEqual(result["requested_amount"], Decimal("1000.10"))
        self.assertEqual(result["platform_fee"], Decimal("10.00"))
        self.assertEqual(result["member_amount"], Decimal("1010.10"))
        self.assertEqual(result["ledger_amount"], Decimal("1000.10"))