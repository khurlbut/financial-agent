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


def get_price_provider_id_raw() -> str | None:
    """Return FINAGENT_PRICE_PROVIDER if explicitly set, else None."""

    raw = _env("FINAGENT_PRICE_PROVIDER")
    return raw.strip().lower() if raw else None


def get_finagent_db_path() -> Path:
    """Path to the local SQLite DB used for scraped/imported holdings."""

    raw = _env("FINAGENT_DB_PATH")
    if raw:
        return Path(raw).expanduser()
    return PROJECT_ROOT / "financial_agent.sqlite3"


def get_schwab_profile_dir() -> Path:
    """Playwright persistent profile directory for Schwab browser state."""

    raw = _env("FINAGENT_SCHWAB_PROFILE_DIR")
    if raw:
        return Path(raw).expanduser()
    return PROJECT_ROOT / "profiles" / "schwab"


def get_schwab_downloads_dir() -> Path:
    """Directory where Schwab CSV exports are saved."""

    raw = _env("FINAGENT_SCHWAB_DOWNLOADS_DIR")
    if raw:
        return Path(raw).expanduser()
    return PROJECT_ROOT / "downloads"


def get_schwab_positions_url() -> str:
    """URL for Schwab positions/holdings page used for CSV export."""

    return _env("FINAGENT_SCHWAB_POSITIONS_URL") or "https://client.schwab.com/app/accounts/positions/#/"


def get_schwab_export_button_selector() -> str:
    """Selector for the Export button on the positions page."""

    # Schwab's Positions UI often renders Export as an icon-only button with
    # aria-label/title="Export" (no visible text). Prefer the stable button id.
    return (
        _env("FINAGENT_SCHWAB_EXPORT_BUTTON_SELECTOR")
        or "button#positionspageheader-utility-bar-export-button"
    )


def get_schwab_export_csv_selector() -> str | None:
    """Optional selector for a CSV menu item after clicking Export (if present)."""

    return _env("FINAGENT_SCHWAB_EXPORT_CSV_SELECTOR")


def get_schwab_container_id() -> str:
    """Container id used for Schwab integrations (Plaid and/or direct CSV)."""

    return (_env("FINAGENT_SCHWAB_CONTAINER_ID") or "schwab").strip()


def get_schwab_csv_price_mode() -> str:
        """How to value Schwab CSV positions.

        - "csv": trust the price/market_value columns in the downloaded Schwab CSV.
        - "live": treat the CSV as positions-only (quantity); pull prices in real time
            from the configured pricing provider.
        """

        return (_env("FINAGENT_SCHWAB_CSV_PRICE_MODE") or "csv").strip().lower()


def get_morgan_stanley_csv_price_mode() -> str:
        """How to value Morgan Stanley CSV positions.

        - "csv": trust the price/market_value columns in the exported CSV (if present).
        - "live": treat the CSV as positions-only (quantity); pull prices in real time
            from the configured pricing provider.

        Defaults to "live" since Morgan Stanley is a traditional broker and we
        generally want third-party equity pricing.
        """

        return (_env("FINAGENT_MORGAN_STANLEY_CSV_PRICE_MODE") or "live").strip().lower()


def get_plaid_tokens_path() -> Path:
    """Path to the local Plaid token store (single-user local mode)."""

    raw = _env("FINAGENT_PLAID_TOKENS_PATH")
    if raw:
        return Path(raw).expanduser()
    return PROJECT_ROOT / ".plaid_tokens.json"


@dataclass(frozen=True)
class ETradeCredentials:
    consumer_key: str
    consumer_secret: str


def get_etrade_credentials() -> ETradeCredentials:
    key = _env("ETRADE_CONSUMER_KEY")
    secret = _env("ETRADE_CONSUMER_SECRET")
    if not key or not secret:
        raise RuntimeError("ETRADE_CONSUMER_KEY or ETRADE_CONSUMER_SECRET not set in environment")
    return ETradeCredentials(consumer_key=key, consumer_secret=secret)


def get_etrade_environment() -> str:
    """E*Trade API environment.

    - "sandbox": for SANDBOX API keys
    - "prod": for production API keys
    """

    return (_env("ETRADE_ENV") or "sandbox").strip().lower()


def get_etrade_base_url() -> str:
    """Base URL for E*Trade API.

    Can be overridden with ETRADE_BASE_URL. Defaults are best-effort.
    """

    raw = _env("ETRADE_BASE_URL")
    if raw:
        return raw.rstrip("/")

    env = get_etrade_environment()
    if env in {"prod", "production"}:
        return "https://api.etrade.com"
    return "https://apisb.etrade.com"


def get_etrade_callback_url() -> str:
    """OAuth callback URL.

    For CLI flows, many OAuth1 providers support out-of-band ("oob").
    If your E*Trade app requires a registered callback URL, set ETRADE_CALLBACK_URL.
    """

    return (_env("ETRADE_CALLBACK_URL") or "oob").strip()


def get_etrade_tokens_path() -> Path:
    """Path to local E*Trade token store (single-user local mode)."""

    raw = _env("FINAGENT_ETRADE_TOKENS_PATH")
    if raw:
        return Path(raw).expanduser()
    return PROJECT_ROOT / ".etrade_tokens.json"
