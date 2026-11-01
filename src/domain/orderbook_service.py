"""Session-owned legacy feeds with bounded recovery and explicit history gaps.

Each Streamlit session owns a service until the workstation registry bridge is
available. Replacement fences callbacks, cancels and joins every old worker.
REST is a degraded snapshot source, not a continuous event stream; histories
restart on errors, stale intervals and source changes. WS recovery is probed
at least every 60 seconds of REST polling (plus a bounded in-flight request).
"""

from __future__ import annotations

import logging
import math
import random
import threading
import time
from collections import deque
from decimal import Decimal
from typing import Deque, Dict, List, MutableMapping, Optional, Sequence, Tuple

from src.data.clients import HttpClient, RateLimitError, WsConnection
from src.data.adapters.base import ExchangeAdapter
from src.data.adapters.binance import BinanceUSAdapter
from src.data.adapters.bybit import BybitAdapter
from src.data.adapters.coinbase import CoinbaseAdapter
from src.data.adapters.kraken import KrakenAdapter
from src.data.adapters.kucoin import KucoinAdapter
from src.domain.models import FeedState, FeedStatus, OrderBookSnapshot
from src.domain.symbols import CANONICAL_ASSETS, pair_for

logger = logging.getLogger(__name__)
ADAPTER_FACTORIES = {
    "Binance.US": BinanceUSAdapter,
    "Coinbase": CoinbaseAdapter,
    "Kraken": KrakenAdapter,
    "KuCoin": KucoinAdapter,
    "Bybit": BybitAdapter,
}
AVAILABLE_EXCHANGES: Tuple[str, ...] = tuple(ADAPTER_FACTORIES)
DEFAULT_EXCHANGES: Tuple[str, ...] = ("Coinbase", "Kraken")
MAX_WS_FAILURES = 3
WS_RECOVERY_INTERVAL = 60.0
SpreadPoint = Tuple[float, Decimal, Decimal, Decimal, Decimal]
DepthPoint = Tuple[float, OrderBookSnapshot]


class FeedWorker(threading.Thread):
    """One cancellable owner per legacy exchange/asset subscription."""

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
        self._ws_lock = threading.Lock()
        self._ws: Optional[WsConnection] = None
        self._http = HttpClient(stop_event=self._stop_event)
        self._retry_after = 0.0

    def stop(self) -> None:
        self._stop_event.set()
        with self._ws_lock:
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
            logger.exception("feed worker crashed: %s", self.name)
            self._service._record_error(
                self.adapter.name, "worker", f"worker crashed: {exc}", self._generation
            )
        finally:
            self._http.close()

    def _run_ws_with_fallback(self) -> None:
        failures = 0
        while not self.stopped:
            self.adapter.reset_ws_state()
            started = time.monotonic()
            got_data = self._run_ws_once()
            if self.stopped:
                return
            # A single valid frame followed by immediate disconnect must not
            # evade the failure budget either. Sustained valid sessions recover.
            failures = (
                0 if got_data and time.monotonic() - started >= 15.0 else failures + 1
            )
            self._service._record_error(
                self.adapter.name,
                "websocket",
                "websocket connection lost; reconnecting",
                self._generation,
            )
            if failures >= MAX_WS_FAILURES:
                self._service._record_error(
                    self.adapter.name,
                    "rest",
                    "websocket unavailable; REST snapshot fallback",
                    self._generation,
                )
                self._run_rest_loop()
                failures = 0
            else:
                delay = min(2.0**failures, 10.0)
                self._stop_event.wait(delay + random.uniform(0, delay * 0.25))

    def _run_ws_once(self) -> bool:
        def on_message(raw: str) -> bool:
            if self.stopped:
                return False
            try:
                snapshot = self.adapter.handle_ws_message(raw, self.base)
            except Exception as exc:
                self._service._record_error(
                    self.adapter.name,
                    "websocket",
                    f"parse error: {exc}",
                    self._generation,
                )
                # Invalid reconstruction cannot continue on this connection.
                connection.close()
                return False
            if snapshot is None:
                return False
            accepted = self._service._record_snapshot(
                snapshot, "websocket", self._generation
            )
            if not accepted:
                connection.close()
            return accepted

        connection = WsConnection(
            self.adapter.ws_url(self.base),
            on_message,
            subscribe_messages=self.adapter.ws_subscribe_messages(self.base),
        )
        with self._ws_lock:
            if self.stopped:
                return False
            self._ws = connection
        try:
            return connection.run()
        finally:
            with self._ws_lock:
                self._ws = None

    def _run_rest_loop(self) -> None:
        consecutive_failures = 0
        recovery_at = time.monotonic() + WS_RECOVERY_INTERVAL
        while not self.stopped:
            if self.adapter.supports_ws and time.monotonic() >= recovery_at:
                return  # supervisor starts a fresh, fenced WS reconstruction
            ok = self._poll_once()
            consecutive_failures = 0 if ok else consecutive_failures + 1
            interval = max(self._service.poll_interval, self.adapter.rest_poll_interval)
            if not ok:
                interval = min(interval * (2 ** min(consecutive_failures, 3)), 30.0)
            interval = max(interval, self._retry_after)
            # Venue Retry-After takes precedence over the recovery cadence.
            self._stop_event.wait(interval)

    def _poll_once(self) -> bool:
        self._retry_after = 0.0
        try:
            snapshot = self.adapter.fetch_snapshot(self._http, self.base)
        except Exception as exc:
            if isinstance(exc, RateLimitError):
                self._retry_after = exc.retry_after
            self._service._record_error(
                self.adapter.name, "rest", str(exc), self._generation
            )
            return False
        if self.stopped:
            return False
        return self._service._record_snapshot(snapshot, "rest", self._generation)


