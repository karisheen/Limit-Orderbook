"""Offline native supervision regressions; only transport and time are simulated."""

import asyncio
from decimal import Decimal
import json
from types import SimpleNamespace
import zlib

import pytest

import src.workstation.ingestion as ingestion
from src.workstation.adapters.bybit import BybitAdapter
from src.workstation.adapters.coinbase import CoinbaseAdapter
from src.workstation.adapters.kraken import KrakenAdapter
from src.workstation.adapters.kucoin import KuCoinAdapter
from src.workstation.events import BookUpdate, Control, Gap, SubscriptionKey
from src.workstation.ingestion import FeedCallbacks, FeedSupervisor, SupervisorConfig
from src.workstation.instruments import AssetId, AssetIdentity, Instrument, InstrumentId
from src.workstation.quality import ClockReading, evaluate
from src.workstation.transport import PublicTransportError


def native_feed_identity(adapter):
    symbol, currency, depth = {
        "kraken": ("BTC/USD", "USD", 25),
        "coinbase": ("BTC-USD", "USD", None),
        "bybit": ("BTCUSDT", "USDT", 50),
        "kucoin": ("BTC-USDT", "USDT", 20),
    }[adapter.venue]
    instrument = Instrument(
        InstrumentId(f"{adapter.venue}:{adapter.api_family}:spot:{symbol}"),
        "recovery-fixture-v1",
        adapter.venue,
        adapter.api_family,
        symbol,
        AssetIdentity(AssetId("BTC"), "BTC"),
        AssetIdentity(AssetId(currency), currency),
        Decimal("0.1"),
        Decimal("0.00000001"),
        native_asset_id="XXBTZUSD" if adapter.venue == "kraken" else symbol,
    )
    return instrument, SubscriptionKey.for_instrument(instrument, depth=depth)


def book_frame(venue, *, snapshot=True, quantity="1", sequence=0):
    bids, asks = [["100.0", quantity]], [["102.0", "2"]]
    if venue == "kraken":
        # Independent CRC fixture oracle: asks, then bids, price then quantity.
        digits = "".join(
            value.replace(".", "").lstrip("0")
            for rows in (asks, bids)
            for row in rows
            for value in row
        )
        payload = {
            "channel": "book",
            "type": "snapshot" if snapshot else "update",
            "data": [
                {
                    "symbol": "BTC/USD",
                    "bids": [{"price": p, "qty": q} for p, q in bids],
                    "asks": [{"price": p, "qty": q} for p, q in asks],
                    "checksum": zlib.crc32(digits.encode()) & 0xFFFFFFFF,
                }
            ],
        }
    elif venue == "coinbase":
        payload = {
            "channel": "l2_data",
            "sequence_num": sequence,
            "timestamp": "2026-10-31T10:00:00.123456789Z",
            "events": [
                {
                    "type": "snapshot" if snapshot else "update",
                    "product_id": "BTC-USD",
                    "updates": [
                        {"side": side, "price_level": p, "new_quantity": q}
                        for side, rows in (("bid", bids), ("offer", asks))
                        for p, q in rows
                    ],
                }
            ],
        }
    elif venue == "bybit":
        payload = {
            "topic": "orderbook.50.BTCUSDT",
            "type": "snapshot" if snapshot else "delta",
            "ts": 1234567890123,
            "data": {
                "s": "BTCUSDT",
                "u": 100 + sequence,
                "seq": 500 + sequence,
                "b": bids,
                "a": asks,
            },
        }
    else:
        assert venue == "kucoin" and snapshot
        payload = {
            "code": "200000",
            "data": {
                "sequence": str(100 + sequence),
                "time": 1234567890123,
                "bids": bids,
                "asks": asks,
            },
        }
    return json.dumps(payload, separators=(",", ":")).encode()


