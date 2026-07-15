from __future__ import annotations

from decimal import Decimal
from typing import Any

import requests
from django.conf import settings

from ..base import BasePSPProvider, CheckoutResult, StatusResult


class IntaSendProvider(BasePSPProvider):
    """Implement the IntaSend PSP strategy for checkout and callback handling."""

    provider_name = "intasend"
    SUCCESS_STATUSES = frozenset({"COMPLETE", "SUCCESS"})
    FAILED_STATUSES = frozenset({"FAILED", "CANCELLED", "RETRY"})

    def __init__(self) -> None:
        self.api_key = getattr(settings, "INTASEND_API_KEY", "")
        self.secret_key = getattr(settings, "INTASEND_SECRET_KEY", "")
        self.webhook_challenge = getattr(settings, "INTASEND_WEBHOOK_CHALLENGE", "")
        self.base_url = (
            getattr(settings, "INTASEND_BASE_URL", "https://sandbox.intasend.com/api/v1/")
        )

    def create_checkout(
        self,
        transaction_id: str,
        phone: str,
        gross_amount: Decimal,
        net_amount: Decimal,
        platform_fee: Decimal,
        sacco,
    ) -> CheckoutResult:
        """Create an IntaSend checkout request and return a normalized result."""
        payload = {
            "phone_number": phone,
            "amount": str(gross_amount),
            "currency": "KES",
            "api_ref": transaction_id,
            "comment": f"Deposit to {sacco.name}",
        }

        if settings.DEBUG:
            return CheckoutResult(
                provider_reference=f"MOCK-INTASEND-{transaction_id[:8]}",
                status="PENDING",
                raw_response=payload,
                success=True,
                error_message="",
            )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url.rstrip('/')}/payment/mpesa-stk-push/"

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
        except requests.Timeout as exc:
            raise RuntimeError("IntaSend checkout request timed out") from exc

        if response.ok:
            details = response.json()
            provider_reference = details.get("invoice_id") or details.get("id") or ""
            return CheckoutResult(
                provider_reference=provider_reference,
                status="PENDING",
                raw_response=details,
                success=True,
                error_message="",
            )

        raise RuntimeError(f"IntaSend checkout failed: {response.text}")

    def verify_webhook(self, request) -> bool:
        """Validate an incoming IntaSend webhook using the shared challenge value."""
        payload = getattr(request, "data", {}) or {}
        challenge = payload.get("challenge")
        return bool(challenge and challenge == self.webhook_challenge)

    def parse_callback(self, payload: dict[str, Any]) -> StatusResult:
        """Translate an IntaSend callback payload into a normalized status result."""
        raw_status = str(payload.get("state", "")).upper()

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
        """Fetch the latest IntaSend payment status for a transaction."""
        if settings.DEBUG:
            return StatusResult(
                is_successful=True,
                is_failed=False,
                is_pending=False,
                provider_status="SUCCESS",
                amount_confirmed=Decimal("0.00"),
            )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url.rstrip('/')}/payment/{transaction_id}/"

        try:
            response = requests.get(url, headers=headers, timeout=30)
        except requests.Timeout as exc:
            raise RuntimeError("IntaSend status request timed out") from exc

        if response.ok:
            payload = response.json()
            return self.parse_callback(payload)

        raise RuntimeError(f"IntaSend status failed: {response.text}")
