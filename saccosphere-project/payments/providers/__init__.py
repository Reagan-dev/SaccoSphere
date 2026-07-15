from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings

from .base import BasePSPProvider, CheckoutResult, StatusResult
from .registry import get_provider_class

if TYPE_CHECKING:
    from accounts.models import Sacco


def get_psp_provider(sacco: "Sacco | None" = None) -> BasePSPProvider:
    """Resolve and instantiate the configured PSP provider for the request."""
    provider_name = ""

    if settings.DEBUG and not getattr(settings, "PAYMENT_PROVIDER", ""):
        provider_name = "mock"
    elif sacco is not None and getattr(sacco, "payment_provider", ""):
        provider_name = sacco.payment_provider
    else:
        provider_name = getattr(settings, "PAYMENT_PROVIDER", "")

    if not provider_name:
        raise ValueError("No PSP provider configured.")

    return get_provider_class(provider_name)()


__all__ = ["get_psp_provider", "BasePSPProvider", "CheckoutResult", "StatusResult"]