class ControlledSocket:
    def __init__(self, opened):
        self.opened = opened
        self.messages = asyncio.Queue()
        self.sent = []
        self.closed = False

    async def __aenter__(self):
        self.opened.put_nowait(self)
        return self

    async def __aexit__(self, *args):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.messages.get()

    async def send(self, body):
        self.sent.append(body)


async def bounded(awaitable):
    return await asyncio.wait_for(awaitable, 2)


async def stop_owner(task, feed):
    feed.request_stop()
    task.cancel()
    await bounded(asyncio.gather(task, return_exceptions=True))


def eligibility(book, instrument):
    # Evaluate at the publication's own as-of, not a wall-clock-dependent age.
    return evaluate(
        book,
        instrument,
        "display",
        ClockReading(book.asof.clock_id, book.asof.receive_mono_ns),
    )


@pytest.mark.parametrize("factory", [KrakenAdapter, BybitAdapter, CoinbaseAdapter])
@pytest.mark.parametrize(
    "malformed",
    [b"{", b"[]", b'{"x":1,"x":2}', b'{"x":NaN}'],
    ids=["truncated-json", "array-root", "duplicate-key", "nonfinite-number"],
)
def test_initialized_native_malformed_frame_requires_fresh_snapshot(factory, malformed):
    async def scenario():
        adapter = factory()
        instrument, key = native_feed_identity(adapter)
        opened, publications, diagnostics = (
            asyncio.Queue(),
            asyncio.Queue(),
            asyncio.Queue(),
        )
        books, events, sockets = [], [], []

        def connector(*args, **kwargs):
            socket = ControlledSocket(opened)
            sockets.append(socket)
            return socket

        def publish(book):
            books.append(book)
            publications.put_nowait(book)

        async def unexpected_http(request):
            pytest.fail("native WS recovery must not silently become a REST snapshot")

        feed = FeedSupervisor(
            adapter,
            instrument,
            key,
            callbacks=FeedCallbacks(
                book=publish,
                events=events.extend,
                diagnostic=diagnostics.put_nowait,
            ),
            config=SupervisorConfig(
                backoff_base=0.001,
                backoff_max=0.001,
                ws_failures_before_rest=100,
                circuit_failures=100,
            ),
            ws_connect=connector,
            http_fetch=unexpected_http,
        )
        owner = asyncio.create_task(feed.run())
        try:
            first = await bounded(opened.get())
            first.messages.put_nowait(book_frame(adapter.venue))
            initial = await bounded(publications.get())
            assert initial.quality.integrity == "valid"
            assert initial.quality.lifecycle == "live"
            assert eligibility(initial, instrument).eligible
            first.messages.put_nowait(malformed)
            reason = await bounded(diagnostics.get())
            assert reason.startswith("decode:")
            invalid = await bounded(publications.get())
            assert invalid.quality.integrity == "invalid"
            assert invalid.quality.lifecycle == "resynchronizing"
            assert "integrity_invalid" in eligibility(invalid, instrument).reasons
            assert not eligibility(invalid, instrument).eligible
            assert any(
                isinstance(e.payload, Gap) and e.payload.reason_code == reason
                for e in events
            )
            assert first.closed

            second = await bounded(opened.get())
            assert feed.lifecycle == "waiting_snapshot"
            boundary = len(books)
            # This is a valid native delta at a fresh connection's first sequence,
            # not a stale/duplicate update or an unrelated sequence-gap rejection.
            second.messages.put_nowait(
                book_frame(adapter.venue, snapshot=False, quantity="3")
            )
            delta_reason = await bounded(diagnostics.get())
            assert delta_reason == "snapshot_required"
            assert not any(
                book.quality.integrity == "valid" for book in books[boundary:]
            )
            assert second.closed
            assert feed.lifecycle not in ("live", "degraded")

            third = await bounded(opened.get())
            # An invalid diagnostic book may have been republished for the delta;
            # wait specifically for the next valid consumer publication.
            third.messages.put_nowait(book_frame(adapter.venue, quantity="4"))
            while True:
                recovered = await bounded(publications.get())
                if recovered.quality.integrity == "valid":
                    break
            assert recovered.bids[0].quantity == Decimal("4")
            assert recovered.quality.lifecycle == "live"
            assert eligibility(recovered, instrument).eligible
            assert recovered.connection_epoch > initial.connection_epoch
            assert recovered.segment_id != initial.segment_id
            assert len(sockets) == 3
            assert all(socket.sent for socket in sockets)
        finally:
            await stop_owner(owner, feed)
        assert all(socket.closed for socket in sockets)
        assert feed.lifecycle == "disconnected"
        assert not eligibility(books[-1], instrument).eligible

    asyncio.run(scenario())


