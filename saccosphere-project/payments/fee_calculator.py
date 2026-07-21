from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from typing import Dict

from django.conf import settings


class SaccoInvoiceFeeCalculator:
    """Calculate platform fee breakdowns for different transaction types.

    - For inflows (deposit, repayment) the provided `amount` is treated as
      the net amount the member intends to deposit/repay. The member pays
      gross = net + fee.
    - For outflows (disbursement, withdrawal) the provided `amount` is
      treated as the gross/approved amount; the member receives net = gross - fee.
    """

    DEFAULT_FEES: Dict[str, Decimal] = {
        'deposit': Decimal('0.01'),
        'repayment': Decimal('0.01'),
        'disbursement': Decimal('0.0035'),
        'withdrawal': Decimal('0.0035'),
    }

    def _get_fee_rate(self, tx_type: str) -> Decimal:
        fees = getattr(settings, 'PLATFORM_FEES', None)
        if isinstance(fees, dict) and tx_type in fees:
            return Decimal(str(fees[tx_type]))
        return self.DEFAULT_FEES.get(tx_type, Decimal('0.01'))

    def calculate(self, tx_type: str, amount: Decimal) -> dict:
        tx_type = (tx_type or '').strip().lower()
        fee_rate = self._get_fee_rate(tx_type)

        if tx_type in ('deposit', 'repayment'):
            net_amount = Decimal(amount)
            platform_fee = (net_amount * fee_rate).quantize(Decimal('0.01'))
            gross_amount = (net_amount + platform_fee).quantize(Decimal('0.01'))
        else:
            gross_amount = Decimal(amount)
            platform_fee = (gross_amount * fee_rate).quantize(Decimal('0.01'))
            net_amount = (gross_amount - platform_fee).quantize(Decimal('0.01'))

        return {
            'tx_type': tx_type,
            'fee_rate': fee_rate,
            'net_amount': net_amount,
            'platform_fee': platform_fee,
            'gross_amount': gross_amount,
        }
