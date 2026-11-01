"""Deterministic safety contracts for legacy precision, UI and supervision.

Synthetic inputs only. Transport, clocks and thread starts are controlled;
the real normalizer, adapter, renderer and supervision remain under test.
"""

import json
import zlib
from decimal import Decimal
from unittest.mock import Mock

import pytest

from src.data import clients
from src.data.adapters.base import LiveBook
from src.data.adapters.kraken import KrakenAdapter
from src.domain import orderbook_service as service_module
from src.domain.models import FeedState, FeedStatus, make_snapshot
from src.domain.orderbook_service import FeedWorker, OrderBookService
from src.ui import components


def assert_snapshot_rejected(bids, asks):
    try:
        make_snapshot("Kraken", "BTC/USD", bids, asks)
    except ValueError:
        return
    raise AssertionError("unsafe snapshot must raise ValueError")


@pytest.mark.parametrize("side", ["bids", "asks"])
@pytest.mark.parametrize("field", [0, 1], ids=["price", "quantity"])
def test_nonfinite_snapshot_is_rejected(side, field):
    rows = {"bids": [["100", "1"]], "asks": [["101", "1"]]}
    rows[side][0][field] = "Infinity"
    assert_snapshot_rejected(**rows)


@pytest.mark.parametrize("side", ["bids", "asks"])
def test_duplicate_snapshot_prices_are_rejected(side):
    rows = {"bids": [["100", "1"]], "asks": [["101", "1"]]}
    rows[side].append([rows[side][0][0], "2"])
    # A snapshot is already price-aggregated: ambiguous duplicate rows must not
    # be summed. This does not forbid repeated absolute replacements in deltas.
    assert_snapshot_rejected(**rows)


@pytest.mark.parametrize("side", ["bids", "asks"])
@pytest.mark.parametrize("field", [0, 1], ids=["price", "quantity"])
def test_nan_delta_is_rejected_before_mutating_book(side, field):
    book = LiveBook()
    book.apply([["100", "1"]], [["101", "1"]], replace=True)
    update = {"bids": [], "asks": []}
    row = ["99" if side == "bids" else "102", "1"]
    row[field] = "NaN"
    update[side] = [row]
    try:
        book.apply(**update)
    except ValueError:
        assert book.bids == {100.0: 1.0}
        assert book.asks == {101.0: 1.0}
        return
    raise AssertionError("nonfinite delta must raise ValueError before mutation")


def test_internally_crossed_snapshot_is_rejected():
    assert_snapshot_rejected([["102", "1"]], [["101", "1"]])


def kraken_message(kind, bids, asks, checksum=None):
    return json.dumps(
        {
            "channel": "book",
            "type": kind,
            "data": [
                {
                    "symbol": "BTC/USD",
                    "checksum": checksum
                    if checksum is not None
                    else kraken_checksum(bids, asks),
                    "bids": [{"price": price, "qty": qty} for price, qty in bids],
                    "asks": [{"price": price, "qty": qty} for price, qty in asks],
                }
            ],
        }
    )


def kraken_checksum(bids, asks):
    def digits(value):
        return format(Decimal(str(value)), "f").replace(".", "").lstrip("0")

    levels = sorted(asks)[:10] + sorted(bids, reverse=True)[:10]
    return zlib.crc32("".join(digits(p) + digits(q) for p, q in levels).encode())


@pytest.mark.parametrize("side", ["bids", "asks"])
def test_kraken_truncates_each_update_to_subscription_depth(side):
    adapter = KrakenAdapter()
    depth = json.loads(adapter.ws_subscribe_message("BTC"))["params"]["depth"]
    bids = [(100 - index, 1) for index in range(depth)]
    asks = [(110 + index, 1) for index in range(depth)]
    initial = adapter.handle_ws_message(kraken_message("snapshot", bids, asks), "BTC")
    assert initial is not None and len(getattr(initial, side)) == depth
    update = {"bids": [], "asks": []}
    update[side] = [(101 if side == "bids" else 109, 1)]
    next_bids = sorted(bids + update["bids"], reverse=True)[:depth]
    next_asks = sorted(asks + update["asks"])[:depth]
    snapshot = adapter.handle_ws_message(
        kraken_message(
            "update", **update, checksum=kraken_checksum(next_bids, next_asks)
        ),
        "BTC",
    )
    assert snapshot is not None
    # Check stored state too: slicing only the published view leaves ghost
    # liquidity that can reappear when a better price is subsequently deleted.
    assert len(getattr(snapshot, side)) == depth
    assert len(getattr(adapter._book, side)) == depth
    assert (bids if side == "bids" else asks)[-1][0] not in getattr(adapter._book, side)


