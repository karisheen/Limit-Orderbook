"""Adapter contract all exchange connectors implement."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Dict, Iterable, Optional, Sequence, Tuple

from src.data.clients import HttpClient
from src.domain.models import OrderBookSnapshot, make_snapshot
from src.workstation.precision import decimal


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
    def fetch_snapshot(
        self, http: HttpClient, base: str, limit: int = 100
    ) -> OrderBookSnapshot:
        """Fetch a full order book snapshot over REST. Raises FeedError/ValueError on failure."""

    # --- websocket hooks (only used when supports_ws is True) ---

    def ws_url(self, base: str) -> str:
        raise NotImplementedError

    def ws_subscribe_message(self, base: str) -> Optional[str]:
        return None

    def ws_subscribe_messages(self, base: str) -> Tuple[str, ...]:
        message = self.ws_subscribe_message(base)
        return (message,) if message is not None else ()

    def handle_ws_message(self, raw: str, base: str) -> Optional[OrderBookSnapshot]:
        """Parse one websocket frame. Returns a snapshot when the book changed, else None."""
        raise NotImplementedError

    def reset_ws_state(self) -> None:
        """Called before each (re)connect so stale diff state is discarded."""


class LiveBook:
    """Transactional exact absolute-quantity book.

    Snapshot duplicates are invalid; repeated delta prices apply in wire order.
    A failed transaction leaves the prior maps unchanged. The owning adapter
    invalidates its connection state and requests recovery by raising.
    """

    def __init__(self, max_levels: Optional[int] = 200) -> None:
        if max_levels is not None and (type(max_levels) is not int or max_levels <= 0):
            raise ValueError("invalid reconstruction depth")
        self._max_levels = max_levels
        self.bids: Dict[Decimal, Decimal] = {}
        self.asks: Dict[Decimal, Decimal] = {}
        self.initialized = False

    def apply(
        self,
        bids: Iterable[Sequence],
        asks: Iterable[Sequence],
        replace: bool = False,
    ) -> None:
        self.apply_batch(((bids, asks, replace),))

    def apply_batch(
        self, batches: Iterable[Tuple[Iterable[Sequence], Iterable[Sequence], bool]]
    ) -> None:
        """Apply a full protocol message before validating or committing."""
        bids, asks = self.bids.copy(), self.asks.copy()
        initialized = self.initialized
        for bid_rows, ask_rows, replace in batches:
            if replace:
                bids, asks = {}, {}
                initialized = True
            elif not initialized:
                raise ValueError("delta received before snapshot")
            self._apply_side(bids, bid_rows, replace)
            self._apply_side(asks, ask_rows, replace)
        if not initialized or not bids or not asks:
            raise ValueError("uninitialized or empty order book")
        if max(bids) >= min(asks):
            raise ValueError("locked or crossed order book")
        if self._max_levels is not None:
            bids = {p: bids[p] for p in sorted(bids, reverse=True)[: self._max_levels]}
            asks = {p: asks[p] for p in sorted(asks)[: self._max_levels]}
        self.bids, self.asks = bids, asks
        self.initialized = True

    @staticmethod
    def _apply_side(
        side: Dict[Decimal, Decimal], rows: Iterable[Sequence], replace: bool
    ) -> None:
        seen = set()
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) != 2:
                raise ValueError("malformed delta level")
            price = decimal(row[0], positive=True)
            qty = decimal(row[1], positive=replace, nonnegative=True)
            if replace and price in seen:
                raise ValueError("duplicate snapshot price")
            seen.add(price)
            # Replace the key too: Decimal equality must not retain old lexical
            # scale when the exchange supplies a new spelling used by CRC32.
            side.pop(price, None)
            if qty:
                side[price] = qty

    def snapshot(
        self,
        exchange: str,
        symbol: str,
        sequence: Optional[int] = None,
    ) -> Optional[OrderBookSnapshot]:
        if not self.initialized:
            return None
        return make_snapshot(
            exchange,
            symbol,
            tuple(self.bids.items()),
            tuple(self.asks.items()),
            timestamp=time.time(),
            sequence=sequence,
            max_levels=self._max_levels or 200,
        )
