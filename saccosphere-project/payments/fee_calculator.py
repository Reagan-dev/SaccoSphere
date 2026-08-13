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
        platform_fee = (net_amount * rate).quantize(
            Decimal('0.01'),
            rounding=ROUND_HALF_UP,
        )
        gross_amount = net_amount + platform_fee

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

        platform_fee, tier_desc = self._tiered_fee(gross_amount, tiers)
        net_amount = gross_amount - platform_fee

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
