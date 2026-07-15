from __future__ import annotations

from decimal import Decimal

from ..base import BasePSPProvider, CheckoutResult, StatusResult
from .client import TinggClient
from .security import CellulantWebhookVerifier


class CellulantProvider(BasePSPProvider):
    """Implement the Cellulant/Tingg PSP strategy for checkout and status handling."""

    provider_name = "cellulant"
    SUCCESS_STATUSES = frozenset({"PAID", "BILLED", "SUCCESS", "00", "175"})
    FAILED_STATUSES = frozenset({"FAILED", "CANCELLED", "TIMEOUT", "ERROR"})

    def __init__(self) -> None:
        self.client = TinggClient()
        self.verifier = CellulantWebhookVerifier()

    def create_checkout(
        self,
        transaction_id: str,
        phone: str,
        gross_amount: Decimal,
        net_amount: Decimal,
        platform_fee: Decimal,
        sacco,
    ) -> CheckoutResult:
        """Create a Cellulant checkout request and return a normalized result."""
        payload = self.client.build_deposit_payload(
            transaction_id=transaction_id,
            phone=phone,
            gross_amount=gross_amount,
            net_amount=net_amount,
            platform_fee=platform_fee,
            sacco=sacco,
        )
        response = self.client.create_checkout(payload)
        provider_reference = response.get("checkoutRequestID", "")

        return CheckoutResult(
            provider_reference=provider_reference,
            status="PENDING",
            raw_response=response,
            success=True,
            error_message="",
        )

    def verify_webhook(self, request) -> bool:
        """Delegate webhook signature validation to the dedicated verifier."""
        return self.verifier.verify(request)

    def parse_callback(self, payload: dict) -> StatusResult:
        """Translate a Cellulant callback payload into a normalized status result."""
        raw_status = str(payload.get("status", "")).upper()

        if raw_status in self.SUCCESS_STATUSES:
            return StatusResult(
                is_successful=True,
                is_failed=False,
                is_pending=False,
                provider_status=raw_status,
                amount_confirmed=None,
            )

        if raw_status in self.FAILED_STATUSES:
            return StatusResult(
                is_successful=False,
                is_failed=True,
                is_pending=False,
                provider_status=raw_status,
                amount_confirmed=None,
            )

        return StatusResult(
            is_successful=False,
            is_failed=False,
            is_pending=True,
            provider_status=raw_status,
            amount_confirmed=None,
        )

    def query_status(self, transaction_id: str) -> StatusResult:
        """Fetch the latest provider status and normalize it for the app."""
        response = self.client.query_status(transaction_id)
        return self.parse_callback(response)