class ControlledTime:
    """Gate supervisor sleeps without patching asyncio's own timeout clock."""

    def __init__(self, monkeypatch):
        self.now = 1000.0
        self.actions = asyncio.Queue()
        monkeypatch.setattr(
            ingestion,
            "time",
            SimpleNamespace(
                monotonic=lambda: self.now,
                monotonic_ns=lambda: int(self.now * 1_000_000_000),
                time_ns=lambda: int(self.now * 1_000_000_000),
            ),
        )
        monkeypatch.setattr(ingestion, "asyncio", self)

    def __getattr__(self, name):
        return getattr(asyncio, name)

    async def sleep(self, delay):
        if delay == 0:
            # A cooperative yield is not a scheduled timer or clock advance.
            await asyncio.sleep(0)
            return
        release = asyncio.Event()
        self.actions.put_nowait(("sleep", delay, release))
        await release.wait()
        self.now += delay


@pytest.mark.parametrize("fallback_threshold", [1, 2, 100])
def test_http_rate_limit_cooldown_governs_supervisor_request_schedule(
    monkeypatch, fallback_threshold
):
    async def scenario():
        clock = ControlledTime(monkeypatch)
        adapter = KuCoinAdapter()
        instrument, key = native_feed_identity(adapter)
        books, events, calls = asyncio.Queue(), [], []

        async def fetch(request):
            assert request == adapter.snapshot_request(instrument, key)
            calls.append(clock.now)
            clock.actions.put_nowait(("request", clock.now, None))
            if len(calls) <= 2:
                raise PublicTransportError("rate_limited", status=429, retry_after=90)
            return book_frame("kucoin")

        feed = FeedSupervisor(
            adapter,
            instrument,
            key,
            callbacks=FeedCallbacks(book=books.put_nowait, events=events.extend),
            config=SupervisorConfig(
                backoff_base=0.1,
                backoff_max=0.2,
                ws_failures_before_rest=fallback_threshold,
            ),
            http_fetch=fetch,
        )
        owner = asyncio.create_task(feed.run())
        try:
            for attempt in range(2):
                kind, requested_at, _ = await bounded(clock.actions.get())
                assert kind == "request"
                assert len(calls) == attempt + 1
                kind, delay, release = await bounded(clock.actions.get())
                assert kind == "sleep", (
                    "supervisor retried HTTP before its rate-limit cooldown"
                )
                assert delay >= 90, "ordinary exponential backoff shortened Retry-After"
                assert feed.lifecycle not in ("live", "degraded")
                assert books.empty()
                release.set()
                assert (
                    clock.now == requested_at
                )  # advancing requires the owner to resume
            kind, requested_at, _ = await bounded(clock.actions.get())
            assert kind == "request"
            assert calls == [1000.0, 1090.0, 1180.0]
            assert requested_at == calls[-1]
            recovered = await bounded(books.get())
            assert recovered.quality.integrity == "valid"
            assert recovered.quality.lifecycle == "degraded"
            assert eligibility(recovered, instrument).eligible
            assert any(
                isinstance(e.payload, Gap) and "rate_limited" in e.payload.reason_code
                for e in events
            )
        finally:
            await stop_owner(owner, feed)
        assert len(calls) == 3
        assert feed.lifecycle == "disconnected"

    asyncio.run(scenario())


