from __future__ import annotations

import importlib

from .base import BasePSPProvider


PROVIDER_REGISTRY: dict[str, str] = {
    "cellulant": "payments.providers.cellulant.provider.CellulantProvider",
    "intasend": "payments.providers.intasend.provider.IntaSendProvider",
    "flutterwave": "payments.providers.flutterwave.provider.FlutterwaveProvider",
    "mock": "payments.providers.mock.MockPSPProvider",
}


def get_provider_class(name: str) -> type[BasePSPProvider]:
    """Dynamically load a PSP provider class from the registry."""
    normalized_name = (name or "").strip().lower()

    if normalized_name not in PROVIDER_REGISTRY:
        registered = ", ".join(sorted(PROVIDER_REGISTRY))
        raise ValueError(
            f"Unknown PSP provider '{name}'. Registered providers: {registered}"
        )

    module_path, class_name = PROVIDER_REGISTRY[normalized_name].rsplit(".", 1)
    module = importlib.import_module(module_path)
    provider_class = getattr(module, class_name)

    if not issubclass(provider_class, BasePSPProvider):
        raise TypeError(
            f"Provider '{provider_class.__name__}' does not implement "
            "BasePSPProvider"
        )

    return provider_class
