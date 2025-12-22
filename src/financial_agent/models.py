from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

Source = Literal["coinbase"]


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
