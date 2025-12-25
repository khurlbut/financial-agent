from __future__ import annotations

from decimal import Decimal

from ..cold_storage import load_cold_storage_devices
from .. import settings
from .protocols import AccountRef, ContainerRef, Holding, HoldingsProvider


class ColdStorageHoldingsProvider(HoldingsProvider):
    source = "cold_storage"

    async def list_containers(self) -> list[ContainerRef]:
        devices = load_cold_storage_devices(settings.get_cold_storage_path())
        return [ContainerRef(source=self.source, container_id=d.name, name=d.name) for d in devices]

    async def list_accounts(self, *, container_id: str) -> list[AccountRef]:
        # Cold storage devices are treated as containers with no sub-accounts.
        return []

    async def get_holdings(self, *, container_id: str) -> list[Holding]:
        devices = load_cold_storage_devices(settings.get_cold_storage_path())
        ignored = settings.get_ignored_assets()

        device = next((d for d in devices if d.name == container_id), None)
        if device is None:
            return []

        holdings: list[Holding] = []
        for asset, qty_s in device.holdings.items():
            asset_upper = (asset or "").strip().upper()
            if not asset_upper or asset_upper in ignored:
                continue

            qty = _parse_decimal(qty_s)
            if qty <= 0:
                continue

            holdings.append(
                Holding(
                    source=self.source,
                    container_id=device.name,
                    account_id=None,
                    asset=asset_upper,
                    quantity=qty,
                    quote_currency="USD",
                )
            )

        return holdings


def _parse_decimal(value: str | None) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")
