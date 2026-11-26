"""App connectors: one module per app. Implement :class:`AppConnector` (or use the ``rest`` connector)."""

from .base import Account, AppConnector, Person

__all__ = ["Account", "AppConnector", "Person"]
