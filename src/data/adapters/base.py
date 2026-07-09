"""Adapter contract all exchange connectors implement."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Dict, Iterable, Optional, Sequence, Tuple

from src.data.clients import HttpClient
from src.domain.models import OrderBookSnapshot, make_snapshot


class ExchangeAdapter(ABC):
    """One exchange's data source.

    Every adapter must provide REST snapshots. Adapters that also stream set
    supports_ws=True and implement the ws_* / handle_ws_message hooks. The
    feed worker owns reconnect/fallback policy; adapters stay stateless except
    for an optional LiveBook used to maintain diff-based streams.
    """

    name: str = "abstract"
    supports_ws: bool = False
    rest_poll_interval: float = 2.0

    @abstractmethod
    def fetch_snapshot(self, http: HttpClient, base: str, limit: int = 100) -> OrderBookSnapshot:
        """Fetch a full order book snapshot over REST. Raises FeedError/ValueError on failure."""

    # --- websocket hooks (only used when supports_ws is True) ---

    def ws_url(self, base: str) -> str:
        raise NotImplementedError

    def ws_subscribe_message(self, base: str) -> Optional[str]:
        return None

    def handle_ws_message(self, raw: str, base: str) -> Optional[OrderBookSnapshot]:
        """Parse one websocket frame. Returns a snapshot when the book changed, else None."""
        raise NotImplementedError

    def reset_ws_state(self) -> None:
        """Called before each (re)connect so stale diff state is discarded."""


class LiveBook:
    """Maintains a price->qty book from snapshot + delta websocket streams."""

    def __init__(self, max_levels: int = 200) -> None:
        self._max_levels = max_levels
        self.bids: Dict[float, float] = {}
        self.asks: Dict[float, float] = {}

    def apply(
        self,
        bids: Iterable[Sequence],
        asks: Iterable[Sequence],
        replace: bool = False,
    ) -> None:
        if replace:
            self.bids.clear()
            self.asks.clear()
        self._apply_side(self.bids, bids)
        self._apply_side(self.asks, asks)
        self._trim()

    @staticmethod
    def _apply_side(side: Dict[float, float], rows: Iterable[Sequence]) -> None:
        for row in rows:
            try:
                price = float(row[0])
                qty = float(row[1])
            except (TypeError, ValueError, IndexError):
                continue
            if price <= 0:
                continue
            if qty <= 0:
                side.pop(price, None)
            else:
                side[price] = qty

    def _trim(self) -> None:
        if len(self.bids) > self._max_levels:
            keep = sorted(self.bids, reverse=True)[: self._max_levels]
            self.bids = {p: self.bids[p] for p in keep}
        if len(self.asks) > self._max_levels:
            keep = sorted(self.asks)[: self._max_levels]
            self.asks = {p: self.asks[p] for p in keep}

    def snapshot(
        self,
        exchange: str,
        symbol: str,
        sequence: Optional[int] = None,
    ) -> Optional[OrderBookSnapshot]:
        if not self.bids or not self.asks:
            return None
        bids: Tuple = tuple((p, self.bids[p]) for p in sorted(self.bids, reverse=True))
        asks: Tuple = tuple((p, self.asks[p]) for p in sorted(self.asks))
        return make_snapshot(exchange, symbol, bids, asks, timestamp=time.time(), sequence=sequence)
