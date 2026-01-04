from __future__ import annotations

from dataclasses import replace

from .. import settings
from ..schwab_csv import db as schwab_db
from .protocols import AccountRef, ContainerRef, Holding, HoldingsProvider
from .schwab_csv_provider import SchwabCsvHoldingsProvider
from .schwab_plaid_provider import SchwabPlaidHoldingsProvider


class SchwabHoldingsProvider(HoldingsProvider):
    """Unified Schwab provider.

    Exposes a single `source == "schwab"` regardless of backend:
    - If a Plaid Item exists, uses the Plaid-backed provider.
    - Else, if a CSV snapshot exists, uses the CSV-backed provider.

    This avoids having multiple providers with the same public `source`.
    """

    source = "schwab"

    def __init__(self, *, container_id: str | None = None) -> None:
        self._container_id = (container_id or settings.get_schwab_container_id()).strip() or "schwab"
        self._plaid = SchwabPlaidHoldingsProvider(container_id=self._container_id)
        # Internal CSV provider uses a distinct source id; we translate to `schwab`.
        self._csv = SchwabCsvHoldingsProvider(container_id=self._container_id)

    async def list_containers(self) -> list[ContainerRef]:
        # Prefer Plaid when linked.
        plaid = await self._plaid.list_containers()
        if plaid:
            return [replace(plaid[0], source=self.source, container_id=self._container_id)]

        # Otherwise, advertise CSV only if a snapshot exists.
        conn = schwab_db.connect(settings.get_finagent_db_path())
        try:
            snap = schwab_db.get_latest_snapshot(conn)
        finally:
            conn.close()

        if snap is None:
            return []

        # CSV provider always returns a container; we standardize the source.
        csv_containers = await self._csv.list_containers()
        if not csv_containers:
            return []
        c = csv_containers[0]
        return [replace(c, source=self.source, container_id=self._container_id, name=c.name or "Schwab")]

    async def list_accounts(self, *, container_id: str) -> list[AccountRef]:
        if container_id != self._container_id:
            return []

        plaid = await self._plaid.list_containers()
        if plaid:
            accounts = await self._plaid.list_accounts(container_id=container_id)
            return [replace(a, source=self.source, container_id=self._container_id) for a in accounts]

        accounts = await self._csv.list_accounts(container_id=container_id)
        return [replace(a, source=self.source, container_id=self._container_id) for a in accounts]

    async def get_holdings(self, *, container_id: str) -> list[Holding]:
        if container_id != self._container_id:
            return []

        plaid = await self._plaid.list_containers()
        if plaid:
            holdings = await self._plaid.get_holdings(container_id=container_id)
            return [replace(h, source=self.source, container_id=self._container_id) for h in holdings]

        holdings = await self._csv.get_holdings(container_id=container_id)
        return [replace(h, source=self.source, container_id=self._container_id) for h in holdings]
