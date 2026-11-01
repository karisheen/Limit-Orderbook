"""Normalized domain models shared by all exchange feeds."""

from __future__ import annotations

import time
import math
from dataclasses import dataclass
from decimal import Decimal, localcontext
from enum import Enum
from typing import Iterable, Optional, Sequence, Tuple

from src.workstation.precision import DECIMAL_CONTEXT, decimal, nonnegative_int

# Authoritative values remain exact; only plotting consumers convert to float.
Level = Tuple[Decimal, Decimal]


class FeedState(Enum):
    CONNECTED = "connected"
    DEGRADED = "degraded"
    DISCONNECTED = "disconnected"


@dataclass
class FeedStatus:
    """Health of a single exchange feed as seen by the service."""

    state: FeedState = FeedState.DISCONNECTED
    source: str = "none"  # "websocket" | "rest" | "none"
    last_update: Optional[float] = None
    last_error: Optional[str] = None

    def age_seconds(self) -> Optional[float]:
        if self.last_update is None:
            return None
        return max(0.0, time.time() - self.last_update)

    def copy(self) -> "FeedStatus":
        return FeedStatus(
            state=self.state,
            source=self.source,
            last_update=self.last_update,
            last_error=self.last_error,
        )


@dataclass(frozen=True)
class OrderBookSnapshot:
    """A normalized, immutable view of one exchange's order book."""

    exchange: str
    symbol: str
    bids: Tuple[Level, ...]  # sorted by price, descending
    asks: Tuple[Level, ...]  # sorted by price, ascending
    timestamp: float
    sequence: Optional[int] = None

    def __post_init__(self) -> None:
        if isinstance(self.timestamp, bool) or not isinstance(
            self.timestamp, (int, float)
        ):
            raise ValueError("timestamp must be local receive wall seconds")
        if not math.isfinite(self.timestamp) or self.timestamp < 0:
            raise ValueError("invalid receive timestamp")
        if self.sequence is not None:
            nonnegative_int(self.sequence, "sequence")
        for levels, reverse in ((self.bids, True), (self.asks, False)):
            if not isinstance(levels, tuple) or not levels:
                raise ValueError("book sides must be nonempty immutable tuples")
            prices = []
            for row in levels:
                if not isinstance(row, tuple) or len(row) != 2:
                    raise ValueError("invalid level")
                if not all(isinstance(value, Decimal) for value in row):
                    raise ValueError("book levels require Decimal values")
                decimal(row[0], positive=True)
                decimal(row[1], positive=True)
                prices.append(row[0])
            if len(set(prices)) != len(prices) or prices != sorted(
                prices, reverse=reverse
            ):
                raise ValueError("book levels must be unique and sorted")
        if self.best_bid >= self.best_ask:
            raise ValueError("locked or crossed order book")

    @property
    def best_bid(self) -> Decimal:
        return self.bids[0][0]

    @property
    def best_ask(self) -> Decimal:
        return self.asks[0][0]

    @property
    def mid_price(self) -> Decimal:
        with localcontext(DECIMAL_CONTEXT):
            return (self.best_bid + self.best_ask) / 2

    @property
    def spread(self) -> Decimal:
        with localcontext(DECIMAL_CONTEXT):
            return self.best_ask - self.best_bid

    @property
    def spread_bps(self) -> Decimal:
        with localcontext(DECIMAL_CONTEXT):
            return (self.spread / self.mid_price) * 10_000

    def imbalance(self, levels: int = 10) -> Decimal:
        """Bid volume share of total volume over the top N levels (0..1)."""
        if type(levels) is not int or levels <= 0:
            raise ValueError("levels must be a positive integer")
        with localcontext(DECIMAL_CONTEXT):
            bid_vol = sum((q for _, q in self.bids[:levels]), Decimal(0))
            ask_vol = sum((q for _, q in self.asks[:levels]), Decimal(0))
            return bid_vol / (bid_vol + ask_vol)


def _clean_levels(raw: Iterable[Sequence]) -> list[Level]:
    """Validate every snapshot row; duplicate prices are ambiguous and rejected."""
    try:
        raw = iter(raw)
    except TypeError as exc:
        raise ValueError("book side must contain levels") from exc
    levels = []
    seen = set()
    for row in raw:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            raise ValueError("malformed order book level")
        price = decimal(row[0], positive=True)
        qty = decimal(row[1], positive=True)
        if price in seen:
            raise ValueError("duplicate snapshot price")
        seen.add(price)
        levels.append((price, qty))
    return levels


def make_snapshot(
    exchange: str,
    symbol: str,
    bids: Iterable[Sequence],
    asks: Iterable[Sequence],
    timestamp: Optional[float] = None,
    sequence: Optional[int] = None,
    max_levels: int = 200,
) -> OrderBookSnapshot:
    """Validate all exact rows, then sort and bound the published depth.

    Numeric strings, integers and Decimal are accepted; binary floats are not.
    Extra REST row columns are ignored. Any malformed row rejects the snapshot,
    including rows outside the eventual display-depth limit.
    """
    if type(max_levels) is not int or max_levels <= 0:
        raise ValueError("max_levels must be a positive integer")
    clean_bids = sorted(_clean_levels(bids), key=lambda l: l[0], reverse=True)[
        :max_levels
    ]
    clean_asks = sorted(_clean_levels(asks), key=lambda l: l[0])[:max_levels]
    if not clean_bids or not clean_asks:
        raise ValueError(
            f"{exchange} {symbol}: empty or unparseable order book side "
            f"(bids={len(clean_bids)}, asks={len(clean_asks)})"
        )
    return OrderBookSnapshot(
        exchange=exchange,
        symbol=symbol,
        bids=tuple(clean_bids),
        asks=tuple(clean_asks),
        timestamp=timestamp if timestamp is not None else time.time(),
        sequence=sequence,
    )
