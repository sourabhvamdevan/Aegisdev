"""Warehouse inventory synchronisation."""


def reconcile_stock_levels(local: dict, remote: dict) -> dict:
    """Return SKUs whose local and remote counts disagree."""
    return {sku: (local.get(sku), remote.get(sku)) for sku in set(local) | set(remote) if local.get(sku) != remote.get(sku)}


class WarehouseSyncError(Exception):
    """Raised when the remote warehouse API rejects a sync batch."""
