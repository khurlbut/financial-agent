from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

from fastapi.concurrency import run_in_threadpool

from .. import settings
from ..etrade_client import ETradeClient
from ..etrade_store import load_etrade_tokens
from .protocols import AccountRef, ContainerRef, Holding, HoldingsProvider


class ETradeHoldingsProvider(HoldingsProvider):
    """E*Trade holdings provider.

    Backed by live API calls using OAuth1 tokens stored locally.
    """

    source = "etrade"

    def __init__(self) -> None:
        self._client: ETradeClient | None = None

    def _get_client(self) -> ETradeClient:
        if self._client is None:
            self._client = ETradeClient()
        return self._client

    async def list_containers(self) -> list[ContainerRef]:
        tokens = load_etrade_tokens()
        out: list[ContainerRef] = []
        for cid in sorted(tokens.keys()):
            out.append(ContainerRef(source=self.source, container_id=cid, name=f"E*Trade ({cid})"))
        return out

    async def list_accounts(self, *, container_id: str) -> list[AccountRef]:
        cid = (container_id or "").strip()
        if not cid:
            return []

        tok = load_etrade_tokens().get(cid)
        if tok is None:
            return []

        resp = await run_in_threadpool(
            self._get_client().list_accounts,
            oauth_token=tok.oauth_token,
            oauth_token_secret=tok.oauth_token_secret,
        )

        out: list[AccountRef] = []
        for acct in _iter_accounts(resp):
            account_id_key = _get_str(acct, "accountIdKey", "account_id_key", "accountId")
            if not account_id_key:
                continue
            name = (
                _get_str(acct, "accountDesc", "accountDescription", "accountName")
                or _get_str(acct, "accountType")
                or account_id_key
            )
            out.append(
                AccountRef(
                    source=self.source,
                    container_id=cid,
                    account_id=account_id_key,
                    name=name,
                )
            )

        return out

    async def get_holdings(self, *, container_id: str) -> list[Holding]:
        cid = (container_id or "").strip()
        if not cid:
            return []

        tok = load_etrade_tokens().get(cid)
        if tok is None:
            return []

        accounts = await self.list_accounts(container_id=cid)
        holdings: list[Holding] = []

        for acct in accounts:
            account_id_key = acct.account_id

            # Portfolio positions.
            portfolio = await run_in_threadpool(
                self._get_client().get_portfolio,
                oauth_token=tok.oauth_token,
                oauth_token_secret=tok.oauth_token_secret,
                account_id_key=account_id_key,
            )
            for pos in _iter_positions(portfolio):
                symbol = _extract_symbol(pos)
                if not symbol:
                    continue
                qty = _parse_decimal(_get_any(pos, "quantity", "qty", "positionQty", "shares"))
                if qty <= 0:
                    continue

                # Prefer institution valuation when available.
                price = _parse_decimal_opt(_get_any(pos, "price", "lastPrice", "markPrice"))
                mv = _parse_decimal_opt(_get_any(pos, "marketValue", "market_value", "value"))

                holdings.append(
                    Holding(
                        source=self.source,
                        container_id=cid,
                        account_id=account_id_key,
                        asset=symbol,
                        quantity=qty,
                        quote_currency="USD",
                        price=price,
                        market_value=mv,
                    )
                )

            # Cash (best-effort; API response shapes vary).
            try:
                bal = await run_in_threadpool(
                    self._get_client().get_balance,
                    oauth_token=tok.oauth_token,
                    oauth_token_secret=tok.oauth_token_secret,
                    account_id_key=account_id_key,
                )
            except Exception:
                bal = None

            cash = _extract_cash_usd(bal) if isinstance(bal, dict) else None
            if cash is not None and cash > 0:
                holdings.append(
                    Holding(
                        source=self.source,
                        container_id=cid,
                        account_id=account_id_key,
                        asset="USD",
                        quantity=cash,
                        quote_currency="USD",
                        price=Decimal("1"),
                        market_value=cash,
                    )
                )

        # Respect ignored assets.
        ignored = settings.get_ignored_assets()
        return [h for h in holdings if h.asset not in ignored]


