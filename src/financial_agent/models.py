from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

Source = Literal["coinbase", "cold_storage", "aggregate"]


class Account(BaseModel):
    source: Source
    account_id: Optional[str] = None
    name: Optional[str] = None
    asset: Optional[str] = None
    available: Optional[str] = None
    total: Optional[str] = None


class Position(BaseModel):
    source: Source
    account_id: Optional[str] = None
    symbol: Optional[str] = None
    asset: Optional[str] = None

    quantity: str = Field(default="0")
    cost_basis: Optional[str] = None
    current_price: Optional[str] = None
    market_value: Optional[str] = None

    quote_currency: str = Field(default="USD")


class CashBalance(BaseModel):
    source: Source
    account_id: Optional[str] = None
    currency: str

    available: Optional[str] = None
    total: Optional[str] = None


class PortfolioSnapshot(BaseModel):
    source: Source
    as_of: datetime

    accounts: list[Account] = Field(default_factory=list)
    positions: list[Position] = Field(default_factory=list)
    cash: list[CashBalance] = Field(default_factory=list)


class PriceQuote(BaseModel):
    source: Source
    as_of: datetime

    product_id: str
    price: str


class PortfolioValue(BaseModel):
    source: Source
    as_of: datetime

    currency: str = Field(default="USD")
    total_value: str
    missing_prices: list[str] = Field(default_factory=list)


class AssetAccountBreakdown(BaseModel):
    source: Source
    account_id: Optional[str] = None
    quantity: str = Field(default="0")
    market_value: Optional[str] = None


class AssetValuation(BaseModel):
    asset: str
    quote_currency: str = Field(default="USD")
    total_quantity: str = Field(default="0")
    price: Optional[str] = None
    market_value: Optional[str] = None
    accounts: list[AssetAccountBreakdown] = Field(default_factory=list)


class AccountValuation(BaseModel):
    source: Source
    account_id: Optional[str] = None
    name: Optional[str] = None
    currency: str = Field(default="USD")
    total_value: str
    cash: list[CashBalance] = Field(default_factory=list)
    positions: list[Position] = Field(default_factory=list)


class PortfolioValuation(BaseModel):
    source: Source
    as_of: datetime

    currency: str = Field(default="USD")
    total_value: str
    cash_value: str
    positions_value: str

    by_asset: list[AssetValuation] = Field(default_factory=list)
    by_account: list[AccountValuation] = Field(default_factory=list)
    missing_prices: list[str] = Field(default_factory=list)


class NetWorthSummary(BaseModel):
    """Total net worth across all sources."""

    source: Source = Field(default="aggregate")
    as_of: datetime
    currency: str = Field(default="USD")
    total_value: str


class ContainerSummary(BaseModel):
    """Value summary for a single brokerage/exchange/device."""

    source: Source
    account_id: Optional[str] = None
    name: Optional[str] = None
    currency: str = Field(default="USD")
    total_value: str


class ContainerSummaries(BaseModel):
    """List of all containers and their total values."""

    source: Source = Field(default="aggregate")
    as_of: datetime
    currency: str = Field(default="USD")
    containers: list[ContainerSummary] = Field(default_factory=list)


class HoldingLine(BaseModel):
    """A single holding line item for a container."""

    asset: str
    quantity: str
    quote_currency: str = Field(default="USD")
    price: Optional[str] = None
    market_value: Optional[str] = None


class ContainerHoldings(BaseModel):
    """Holdings and totals for a single brokerage/exchange/device."""

    source: Source
    as_of: datetime
    account_id: Optional[str] = None
    name: Optional[str] = None
    currency: str = Field(default="USD")
    total_value: str
    holdings: list[HoldingLine] = Field(default_factory=list)
    missing_prices: list[str] = Field(default_factory=list)


OrderSide = Literal["buy", "sell"]
OrderType = Literal["market", "limit"]


class TradeRequest(BaseModel):
    source: Source = Field(default="coinbase")

    account_id: Optional[str] = None
    symbol: str
    side: OrderSide
    order_type: OrderType = Field(default="market")

    quantity: str
    quote_currency: str = Field(default="USD")
    limit_price: Optional[str] = None

    client_order_id: Optional[str] = None
    rationale: Optional[str] = None


class TradePreview(BaseModel):
    source: Source
    as_of: datetime

    request: TradeRequest

    is_valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    requires_human_confirmation: bool = Field(default=True)
    execution_ready: bool = Field(default=False)


class TradeExecutionResponse(BaseModel):
    source: Source
    as_of: datetime

    request: TradeRequest

    status: Literal["rejected", "not_implemented", "submitted"]
    message: str

    broker_order_id: Optional[str] = None
    raw: Optional[dict] = None

    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    requires_human_confirmation: bool = Field(default=True)
    execution_ready: bool = Field(default=False)
