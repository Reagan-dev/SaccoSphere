from __future__ import annotations

import logging
from decimal import Decimal
from uuid import uuid4

from .base import BasePSPProvider, CheckoutResult, StatusResult


logger = logging.getLogger(__name__)


class MockPSPProvider(BasePSPProvider):
    """Provide a deterministic in-process PSP stub for local development and tests."""

    provider_name = "mock"
    mock_should_succeed: bool = True

    def create_checkout(
        self,
        transaction_id: str,
        phone: str,
        amount: Decimal,
        sacco,
        **kwargs,
    ) -> CheckoutResult:
        """Create a fake checkout response without contacting any external PSP."""
        logger.info(
            "Creating mock checkout for transaction %s with phone %s",
            transaction_id,
            phone,
        )
        provider_reference = f"MOCK-{uuid4().hex[:12]}"
        return CheckoutResult(
            provider_reference=provider_reference,
            status="PENDING",
            raw_response={
                "transaction_id": transaction_id,
                "provider_reference": provider_reference,
                "amount": str(amount),
            },
            success=True,
        )

    def verify_webhook(self, request) -> bool:
        """Accept mock webhook payloads as valid for local and test flows."""
        logger.debug("Mock PSP webhook verification for request %s", request)
        return True

    def parse_callback(self, payload: dict) -> StatusResult:
        """Translate a mock callback payload into a normalized status result."""
        logger.debug("Parsing mock callback payload %s", payload)

        if self.mock_should_succeed:
            return StatusResult(
                is_successful=True,
                is_failed=False,
                is_pending=False,
                provider_status="SUCCESS",
                amount_confirmed=Decimal("0"),
            )

        return StatusResult(
            is_successful=False,
            is_failed=True,
            is_pending=False,
            provider_status="FAILED",
            amount_confirmed=None,
        )

    def query_status(self, transaction_id: str) -> StatusResult:
        """Return a successful mock status result for any transaction identifier."""
        logger.debug("Querying mock PSP status for transaction %s", transaction_id)
        return StatusResult(
            is_successful=True,
            is_failed=False,
            is_pending=False,
            provider_status="SUCCESS",
            amount_confirmed=Decimal("0"),
        )