class ComparisonOutput:
    """Capture real renderer output without a Streamlit session or browser."""

    def __init__(self):
        self.metrics = []

    def columns(self, count):
        return [self] * count

    def metric(self, label, value, delta=None, **kwargs):
        self.metrics.append((label, value, delta))

    def dataframe(self, *args, **kwargs):
        pass

    def caption(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass


@pytest.fixture
def comparison_output(monkeypatch):
    output = ComparisonOutput()
    monkeypatch.setattr(components, "st", output)
    return output


def snapshot(venue, symbol="BTC/USD", bid="100", ask="101", timestamp=1000.0):
    return make_snapshot(venue, symbol, [[bid, "1"]], [[ask, "1"]], timestamp=timestamp)


@pytest.mark.parametrize("state", [FeedState.DEGRADED, FeedState.DISCONNECTED])
def test_comparison_does_not_select_unhealthy_venue(
    comparison_output, state, monkeypatch
):
    monkeypatch.setattr("src.domain.models.time.time", lambda: 1000.0)
    components.render_comparison(
        {
            "Kraken": snapshot("Kraken"),
            "Coinbase": snapshot("Coinbase", "BTC-USD", "105", "106"),
        },
        {
            "Kraken": FeedStatus(FeedState.CONNECTED, "websocket", 1000.0),
            "Coinbase": FeedStatus(state, "rest", 1.0, "feed unavailable"),
        },
    )
    assert any(venue == "Kraken" for _, _, venue in comparison_output.metrics)
    assert all(venue != "Coinbase" for _, _, venue in comparison_output.metrics)


def test_comparison_does_not_select_old_connected_snapshot(
    comparison_output, monkeypatch
):
    monkeypatch.setattr("src.domain.models.time.time", lambda: 1000.0)
    components.render_comparison(
        {
            "Kraken": snapshot("Kraken"),
            "Coinbase": snapshot("Coinbase", "BTC-USD", "105", "106", timestamp=1.0),
        },
        {
            "Kraken": FeedStatus(FeedState.CONNECTED, "websocket", 1000.0),
            "Coinbase": FeedStatus(FeedState.CONNECTED, "websocket", 1.0),
        },
    )
    assert any(venue == "Kraken" for _, _, venue in comparison_output.metrics)
    assert all(venue != "Coinbase" for _, _, venue in comparison_output.metrics)


def test_comparison_does_not_combine_different_quote_currencies(
    comparison_output, monkeypatch
):
    monkeypatch.setattr("src.domain.models.time.time", lambda: 1000.0)
    components.render_comparison(
        {
            "Kraken": snapshot("Kraken"),
            "Binance.US": snapshot("Binance.US", "BTCUSDT", "110", "111"),
        },
        {
            name: FeedStatus(FeedState.CONNECTED, "websocket", 1000.0)
            for name in ("Kraken", "Binance.US")
        },
    )
    # These are two healthy, internally uncrossed books, but the legacy output
    # creates a fictitious cross using a USDT bid and a USD ask without FX.
    assert not any(
        "crossed" in str(value).lower() for _, value, _ in comparison_output.metrics
    )


@pytest.fixture
def heartbeat_socket(monkeypatch):
    class HeartbeatSocket:
        def __init__(self):
            self.frames = [
                (
                    clients.websocket.ABNF.OPCODE_TEXT,
                    json.dumps({"channel": "heartbeat"}),
                ),
                (clients.websocket.ABNF.OPCODE_CLOSE, ""),
            ]

        def settimeout(self, timeout):
            pass

        def send(self, message):
            pass

        def recv_data(self, **kwargs):
            return self.frames.pop(0)

        def close(self):
            pass

        def shutdown(self):
            pass

    monkeypatch.setattr(
        clients.websocket,
        "create_connection",
        lambda *args, **kwargs: HeartbeatSocket(),
    )


def test_heartbeat_only_session_is_not_successful_book_data(heartbeat_socket):
    service = OrderBookService()
    worker = FeedWorker(service, KrakenAdapter(), "BTC", service._generation)
    got_data = worker._run_ws_once()
    assert service.get_snapshot("Kraken") is None
    assert got_data is False


class VirtualStopEvent:
    """Bound worker loops by virtual elapsed wait time; never sleep or spawn."""

    def __init__(self, budget):
        self.elapsed = 0.0
        self.budget = budget
        self.stopped = False

    def is_set(self):
        return self.stopped or self.elapsed >= self.budget

    def set(self):
        self.stopped = True

    def wait(self, seconds):
        self.elapsed += seconds
        return self.is_set()


def test_heartbeat_only_reconnects_eventually_fall_back(heartbeat_socket, monkeypatch):
    service = OrderBookService()
    worker = FeedWorker(service, KrakenAdapter(), "BTC", service._generation)
    worker._stop_event = VirtualStopEvent(60.0)
    fallback = Mock(side_effect=worker._stop_event.set)
    monkeypatch.setattr(worker, "_run_rest_loop", fallback)
    worker._run_ws_with_fallback()
    assert fallback.call_count == 1


def test_rest_fallback_probes_websocket_within_five_virtual_minutes(monkeypatch):
    service = OrderBookService()
    worker = FeedWorker(service, KrakenAdapter(), "BTC", service._generation)
    clock = VirtualStopEvent(300.0)
    worker._stop_event = clock
    monkeypatch.setattr(service_module.time, "monotonic", lambda: clock.elapsed)
    monkeypatch.setattr(service_module.time, "time", lambda: 1000.0 + clock.elapsed)
    attempts = []

    def connect():
        attempts.append(clock.elapsed)
        return len(attempts) > service_module.MAX_WS_FAILURES

    monkeypatch.setattr(worker, "_run_ws_once", connect)
    rest_fetch = Mock(return_value=snapshot("Kraken"))
    monkeypatch.setattr(worker.adapter, "fetch_snapshot", rest_fetch)
    worker._run_ws_with_fallback()
    assert rest_fetch.call_count > 0
    assert len(attempts) > service_module.MAX_WS_FAILURES


@pytest.fixture
def inert_workers(monkeypatch):
    monkeypatch.setattr(service_module.time, "time", lambda: 1000.0)
    workers = []

    class InertWorker:
        def __init__(self, service, adapter, base, generation):
            self.base = base
            self.started = False
            self.stopped = False
            self.joined = False
            workers.append(self)

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

        def join(self, timeout=None):
            self.joined = True

    monkeypatch.setattr(service_module, "FeedWorker", InertWorker)
    return workers


def test_second_browser_consumer_does_not_destroy_first_consumers_book(inert_workers):
    # Exercise the exact session-state factory used by the entrypoint without
    # importing Streamlit's executable page or creating real feed threads.
    first_state, second_state = {}, {}
    first_consumer = service_module.get_session_service(first_state)
    second_consumer = service_module.get_session_service(second_state)
    assert first_consumer is service_module.get_session_service(first_state)
    assert first_consumer is not second_consumer
    first_consumer.set_subscription("BTC", ["Kraken"])
    btc = snapshot("Kraken")
    first_consumer._record_snapshot(btc, "websocket", first_consumer._generation)
    second_consumer.set_subscription("ETH", ["Kraken"])
    assert first_consumer.get_snapshot("Kraken") == btc


@pytest.mark.parametrize("operation", ["replace", "shutdown"])
def test_service_waits_for_old_worker_shutdown(inert_workers, operation):
    service = OrderBookService()
    service.set_subscription("BTC", ["Kraken"])
    old_worker = inert_workers[0]
    if operation == "replace":
        service.set_subscription("ETH", ["Kraken"])
    else:
        service.stop()
    assert old_worker.stopped is True
    assert old_worker.joined is True


def test_overlapping_subscription_replacement_does_not_orphan_worker(monkeypatch):
    workers = []

    class InterleavedWorker:
        def __init__(self, service, adapter, base, generation):
            self.service = service
            self.base = base
            self.stopped = False
            workers.append(self)

        def start(self):
            if self.base == "BTC":
                # Deterministically reproduce a second caller replacing the
                # subscription before the first caller registers its workers.
                self.service.set_subscription("ETH", ["Kraken"])

        def stop(self):
            self.stopped = True

        def join(self, timeout=None):
            pass

    monkeypatch.setattr(service_module, "FeedWorker", InterleavedWorker)
    service = OrderBookService()
    service.set_subscription("BTC", ["Kraken"])
    service.stop()
    assert all(worker.stopped for worker in workers)


@pytest.mark.parametrize(
    "venue,symbol",
    [
        ("Unknown", "BTCUSD"),
        ("Kraken", "BTC/USDC"),
        ("Coinbase", "NOTBTC-USD"),
    ],
)
def test_comparison_unknown_mapping_is_excluded(venue, symbol):
    cohorts, excluded = service_module.comparable_snapshots(
        {venue: snapshot(venue, symbol)},
        {venue: FeedStatus(FeedState.CONNECTED, "websocket", 1000)},
        now=1000,
    )
    assert cohorts == {}
    assert excluded[venue] == "unknown instrument mapping"


def test_comparison_enforces_configurable_skew_within_freshness_window():
    books = {
        "Kraken": snapshot("Kraken"),
        "Coinbase": snapshot("Coinbase", "BTC-USD", "105", "106", timestamp=995),
    }
    statuses = {
        name: FeedStatus(FeedState.CONNECTED, "websocket", 1000) for name in books
    }
    cohorts, excluded = service_module.comparable_snapshots(
        books, statuses, now=1000, max_skew=2
    )
    assert set(cohorts[("BTC", "USD")]) == {"Kraken"}
    assert "skew" in excluded["Coinbase"]
    cohorts, excluded = service_module.comparable_snapshots(
        books, statuses, now=1000, max_skew=5
    )
    assert set(cohorts[("BTC", "USD")]) == {"Kraken", "Coinbase"}
    assert not excluded


def test_fresh_status_cannot_resurrect_old_snapshot():
    cohorts, excluded = service_module.comparable_snapshots(
        {"Kraken": snapshot("Kraken", timestamp=900)},
        {"Kraken": FeedStatus(FeedState.CONNECTED, "websocket", 1000)},
        now=1000,
    )
    assert not cohorts
    assert "stale" in excluded["Kraken"]


def test_comparison_keeps_different_bases_separate():
    books = {
        "Kraken": snapshot("Kraken"),
        "Coinbase": snapshot("Coinbase", "ETH-USD"),
    }
    statuses = {
        name: FeedStatus(FeedState.CONNECTED, "websocket", 1000) for name in books
    }
    cohorts, _ = service_module.comparable_snapshots(books, statuses, now=1000)
    assert set(cohorts) == {("BTC", "USD"), ("ETH", "USD")}


def test_decimal_ladder_and_plotting_boundaries(monkeypatch):
    book = snapshot(
        "Kraken", bid="100.000000000000000001", ask="101.000000000000000001"
    )
    assert "100.00" in components.ladder_html(book)
    plots = []
    output = ComparisonOutput()
    output.plotly_chart = plots.append
    monkeypatch.setattr(components, "st", output)
    components.render_depth_chart(book)
    components.render_spread_history(
        [
            (1000 + i, book.best_bid, book.best_ask, book.spread_bps, book.mid_price)
            for i in range(3)
        ]
    )
    components.render_heatmap([(1000 + i, book) for i in range(3)])
    assert len(plots) == 3
    assert book.best_bid == Decimal("100.000000000000000001")


def test_subscription_rejects_wrong_asset_and_obsolete_callbacks(inert_workers):
    service = OrderBookService()
    service.set_subscription("BTC", ["Kraken"])
    old_generation = service._generation
    assert service._record_snapshot(snapshot("Kraken"), "websocket", old_generation)
    service.set_subscription("ETH", ["Kraken"])
    assert not service._record_snapshot(snapshot("Kraken"), "websocket", old_generation)
    assert not service._record_snapshot(
        snapshot("Kraken"), "websocket", service._generation
    )
    assert service.get_snapshot("Kraken") is None
    assert service._record_snapshot(
        snapshot("Kraken", "ETH/USD"), "websocket", service._generation
    )


def test_stopped_service_can_restart_same_subscription(inert_workers):
    service = OrderBookService()
    service.set_subscription("BTC", ["Kraken"])
    service.stop()
    service.set_subscription("BTC", ["Kraken"])
    assert len(inert_workers) == 2
    assert inert_workers[0].stopped
    assert inert_workers[1].started


def test_invalid_service_snapshot_does_not_establish_health():
    service = OrderBookService()
    assert not service._record_snapshot(None, "websocket", 0)
    assert not service.get_statuses()


def test_stale_snapshot_cannot_mark_feed_healthy(monkeypatch):
    monkeypatch.setattr(service_module.time, "time", lambda: 1000.0)
    service = OrderBookService()
    assert not service._record_snapshot(
        snapshot("Kraken", timestamp=900), "websocket", 0
    )
    assert service.get_snapshot("Kraken") is None
    assert service.get_statuses()["Kraken"].state == FeedState.DISCONNECTED
