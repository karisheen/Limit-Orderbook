"""Resilience tests: network failures must degrade gracefully, never crash."""

import json
import threading
from decimal import Decimal

from src.data import clients
from src.domain import orderbook_service as service_module

import pytest
import requests

from src.data.clients import FeedError, HttpClient, RateLimitError
from src.domain.models import FeedState, make_snapshot
from src.domain.orderbook_service import FeedWorker, OrderBookService
from src.data.adapters.base import ExchangeAdapter


class FakeResponse:
    def __init__(self, status_code=200, body=None, text="", headers=None):
        self.status_code = status_code
        self.content = json.dumps(body).encode() if body is not None else text.encode()
        self.text = text
        self.headers = headers or {}


class FakeSession:
    """Yields queued outcomes; an Exception instance is raised, else returned."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else self.outcomes_exhausted()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    @staticmethod
    def outcomes_exhausted():
        raise AssertionError("FakeSession ran out of queued outcomes")


def make_client(outcomes, max_retries=3):
    return HttpClient(
        session=FakeSession(outcomes),
        max_retries=max_retries,
        backoff_base=0.0,  # no sleeping in tests
        sleep=lambda _: None,
    )


class TestHttpClientRetries:
    def test_persistent_timeout_raises_feed_error_after_retries(self):
        client = make_client([requests.exceptions.Timeout("t")] * 3)
        with pytest.raises(FeedError):
            client.get_json("https://x.test/depth")
        assert client._session.calls == 3

    def test_proxy_error_is_retried_then_recovers(self):
        good = FakeResponse(200, {"ok": True})
        client = make_client([requests.exceptions.ProxyError("blocked"), good])
        assert client.get_json("https://x.test/depth") == {"ok": True}
        assert client._session.calls == 2

    def test_429_exhausts_retries_and_raises_rate_limit_error(self):
        client = make_client([FakeResponse(429)] * 3)
        with pytest.raises(RateLimitError):
            client.get_json("https://x.test/depth")

    def test_other_4xx_fails_fast_without_retry(self):
        client = make_client([FakeResponse(404, text="not found")])
        with pytest.raises(FeedError):
            client.get_json("https://x.test/depth")
        assert client._session.calls == 1

    def test_5xx_is_retried(self):
        good = FakeResponse(200, {"ok": 1})
        client = make_client([FakeResponse(502), good])
        assert client.get_json("https://x.test/depth") == {"ok": 1}

    def test_malformed_json_is_retried_then_fails(self):
        client = make_client([FakeResponse(200, body=None)] * 3)
        with pytest.raises(FeedError):
            client.get_json("https://x.test/depth")


class FlakyAdapter(ExchangeAdapter):
    """REST-only adapter whose behavior is scripted per call."""

    name = "Flaky"
    supports_ws = False

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)

    def fetch_snapshot(self, http, base, limit=100):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def good_snapshot():
    return make_snapshot("Flaky", "BTCUSD", [["100", "1"]], [["101", "1"]])


class TestFeedWorkerPolling:
    def _make(self, outcomes):
        service = OrderBookService()
        # Match the worker generation to the service's current generation.
        worker = FeedWorker(service, FlakyAdapter(outcomes), "BTC", service._generation)
        return service, worker

    def test_poll_success_records_snapshot_and_degraded_rest_status(self):
        service, worker = self._make([good_snapshot()])
        assert worker._poll_once() is True
        assert service.get_snapshot("Flaky") is not None
        status = service.get_statuses()["Flaky"]
        assert status.state == FeedState.DEGRADED
        assert status.source == "rest"

    def test_poll_failure_records_error_without_raising(self):
        service, worker = self._make([FeedError("boom")])
        assert worker._poll_once() is False
        status = service.get_statuses()["Flaky"]
        assert status.state == FeedState.DISCONNECTED
        assert "boom" in (status.last_error or "")

    def test_failure_after_success_degrades_but_keeps_book(self):
        service, worker = self._make([good_snapshot(), FeedError("hiccup")])
        assert worker._poll_once() is True
        assert worker._poll_once() is False
        status = service.get_statuses()["Flaky"]
        assert status.state == FeedState.DEGRADED  # old book still available
        assert service.get_snapshot("Flaky") is not None

    def test_stale_generation_updates_are_ignored(self):
        service, worker = self._make([good_snapshot()])
        service._generation += 1  # simulate a new subscription superseding the worker
        assert worker._poll_once() is False  # obsolete result is not meaningful success
        assert service.get_snapshot("Flaky") is None  # ...but is discarded


class TestServiceHistory:
    def test_spread_history_appends_and_is_bounded(self):
        service = OrderBookService(spread_history_len=5)
        gen = service._generation
        for i in range(10):
            snap = make_snapshot(
                "X", "BTCUSD", [[str(100 + i), "1"]], [[str(101 + i), "1"]]
            )
            service._record_snapshot(snap, "rest", gen)
        history = service.get_spread_history("X")
        assert len(history) == 5  # bounded by maxlen
        assert history[-1][1] == 109.0  # latest best bid retained

    def test_stale_connected_feed_reports_degraded(self, monkeypatch):
        clock = [100.0]
        monkeypatch.setattr(
            "src.domain.orderbook_service.time.monotonic", lambda: clock[0]
        )
        service = OrderBookService(stale_after=15.0)
        gen = service._generation
        service._record_snapshot(good_snapshot(), "websocket", gen)
        clock[0] += 16.0
        status = service.get_statuses()["Flaky"]
        assert status.state == FeedState.DEGRADED
        assert "stale" in (status.last_error or "")


@pytest.mark.parametrize(
    "value, expected",
    [
        ("120", 120.0),
        ("Thu, 01 Jan 1970 00:02:00 GMT", 20.0),
        ("bad", 0.0),
        ("NaN", 0.0),
        ("-1", 0.0),
    ],
)
def test_retry_after_seconds_and_http_dates(value, expected):
    assert clients.retry_after_seconds(value, now=100.0) == expected


@pytest.mark.parametrize("status", [429, 503])
def test_retry_after_is_not_clamped_to_backoff_cap(status):
    waits = []
    session = FakeSession(
        [
            FakeResponse(status, headers={"Retry-After": "120"}),
            FakeResponse(200, {"ok": True}),
        ]
    )
    client = HttpClient(session=session, sleep=waits.append, backoff_cap=1)
    assert client.get_json("https://x.test") == {"ok": True}
    assert waits == [120.0]


def test_final_rate_limit_retains_cooldown_for_worker():
    client = HttpClient(
        session=FakeSession([FakeResponse(429, headers={"Retry-After": "90"})]),
        max_retries=1,
    )
    with pytest.raises(RateLimitError) as error:
        client.get_json("https://x.test")
    service = OrderBookService()
    worker = FeedWorker(service, FlakyAdapter([error.value]), "BTC", 0)
    assert worker._poll_once() is False
    assert worker._retry_after == 90.0


def test_http_stop_interrupts_backoff_without_another_request():
    class CancelOnWait:
        stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, delay):
            self.stopped = True

    session = FakeSession([FakeResponse(429, headers={"Retry-After": "600"})])
    client = HttpClient(session=session, stop_event=CancelOnWait())
    with pytest.raises(FeedError, match="cancelled"):
        client.get_json("https://x.test")
    assert session.calls == 1


def test_http_json_preserves_original_decimal_precision():
    response = requests.Response()
    response.status_code = 200
    response._content = b'{"price": 0.10000000000000000001}'
    client = HttpClient(session=FakeSession([response]))
    assert client.get_json("https://x.test")["price"] == Decimal(
        "0.10000000000000000001"
    )


class ScriptedSocket:
    """Advance virtual time on receive, with no sleep or transport threads."""

    def __init__(self, clock, frames=(), heartbeat=False):
        self.clock = clock
        self.frames = list(frames)
        self.heartbeat = heartbeat
        self.closed = False
        self.shutdown_called = False
        self.sent = []
        self.pings = 0

    def settimeout(self, timeout):
        self.timeout = timeout

    def send(self, message):
        self.sent.append(message)

    def ping(self):
        self.pings += 1

    def recv_data(self, **kwargs):
        self.clock[0] += 1
        if self.frames:
            return self.frames.pop(0)
        if self.heartbeat:
            return clients.websocket.ABNF.OPCODE_TEXT, '{"type":"heartbeat"}'
        raise clients.websocket.WebSocketTimeoutException()

    def shutdown(self):
        self.shutdown_called = True

    def close(self):
        self.closed = True


@pytest.mark.parametrize("heartbeat", [False, True])
def test_ws_initial_snapshot_deadline_including_heartbeat_only(monkeypatch, heartbeat):
    clock = [0.0]
    sock = ScriptedSocket(clock, heartbeat=heartbeat)
    monkeypatch.setattr(
        clients.websocket, "create_connection", lambda *args, **kwargs: sock
    )
    connection = clients.WsConnection(
        "wss://x.test", lambda raw: False, snapshot_timeout=5, clock=lambda: clock[0]
    )
    assert connection.run() is False
    assert clock[0] == 5
    assert sock.closed


def test_ws_silence_deadline_is_independent_of_snapshot_deadline(monkeypatch):
    clock = [0.0]
    sock = ScriptedSocket(clock)
    monkeypatch.setattr(
        clients.websocket, "create_connection", lambda *args, **kwargs: sock
    )
    connection = clients.WsConnection(
        "wss://x.test",
        lambda raw: False,
        snapshot_timeout=30,
        silence_timeout=3,
        clock=lambda: clock[0],
    )
    assert connection.run() is False
    assert clock[0] == 3


def test_ws_missing_pong_expires_even_with_valid_book_frames(monkeypatch):
    clock = [0.0]
    sock = ScriptedSocket(clock, heartbeat=True)
    monkeypatch.setattr(
        clients.websocket, "create_connection", lambda *args, **kwargs: sock
    )
    connection = clients.WsConnection(
        "wss://x.test",
        lambda raw: True,
        ping_interval=2,
        ping_timeout=2,
        clock=lambda: clock[0],
    )
    assert connection.run() is True
    assert clock[0] == 4
    assert sock.pings == 1


def test_ws_valid_snapshot_success_and_multiple_subscriptions(monkeypatch):
    clock = [0.0]
    sock = ScriptedSocket(
        clock,
        [
            (clients.websocket.ABNF.OPCODE_TEXT, "valid book"),
            (clients.websocket.ABNF.OPCODE_CLOSE, ""),
        ],
    )
    monkeypatch.setattr(
        clients.websocket, "create_connection", lambda *args, **kwargs: sock
    )
    connection = clients.WsConnection(
        "wss://x.test",
        lambda raw: raw == "valid book",
        subscribe_messages=("level2", "heartbeats"),
        clock=lambda: clock[0],
    )
    assert connection.run() is True
    assert sock.sent == ["level2", "heartbeats"]
    assert sock.closed


def test_ws_close_before_run_never_opens_socket(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("cancelled connection must not open")

    monkeypatch.setattr(clients.websocket, "create_connection", unexpected)
    connection = clients.WsConnection("wss://x.test", lambda raw: True)
    connection.close()
    assert connection.run() is False


def test_ws_close_during_connect_closes_new_socket(monkeypatch):
    sock = ScriptedSocket([0.0])
    connection = clients.WsConnection("wss://x.test", lambda raw: True)

    def connect(*args, **kwargs):
        connection.close()
        return sock

    monkeypatch.setattr(clients.websocket, "create_connection", connect)
    assert connection.run() is False
    assert sock.closed
    assert not sock.sent


@pytest.mark.parametrize("boundary", ["error", "source", "stale"])
def test_history_restarts_at_gap_or_source_boundary(monkeypatch, boundary):
    clock = [100.0]
    monkeypatch.setattr(service_module.time, "monotonic", lambda: clock[0])
    service = OrderBookService(depth_history_interval=0)
    book = good_snapshot()
    assert service._record_snapshot(book, "websocket", 0)
    assert service._record_snapshot(book, "websocket", 0)
    if boundary == "error":
        service._record_error("Flaky", "websocket", "gap", 0)
    elif boundary == "stale":
        clock[0] += 16
    assert service._record_snapshot(
        book, "rest" if boundary == "source" else "websocket", 0
    )
    assert len(service.get_spread_history("Flaky")) == 1
    assert len(service.get_depth_history("Flaky")) == 1


def test_worker_closes_http_session_on_exit(monkeypatch):
    service = OrderBookService()
    worker = FeedWorker(service, FlakyAdapter([]), "BTC", 0)
    worker.stop()
    closed = []
    monkeypatch.setattr(worker._http, "close", lambda: closed.append(True))
    worker.run()
    assert closed == [True]


def test_subscription_replacement_joins_actual_cancelled_thread(monkeypatch):
    workers = []

    class WaitingWorker(threading.Thread):
        def __init__(self, service, adapter, base, generation):
            super().__init__()
            self.cancelled = threading.Event()
            workers.append(self)

        def run(self):
            self.cancelled.wait()

        def stop(self):
            self.cancelled.set()

    monkeypatch.setattr(service_module, "FeedWorker", WaitingWorker)
    service = OrderBookService()
    try:
        service.set_subscription("BTC", ["Kraken"])
        first = workers[0]
        service.set_subscription("ETH", ["Kraken"])
        assert not first.is_alive()
    finally:
        service.stop()
    assert all(not worker.is_alive() for worker in workers)