def _get_str(d: dict[str, Any], *keys: str) -> str | None:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _get_any(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in d:
            return d.get(k)
    return None


def _iter_accounts(resp: Any) -> Iterable[dict[str, Any]]:
    """Best-effort extraction of account dicts from E*Trade account list response."""

    if not isinstance(resp, dict):
        return []

    # Try a few known nesting patterns.
    candidates = [
        resp.get("AccountListResponse"),
        resp.get("accountListResponse"),
        resp,
    ]

    for c in candidates:
        if not isinstance(c, dict):
            continue
        accounts = c.get("Accounts") or c.get("accounts")
        if isinstance(accounts, dict):
            acct_list = accounts.get("Account") or accounts.get("account")
            if isinstance(acct_list, list):
                return [a for a in acct_list if isinstance(a, dict)]
            if isinstance(acct_list, dict):
                return [acct_list]
        if isinstance(accounts, list):
            return [a for a in accounts if isinstance(a, dict)]

    # Fallback: deep scan for a list under key "Account".
    return _deep_find_dict_list(resp, want_key="Account")


def _iter_positions(resp: Any) -> Iterable[dict[str, Any]]:
    if not isinstance(resp, dict):
        return []

    # Try known-ish nesting patterns.
    candidates = [
        resp.get("PortfolioResponse"),
        resp.get("portfolioResponse"),
        resp,
    ]

    for c in candidates:
        if not isinstance(c, dict):
            continue
        # Often: AccountPortfolio -> [ { Position: [...] } ]
        ap = c.get("AccountPortfolio") or c.get("accountPortfolio")
        if isinstance(ap, list):
            out: list[dict[str, Any]] = []
            for entry in ap:
                if not isinstance(entry, dict):
                    continue
                positions = entry.get("Position") or entry.get("position")
                if isinstance(positions, list):
                    out.extend([p for p in positions if isinstance(p, dict)])
                elif isinstance(positions, dict):
                    out.append(positions)
            if out:
                return out

        positions = c.get("Position") or c.get("position")
        if isinstance(positions, list):
            return [p for p in positions if isinstance(p, dict)]
        if isinstance(positions, dict):
            return [positions]

    return _deep_find_dict_list(resp, want_key="Position")


def _deep_find_dict_list(obj: Any, *, want_key: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == want_key and isinstance(v, list):
                out.extend([i for i in v if isinstance(i, dict)])
            else:
                out.extend(_deep_find_dict_list(v, want_key=want_key))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_deep_find_dict_list(v, want_key=want_key))
    return out


def _extract_symbol(pos: dict[str, Any]) -> str | None:
    # Common fields.
    sym = _get_str(pos, "symbol", "Symbol")
    if sym:
        return sym.strip().upper()

    # Sometimes nested.
    product = pos.get("Product") or pos.get("product")
    if isinstance(product, dict):
        sym2 = _get_str(product, "symbol", "Symbol")
        if sym2:
            return sym2.strip().upper()

    return None


def _extract_cash_usd(resp: dict[str, Any] | None) -> Decimal | None:
    if not isinstance(resp, dict):
        return None

    # Best-effort: look for a few common keys first.
    for key in [
        "totalCash",
        "cashAvailableForInvestment",
        "cashAvailable",
        "cashBalance",
    ]:
        v = resp.get(key)
        d = _parse_decimal_opt(v)
        if d is not None:
            return d

    # Otherwise, scan for the first plausible cash-ish leaf.
    for k, v in _deep_iter_items(resp):
        lk = str(k).strip().lower()
        if "cash" in lk and any(t in lk for t in ["total", "available", "balance"]):
            d = _parse_decimal_opt(v)
            if d is not None:
                return d

    return None


def _deep_iter_items(obj: Any):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from _deep_iter_items(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _deep_iter_items(v)


def _parse_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value).replace(",", ""))
    except Exception:
        return Decimal("0")


def _parse_decimal_opt(value: Any) -> Decimal | None:
    if value is None:
        return None
    s = str(value).strip().replace(",", "")
    if not s:
        return None
    try:
        return Decimal(s)
    except Exception:
        return None
