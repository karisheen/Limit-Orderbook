"""M0 safety contracts against the legacy API; remove each xfail with its fix.

Synthetic inputs only. Transport, clocks, and thread starts are controlled; the
actual normalizer, adapter, renderer, and supervision code remain under test.
Strict xfails catch AssertionError only, so unrelated API/import/parser errors
are failures rather than silently accepted evidence of a known bug.
"""

import json
from unittest.mock import Mock

import pytest

from src.data import clients
from src.data.adapters.base import LiveBook
from src.data.adapters.kraken import KrakenAdapter
from src.domain import orderbook_service as service_module
from src.domain.models import FeedState, FeedStatus, make_snapshot
from src.domain.orderbook_service import FeedWorker, OrderBookService
from src.ui import components


def known_bug(reason):
    return pytest.mark.xfail(strict=True, raises=AssertionError, reason=reason)


def assert_snapshot_rejected(bids, asks):
    try:
        make_snapshot("Kraken", "BTC/USD", bids, asks)
    except ValueError:
        return
    raise AssertionError("unsafe snapshot must raise ValueError")


@known_bug("M1: positive infinity passes the legacy positivity-only filter")
@pytest.mark.parametrize("side", ["bids", "asks"])
@pytest.mark.parametrize("field", [0, 1], ids=["price", "quantity"])
def test_nonfinite_snapshot_is_rejected(side, field):
    rows = {"bids": [["100", "1"]], "asks": [["101", "1"]]}
    rows[side][0][field] = "Infinity"
    assert_snapshot_rejected(**rows)


@known_bug("M1: duplicate snapshot prices are retained and double-count depth")
@pytest.mark.parametrize("side", ["bids", "asks"])
def test_duplicate_snapshot_prices_are_rejected(side):
    rows = {"bids": [["100", "1"]], "asks": [["101", "1"]]}
    rows[side].append([rows[side][0][0], "2"])
    # A snapshot is already price-aggregated: ambiguous duplicate rows must not
    # be summed. This does not forbid repeated absolute replacements in deltas.
    assert_snapshot_rejected(**rows)


@known_bug("M1: NaN delta fields enter float-keyed mutable book state")
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


@known_bug("M1: internally crossed venue snapshots are accepted")
def test_internally_crossed_snapshot_is_rejected():
    assert_snapshot_rejected([["102", "1"]], [["101", "1"]])


def kraken_message(kind, bids, asks):
    return json.dumps(
        {
            "channel": "book",
            "type": kind,
            "data": [
                {
                    "symbol": "BTC/USD",
                    "bids": [{"price": price, "qty": qty} for price, qty in bids],
                    "asks": [{"price": price, "qty": qty} for price, qty in asks],
                }
            ],
        }
    )


@known_bug("M1: Kraken subscribes at 25 but retains LiveBook's default 200")
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
    snapshot = adapter.handle_ws_message(kraken_message("update", **update), "BTC")
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


@known_bug("M1: comparison ranks stored books even when their feed is ineligible")
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


@known_bug("M1: comparison trusts CONNECTED without checking freshness or as-of skew")
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


@known_bug("M1: USD and USDT quotes form a single synthetic best bid/ask")
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
        def __init__(self, url, on_open, on_message, on_error):
            self.on_open = on_open
            self.on_message = on_message

        def send(self, message):
            pass

        def run_forever(self, **kwargs):
            self.on_open(self)
            self.on_message(self, json.dumps({"channel": "heartbeat"}))

        def close(self):
            pass

    monkeypatch.setattr(clients.websocket, "WebSocketApp", HeartbeatSocket)


@known_bug("M1: arbitrary websocket frames are counted as successful book recovery")
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


@known_bug("M1: repeated heartbeat-only sessions reset failure budget forever")
def test_heartbeat_only_reconnects_eventually_fall_back(heartbeat_socket, monkeypatch):
    service = OrderBookService()
    worker = FeedWorker(service, KrakenAdapter(), "BTC", service._generation)
    worker._stop_event = VirtualStopEvent(60.0)
    fallback = Mock()
    monkeypatch.setattr(worker, "_run_rest_loop", fallback)
    worker._run_ws_with_fallback()
    assert fallback.call_count == 1


@known_bug("M1: REST fallback never probes websocket recovery")
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


@known_bug("M1: cached global service has only one mutable subscription")
def test_second_browser_consumer_does_not_destroy_first_consumers_book(inert_workers):
    # crypto_lob.get_service is @st.cache_resource with no session key. Model
    # its shared return value directly; importing the entrypoint starts UI/feed
    # work. This is an ownership regression, not a browser end-to-end test.
    first_consumer = second_consumer = OrderBookService()
    first_consumer.set_subscription("BTC", ["Kraken"])
    btc = snapshot("Kraken")
    first_consumer._record_snapshot(btc, "websocket", first_consumer._generation)
    second_consumer.set_subscription("ETH", ["Kraken"])
    assert first_consumer.get_snapshot("Kraken") == btc


@known_bug(
    "M1: subscription replacement/shutdown requests stop without joining workers"
)
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


@known_bug("M1: superseded set_subscription drops ownership of a just-started worker")
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
