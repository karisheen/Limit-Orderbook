"""Resilience tests: network failures must degrade gracefully, never crash."""
import json

import pytest
import requests

from src.data.clients import FeedError, HttpClient, RateLimitError
from src.domain.models import FeedState, make_snapshot
from src.domain.orderbook_service import FeedWorker, OrderBookService
from src.data.adapters.base import ExchangeAdapter


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("no JSON")
        return self._body


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

    def test_poll_success_records_snapshot_and_connected_status(self):
        service, worker = self._make([good_snapshot()])
        assert worker._poll_once() is True
        assert service.get_snapshot("Flaky") is not None
        status = service.get_statuses()["Flaky"]
        assert status.state == FeedState.CONNECTED
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
        assert worker._poll_once() is True  # poll succeeds...
        assert service.get_snapshot("Flaky") is None  # ...but is discarded


class TestServiceHistory:
    def test_spread_history_appends_and_is_bounded(self):
        service = OrderBookService(spread_history_len=5)
        gen = service._generation
        for i in range(10):
            snap = make_snapshot("X", "BTCUSD", [[str(100 + i), "1"]], [[str(101 + i), "1"]])
            service._record_snapshot(snap, "rest", gen)
        history = service.get_spread_history("X")
        assert len(history) == 5  # bounded by maxlen
        assert history[-1][1] == 109.0  # latest best bid retained

    def test_stale_connected_feed_reports_degraded(self):
        service = OrderBookService(stale_after=0.0)
        gen = service._generation
        service._record_snapshot(good_snapshot(), "websocket", gen)
        status = service.get_statuses()["Flaky"]
        assert status.state == FeedState.DEGRADED
        assert "stale" in (status.last_error or "")
