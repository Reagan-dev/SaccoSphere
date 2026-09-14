"""
payments/fee_calculator.py

Enterprise-grade transaction fee calculator for SaccoSphere.

This module calculates the complete financial breakdown for a member
transaction.

Business Rules
--------------
1. Deposit
    - Member enters KES X.
    - Platform fee is added.
    - STK Push requests X + fee.
    - SACCO ledger records only X.

2. Repayment
    - Member enters repayment amount.
    - Platform fee is added.
    - Member pays repayment + fee.
    - Loan ledger records only repayment amount.

3. Disbursement
    - Loan is approved for KES X.
    - Platform fee is deducted.
    - Member receives X - fee.
    - Loan ledger records X.

4. Withdrawal
    - Withdrawal requested for KES X.
    - Platform fee is deducted.
    - Member receives X - fee.
    - Savings ledger records X.

Platform fees are later summarized by the Billing module into monthly
SACCO invoices for reconciliation and reporting.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings


PERCENTAGE_FEES = {
    "deposit": Decimal("0.01"),
    "repayment": Decimal("0.005"),
}


class SaccoInvoiceFeeCalculator:
    """
    Calculates the financial outcome of a transaction.

    This class is responsible ONLY for fee computation.

    It does not:
        - Create transactions
        - Post ledger entries
        - Generate invoices
        - Move money

    Those responsibilities belong to higher-level services.
    """

    CURRENCY_PRECISION = Decimal("0.01")

    DEPOSIT = "deposit"
    REPAYMENT = "repayment"
    DISBURSEMENT = "disbursement"
    WITHDRAWAL = "withdrawal"

    def calculate(
        self,
        transaction_type: str,
        amount: Decimal,
    ) -> dict[str, Decimal | str | None]:
        """
        Calculate the complete fee breakdown for a transaction.

        Parameters
        ----------
        transaction_type
            deposit
            repayment
            disbursement
            withdrawal

        amount
            Requested transaction amount.

        Returns
        -------
        dict
            Complete transaction fee breakdown.
        """

        if not transaction_type:
            raise ValueError(
                "Transaction type is required."
            )

        if not isinstance(amount, Decimal):
            raise TypeError(
                "Amount must be a Decimal instance."
            )

        amount = self._normalize_amount(amount)

        if amount <= Decimal("0.00"):
            raise ValueError(
                "Transaction amount must be greater than zero."
            )

        transaction_type = transaction_type.strip().lower()

        if transaction_type == self.DEPOSIT:

            fee = (
                amount
                * PERCENTAGE_FEES[self.DEPOSIT]
            ).quantize(
                self.CURRENCY_PRECISION,
                rounding=ROUND_HALF_UP,
            )

            return {
                "transaction_type": self.DEPOSIT,
                "requested_amount": amount,
                "member_amount": self._normalize_amount(
                    amount + fee
                ),
                "ledger_amount": amount,
                "platform_fee": fee,
                "fee_model": "percentage",
                "rate_applied": "1.00% of requested amount",
                "tier_applied": None,
            }

        if transaction_type == self.REPAYMENT:

            fee = (
                amount
                * PERCENTAGE_FEES[self.REPAYMENT]
            ).quantize(
                self.CURRENCY_PRECISION,
                rounding=ROUND_HALF_UP,
            )

            return {
                "transaction_type": self.REPAYMENT,
                "requested_amount": amount,
                "member_amount": self._normalize_amount(
                    amount + fee
                ),
                "ledger_amount": amount,
                "platform_fee": fee,
                "fee_model": "percentage",
                "rate_applied": "0.50% of requested amount",
                "tier_applied": None,
            }

        if transaction_type == self.DISBURSEMENT:

            fee, tier = self._tiered_fee(
                amount,
                settings.DISBURSEMENT_TIERS,
            )

            return {
                "transaction_type": self.DISBURSEMENT,
                "requested_amount": amount,
                "member_amount": self._normalize_amount(
                    amount - fee
                ),
                "ledger_amount": amount,
                "platform_fee": fee,
                "fee_model": "tiered_flat",
                "rate_applied": f"Flat KES {fee}",
                "tier_applied": tier,
            }

        if transaction_type == self.WITHDRAWAL:

            fee, tier = self._tiered_fee(
                amount,
                settings.WITHDRAWAL_TIERS,
            )

            return {
                "transaction_type": self.WITHDRAWAL,
                "requested_amount": amount,
                "member_amount": self._normalize_amount(
                    amount - fee
                ),
                "ledger_amount": amount,
                "platform_fee": fee,
                "fee_model": "tiered_flat",
                "rate_applied": f"Flat KES {fee}",
                "tier_applied": tier,
            }

        raise ValueError(
            f"Unknown transaction type: {transaction_type}"
        )

    def _tiered_fee(
        self,
        amount: Decimal,
        tiers: list[tuple],
    ) -> tuple[Decimal, str]:
        """
        Determine the configured tiered platform fee.

        Returns
        -------
        tuple
            (platform_fee, tier_description)
        """

        for ceiling, fee in tiers:

            if ceiling is None or amount <= ceiling:

                if ceiling is None:

                    description = (
                        f"KES {amount:,.0f} exceeds all "
                        "configured tiers (maximum fee applied)."
                    )

                else:

                    description = (
                        f"KES {amount:,.0f} falls within "
                        f"tier (≤ KES {ceiling:,.0f})."
                    )

                return (
                    self._normalize_amount(fee),
                    description,
                )

        raise ValueError(
            "Invalid tier configuration. "
            "The final tier must have ceiling=None."
        )

    def _normalize_amount(
        self,
        amount: Decimal,
    ) -> Decimal:
        """
        Normalize a monetary value to two decimal places.
        """

        return amount.quantize(
            self.CURRENCY_PRECISION,
            rounding=ROUND_HALF_UP,
        )