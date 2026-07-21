from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from accounts.models import Sacco


@dataclass(slots=True)
class CheckoutResult:
    """Represents the checkout response returned by a PSP provider."""

    provider_reference: str
    status: str
    raw_response: dict
    success: bool
    error_message: str = ""


@dataclass(slots=True)
class StatusResult:
    """Represents the status payload returned by a PSP provider."""

    is_successful: bool
    is_failed: bool
    is_pending: bool
    provider_status: str
    amount_confirmed: Decimal | None = None


class BasePSPProvider(ABC):
    """Define the contract for PSP implementations used by the app."""

    provider_name: str = "base"

    @abstractmethod
    def create_checkout(
        self,
        transaction_id: str,
        phone: str,
        amount: Decimal,
        sacco: "Sacco",
        **kwargs,
    ) -> CheckoutResult:
        """Create a checkout request for a payment transaction."""

    @abstractmethod
    def verify_webhook(self, request) -> bool:
        """Validate that an incoming PSP webhook request is genuine."""

    @abstractmethod
    def parse_callback(self, payload: dict) -> StatusResult:
        """Convert a provider callback payload into a normalized status result."""

    @abstractmethod
    def query_status(self, transaction_id: str) -> StatusResult:
        """Fetch the latest known payment status for a transaction."""

    def get_provider_name(self) -> str:
        """Return the provider identifier used by the registry."""
        return self.provider_name