def test_rest_only_failures_before_and_after_success_invalidate_and_recover(
    monkeypatch,
):
    async def scenario():
        clock = ControlledTime(monkeypatch)
        adapter = KuCoinAdapter()
        instrument, key = native_feed_identity(adapter)
        books, events, reasons, requests = [], [], [], []

        async def fetch(request):
            requests.append(request)
            if len(requests) in (1, 3):
                raise OSError("offline synthetic connection failure")
            return book_frame("kucoin", quantity=str(len(requests)))

        feed = FeedSupervisor(
            adapter,
            instrument,
            key,
            callbacks=FeedCallbacks(
                book=books.append, events=events.extend, diagnostic=reasons.append
            ),
            config=SupervisorConfig(backoff_base=0.1, backoff_max=0.2),
            http_fetch=fetch,
        )
        assert adapter.ws_endpoint(instrument, key) is None
        owner = asyncio.create_task(feed.run())
        try:
            _, _, release = await bounded(clock.actions.get())
            assert len(requests) == 1 and not owner.done()
            assert not books and feed.book is None
            assert feed.lifecycle == "resynchronizing"
            assert "snapshot:network_error" in reasons
            assert any(isinstance(e.payload, Gap) for e in events)

            release.set()
            _, _, release = await bounded(clock.actions.get())
            initial = books[-1]
            assert len(requests) == 2 and not owner.done()
            assert initial.bids[0].quantity == Decimal("2")
            assert initial.quality.integrity == "valid"
            assert initial.quality.lifecycle == feed.lifecycle == "degraded"
            assert initial.quality.source_mode == "rest_snapshot"
            assert not initial.quality.capabilities.exact_event_flow
            assert eligibility(initial, instrument).eligible

            release.set()
            _, _, release = await bounded(clock.actions.get())
            invalid = books[-1]
            assert len(requests) == 3 and not owner.done()
            assert (
                invalid.bids == initial.bids
            )  # retained only as invalid diagnostic state
            assert invalid.quality.integrity == "invalid"
            assert invalid.quality.lifecycle == feed.lifecycle == "resynchronizing"
            assert "snapshot:network_error" in invalid.quality.reason_codes
            assert not eligibility(invalid, instrument).eligible
            assert "integrity_invalid" in eligibility(invalid, instrument).reasons

            release.set()
            await bounded(clock.actions.get())
            recovered = books[-1]
            assert len(requests) == 4 and not owner.done()
            assert recovered.bids[0].quantity == Decimal("4")
            assert recovered.quality.integrity == "valid"
            assert recovered.quality.lifecycle == feed.lifecycle == "degraded"
            assert eligibility(recovered, instrument).eligible
            assert recovered.connection_epoch > initial.connection_epoch
            assert recovered.segment_id != initial.segment_id
            assert all(
                request == adapter.snapshot_request(instrument, key)
                for request in requests
            )
        finally:
            await stop_owner(owner, feed)
        assert len(requests) == 4
        assert feed.lifecycle == "disconnected"
        assert not eligibility(books[-1], instrument).eligible

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "request_stop", [False, True], ids=["cancel", "quiesce-and-cancel"]
)
def test_owner_cancellation_during_connector_entry_joins_socket_without_publication(
    request_stop,
):
    async def scenario():
        adapter = CoinbaseAdapter()
        instrument, key = native_feed_identity(adapter)
        entered, cancelled, finish_close = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )
        permit_entry = asyncio.Event()
        raw, books, events, connections = [], [], [], []

        class PendingSocket:
            live = False
            exited = False
            reads = 0

            def __init__(self):
                self.sent = []

            async def __aenter__(self):
                self.live = True
                entered.set()
                try:
                    await permit_entry.wait()
                    return self
                except asyncio.CancelledError:
                    cancelled.set()
                    await finish_close.wait()
                    self.live = False
                    raise

            async def __aexit__(self, *args):
                self.exited = True
                self.live = False

            async def send(self, body):
                self.sent.append(body)

            def __aiter__(self):
                return self

            async def __anext__(self):
                self.reads += 1
                return book_frame("coinbase")

        socket = PendingSocket()

        def connector(*args, **kwargs):
            connections.append(args)
            return socket

        feed = FeedSupervisor(
            adapter,
            instrument,
            key,
            ws_connect=connector,
            callbacks=FeedCallbacks(
                raw=raw.append, book=books.append, events=events.extend
            ),
        )
        baseline = asyncio.all_tasks()
        owner = asyncio.create_task(feed.run())
        try:
            await bounded(entered.wait())
            assert feed.lifecycle == "waiting_snapshot" and socket.live
            if request_stop:
                feed.request_stop()
            owner.cancel()
            await bounded(cancelled.wait())
            assert not owner.done(), "owner must join pending connector cleanup"
            assert socket.live and not socket.sent
            assert not raw and not books
            finish_close.set()
            with pytest.raises(asyncio.CancelledError):
                await bounded(owner)
            permit_entry.set()
            await asyncio.sleep(0)
            assert not socket.live and not socket.exited
            assert socket.reads == 0 and not socket.sent
            assert len(connections) == 1
            assert not raw and not books
            assert not any(isinstance(e.payload, BookUpdate) for e in events)
            gaps = [e.payload for e in events if isinstance(e.payload, Gap)]
            assert len(gaps) == 1 and gaps[0].reason_code == "owner_stopped"
            assert feed.lifecycle == "disconnected"
            assert asyncio.all_tasks() <= baseline
        finally:
            finish_close.set()
            if not owner.done():
                await stop_owner(owner, feed)

    asyncio.run(scenario())


