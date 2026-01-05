from __future__ import annotations

from dataclasses import replace

from .. import settings
from ..morgan_stanley_csv import db as ms_db
from .morgan_stanley_csv_provider import MorganStanleyCsvHoldingsProvider
from .protocols import AccountRef, ContainerRef, Holding, HoldingsProvider


class MorganStanleyHoldingsProvider(HoldingsProvider):
    """Morgan Stanley holdings provider.

    Currently backed by manual CSV imports into SQLite.
    Exposes public `source == "morgan_stanley"`.
    """

    source = "morgan_stanley"

    def __init__(self) -> None:
        self._csv = MorganStanleyCsvHoldingsProvider()

    async def list_containers(self) -> list[ContainerRef]:
        conn = ms_db.connect(settings.get_finagent_db_path())
        try:
            container_ids = ms_db.list_container_ids(conn)
        finally:
            conn.close()

        return [
            ContainerRef(source=self.source, container_id=cid, name=f"Morgan Stanley ({cid})")
            for cid in sorted(container_ids)
        ]

    async def list_accounts(self, *, container_id: str) -> list[AccountRef]:
        cid = (container_id or "").strip()
        if not cid:
            return []
        accounts = await self._csv.list_accounts(container_id=cid)
        return [replace(a, source=self.source, container_id=cid) for a in accounts]

    async def get_holdings(self, *, container_id: str) -> list[Holding]:
        cid = (container_id or "").strip()
        if not cid:
            return []
        holdings = await self._csv.get_holdings(container_id=cid)
        return [replace(h, source=self.source, container_id=cid) for h in holdings]
