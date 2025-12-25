from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ColdStorageDevice:
    name: str
    holdings: dict[str, str]  # asset -> quantity (decimal string)


def _to_decimal_string(value: Any) -> str:
    try:
        return str(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return "0"


def load_cold_storage_devices(path: Path) -> list[ColdStorageDevice]:
    """Load cold storage devices from a user-maintained JSON file.

    Expected format:

    {
      "devices": [
        {
          "name": "Trezor 2022",
          "holdings": {
            "BTC": 11.08
          }
        }
      ]
    }

    - If the file does not exist, returns an empty list.
    - Invalid / missing fields are ignored conservatively.
    """

    if not path.exists():
        return []

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return []

    devices_raw = raw.get("devices")
    if not isinstance(devices_raw, list):
        return []

    devices: list[ColdStorageDevice] = []

    for item in devices_raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue

        holdings_raw = item.get("holdings")
        holdings: dict[str, str] = {}
        if isinstance(holdings_raw, dict):
            for asset, qty in holdings_raw.items():
                if not isinstance(asset, str) or not asset.strip():
                    continue
                qty_s = _to_decimal_string(qty)
                if Decimal(qty_s) <= 0:
                    continue
                holdings[asset.strip().upper()] = qty_s

        if holdings:
            devices.append(ColdStorageDevice(name=name.strip(), holdings=holdings))

    return devices
