"""Pydantic models for Polymarket data structures."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Token(BaseModel):
    """Represents a Yes/No token in a market."""

    model_config = ConfigDict(populate_by_name=True)

    token_id: str = Field(..., alias="token_id")
    outcome: str
    symbol: Optional[str] = Field(default=None, alias="ticker")

    @field_validator("token_id", mode="before")
    @classmethod
    def extract_token_id(cls, v: str | dict) -> str:
        """Handle both string token_id and nested dict format."""
        if isinstance(v, dict):
            return str(v.get("token_id", v.get("tokenId", "")))
        return str(v)


class Market(BaseModel):
    """Represents a Polymarket prediction market."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    slug: str
    question: str
    category: Optional[str] = None
    tokens: list[Token] = Field(default_factory=list)
    active: bool = True
    end_date: Optional[datetime] = Field(default=None, alias="endDate")

    @property
    def yes_token_id(self) -> Optional[str]:
        """Get the Yes token ID if available."""
        for token in self.tokens:
            if token.outcome.lower() == "yes":
                return token.token_id
        return self.tokens[0].token_id if self.tokens else None

    @property
    def no_token_id(self) -> Optional[str]:
        """Get the No token ID if available."""
        for token in self.tokens:
            if token.outcome.lower() == "no":
                return token.token_id
        return self.tokens[1].token_id if len(self.tokens) > 1 else None


class Trade(BaseModel):
    """Represents a trade event from WebSocket."""

    model_config = ConfigDict(populate_by_name=True)

    trade_id: str = Field(..., alias="id")
    token_id: str = Field(..., alias="market")
    price: Decimal
    amount: Decimal = Field(..., alias="size")
    taker_side: str = Field(..., alias="side")
    timestamp: datetime

    @field_validator("price", "amount", mode="before")
    @classmethod
    def parse_decimal(cls, v: str | float | Decimal) -> Decimal:
        """Convert string/float to Decimal for precision."""
        return Decimal(str(v))

    @field_validator("timestamp", mode="before")
    @classmethod
    def parse_timestamp(cls, v: str | int | datetime) -> datetime:
        """Parse various timestamp formats to datetime."""
        if isinstance(v, datetime):
            return v
        if isinstance(v, int):
            # Unix timestamp in milliseconds
            return datetime.fromtimestamp(v / 1000, tz=timezone.utc)
        if isinstance(v, str):
            # ISO format
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        raise ValueError(f"Cannot parse timestamp: {v}")


class OrderbookLevel(BaseModel):
    """Single price level in orderbook."""

    price: Decimal
    size: Decimal

    @field_validator("price", "size", mode="before")
    @classmethod
    def parse_decimal(cls, v: str | float | Decimal) -> Decimal:
        return Decimal(str(v))


class OrderbookSnapshot(BaseModel):
    """Complete orderbook snapshot."""

    token_id: str
    bids: list[OrderbookLevel] = Field(default_factory=list)
    asks: list[OrderbookLevel] = Field(default_factory=list)
    sequence: Optional[int] = None
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_ws_message(cls, token_id: str, data: dict) -> "OrderbookSnapshot":
        """Create snapshot from WebSocket message format.

        WebSocket sends bids/asks as [[price, size], ...] arrays.
        """
        bids = [
            OrderbookLevel(price=Decimal(p), size=Decimal(s))
            for p, s in data.get("bids", [])
        ]
        asks = [
            OrderbookLevel(price=Decimal(p), size=Decimal(s))
            for p, s in data.get("asks", [])
        ]
        return cls(
            token_id=token_id,
            bids=bids,
            asks=asks,
            sequence=data.get("seq") or data.get("sequence"),
        )


class UrlResolveResult(BaseModel):
    """Result of resolving a Polymarket URL to token IDs."""

    slug: str
    yes_token: str
    no_token: str
    market: Market


class MarketScanResult(BaseModel):
    """Result of scanning active markets."""

    markets: list[Market]
    total_count: int
    new_count: int = 0
    updated_count: int = 0
    deactivated_count: int = 0
    failed_count: int = 0
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
