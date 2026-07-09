"""Order book orchestration: feed workers, failover, and shared state.

One FeedWorker thread runs per (exchange, asset) subscription. Websocket-capable
venues stream with reconnect supervision and fall back to REST polling after
repeated failures; REST venues poll with error backoff. All state lives in
OrderBookService behind a lock so the Streamlit UI can read it from reruns.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Sequence, Tuple

from src.data.clients import HttpClient, WsConnection
from src.data.adapters.base import ExchangeAdapter
from src.data.adapters.binance import BinanceUSAdapter
from src.data.adapters.bybit import BybitAdapter
from src.data.adapters.coinbase import CoinbaseAdapter
from src.data.adapters.kraken import KrakenAdapter
from src.data.adapters.kucoin import KucoinAdapter
from src.domain.models import FeedState, FeedStatus, OrderBookSnapshot

logger = logging.getLogger(__name__)

ADAPTER_FACTORIES = {
    "Binance.US": BinanceUSAdapter,
    "Coinbase": CoinbaseAdapter,
    "Kraken": KrakenAdapter,
    "KuCoin": KucoinAdapter,
    "Bybit": BybitAdapter,
}

AVAILABLE_EXCHANGES: Tuple[str, ...] = tuple(ADAPTER_FACTORIES.keys())
DEFAULT_EXCHANGES: Tuple[str, ...] = ("Binance.US", "Coinbase", "Kraken", "KuCoin")

MAX_WS_FAILURES = 3

# (timestamp, best_bid, best_ask, spread_bps, mid)
SpreadPoint = Tuple[float, float, float, float, float]
# (timestamp, snapshot)
DepthPoint = Tuple[float, OrderBookSnapshot]


class FeedWorker(threading.Thread):
    """Owns one exchange feed: websocket first (if supported), REST fallback."""

    def __init__(
        self,
        service: "OrderBookService",
        adapter: ExchangeAdapter,
        base: str,
        generation: int,
    ) -> None:
        super().__init__(daemon=True, name=f"feed-{adapter.name}-{base}")
        self._service = service
        self.adapter = adapter
        self.base = base
        self._generation = generation
        self._stop_event = threading.Event()
        self._ws: Optional[WsConnection] = None
        self._http = HttpClient()

    def stop(self) -> None:
        self._stop_event.set()
        ws = self._ws
        if ws is not None:
            ws.close()

    @property
    def stopped(self) -> bool:
        return self._stop_event.is_set()

    def run(self) -> None:
        try:
            if self.adapter.supports_ws:
                self._run_ws_with_fallback()
            else:
                self._run_rest_loop()
        except Exception as exc:
            # Last-resort guard: a worker must never die silently.
            logger.exception("feed worker crashed: %s", self.name)
            self._service._record_error(
                self.adapter.name, "worker", f"worker crashed: {exc}", self._generation
            )

    # --- websocket path ---

    def _run_ws_with_fallback(self) -> None:
        failures = 0
        while not self.stopped and failures < MAX_WS_FAILURES:
            self.adapter.reset_ws_state()
            got_data = self._run_ws_once()
            if self.stopped:
                return
            failures = 0 if got_data else failures + 1
            self._service._record_error(
                self.adapter.name,
                "websocket",
                "websocket connection lost; reconnecting",
                self._generation,
            )
            self._stop_event.wait(min(2.0 * max(failures, 1), 10.0))
        if not self.stopped:
            self._service._record_error(
                self.adapter.name,
                "websocket",
                "websocket unavailable; falling back to REST polling",
                self._generation,
            )
            self._run_rest_loop()

    def _run_ws_once(self) -> bool:
        def on_message(raw: str) -> None:
            try:
                snapshot = self.adapter.handle_ws_message(raw, self.base)
            except Exception as exc:
                self._service._record_error(
                    self.adapter.name, "websocket", f"parse error: {exc}", self._generation
                )
                return
            if snapshot is not None:
                self._service._record_snapshot(snapshot, "websocket", self._generation)

        connection = WsConnection(
            self.adapter.ws_url(self.base),
            on_message,
            subscribe_message=self.adapter.ws_subscribe_message(self.base),
        )
        self._ws = connection
        try:
            return connection.run()
        finally:
            self._ws = None

    # --- REST polling path ---

    def _run_rest_loop(self) -> None:
        consecutive_failures = 0
        while not self.stopped:
            ok = self._poll_once()
            consecutive_failures = 0 if ok else consecutive_failures + 1
            interval = max(self._service.poll_interval, self.adapter.rest_poll_interval)
            if not ok:
                interval = min(interval * (2 ** min(consecutive_failures, 3)), 30.0)
            self._stop_event.wait(interval)

    def _poll_once(self) -> bool:
        try:
            snapshot = self.adapter.fetch_snapshot(self._http, self.base)
        except Exception as exc:
            self._service._record_error(self.adapter.name, "rest", str(exc), self._generation)
            return False
        self._service._record_snapshot(snapshot, "rest", self._generation)
        return True


class OrderBookService:
    """Thread-safe store of live books, feed health, and rolling history."""

    def __init__(
        self,
        stale_after: float = 15.0,
        spread_history_len: int = 1200,
        depth_history_len: int = 300,
        depth_history_interval: float = 2.0,
        poll_interval: float = 2.0,
    ) -> None:
        self.stale_after = stale_after
        self.poll_interval = poll_interval
        self._depth_history_interval = depth_history_interval
        self._spread_history_len = spread_history_len
        self._depth_history_len = depth_history_len

        self._lock = threading.Lock()
        self._generation = 0
        self._subscription: Optional[Tuple[str, Tuple[str, ...]]] = None
        self._workers: List[FeedWorker] = []

        self._books: Dict[str, OrderBookSnapshot] = {}
        self._statuses: Dict[str, FeedStatus] = {}
        self._spread_history: Dict[str, Deque[SpreadPoint]] = {}
        self._depth_history: Dict[str, Deque[DepthPoint]] = {}
        self._last_depth_record: Dict[str, float] = {}

    # --- subscription management ---

    def set_subscription(self, base: str, exchanges: Sequence[str]) -> None:
        key = (base, tuple(sorted(exchanges)))
        with self._lock:
            if key == self._subscription:
                return
            self._subscription = key
            self._generation += 1
            generation = self._generation
            old_workers = self._workers
            self._workers = []
            self._books.clear()
            self._spread_history.clear()
            self._depth_history.clear()
            self._last_depth_record.clear()
            self._statuses = {
                name: FeedStatus(state=FeedState.DISCONNECTED, source="none")
                for name in exchanges
            }

        for worker in old_workers:
            worker.stop()

        new_workers = []
        for name in exchanges:
            factory = ADAPTER_FACTORIES.get(name)
            if factory is None:
                continue
            worker = FeedWorker(self, factory(), base, generation)
            new_workers.append(worker)
            worker.start()

        with self._lock:
            if self._generation == generation:
                self._workers = new_workers

    def stop(self) -> None:
        with self._lock:
            workers = self._workers
            self._workers = []
            self._generation += 1
        for worker in workers:
            worker.stop()

    # --- worker callbacks ---

    def _record_snapshot(self, snapshot: OrderBookSnapshot, source: str, generation: int) -> None:
        now = time.time()
        with self._lock:
            if generation != self._generation:
                return
            exchange = snapshot.exchange
            self._books[exchange] = snapshot

            status = self._statuses.setdefault(exchange, FeedStatus())
            status.state = FeedState.CONNECTED
            status.source = source
            status.last_update = now
            status.last_error = None

            spread_hist = self._spread_history.setdefault(
                exchange, deque(maxlen=self._spread_history_len)
            )
            spread_hist.append(
                (now, snapshot.best_bid, snapshot.best_ask, snapshot.spread_bps, snapshot.mid_price)
            )

            last_depth = self._last_depth_record.get(exchange, 0.0)
            if now - last_depth >= self._depth_history_interval:
                depth_hist = self._depth_history.setdefault(
                    exchange, deque(maxlen=self._depth_history_len)
                )
                depth_hist.append((now, snapshot))
                self._last_depth_record[exchange] = now

    def _record_error(self, exchange: str, source: str, message: str, generation: int) -> None:
        with self._lock:
            if generation != self._generation:
                return
            status = self._statuses.setdefault(exchange, FeedStatus())
            status.source = source
            status.last_error = message
            # Keep DEGRADED if we still have a book to show; DISCONNECTED otherwise.
            status.state = FeedState.DEGRADED if exchange in self._books else FeedState.DISCONNECTED

    # --- readers (called from the UI thread) ---

    def get_snapshot(self, exchange: str) -> Optional[OrderBookSnapshot]:
        with self._lock:
            return self._books.get(exchange)

    def get_all_snapshots(self) -> Dict[str, OrderBookSnapshot]:
        with self._lock:
            return dict(self._books)

    def get_statuses(self) -> Dict[str, FeedStatus]:
        with self._lock:
            statuses = {name: status.copy() for name, status in self._statuses.items()}
        # Downgrade feeds whose data has gone stale.
        for status in statuses.values():
            age = status.age_seconds()
            if status.state == FeedState.CONNECTED and age is not None and age > self.stale_after:
                status.state = FeedState.DEGRADED
                status.last_error = f"no update for {age:.0f}s (stale)"
        return statuses

    def get_spread_history(self, exchange: str) -> List[SpreadPoint]:
        with self._lock:
            return list(self._spread_history.get(exchange, ()))

    def get_depth_history(self, exchange: str) -> List[DepthPoint]:
        with self._lock:
            return list(self._depth_history.get(exchange, ()))