def test_repeated_native_heartbeat_only_connections_fall_back_to_rest(monkeypatch):
    async def scenario():
        clock = ControlledTime(monkeypatch)
        adapter = KrakenAdapter()
        instrument, key = native_feed_identity(adapter)
        opened = asyncio.Queue()
        sockets, books, events, reasons, requests = [], [], [], [], []

        def connector(*args, **kwargs):
            socket = ControlledSocket(opened)
            socket.messages.put_nowait(
                json.dumps(
                    {
                        "method": "subscribe",
                        "success": True,
                        "result": {"channel": "book", "symbol": "BTC/USD", "depth": 25},
                    }
                ).encode()
            )
            socket.messages.put_nowait(b'{"channel":"heartbeat"}')
            sockets.append(socket)
            return socket

        def observe(delivered, book):
            if any(
                isinstance(event.payload, Control) and event.payload.kind == "heartbeat"
                for event in delivered
            ):
                assert book is None
                # Advance the external clock only after the real decoder and
                # reducer consumed transport activity without a valid snapshot.
                clock.now += 21

        async def fetch(request):
            requests.append(request)
            assert len(sockets) == 2 and all(socket.closed for socket in sockets)
            assert not books
            return json.dumps(
                {
                    "error": [],
                    "result": {
                        "XXBTZUSD": {
                            "bids": [["100.0", "1", 1234567890]],
                            "asks": [["102.0", "2", 1234567890]],
                        },
                    },
                }
            ).encode()

        feed = FeedSupervisor(
            adapter,
            instrument,
            key,
            callbacks=FeedCallbacks(
                book=books.append,
                events=events.extend,
                diagnostic=reasons.append,
                processed=observe,
            ),
            config=SupervisorConfig(
                snapshot_timeout=20,
                silence_timeout=30,
                backoff_base=0.1,
                backoff_max=0.2,
                ws_failures_before_rest=2,
            ),
            ws_connect=connector,
            http_fetch=fetch,
        )
        owner = asyncio.create_task(feed.run())
        try:
            kind, _, release = await bounded(clock.actions.get())
            assert kind == "sleep"
            assert len(sockets) == 1 and sockets[0].closed
            assert reasons == ["valid_snapshot_timeout"]
            assert not books and feed.book is None and not requests
            assert feed.lifecycle == "resynchronizing"
            release.set()

            kind, _, _ = await bounded(clock.actions.get())
            assert kind == "sleep"
            assert reasons == ["valid_snapshot_timeout", "valid_snapshot_timeout"]
            assert len(sockets) == 2 and all(socket.closed for socket in sockets)
            assert all(socket.sent for socket in sockets)
            assert len(requests) == 1 and len(books) == 1
            assert requests[0] == adapter.snapshot_request(instrument, key)
            recovered = books[0]
            assert recovered.quality.integrity == "valid"
            assert recovered.quality.source_mode == "rest_snapshot"
            assert recovered.quality.lifecycle == feed.lifecycle == "degraded"
            assert eligibility(recovered, instrument).eligible
            assert not recovered.quality.capabilities.exact_event_flow
            assert (
                sum(
                    isinstance(event.payload, Control)
                    and event.payload.kind == "heartbeat"
                    for event in events
                )
                == 2
            )
            assert all(
                event.source_mode == "rest_snapshot"
                for event in events
                if isinstance(event.payload, BookUpdate)
            )
        finally:
            await stop_owner(owner, feed)
        assert feed.lifecycle == "disconnected"
        assert all(socket.closed for socket in sockets)

    asyncio.run(scenario())