class OrderBookService:
    """Thread-safe session store. Lifecycle serialization never blocks callbacks."""

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
        self._lifecycle_lock = threading.RLock()
        self._generation = 0
        self._subscription: Optional[Tuple[str, Tuple[str, ...]]] = None
        self._workers: List[FeedWorker] = []
        self._books: Dict[str, OrderBookSnapshot] = {}
        self._statuses: Dict[str, FeedStatus] = {}
        self._spread_history: Dict[str, Deque[SpreadPoint]] = {}
        self._depth_history: Dict[str, Deque[DepthPoint]] = {}
        self._last_depth_record: Dict[str, float] = {}
        self._last_observation: Dict[str, float] = {}

    @staticmethod
    def _stop_workers(workers: Sequence[FeedWorker]) -> None:
        for worker in workers:
            worker.stop()
        for worker in workers:
            worker.join()

    def set_subscription(self, base: str, exchanges: Sequence[str]) -> None:
        names = tuple(sorted(set(exchanges)))
        if base not in CANONICAL_ASSETS or any(
            name not in ADAPTER_FACTORIES for name in names
        ):
            raise ValueError("unknown legacy instrument or venue")
        key = (base, names)
        with self._lifecycle_lock:
            with self._lock:
                if key == self._subscription:
                    return
                self._subscription = key
                self._generation += 1
                generation = self._generation
                old_workers, self._workers = self._workers, []
                self._books.clear()
                self._spread_history.clear()
                self._depth_history.clear()
                self._last_depth_record.clear()
                self._last_observation.clear()
                self._statuses = {name: FeedStatus() for name in names}
            self._stop_workers(old_workers)
            for name in names:
                worker = FeedWorker(self, ADAPTER_FACTORIES[name](), base, generation)
                with self._lock:
                    if self._generation != generation:
                        break
                    self._workers.append(worker)
                try:
                    worker.start()
                except Exception:
                    with self._lock:
                        self._workers.remove(worker)
                    self.stop()
                    raise

    def stop(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                workers, self._workers = self._workers, []
                self._generation += 1
                self._subscription = None
                self._books.clear()
                self._statuses.clear()
                self._spread_history.clear()
                self._depth_history.clear()
                self._last_depth_record.clear()
                self._last_observation.clear()
            self._stop_workers(workers)

    def _clear_history(self, exchange: str) -> None:
        self._spread_history.pop(exchange, None)
        self._depth_history.pop(exchange, None)
        self._last_depth_record.pop(exchange, None)

    def _record_snapshot(
        self, snapshot: OrderBookSnapshot, source: str, generation: int
    ) -> bool:
        now, mono = time.time(), time.monotonic()
        if not isinstance(snapshot, OrderBookSnapshot) or source not in (
            "rest",
            "websocket",
        ):
            return False
        if (
            not math.isfinite(snapshot.timestamp)
            or not 0 <= now - snapshot.timestamp <= self.stale_after
        ):
            self._record_error(
                snapshot.exchange, source, "stale or invalid snapshot time", generation
            )
            return False
        with self._lock:
            if generation != self._generation:
                return False
            exchange = snapshot.exchange
            if self._subscription is not None:
                base, names = self._subscription
                if exchange not in names or snapshot_cohort(snapshot) != (
                    base,
                    _VENUE_PAIRS[exchange][1],
                ):
                    return False
            status = self._statuses.setdefault(exchange, FeedStatus())
            previous = self._last_observation.get(exchange)
            if status.source != source or (
                previous is not None and mono - previous > self.stale_after
            ):
                self._clear_history(exchange)
            self._books[exchange] = snapshot
            status.state = (
                FeedState.DEGRADED if source == "rest" else FeedState.CONNECTED
            )
            status.source = source
            status.last_update = now
            status.last_error = (
                "REST snapshot mode; no event continuity" if source == "rest" else None
            )
            self._last_observation[exchange] = mono
            spread_hist = self._spread_history.setdefault(
                exchange, deque(maxlen=self._spread_history_len)
            )
            spread_hist.append(
                (
                    now,
                    snapshot.best_bid,
                    snapshot.best_ask,
                    snapshot.spread_bps,
                    snapshot.mid_price,
                )
            )
            last_depth = self._last_depth_record.get(exchange)
            if last_depth is None or mono - last_depth >= self._depth_history_interval:
                self._depth_history.setdefault(
                    exchange, deque(maxlen=self._depth_history_len)
                ).append((now, snapshot))
                self._last_depth_record[exchange] = mono
            return True

    def _record_error(
        self, exchange: str, source: str, message: str, generation: int
    ) -> None:
        with self._lock:
            if generation != self._generation:
                return
            status = self._statuses.setdefault(exchange, FeedStatus())
            status.source = source
            status.last_error = message
            status.state = (
                FeedState.DEGRADED
                if exchange in self._books
                else FeedState.DISCONNECTED
            )
            self._clear_history(exchange)

    def get_snapshot(self, exchange: str) -> Optional[OrderBookSnapshot]:
        with self._lock:
            return self._books.get(exchange)

    def get_all_snapshots(self) -> Dict[str, OrderBookSnapshot]:
        with self._lock:
            return dict(self._books)

    def get_statuses(self) -> Dict[str, FeedStatus]:
        with self._lock:
            statuses = {name: status.copy() for name, status in self._statuses.items()}
            observations = dict(self._last_observation)
        now = time.monotonic()
        for name, status in statuses.items():
            observed = observations.get(name)
            if observed is not None and now - observed > self.stale_after:
                status.state = FeedState.DEGRADED
                status.last_error = f"no update for {now - observed:.0f}s (stale)"
        return statuses

    def get_spread_history(self, exchange: str) -> List[SpreadPoint]:
        with self._lock:
            return list(self._spread_history.get(exchange, ()))

    def get_depth_history(self, exchange: str) -> List[DepthPoint]:
        with self._lock:
            return list(self._depth_history.get(exchange, ()))


def get_session_service(state: MutableMapping) -> OrderBookService:
    """No process-global Streamlit cache: each browser session owns its store."""
    key = "legacy_orderbook_service"
    if key not in state:
        state[key] = OrderBookService()
    return state[key]


_VENUE_PAIRS = {
    "Binance.US": (("binanceus",), "USDT"),
    "Coinbase": (("coinbase",), "USD"),
    "Kraken": (("kraken_rest", "kraken_ws"), "USD"),
    "KuCoin": (("kucoin",), "USDT"),
    "Bybit": (("bybit",), "USDT"),
}


def snapshot_cohort(snapshot: OrderBookSnapshot) -> Optional[Tuple[str, str]]:
    """Resolve only known legacy spot mappings; never guess a quote suffix."""
    rules = _VENUE_PAIRS.get(snapshot.exchange)
    if rules is None:
        return None
    for base in CANONICAL_ASSETS:
        if any(snapshot.symbol == pair_for(key, base) for key in rules[0]):
            return base, rules[1]
    return None


def comparable_snapshots(
    snapshots: Dict[str, OrderBookSnapshot],
    statuses: Dict[str, FeedStatus],
    max_age: float = 15.0,
    max_skew: float = 2.0,
    now: Optional[float] = None,
):
    """Legacy eligibility shared by comparison display; return exclusions too.

    Both local observation freshness and snapshot as-of freshness are required.
    USD/USDT and base assets remain separate; unknown mappings fail closed.
    This path is informational spot comparison, not execution eligibility.
    """
    now = time.time() if now is None else now
    cohorts: Dict[Tuple[str, str], Dict[str, OrderBookSnapshot]] = {}
    excluded: Dict[str, str] = {}
    for name, snap in snapshots.items():
        status = statuses.get(name)
        cohort = snapshot_cohort(snap)
        if name != snap.exchange or cohort is None:
            excluded[name] = "unknown instrument mapping"
        elif status is None or status.state != FeedState.CONNECTED:
            excluded[name] = "feed unavailable or degraded"
        elif status.last_update is None or not all(
            math.isfinite(t) and 0 <= now - t <= max_age
            for t in (status.last_update, snap.timestamp)
        ):
            excluded[name] = "stale or invalid observation time"
        else:
            cohorts.setdefault(cohort, {})[name] = snap
    for books in cohorts.values():
        newest = max(snap.timestamp for snap in books.values())
        for name, snap in list(books.items()):
            if newest - snap.timestamp > max_skew:
                excluded[name] = "as-of skew exceeds comparison bound"
                del books[name]
    return cohorts, excluded
