from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import settings


@dataclass(frozen=True)
class PlaidItem:
    access_token: str
    item_id: str
    institution_name: str | None = None
    created_at: str | None = None


def get_plaid_item(*, container_id: str, path: Path | None = None) -> PlaidItem | None:
    items = load_plaid_items(path=path)
    return items.get(container_id)


def delete_plaid_item(*, container_id: str, path: Path | None = None) -> bool:
    """Delete a Plaid item from the local token store.

    Returns True if an entry existed and was removed, else False.
    """

    p = path or settings.get_plaid_tokens_path()
    if not p.exists():
        return False

    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return False

    if not isinstance(raw, dict) or container_id not in raw:
        return False

    raw.pop(container_id, None)
    p.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")
    return True


def load_plaid_items(path: Path | None = None) -> dict[str, PlaidItem]:
    """Load Plaid items keyed by container_id (e.g., 'schwab')."""

    p = path or settings.get_plaid_tokens_path()
    if not p.exists():
        return {}

    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}

    out: dict[str, PlaidItem] = {}
    for container_id, item in raw.items():
        if not isinstance(container_id, str) or not isinstance(item, dict):
            continue
        access_token = item.get("access_token")
        item_id = item.get("item_id")
        if not isinstance(access_token, str) or not isinstance(item_id, str):
            continue
        out[container_id] = PlaidItem(
            access_token=access_token,
            item_id=item_id,
            institution_name=item.get("institution_name"),
            created_at=item.get("created_at"),
        )

    return out


def save_plaid_item(
    *,
    container_id: str,
    access_token: str,
    item_id: str,
    institution_name: str | None = None,
    path: Path | None = None,
) -> None:
    p = path or settings.get_plaid_tokens_path()

    data: dict[str, dict] = {}
    if p.exists():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}

    data[container_id] = {
        "access_token": access_token,
        "item_id": item_id,
        "institution_name": institution_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    p.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
