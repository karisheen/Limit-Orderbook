"""Normalized domain models shared by all exchange feeds."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional, Sequence, Tuple

# (price, quantity)
Level = Tuple[float, float]


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

    @property
    def best_bid(self) -> float:
        return self.bids[0][0]

    @property
    def best_ask(self) -> float:
        return self.asks[0][0]

    @property
    def mid_price(self) -> float:
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid

    @property
    def spread_bps(self) -> float:
        mid = self.mid_price
        if mid <= 0:
            return 0.0
        return (self.spread / mid) * 10_000.0

    def imbalance(self, levels: int = 10) -> float:
        """Bid volume share of total volume over the top N levels (0..1)."""
        bid_vol = sum(q for _, q in self.bids[:levels])
        ask_vol = sum(q for _, q in self.asks[:levels])
        total = bid_vol + ask_vol
        if total <= 0:
            return 0.5
        return bid_vol / total


def _clean_levels(raw: Iterable[Sequence]) -> list:
    """Coerce raw [price, qty, ...] rows to float pairs, dropping bad rows."""
    levels = []
    for row in raw:
        try:
            price = float(row[0])
            qty = float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if price > 0 and qty > 0:
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
    """Normalize raw exchange payload rows into an OrderBookSnapshot.

    Accepts rows shaped like [price, qty] or [price, qty, extra...] with
    string or numeric values. Keeps at most max_levels per side (some venues,
    e.g. Coinbase level-2, return the entire aggregated book). Raises
    ValueError if either side ends up empty.
    """
    clean_bids = sorted(_clean_levels(bids), key=lambda l: l[0], reverse=True)[:max_levels]
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