@pytest.mark.parametrize("fallback_threshold", [1, 2])
def test_owner_cancellation_during_http_cooldown_prevents_fallback_request(
    monkeypatch, fallback_threshold
):
    async def scenario():
        clock = ControlledTime(monkeypatch)
        adapter = KuCoinAdapter()
        instrument, key = native_feed_identity(adapter)
        requests, books, events = [], [], []

        async def fetch(request):
            requests.append(request)
            raise PublicTransportError("rate_limited", status=429, retry_after=90)

        feed = FeedSupervisor(
            adapter,
            instrument,
            key,
            callbacks=FeedCallbacks(book=books.append, events=events.extend),
            config=SupervisorConfig(
                backoff_base=0.1,
                backoff_max=0.2,
                ws_failures_before_rest=fallback_threshold,
            ),
            http_fetch=fetch,
        )
        baseline = asyncio.all_tasks()
        owner = asyncio.create_task(feed.run())
        try:
            for attempt in range(fallback_threshold):
                kind, delay, release = await bounded(clock.actions.get())
                assert kind == "sleep" and delay >= 90
                assert len(requests) == attempt + 1
                assert not owner.done() and not books
                assert feed.lifecycle not in ("live", "degraded")
                if attempt + 1 < fallback_threshold:
                    release.set()

            # The final sleep is the cooldown immediately before REST fallback.
            # Cancellation must join it without waiting for virtual time to pass.
            stopped_at = clock.now
            await stop_owner(owner, feed)
            assert owner.cancelled()
            assert clock.now == stopped_at
            assert len(requests) == fallback_threshold
            assert feed.lifecycle == "disconnected"
            assert not books and feed.book is None
            assert not any(isinstance(event.payload, BookUpdate) for event in events)
            assert any(
                isinstance(event.payload, Gap)
                and "rate_limited" in event.payload.reason_code
                for event in events
            )

            # Releasing the external timer after the join cannot revive intake.
            release.set()
            clock.now += 90
            await asyncio.sleep(0)
            assert len(requests) == fallback_threshold
            assert not books and feed.lifecycle == "disconnected"
            assert asyncio.all_tasks() <= baseline
        finally:
            if not owner.done():
                await stop_owner(owner, feed)

    asyncio.run(scenario())
