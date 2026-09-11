"""
payments/fee_calculator.py

Calculates what the SACCO will be invoiced by SaccoSphere for a
transaction. SaccoSphere never touches member money directly -- see the
confirmed business model. This calculator is the single source of truth
for that math, and its output feeds directly into FeePreviewView.
"""

from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings


class SaccoInvoiceFeeCalculator:
    """
    Calculate fee breakdowns for SaccoSphere invoice line items.

    Inflows receive the net amount and add the platform fee on top. Outflows
    receive the gross approved amount and subtract a tiered flat fee.
    Every result includes gross_amount, net_amount, and platform_fee so
    FeePreviewView can consume the dict without key translation.

    Whole-shilling policy
    ----------------------
    M-Pesa only ever moves whole-shilling amounts - both
    DarajaClient.initiate_stk_push and .initiate_b2c cast their Amount
    field to int(), truncating anything fractional. Whichever side of a
    breakdown is the one actually sent to Daraja (gross for an inflow's
    STK push, net for an outflow's B2C payout) is rounded to the nearest
    whole KES *here*, once, at calculation time - not later, at the
    moment it happens to be handed to Daraja. Every caller (initiation,
    the Transaction.gross_amount/platform_fee stored at creation, the
    callback's expected-amount comparison, and the invoiced fee) reads
    that same already-rounded value, so they can never drift apart.

    The platform fee absorbs the rounding delta - it is the SACCO's
    *actual* fee (rounded value minus the anchor amount), not the
    theoretical percentage/tier figure. This is a deliberate choice, not
    an accident: crediting a member's deposit for a sub-shilling amount
    Daraja never actually moved, or silently shorting the SACCO's
    invoice, would both be worse than the fee absorbing a fraction of a
    shilling either way.
    """

    INFLOW_TYPES = ('deposit', 'repayment')
    OUTFLOW_TYPES = ('disbursement', 'withdrawal')

    def calculate(self, transaction_type: str, amount: Decimal) -> dict:
        transaction_type = (transaction_type or '').strip().lower()
        amount = Decimal(amount)

        if transaction_type in self.INFLOW_TYPES:
            return self._calculate_inflow(transaction_type, amount)

        if transaction_type in self.OUTFLOW_TYPES:
            return self._calculate_outflow(transaction_type, amount)

        raise ValueError(f'Unknown transaction type: {transaction_type}')

    def _calculate_inflow(
        self,
        transaction_type: str,
        net_amount: Decimal,
    ) -> dict:
        rate = settings.PLATFORM_FEES[transaction_type]
        raw_fee = (net_amount * rate).quantize(
            Decimal('0.01'),
            rounding=ROUND_HALF_UP,
        )

        # gross_amount is what STK Push sends to Daraja - round it to a
        # whole shilling here (see class docstring) and let the fee
        # absorb the difference, instead of storing a fractional
        # "expected" amount a whole-shilling callback can never match.
        gross_amount = self._round_to_whole_shilling(net_amount + raw_fee)
        platform_fee = gross_amount - net_amount

        return {
            'transaction_type': transaction_type,
            'direction': 'inflow',
            'net_amount': net_amount,
            'platform_fee': platform_fee,
            'gross_amount': gross_amount,
            'fee_rate': rate,
            'fee_model': 'percentage',
            'rate_applied': (
                f'{rate * Decimal("100"):.1f}% of {transaction_type} amount'
            ),
            'tier_applied': None,
        }

    def _calculate_outflow(
        self,
        transaction_type: str,
        gross_amount: Decimal,
    ) -> dict:
        if transaction_type == 'disbursement':
            tiers = settings.DISBURSEMENT_TIERS
        else:
            tiers = settings.WITHDRAWAL_TIERS

        raw_fee, tier_desc = self._tiered_fee(gross_amount, tiers)

        # net_amount is what B2C sends to Daraja - round it to a whole
        # shilling here (see class docstring) and let the fee absorb the
        # difference. In the common case gross_amount and the tiered fee
        # are both already whole, so this is a no-op; it only bites when
        # gross_amount itself is fractional (e.g. a non-whole loan
        # amount) or a tier fee has been configured with cents.
        net_amount = self._round_to_whole_shilling(gross_amount - raw_fee)
        platform_fee = gross_amount - net_amount

        return {
            'transaction_type': transaction_type,
            'direction': 'outflow',
            'gross_amount': gross_amount,
            'platform_fee': platform_fee,
            'net_amount': net_amount,
            'fee_rate': None,
            'fee_model': 'tiered_flat',
            'rate_applied': f'Flat KES {platform_fee} (tiered)',
            'tier_applied': tier_desc,
        }

    def _round_to_whole_shilling(self, amount: Decimal) -> Decimal:
        """Round to the nearest whole KES (see class docstring).

        Rounds to zero decimal places, then re-expresses at 2 decimal
        places so every amount in a breakdown keeps the same precision -
        the value itself has no fractional component either way.
        """
        return amount.quantize(
            Decimal('1'),
            rounding=ROUND_HALF_UP,
        ).quantize(Decimal('0.01'))

    def _tiered_fee(
        self,
        amount: Decimal,
        tiers: list,
    ) -> tuple[Decimal, str]:
        """Walk tiers lowest to highest and return fee plus description."""
        for ceiling, fee in tiers:
            if ceiling is None or amount <= ceiling:
                if ceiling is None:
                    desc = (
                        f'KES {amount:,.0f} exceeds all tiers (capped fee)'
                    )
                else:
                    desc = (
                        f'KES {amount:,.0f} falls in tier <= '
                        f'KES {ceiling:,.0f}'
                    )
                return fee, desc

        raise ValueError('Tier configuration error -- no ceiling=None entry')
