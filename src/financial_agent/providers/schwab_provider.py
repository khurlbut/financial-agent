from __future__ import annotations

from dataclasses import replace

from .. import settings
from ..schwab_csv import db as schwab_db
from ..plaid_store import load_plaid_items
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

    def __init__(self) -> None:
        # Internal providers use distinct source ids; we translate to public `schwab`.
        self._csv = SchwabCsvHoldingsProvider()

    async def list_containers(self) -> list[ContainerRef]:
        # Containers can exist via Plaid (linked) and/or CSV snapshots (manual imports).
        plaid_items = load_plaid_items()
        container_ids: set[str] = set(plaid_items.keys())

        conn = schwab_db.connect(settings.get_finagent_db_path())
        try:
            container_ids.update(schwab_db.list_container_ids(conn))
        finally:
            conn.close()

        # If the user has adopted explicit container ids (e.g., kev/deb), hide the
        # legacy default container id that was used before multi-container support.
        # This avoids showing an extra "schwab/schwab" container in /agent/containers.
        if "schwab" in container_ids and any(cid != "schwab" for cid in container_ids):
            container_ids.discard("schwab")

        out: list[ContainerRef] = []
        for cid in sorted(container_ids):
            item = plaid_items.get(cid)
            name = None
            if item is not None:
                name = item.institution_name or "Schwab"
            else:
                name = f"Schwab ({cid})"
            out.append(ContainerRef(source=self.source, container_id=cid, name=name))

        return out

    async def list_accounts(self, *, container_id: str) -> list[AccountRef]:
        cid = (container_id or "").strip()
        if not cid:
            return []

        # Prefer Plaid when linked for this container.
        if cid in load_plaid_items():
            plaid = SchwabPlaidHoldingsProvider(container_id=cid)
            accounts = await plaid.list_accounts(container_id=cid)
            return [replace(a, source=self.source, container_id=cid) for a in accounts]

        accounts = await self._csv.list_accounts(container_id=cid)
        return [replace(a, source=self.source, container_id=cid) for a in accounts]

    async def get_holdings(self, *, container_id: str) -> list[Holding]:
        cid = (container_id or "").strip()
        if not cid:
            return []

        # Prefer Plaid when linked for this container.
        if cid in load_plaid_items():
            plaid = SchwabPlaidHoldingsProvider(container_id=cid)
            holdings = await plaid.get_holdings(container_id=cid)
            return [replace(h, source=self.source, container_id=cid) for h in holdings]

        holdings = await self._csv.get_holdings(container_id=cid)
        return [replace(h, source=self.source, container_id=cid) for h in holdings]
