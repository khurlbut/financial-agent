from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"

# Load .env once, at import time, so all modules share the same behavior.
load_dotenv(dotenv_path=ENV_PATH)


def _env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value if value else None


def get_ignored_assets() -> set[str]:
    """Comma-separated asset symbols to ignore for pricing/valuation."""

    raw = _env("FINAGENT_IGNORED_ASSETS")
    if not raw:
        return set()
    return {s.strip().upper() for s in raw.split(",") if s.strip()}


def get_allowed_symbols() -> set[str]:
    """Comma-separated allowlist for execution, e.g. BTC,ETH."""

    raw = _env("FINAGENT_ALLOWED_SYMBOLS")
    if not raw:
        return set()
    return {s.strip().upper() for s in raw.split(",") if s.strip()}


def get_max_notional_usd() -> Decimal | None:
    raw = _env("FINAGENT_MAX_NOTIONAL_USD")
    if not raw:
        return None
    try:
        dec = Decimal(raw)
    except (InvalidOperation, TypeError):
        return None
    return dec


@dataclass(frozen=True)
class CoinbaseCredentials:
    api_key: str
    api_secret: str


def get_coinbase_credentials() -> CoinbaseCredentials:
    api_key = _env("COINBASE_API_KEY")
    api_secret = os.getenv("COINBASE_API_SECRET")  # keep exact formatting

    if api_secret is not None:
        # Turn the literal backslash-n sequences into real newlines for PEM parsing.
        api_secret = api_secret.replace("\\n", "\n")

    if not api_key or not api_secret:
        raise RuntimeError("COINBASE_API_KEY or COINBASE_API_SECRET not set in environment")

    return CoinbaseCredentials(api_key=api_key, api_secret=api_secret)


def get_finagent_host() -> str:
    return _env("FINAGENT_HOST") or "127.0.0.1"


def get_finagent_port() -> int:
    raw = _env("FINAGENT_PORT") or "8000"
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 8000


def get_finagent_reload() -> bool:
    raw = _env("FINAGENT_RELOAD") or "false"
    return raw.strip().lower() in {"1", "true", "yes"}


def get_cold_storage_path() -> Path:
    """Path to the user-maintained cold storage holdings file."""

    raw = _env("FINAGENT_COLD_STORAGE_PATH")
    if raw:
        return Path(raw).expanduser()
    return PROJECT_ROOT / "cold_storage.json"


def get_price_provider_id() -> str:
    """Pricing provider identifier.

    Defaults to 'coinbase'. Intended to be swappable (e.g., 'binance') later.
    """

    return (_env("FINAGENT_PRICE_PROVIDER") or "coinbase").strip().lower()
