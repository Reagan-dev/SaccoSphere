from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

import requests
from django.conf import settings


logger = logging.getLogger(__name__)


class CellulantError(Exception):
    """Raised when the Cellulant API returns an error response."""


class CellulantTimeoutError(CellulantError):
    """Raised when a Cellulant API request times out."""


def normalize_phone(phone: str) -> str:
    """Normalize a Kenyan phone number into the E.164-style format expected by PSPs."""
    cleaned_phone = (phone or "").strip()

    if cleaned_phone.startswith("+254"):
        local_phone = cleaned_phone[4:]
    elif cleaned_phone.startswith("254"):
        local_phone = cleaned_phone[3:]
    elif cleaned_phone.startswith("0"):
        local_phone = cleaned_phone[1:]
    elif cleaned_phone.startswith("7"):
        local_phone = cleaned_phone
    else:
        raise ValueError("Phone number must be a Kenyan mobile number")

    if len(local_phone) != 9 or not local_phone.startswith("7"):
        raise ValueError("Phone number must be a Kenyan mobile number")

    return f"254{local_phone}"


class TinggClient:
    """Thin client for interacting with the Tingg Cellulant API."""

    def __init__(self) -> None:
        self.api_key = getattr(settings, "CELLULANT_API_KEY", "")
        self.secret_key = getattr(settings, "CELLULANT_SECRET_KEY", "")
        self.service_code = getattr(settings, "CELLULANT_SERVICE_CODE", "")
        self.platform_service_code = getattr(
            settings,
            "CELLULANT_PLATFORM_SVC_CODE",
            "",
        )
        self.checkout_url = getattr(settings, "CELLULANT_CHECKOUT_URL", "")
        self.status_url = getattr(settings, "CELLULANT_STATUS_URL", "")

    def _get_headers(self) -> dict[str, str]:
        """Return the default headers required for Tingg API calls."""
        return {
            "Content-Type": "application/json",
            "apiKey": self.api_key,
            "checkoutType": "DIRECT_STK_PUSH",
        }

    def create_checkout(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Submit a checkout request to Tingg or return a mock response in DEBUG mode."""
        if settings.DEBUG:
            logger.info("Skipping real Cellulant checkout call in DEBUG mode")
            logger.debug("Cellulant payload: %s", payload)
            return {
                "checkoutRequestID": "MOCK-CELLULANT",
                "status": "PENDING",
                "message": "mock response",
            }

        try:
            response = requests.post(
                self.checkout_url,
                json=payload,
                headers=self._get_headers(),
                timeout=30,
            )
        except requests.Timeout as exc:
            raise CellulantTimeoutError("Cellulant checkout request timed out") from exc

        if response.ok:
            return response.json()

        if 400 <= response.status_code < 500:
            raise CellulantError(f"Cellulant checkout failed: {response.text}")

        if response.status_code >= 500:
            raise CellulantError("Cellulant service unavailable")

        raise CellulantError(f"Unexpected Cellulant response: {response.text}")

    def build_deposit_payload(
        self,
        transaction_id: str,
        phone: str,
        gross_amount: str | int | float,
        net_amount: str | int | float,
        platform_fee: str | int | float,
        sacco,
    ) -> dict[str, Any]:
        """Build the Tingg checkout payload for a deposit transaction."""
        normalized_phone = normalize_phone(phone)
        return {
            "merchantTransactionID": transaction_id,
            "msisdn": normalized_phone,
            "amount": str(gross_amount),
            "currency": "KES",
            "paymentSplits": [
                {
                    "serviceCode": sacco.cellulant_service_code,
                    "amount": str(net_amount),
                },
                {
                    "serviceCode": settings.CELLULANT_PLATFORM_SVC_CODE,
                    "amount": str(platform_fee),
                },
            ],
        }

    def query_status(self, transaction_id: str) -> dict[str, Any]:
        """Query the Tingg status endpoint for a transaction."""
        params = urlencode({"merchantTransactionID": transaction_id})
        url = f"{self.status_url}?{params}"

        try:
            response = requests.get(url, headers=self._get_headers(), timeout=30)
        except requests.Timeout as exc:
            raise CellulantTimeoutError("Cellulant status request timed out") from exc

        if response.ok:
            return response.json()

        if 400 <= response.status_code < 500:
            raise CellulantError(f"Cellulant status failed: {response.text}")

        if response.status_code >= 500:
            raise CellulantError("Cellulant service unavailable")

        raise CellulantError(f"Unexpected Cellulant response: {response.text}")
