"""Synthetic offline supervision/lease faults; no external sockets or credentials."""

import asyncio
from dataclasses import replace
from decimal import Decimal
import time

import httpx
import pytest

from src.workstation.adapter_catalog import AdapterCatalog, VenueStatus
from src.workstation.adapters.base import (
    DecodeError,
    DecodedEvent,
    DecodeResult,
    TransportRequest,
)
from src.workstation.book import BookReducer
from src.workstation.events import (
    BookUpdate,
    Control,
    Gap,
    LevelChange,
    SubscriptionKey,
    Trade,
    synthetic_envelope,
)
from src.workstation.ingestion import (
    FeedCallbacks,
    FeedFault,
    FeedSupervisor,
    ReceiveBoundary,
    SinkOverflow,
    SupervisorConfig,
)
from src.workstation.instruments import MetadataSnapshot, synthetic_instrument
from src.workstation.quality import Capabilities
from src.workstation.subscriptions import PresentationQueue, SubscriptionRegistry
from src.workstation.transport import (
    PublicTransportError,
    fetch_public,
    retry_after_seconds,
)


class FixtureAdapter:
    def __init__(self, *, ws=True, snapshot=True):
        self.ws, self.snapshot = ws, snapshot

    def capabilities(self, instrument, key):
        return Capabilities(snapshot=True, trades=True)

    def new_reducer(self, instrument, key):
        return BookReducer(
            instrument, key, capabilities=self.capabilities(instrument, key)
        )

    def ws_endpoint(self, instrument, key):
        return "wss://fixture.example/ws" if self.ws else None

    def snapshot_request(self, instrument, key):
        return (
            TransportRequest("GET", "https://fixture.example/snapshot")
            if self.snapshot
            else None
        )

    def subscription_requests(self, instrument, key):
        return (
            TransportRequest(
                "WS", self.ws_endpoint(instrument, key), b'{"subscribe":true}'
            ),
        )

    def decode(self, frame, instrument, key):
        if frame.payload == b"bad":
            return DecodeResult(
                error=DecodeError(
                    "bad_json", frame.payload_sha256, "synthetic malformed payload"
                )
            )
        if frame.payload == b"heartbeat":
            return DecodeResult(controls=(Control("heartbeat"),))
        if frame.payload == b"ack":
            return DecodeResult(controls=(Control("ack"),))
        if frame.payload == b"reject":
            return DecodeResult(controls=(Control("rejection", "unsupported_symbol"),))
        if frame.payload.startswith(b"trade"):
            return DecodeResult(
                events=(
                    DecodedEvent(
                        Trade(
                            "one", frame.payload.decode(), Decimal("100"), Decimal("1")
                        )
                    ),
                )
            )
        update = BookUpdate(
            "snapshot",
            (
                LevelChange("bid", Decimal("99"), Decimal("1")),
                LevelChange("ask", Decimal("101"), Decimal("1")),
            ),
        )
        return DecodeResult(events=(DecodedEvent(update),))


class FixtureSocket:
    def __init__(self, messages=(), *, repeat=None, fail_open=False):
        self.messages = list(messages)
        self.repeat = repeat
        self.fail_open = fail_open
        self.sent = []
        self.closed = False
        self.opened = False

    async def __aenter__(self):
        if self.fail_open:
            raise OSError("synthetic connection failure")
        self.opened = True
        return self

    async def __aexit__(self, *args):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(0.001)
        if self.messages:
            return self.messages.pop(0)
        if self.repeat is not None:
            return self.repeat
        await asyncio.Event().wait()
        raise StopAsyncIteration

    async def send(self, body):
        self.sent.append(body)


def supervisor(
    *,
    adapter=None,
    callbacks=None,
    config=None,
    ws_connect=None,
    http_fetch=None,
    channel="book",
):
    instrument = synthetic_instrument()
    key = SubscriptionKey.for_instrument(instrument, channel=channel)
    options = {}
    if ws_connect is not None:
        options["ws_connect"] = ws_connect
    if http_fetch is not None:
        options["http_fetch"] = http_fetch
    return FeedSupervisor(
        adapter or FixtureAdapter(),
        instrument,
        key,
        callbacks=callbacks,
        config=config or SupervisorConfig(),
        **options,
    )


def fast_config(**overrides):
    return SupervisorConfig(
        **dict(
            dict(
                ack_timeout=0.03,
                snapshot_timeout=0.05,
                silence_timeout=0.03,
                valid_data_timeout=0.04,
                poll_interval=0.005,
                ws_recovery_interval=0.02,
                backoff_base=0.001,
                backoff_max=0.005,
                circuit_cooldown=0.01,
            ),
            **overrides,
        )
    )


def test_shared_receive_boundary_assigns_global_ordinals():
    boundary = ReceiveBoundary()
    a = boundary.frame("a", "ca", 1, "ws", "endpoint", b"one")
    b = boundary.frame("b", "cb", 1, "rest", "endpoint", b"two")
    assert (a.ingest_ordinal, b.ingest_ordinal) == (1, 2)
    assert a.clock_id == b.clock_id and a.session_id == b.session_id
    assert b.receive_mono_ns >= a.receive_mono_ns


@pytest.mark.parametrize("bound", ["count", "bytes"])
def test_required_queue_overflow_is_out_of_band_and_raw_preserved(bound):
    async def scenario():
        raw, events, books = [], [], []
        config = SupervisorConfig(
            queue_count=1 if bound == "count" else 10,
            queue_bytes=3 if bound == "bytes" else 100,
        )
        feed = supervisor(
            callbacks=FeedCallbacks(
                raw=raw.append, events=events.extend, book=books.append
            ),
            config=config,
        )
        feed._start_epoch(False)
        feed._receive(b"ok", "ws", "endpoint", feed.epoch)
        feed._receive(b"ok", "ws", "endpoint", feed.epoch)
        assert len(raw) == 2 and feed.queue.qsize() == 1
        assert feed._fault.is_set()
        with pytest.raises(FeedFault, match="required_queue_overflow"):
            await feed._consume(time.monotonic(), require_ack=False)
        feed._gap(feed._fault_reason)
        assert isinstance(events[-1].payload, Gap)
        assert events[-1].ingest_ordinal > raw[-1].ingest_ordinal
        assert feed.lifecycle == "resynchronizing"

    asyncio.run(scenario())


def test_capture_overflow_marks_capture_incomplete():
    async def scenario():
        def full(frame):
            raise SinkOverflow("recorder queue full")

        feed = supervisor(callbacks=FeedCallbacks(raw=full))
        feed._start_epoch(False)
        feed._receive(b"ok", "ws", "endpoint", feed.epoch)
        assert not feed.capture_complete
        assert feed._fault_reason == "capture_overflow"
        assert feed.queue.empty()

    asyncio.run(scenario())


def test_heartbeat_only_and_ack_only_do_not_establish_valid_book():
    async def scenario(payload):
        socket = FixtureSocket([b"ack"], repeat=payload)
        feed = supervisor(
            adapter=FixtureAdapter(snapshot=False),
            config=fast_config(),
            ws_connect=lambda *a, **kw: socket,
        )
        with pytest.raises(FeedFault, match="valid_snapshot_timeout"):
            await feed._ws_session()
        assert feed.book is None
        assert socket.closed

    asyncio.run(scenario(b"heartbeat"))
    asyncio.run(scenario(b"ack"))


def test_silence_and_stalled_data_are_distinct():
    async def scenario(repeat, reason):
        socket = FixtureSocket([b"ok"], repeat=repeat)
        feed = supervisor(config=fast_config(), ws_connect=lambda *a, **kw: socket)
        with pytest.raises(FeedFault, match=reason):
            await feed._ws_session()
        assert feed.book is not None

    asyncio.run(scenario(None, "transport_silence"))
    asyncio.run(scenario(b"heartbeat", "valid_data_stalled"))


@pytest.mark.parametrize(
    "payload,reason",
    [(b"bad", "decode:bad_json"), (b"reject", "subscription_rejected")],
)
def test_decode_and_subscription_failures_trigger_recovery(payload, reason):
    async def scenario():
        socket = FixtureSocket([payload])
        feed = supervisor(config=fast_config(), ws_connect=lambda *a, **kw: socket)
        with pytest.raises(FeedFault, match=reason):
            await feed._ws_session()
        assert socket.closed

    asyncio.run(scenario())


def test_trade_dedup_survives_epoch_change_raw_keeps_duplicates():
    async def scenario():
        raw, events = [], []
        feed = supervisor(
            channel="trades",
            callbacks=FeedCallbacks(raw=raw.append, events=events.extend),
            config=SupervisorConfig(dedup_capacity=2),
        )
        for payload in (b"trade1", b"trade1", b"trade2", b"trade3", b"trade1"):
            feed._start_epoch(False)
            feed._receive(payload, "ws", "endpoint", feed.epoch)
            frame = feed.queue.get_nowait()
            feed.queued_bytes -= len(payload)
            await feed._process(frame)
        assert len(raw) == 5
        assert sum(isinstance(e.payload, Trade) for e in events) == 4
        assert feed.duplicate_trades == 1
        assert any(
            isinstance(e.payload, Control)
            and e.payload.reason_code == "duplicate_trade"
            for e in events
        )
        assert len(feed._dedup) == 2

    asyncio.run(scenario())


def test_obsolete_epoch_cannot_write_new_book():
    async def scenario():
        feed = supervisor()
        feed._start_epoch(False)
        old = feed.boundary.frame(
            feed.stream_id, feed.connection_id, feed.epoch, "ws", "endpoint", b"ok"
        )
        feed._start_epoch(False)
        await feed._process(old)
        assert feed.book is None and feed.reason == "obsolete_epoch"
        feed._receive(b"ok", "ws", "endpoint", old.connection_epoch)
        assert feed.queue.empty()

    asyncio.run(scenario())


def test_rest_fallback_recovers_ws_and_resegments():
    async def scenario():
        books, events, sockets = [], [], []
        recovered = asyncio.Event()

        def connector(*args, **kwargs):
            socket = FixtureSocket(fail_open=not sockets, repeat=b"ok")
            sockets.append(socket)
            return socket

        async def fetch(request):
            return b"ok"

        def book_callback(book):
            books.append(book)
            if (
                book.quality.integrity == "valid"
                and book.quality.source_mode == "ws_snapshot"
            ):
                recovered.set()

        feed = supervisor(
            callbacks=FeedCallbacks(book=book_callback, events=events.extend),
            config=fast_config(ws_failures_before_rest=1),
            ws_connect=connector,
            http_fetch=fetch,
        )
        task = asyncio.create_task(feed.run())
        try:
            await asyncio.wait_for(recovered.wait(), 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        rest = next(
            book for book in books if book.quality.source_mode == "rest_snapshot"
        )
        ws = next(
            book
            for book in books
            if book.quality.source_mode == "ws_snapshot"
            and book.quality.integrity == "valid"
        )
        assert rest.quality.lifecycle == "degraded"
        assert not rest.quality.capabilities.exact_event_flow
        assert (
            ws.segment_id != rest.segment_id
            and ws.connection_epoch > rest.connection_epoch
        )
        assert any(
            isinstance(e.payload, Gap) and e.payload.reason_code == "ws_recovery_probe"
            for e in events
        )
        assert all(socket.closed for socket in sockets[1:])
        assert feed.lifecycle == "disconnected"

    asyncio.run(scenario())


def test_rest_only_trade_unavailability_is_explicit():
    async def scenario():
        feed = supervisor(
            adapter=FixtureAdapter(ws=False, snapshot=False), channel="trades"
        )
        with pytest.raises(FeedFault, match="rest_channel_unsupported"):
            await feed._rest_session()

    asyncio.run(scenario())


def make_catalog():
    catalog = AdapterCatalog()
    instrument = synthetic_instrument()
    catalog.instruments.apply_metadata(
        MetadataSnapshot("fixture", 1, "synthetic", (instrument,))
    )
    catalog.adapters[instrument.venue] = FixtureAdapter()
    return catalog, instrument


class WaitingSupervisor(FeedSupervisor):
    created = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stopped = asyncio.Event()
        self.started = asyncio.Event()
        self.created.append(self)

    async def run(self):
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0.001)
            self.stopped.set()


def test_registry_shares_owner_and_headless_lease_survives_tab(tmp_path):
    async def scenario():
        catalog, instrument = make_catalog()
        registry = SubscriptionRegistry(
            catalog,
            supervisor_factory=WaitingSupervisor,
            lock_path=tmp_path / "collector",
        )
        key = SubscriptionKey.for_instrument(instrument)
        try:
            first, second, recorder = await asyncio.gather(
                registry.acquire("tab-a", key),
                registry.acquire("tab-b", key),
                registry.acquire("recorder", key),
            )
            assert first.stream_id == second.stream_id == recorder.stream_id
            assert registry.snapshot()[0].reference_count == 3
            owner = registry._entries[key].supervisor
            await owner.started.wait()
            await registry.release_client("tab-a")
            await registry.release_client("tab-b")
            assert registry.snapshot()[0].reference_count == 1
            assert not owner.stopped.is_set()
            await registry.release(recorder.lease_id)
            assert owner.stopped.is_set()
            assert not registry.snapshot()
        finally:
            await registry.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "venue,aggregation,depth",
    [
        ("binance-global", (), 5000),
        ("binance-us", (), 20),
        ("kraken", (), 25),
        ("bybit", (), 50),
        ("kucoin", (), 20),
        ("hyperliquid", (), 20),
        ("hyperliquid", (("fast", "true"),), 5),
    ],
)
def test_registry_default_depth_and_explicit_depth_share_owner(
    tmp_path, venue, aggregation, depth
):
    async def scenario():
        catalog = AdapterCatalog(include_binance_us=True)
        instrument = synthetic_instrument(venue=venue)
        catalog.instruments.apply_metadata(
            MetadataSnapshot("fixture", 1, "synthetic", (instrument,))
        )
        catalog.adapters[venue] = FixtureAdapter()
        implicit = SubscriptionKey.for_instrument(instrument, aggregation=aggregation)
        explicit = replace(implicit, depth=depth)
        async with SubscriptionRegistry(
            catalog,
            supervisor_factory=WaitingSupervisor,
            lock_path=tmp_path / "collector",
        ) as registry:
            first = await registry.acquire("tab", implicit)
            assert first.key == explicit
            assert await registry.acquire("tab", explicit) == first
            (recorder,) = await registry.replace(
                "recorder", frozenset({implicit, explicit}), 1
            )
            assert recorder.stream_id == first.stream_id
            assert registry.snapshot()[0].reference_count == 2
            assert await registry.replace("recorder", frozenset({implicit}), 1) == (
                recorder,
            )
            await registry.release(first.lease_id)
            assert registry.snapshot()[0].reference_count == 1
            # Explicit reconstruction options are not silently rewritten.
            different = replace(explicit, depth=depth + 1)
            other = await registry.acquire("other", different)
            assert other.key == different and other.stream_id != recorder.stream_id
            aggregated = replace(explicit, aggregation=(("nSigFigs", "5"),))
            third = await registry.acquire("aggregated", aggregated)
            assert third.key == aggregated and third.stream_id != recorder.stream_id

    asyncio.run(scenario())


def test_registry_replacement_generations_and_rollback(tmp_path):
    async def scenario():
        catalog, instrument = make_catalog()
        registry = SubscriptionRegistry(
            catalog,
            supervisor_factory=WaitingSupervisor,
            lock_path=tmp_path / "collector",
        )
        key = SubscriptionKey.for_instrument(instrument)
        trades = SubscriptionKey.for_instrument(instrument, channel="trades")
        try:
            (lease,) = await registry.replace("tab", frozenset({key}), 1)
            await registry.acquire("other-tab", key)
            (replaced,) = await registry.replace("tab", frozenset({trades}), 2)
            assert replaced.key == trades
            assert len(registry.snapshot()) == 2
            with pytest.raises(ValueError, match="obsolete"):
                await registry.replace("tab", frozenset({key}), 1)
            bad = replace(key, instrument_id="missing")
            with pytest.raises(LookupError):
                await registry.replace("tab", frozenset({bad, key}), 3)
            assert {
                item.key
                for item in registry._leases.values()
                if item.client_id == "tab"
            } == {trades}
            assert registry._generations["tab"] == 2
            assert lease.lease_id not in registry._leases
        finally:
            await registry.close()
        assert not registry.snapshot()

    asyncio.run(scenario())


def test_acquire_waits_for_last_owner_to_join(tmp_path):
    async def scenario():
        catalog, instrument = make_catalog()
        registry = SubscriptionRegistry(
            catalog,
            supervisor_factory=WaitingSupervisor,
            lock_path=tmp_path / "collector",
        )
        key = SubscriptionKey.for_instrument(instrument)
        try:
            lease = await registry.acquire("first", key)
            owner = registry._entries[key].supervisor
            await owner.started.wait()
            releasing = asyncio.create_task(registry.release(lease.lease_id))
            await asyncio.sleep(0)
            replacement = await registry.acquire("second", key)
            await releasing
            assert owner.stopped.is_set()
            assert replacement.stream_id != lease.stream_id
            assert registry.snapshot()[0].reference_count == 1
        finally:
            await registry.close()

    asyncio.run(scenario())


def test_registry_singleton_lock_and_close_race(tmp_path):
    async def scenario():
        catalog, instrument = make_catalog()
        key = SubscriptionKey.for_instrument(instrument)
        first = SubscriptionRegistry(
            catalog,
            supervisor_factory=WaitingSupervisor,
            lock_path=tmp_path / "collector",
        )
        second = SubscriptionRegistry(
            catalog,
            supervisor_factory=WaitingSupervisor,
            lock_path=tmp_path / "collector",
        )
        try:
            await first.acquire("first", key)
            with pytest.raises(RuntimeError, match="singleton"):
                await second.acquire("second", key)
            await asyncio.gather(first.close(), first.close())
            await second.acquire("second", key)
            await asyncio.gather(second.release_client("second"), second.close())
            assert not first.snapshot() and not second.snapshot()
            with pytest.raises(RuntimeError, match="closed"):
                await first.acquire("late", key)
        finally:
            await first.close()
            await second.close()

    asyncio.run(scenario())


def test_multiworker_configuration_is_rejected():
    with pytest.raises(RuntimeError, match="one backend worker"):
        SubscriptionRegistry(worker_count=2)


def fixture_book():
    instrument = synthetic_instrument()
    key = SubscriptionKey.for_instrument(instrument)
    reducer = BookReducer(instrument, key, segment_id="synthetic-segment")
    event = synthetic_envelope(
        instrument,
        BookUpdate(
            "snapshot",
            (
                LevelChange("bid", Decimal("99"), Decimal("1")),
                LevelChange("ask", Decimal("101"), Decimal("1")),
            ),
        ),
        key=key,
    )
    return reducer.apply((event,)).book


def test_slow_presentation_coalesces_without_changing_book():
    async def scenario():
        queue = PresentationQueue(1)
        book = fixture_book()
        for revision in range(100):
            queue.put(replace(book, revision=revision))
        assert len(queue) == 1
        presentation = await queue.get()
        assert presentation.book.revision == 99
        assert presentation.coalesced == 99 and presentation.resync_required
        queue.close()
        with pytest.raises(EOFError):
            await queue.get()

    asyncio.run(scenario())


def test_empty_presentation_queue_closes_on_disconnect(tmp_path):
    async def scenario():
        registry = SubscriptionRegistry(lock_path=tmp_path / "collector")
        queue = registry.presentation_queue("tab")
        await registry.release_client("tab")
        assert queue.closed
        await registry.close()

    asyncio.run(scenario())


def test_catalog_preference_requires_every_dependency_and_distinct_us():
    catalog = AdapterCatalog(include_binance_us=True)
    full = tuple(
        (name, "available") for name in ("metadata", "snapshot", "trades", "ws")
    )
    catalog.statuses["binance-global"] = VenueStatus(
        "binance-global", dependencies=full[:-1]
    )
    assert catalog.preferred_binance is None
    catalog.statuses["binance-us"] = VenueStatus("binance-us", dependencies=full)
    assert catalog.preferred_binance == "binance-us"
    catalog.statuses["binance-global"] = VenueStatus(
        "binance-global", dependencies=full
    )
    assert catalog.preferred_binance == "binance-global"
    with pytest.raises(ValueError, match="opt-in"):
        AdapterCatalog().adapter("binance-us")


def test_retry_after_parsing():
    assert retry_after_seconds("2") == 2
    assert retry_after_seconds("Thu, 01 Jan 1970 00:00:03 GMT", now=1) == 2
    assert retry_after_seconds("invalid") == 0
    assert retry_after_seconds("NaN") == 0
    assert retry_after_seconds("-1") == 0


@pytest.fixture
def public_transport_clock(monkeypatch):
    from types import SimpleNamespace

    import src.workstation.transport as transport

    clock = SimpleNamespace(now=1000.0, waits=[], before_sleep=None)

    async def sleep(delay):
        clock.waits.append(delay)
        if clock.before_sleep is not None:
            await clock.before_sleep(delay)
        clock.now += delay
        await asyncio.sleep(0)

    # Replace only this transport's external clock/wait boundaries, not the
    # event loop's clock or scheduling primitives used by the test itself.
    monkeypatch.setattr(transport, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(
        transport,
        "asyncio",
        SimpleNamespace(sleep=sleep, get_running_loop=asyncio.get_running_loop),
    )
    return clock


@pytest.mark.parametrize("attempts", [1, 3])
def test_public_transport_read_timeout_exhausts_configured_attempts(
    public_transport_clock, attempts
):
    async def scenario():
        failures = []

        def handler(request):
            error = httpx.ReadTimeout("read stalled", request=request)
            failures.append(error)
            raise error

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PublicTransportError) as caught:
                await fetch_public(
                    TransportRequest("GET", "https://timeout.example/public"),
                    client=client,
                    attempts=attempts,
                )
            assert not client.is_closed
        assert len(failures) == attempts
        assert caught.value.reason == "network_error"
        assert caught.value.status is None
        assert caught.value.retry_after == 0
        assert caught.value.__cause__ is failures[-1]

    asyncio.run(scenario())


def test_public_transport_proxy_error_recovers_exact_bytes(public_transport_clock):
    async def scenario():
        calls = []
        raw = b' { "price":1.2300,"id":999999999999999999 }\n'

        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                raise httpx.ProxyError("proxy unavailable", request=request)
            return httpx.Response(200, content=raw)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await fetch_public(
                TransportRequest("GET", "https://proxy.example/public"),
                client=client,
                attempts=2,
            )
            assert not client.is_closed
        assert result == raw
        assert len(calls) == 2

    asyncio.run(scenario())


def test_public_transport_repeated_429_preserves_final_cooldown(
    public_transport_clock,
):
    async def scenario():
        calls = []
        cooldowns = (2, 5, 7)

        def handler(request):
            calls.append(public_transport_clock.now)
            return httpx.Response(
                429, headers={"Retry-After": str(cooldowns[len(calls) - 1])}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PublicTransportError) as caught:
                await fetch_public(
                    TransportRequest("GET", "https://exhausted-rate.example/public"),
                    client=client,
                    attempts=3,
                )
        assert calls == [1000, 1002, 1007]
        assert caught.value.reason == "rate_limited"
        assert caught.value.status == 429
        assert caught.value.retry_after == 7
        assert [delay for delay in public_transport_clock.waits if delay > 0] == [2, 5]

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [429, 503])
@pytest.mark.parametrize("retry_after", [2, 120])
def test_public_transport_positive_retry_after_waits_without_clamping(
    public_transport_clock, status, retry_after
):
    async def scenario():
        waiting, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def hold_wait(delay):
            if delay > 0:
                waiting.set()
                await release.wait()

        public_transport_clock.before_sleep = hold_wait

        def handler(request):
            calls.append(public_transport_clock.now)
            if len(calls) == 1:
                return httpx.Response(status, headers={"Retry-After": str(retry_after)})
            return httpx.Response(200, content=b"recovered")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            task = asyncio.create_task(
                fetch_public(
                    TransportRequest("GET", "https://cooldown.example/public"),
                    client=client,
                    attempts=2,
                )
            )
            try:
                await asyncio.wait_for(waiting.wait(), 1)
                assert calls == [1000]
                assert public_transport_clock.waits[-1] == retry_after
                assert not task.done()
                release.set()
                assert await asyncio.wait_for(task, 1) == b"recovered"
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert calls == [1000, 1000 + retry_after]

    asyncio.run(scenario())


def test_public_transport_cancellation_during_retry_wait_stops_requests(
    public_transport_clock,
):
    async def scenario():
        waiting, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def hold_wait(delay):
            if delay > 0:
                waiting.set()
                await release.wait()

        public_transport_clock.before_sleep = hold_wait

        def handler(request):
            calls.append(request)
            return httpx.Response(429, headers={"Retry-After": "30"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            task = asyncio.create_task(
                fetch_public(
                    TransportRequest("GET", "https://cancel-retry.example/public"),
                    client=client,
                    attempts=3,
                )
            )
            try:
                await asyncio.wait_for(waiting.wait(), 1)
                assert len(calls) == 1
                assert public_transport_clock.waits[-1] == 30
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                release.set()
                await asyncio.sleep(0)
                assert len(calls) == 1
                assert not client.is_closed
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["success", "error", "cancellation"])
def test_public_transport_owned_client_closes_on_every_exit(
    monkeypatch, public_transport_clock, outcome
):
    async def scenario():
        entered, cancelled = asyncio.Event(), asyncio.Event()
        clients, calls, closes = [], [], []
        raw = b'{"price":"1.2300"}'
        real_client = httpx.AsyncClient

        async def handler(request):
            calls.append(request)
            entered.set()
            if outcome == "error":
                raise httpx.ReadTimeout("read stalled", request=request)
            if outcome == "cancellation":
                try:
                    await asyncio.Future()
                finally:
                    cancelled.set()
            return httpx.Response(200, content=raw)

        class ClosingTransport(httpx.MockTransport):
            async def aclose(self):
                closes.append(self)
                await super().aclose()

        mock_transport = ClosingTransport(handler)

        def make_client(**kwargs):
            client = real_client(transport=mock_transport, **kwargs)
            clients.append(client)
            return client

        monkeypatch.setattr(httpx, "AsyncClient", make_client)
        task = asyncio.create_task(
            fetch_public(
                TransportRequest("GET", "https://owned-client.example/public"),
                attempts=1,
            )
        )
        try:
            if outcome == "cancellation":
                await asyncio.wait_for(entered.wait(), 1)
                assert not clients[0].is_closed
                assert closes == []
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert cancelled.is_set()
            elif outcome == "error":
                with pytest.raises(PublicTransportError) as caught:
                    await asyncio.wait_for(task, 1)
                assert caught.value.reason == "network_error"
            else:
                assert await asyncio.wait_for(task, 1) == raw
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert len(calls) == 1
        assert len(clients) == 1
        assert clients[0].is_closed
        assert closes == [mock_transport]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status,reason", [(403, "blocked"), (451, "blocked"), (404, "unsupported")]
)
def test_public_transport_classifies_without_retry(status, reason):
    async def scenario():
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(status, content=b"not a book")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PublicTransportError, match=reason):
                await fetch_public(
                    TransportRequest("GET", "https://blocked.example/public"),
                    client=client,
                )
        assert len(calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [429, 503])
def test_public_transport_retries_and_preserves_exact_body(status):
    async def scenario():
        calls = []
        raw = b'{"price":"1.2300","id":999999999999999999}'

        def handler(request):
            calls.append(request)
            return (
                httpx.Response(status, headers={"Retry-After": "0"})
                if len(calls) == 1
                else httpx.Response(200, content=raw)
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await fetch_public(
                TransportRequest("GET", "https://retry.example/public"), client=client
            )
        assert result == raw and len(calls) == 2

    asyncio.run(scenario())


def test_public_transport_long_retry_after_opens_circuit():
    async def scenario():
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(429, headers={"Retry-After": "3600"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            request = TransportRequest("GET", "https://rate.example/public")
            with pytest.raises(PublicTransportError, match="rate_limited"):
                await fetch_public(request, client=client)
            with pytest.raises(PublicTransportError, match="circuit_open"):
                await fetch_public(request, client=client)
        assert len(calls) == 1

    asyncio.run(scenario())


def test_binance_ws_opens_and_buffers_before_snapshot_in_receive_order():
    from src.workstation.adapters.binance import BinanceGlobalAdapter

    async def scenario():
        instrument = synthetic_instrument(
            venue="binance-global", api_family="spot-v3", native_symbol="BTCUSDT"
        )
        key = SubscriptionKey.for_instrument(instrument)
        delta = (
            b'{"stream":"btcusdt@depth@100ms","data":'
            b'{"e":"depthUpdate","s":"BTCUSDT","E":1,"U":100,"u":101,'
            b'"b":[["99","2"]],"a":[]}}'
        )
        snapshot = b'{"lastUpdateId":100,"bids":[["99","1"]],"asks":[["101","1"]]}'
        socket = FixtureSocket([delta])
        buffered, valid = asyncio.Event(), asyncio.Event()
        raw, checkpoints = [], []
        feed = None

        def events_callback(events):
            if any(
                isinstance(event.payload, BookUpdate) and event.payload.kind == "delta"
                for event in events
            ):
                checkpoints.append(feed.reducer.checkpoint())
                buffered.set()

        def books_callback(book):
            if book.quality.integrity == "valid":
                valid.set()

        def connect(endpoint, **kwargs):
            assert endpoint == "wss://data-stream.binance.vision:443/stream"
            return socket

        async def fetch(request):
            assert socket.opened and not socket.closed
            assert socket.sent == [
                '{"method":"SUBSCRIBE","params":["btcusdt@depth@100ms"],"id":1}'
            ]
            assert request == TransportRequest(
                "GET",
                "https://data-api.binance.vision/api/v3/depth?symbol=BTCUSDT&limit=5000",
            )
            await buffered.wait()
            return snapshot

        feed = FeedSupervisor(
            BinanceGlobalAdapter(),
            instrument,
            key,
            callbacks=FeedCallbacks(
                raw=raw.append, events=events_callback, book=books_callback
            ),
            ws_connect=connect,
            http_fetch=fetch,
        )
        task = asyncio.create_task(feed._ws_session())
        valid_task = asyncio.create_task(valid.wait())
        try:
            done, _ = await asyncio.wait(
                (task, valid_task), timeout=2, return_when=asyncio.FIRST_COMPLETED
            )
            if task in done:
                await task  # Surface protocol/transport failures instead of timing out.
            assert valid_task in done
            assert feed.book.bids[0].quantity == Decimal("2")
            assert [frame.transport for frame in raw] == ["ws", "rest"]
            assert raw[0].payload == delta and raw[1].payload == snapshot
            assert raw[0].endpoint_id == "wss://data-stream.binance.vision:443/stream"
            assert raw[1].endpoint_id == (
                "https://data-api.binance.vision/api/v3/depth?symbol=BTCUSDT&limit=5000"
            )
            assert raw[0].ingest_ordinal < raw[1].ingest_ordinal
            assert feed.book.asof.ingest_ordinal == raw[1].ingest_ordinal
            restored = feed.adapter.new_reducer(instrument, key)
            restored.restore(checkpoints[0])
            assert len(restored.buffered_events) == 1
            assert restored.buffered_events[0].ingest_ordinal == raw[0].ingest_ordinal
        finally:
            task.cancel()
            valid_task.cancel()
            await asyncio.gather(task, valid_task, return_exceptions=True)
        assert socket.closed

    asyncio.run(scenario())


def test_cancellation_joins_inflight_decode_before_owner_stops():
    import threading

    async def scenario():
        entered, release = threading.Event(), threading.Event()

        class SlowAdapter(FixtureAdapter):
            def decode(self, frame, instrument, key):
                entered.set()
                release.wait(2)
                return super().decode(frame, instrument, key)

        feed = supervisor(adapter=SlowAdapter())
        feed._start_epoch(False)
        frame = feed.boundary.frame(
            feed.stream_id, feed.connection_id, feed.epoch, "ws", "endpoint", b"ok"
        )
        task = asyncio.create_task(feed._process(frame))
        await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        (result,) = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result, asyncio.CancelledError)
        assert feed.book is None

    asyncio.run(scenario())


def test_analytical_sink_failure_is_not_silently_coalesced():
    async def scenario():
        gaps = []

        def sink(events):
            if any(isinstance(event.payload, Trade) for event in events):
                raise SinkOverflow("analytical queue full")
            gaps.extend(event for event in events if isinstance(event.payload, Gap))

        feed = supervisor(channel="trades", callbacks=FeedCallbacks(events=sink))
        feed._start_epoch(False)
        frame = feed.boundary.frame(
            feed.stream_id, feed.connection_id, feed.epoch, "ws", "endpoint", b"trade1"
        )
        with pytest.raises(FeedFault, match="event_sink_failed"):
            await feed._process(frame)
        assert not feed.capture_complete
        assert any(
            gap.payload.continuity_lost and gap.payload.capture_lost for gap in gaps
        )
        feed._gap("analytical_queue_overflow")
        assert any(
            gap.payload.first_missing_ordinal == frame.ingest_ordinal
            and gap.payload.last_missing_ordinal == frame.ingest_ordinal
            for gap in gaps
        )

    asyncio.run(scenario())


def test_ws_recovery_deadline_interrupts_stalled_rest_request():
    async def scenario():
        cancelled = asyncio.Event()

        async def hanging_fetch(request):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        events = []
        feed = supervisor(
            config=fast_config(),
            http_fetch=hanging_fetch,
            callbacks=FeedCallbacks(events=events.extend),
        )
        await asyncio.wait_for(feed._rest_session(), 0.5)
        assert cancelled.is_set()
        assert any(
            isinstance(event.payload, Gap)
            and event.payload.reason_code == "ws_recovery_probe"
            for event in events
        )

    asyncio.run(scenario())


def test_public_transport_response_bound_and_retry_circuit(monkeypatch):
    import src.workstation.transport as transport

    async def scenario():
        monkeypatch.setattr(transport, "MAX_MARKET_DATA_BYTES", 4)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"12345")
            )
        ) as client:
            with pytest.raises(PublicTransportError, match="response_too_large"):
                await fetch_public(
                    TransportRequest("GET", "https://large.example/public"),
                    client=client,
                )
        calls = []

        def failing(request):
            calls.append(request)
            return httpx.Response(503)

        async with httpx.AsyncClient(transport=httpx.MockTransport(failing)) as client:
            request = TransportRequest("GET", "https://circuit.example/public")
            for attempt in range(6):
                with pytest.raises(
                    PublicTransportError,
                    match="circuit_open" if attempt == 5 else "server_error",
                ):
                    await fetch_public(request, client=client, attempts=1)
            with pytest.raises(PublicTransportError, match="circuit_open"):
                await fetch_public(request, client=client, attempts=1)
        assert len(calls) == 6

    asyncio.run(scenario())


def test_cancelled_registry_close_still_joins_owner(tmp_path):
    async def scenario():
        catalog, instrument = make_catalog()
        registry = SubscriptionRegistry(
            catalog,
            supervisor_factory=WaitingSupervisor,
            lock_path=tmp_path / "collector",
        )
        key = SubscriptionKey.for_instrument(instrument)
        await registry.acquire("tab", key)
        owner = registry._entries[key].supervisor
        await owner.started.wait()
        closing = asyncio.create_task(registry.close())
        await asyncio.sleep(0)
        closing.cancel()
        await asyncio.gather(closing, return_exceptions=True)
        assert owner.stopped.is_set()
        assert not registry.snapshot()
        await registry.close()

    asyncio.run(scenario())


def test_capture_incompleteness_survives_source_reset():
    feed = supervisor()
    feed.capture_complete = False
    feed._start_epoch(True)
    assert not feed.reducer.quality.capture_complete
    feed._start_epoch(False)
    assert not feed.reducer.quality.capture_complete


@pytest.mark.parametrize(
    "continuous,rest", [(True, False), (False, False), (True, True)]
)
def test_only_healthy_continuous_book_heartbeats_refresh_valid_data_deadline(
    monkeypatch, continuous, rest
):
    from types import SimpleNamespace

    import src.workstation.ingestion as ingestion

    clock = [1]
    monkeypatch.setattr(
        ingestion,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock[0],
            monotonic_ns=lambda: clock[0] * 1_000_000_000,
            time_ns=lambda: clock[0] * 1_000_000_000,
        ),
    )

    class ContinuousAdapter(FixtureAdapter):
        def capabilities(self, instrument, key):
            return Capabilities(
                snapshot=True, deltas=continuous, exact_event_flow=continuous
            )

    async def scenario():
        feed = supervisor(adapter=ContinuousAdapter())
        feed._start_epoch(rest)

        async def receive(payload):
            raw = feed.boundary.frame(
                feed.stream_id,
                feed.connection_id,
                feed.epoch,
                "rest" if rest else "ws",
                "fixture",
                payload,
            )
            await feed._process(raw)

        await receive(b"heartbeat")
        assert feed.book is None and not feed._valid
        clock[0] = 2
        await receive(b"snapshot")
        initial = feed.book
        assert initial.quality.integrity == "valid" and feed.last_valid == 2
        for now in (42, 82):
            clock[0] = now
            await receive(b"heartbeat")
            healthy = continuous and not rest
            assert feed.last_valid == (now if healthy else 2)
            assert feed.book.quality.last_healthy_observation_mono_ns == (
                (now if healthy else 2) * 1_000_000_000
            )
            assert (
                feed.book.quality.last_book_observation_mono_ns
                == initial.quality.last_book_observation_mono_ns
            )
            assert (
                feed.book.quality.last_book_change_mono_ns
                == initial.quality.last_book_change_mono_ns
            )
        last_valid = feed.last_valid
        feed._gap("synthetic_loss")
        clock[0] = 122
        await receive(b"heartbeat")
        assert feed.last_valid == last_valid
        assert feed.book.quality.integrity == "invalid"
        assert feed.lifecycle == "resynchronizing"

    asyncio.run(scenario())


@pytest.mark.parametrize("acknowledged", [True, False])
def test_binance_protocol_ping_records_only_observed_pong(acknowledged):
    from src.workstation.adapters.binance import BinanceGlobalAdapter

    async def scenario():
        instrument = synthetic_instrument(
            venue="binance-global", api_family="spot-v3", native_symbol="BTCUSDT"
        )
        raw = []
        ping_sent, received = asyncio.Event(), asyncio.Event()
        pong = asyncio.get_running_loop().create_future()

        class PingSocket:
            async def ping(self):
                ping_sent.set()
                return pong

        def captured(frame):
            raw.append(frame)
            received.set()

        feed = FeedSupervisor(
            BinanceGlobalAdapter(),
            instrument,
            SubscriptionKey.for_instrument(instrument),
            callbacks=FeedCallbacks(raw=captured),
            config=SupervisorConfig(heartbeat_interval=0.001, silence_timeout=0.05),
        )
        feed._start_epoch(False)
        task = asyncio.create_task(feed._heartbeat(PingSocket()))
        try:
            await asyncio.wait_for(ping_sent.wait(), 1)
            assert not raw and feed.queue.empty()
            if acknowledged:
                pong.set_result(0.001)
                await asyncio.wait_for(received.wait(), 1)
                assert len(raw) == 1
                assert raw[0].transport == "local" and raw[0].endpoint_id == "ws_pong"
                queued = feed.queue.get_nowait()
                assert queued is raw[0]
                events, reduction, _ = feed._decode_reduce(queued)
                assert events[0].payload == Control("heartbeat", "ws_pong")
                assert events[0].ingest_ordinal == raw[0].ingest_ordinal
                assert reduction.book is None and not feed.reducer.synchronized
            else:
                await asyncio.wait_for(task, 1)
                assert feed._fault.is_set()
                assert not raw and feed.queue.empty()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_hyperliquid_rest_clone_preserves_effective_depth_and_price_rules():
    from src.workstation.adapters.hyperliquid import HyperliquidAdapter

    async def scenario():
        adapter = HyperliquidAdapter()
        instrument = synthetic_instrument(
            venue="hyperliquid",
            api_family="info",
            native_symbol="BTC",
            market_type="perpetual",
            dex_namespace="default",
            tick_size="1",
        )
        key = SubscriptionKey.for_instrument(
            instrument, aggregation=(("nSigFigs", "5"),)
        )
        body = (
            b'{"coin":"BTC","time":1,"levels":'
            b'[[{"px":"99.9","sz":"1","n":1}],'
            b'[{"px":"101.1","sz":"1","n":1}]]}'
        )

        async def fetch(request):
            return body

        feed = FeedSupervisor(adapter, instrument, key, http_fetch=fetch)
        feed._start_epoch(True)
        request = adapter.snapshot_request(instrument, key)
        await feed._snapshot(request, feed.epoch)
        await feed._process(feed.queue.get_nowait())
        assert feed.book.key == key and key.depth is None
        assert feed.book.bids[0].price == Decimal("99.9")
        assert feed.book.quality.integrity == "valid"
        assert feed.book.quality.source_mode == "rest_snapshot"
        assert feed.book.quality.coverage.subscribed_depth == 20
        assert dict(feed.book.quality.coverage.source_aggregation) == {
            "fast": "false",
            "nSigFigs": "5",
        }
        assert feed.book.quality.coverage.expected_cadence_ns == 2_000_000_000
        assert not feed.book.quality.capabilities.exact_event_flow

    asyncio.run(scenario())


def test_coinbase_rest_snapshot_uses_same_reducer_in_supervisor_and_probe(monkeypatch):
    from src.workstation.adapters.coinbase import CoinbaseAdapter
    import src.workstation.transport as transport
    from tools.probe_feeds import rest_dependency

    async def scenario():
        adapter = CoinbaseAdapter()
        instrument = synthetic_instrument(
            venue="coinbase", api_family="advanced-trade", native_symbol="BTC-USD"
        )
        key = SubscriptionKey.for_instrument(instrument)
        body = (
            b'{"pricebook":{"product_id":"BTC-USD",'
            b'"bids":[{"price":"99","size":"1"}],'
            b'"asks":[{"price":"101","size":"1"}],'
            b'"time":"2026-10-31T10:00:00Z"}}'
        )

        async def fetch(request):
            return body

        monkeypatch.setattr(transport, "fetch_public", fetch)
        feed = FeedSupervisor(adapter, instrument, key, http_fetch=fetch)
        feed._start_epoch(True)
        await feed._snapshot(adapter.snapshot_request(instrument, key), feed.epoch)
        await feed._process(feed.queue.get_nowait())
        assert feed.book.quality.integrity == "valid"
        assert feed.book.quality.lifecycle == "degraded"
        assert feed.book.quality.source_mode == "rest_snapshot"
        assert feed.book.quality.capabilities.sequence_scope is None
        assert not feed.book.quality.capabilities.exact_event_flow
        assert not feed.book.quality.coverage.full_depth
        assert feed.book.quality.coverage.expected_cadence_ns == 2_000_000_000
        report = await rest_dependency(adapter, instrument, key, 1, ReceiveBoundary())
        assert report["status"] == "passed" and report["integrity"] == "valid"
        assert report["recovery"] is None
        assert report["coverage"]["expected_cadence_ns"] is None
        assert report["coverage"]["full_depth"] is False

    asyncio.run(scenario())


def test_supervisor_publishes_checked_checksum_and_preserves_rejected_source_event():
    import zlib

    from src.workstation.adapters.kraken import KrakenAdapter

    async def scenario():
        adapter = KrakenAdapter()
        instrument = synthetic_instrument(
            venue="kraken", api_family="spot-v2", native_symbol="BTC/USD"
        )
        key = SubscriptionKey.for_instrument(instrument, depth=25)
        # Kraken's top-ten CRC concatenates asks before bids, retaining decimal scale.
        checksum = zlib.crc32(b"101010009901000") & 0xFFFFFFFF
        template = (
            b'{"channel":"book","type":"snapshot","data":[{"symbol":"BTC/USD",'
            b'"bids":[{"price":"99.0","qty":"1.000"}],'
            b'"asks":[{"price":"101.0","qty":"1.000"}],"checksum":CHECKSUM}]}'
        )
        raw, normalized = [], []
        feed = FeedSupervisor(
            adapter,
            instrument,
            key,
            callbacks=FeedCallbacks(raw=raw.append, events=normalized.extend),
        )
        feed._start_epoch(False)
        body = template.replace(b"CHECKSUM", str(checksum).encode())
        feed._receive(body, "ws", adapter.websocket_url, feed.epoch)
        frame = feed.queue.get_nowait()
        decoded = adapter.decode(frame, instrument, key)
        assert decoded.events[0].checksum.result == "unchecked"
        await feed._process(frame)
        event = next(
            event for event in normalized if isinstance(event.payload, BookUpdate)
        )
        assert event.checksum.result == "match"
        assert event.checksum.calculated == event.checksum.supplied == str(checksum)
        assert event.ingest_ordinal == frame.ingest_ordinal
        assert raw[0].payload == body
        replay = adapter.new_reducer(instrument, key)
        replay.reset(feed.epoch, feed.segment_id)
        assert replay.apply((event,)).book == feed.book

        corrupt = template.replace(b"CHECKSUM", str(checksum ^ 1).encode())
        feed._receive(corrupt, "ws", adapter.websocket_url, feed.epoch)
        rejected = feed.queue.get_nowait()
        with pytest.raises(FeedFault, match="checksum_mismatch"):
            await feed._process(rejected)
        # Rejected source remains visible; its explicit gap follows, not a fake match.
        failed_event = normalized[-1]
        assert isinstance(failed_event.payload, BookUpdate)
        assert failed_event.ingest_ordinal == rejected.ingest_ordinal
        assert failed_event.checksum.result == "unchecked"
        assert failed_event.checksum.calculated is None
        feed._gap("checksum_mismatch")
        assert isinstance(normalized[-1].payload, Gap)
        assert normalized[-1].payload.reason_code == "checksum_mismatch"
        assert normalized[-1].ingest_ordinal > failed_event.ingest_ordinal
        assert raw[1].payload == corrupt

    asyncio.run(scenario())


@pytest.mark.parametrize("sink_kind", ["raw", "events", "book"])
@pytest.mark.parametrize("failure_kind", ["object", "long", "control", "broken_str"])
def test_arbitrary_sink_failure_recovers_and_cleans_up(sink_kind, failure_kind):
    async def scenario():
        class BrokenFailure(RuntimeError):
            def __str__(self):
                raise RuntimeError("cannot stringify")

        failure = (
            BrokenFailure()
            if failure_kind == "broken_str"
            else RuntimeError(
                "x" * 5000 if failure_kind == "long" else "bad\n\x00detail"
            )
        )
        failure.reason = object() if failure_kind == "object" else failure.args
        failed = False
        gaps, sockets = [], []
        recovered = asyncio.Event()

        def fail_once():
            nonlocal failed
            if not failed:
                failed = True
                raise failure

        def raw_sink(frame):
            if sink_kind == "raw":
                fail_once()

        def event_sink(events):
            gaps.extend(e for e in events if isinstance(e.payload, Gap))
            if sink_kind == "events" and any(
                isinstance(e.payload, BookUpdate) for e in events
            ):
                fail_once()

        def book_sink(book):
            if book.quality.integrity == "valid":
                if sink_kind == "book":
                    fail_once()
                if feed.epoch >= 2:
                    recovered.set()

        def connect(*args, **kwargs):
            socket = FixtureSocket([b"ok"])
            sockets.append(socket)
            return socket

        feed = supervisor(
            adapter=FixtureAdapter(snapshot=False),
            callbacks=FeedCallbacks(raw=raw_sink, events=event_sink, book=book_sink),
            config=fast_config(snapshot_timeout=1, silence_timeout=1),
            ws_connect=connect,
        )
        task = asyncio.create_task(feed.run())
        try:
            await asyncio.wait_for(recovered.wait(), 2)
            assert not task.done()
            assert feed.book.quality.integrity == "valid"
            assert (
                gaps
                and gaps[0].payload.reason_code
                == {
                    "raw": "capture_overflow",
                    "events": "event_sink_failed",
                    "book": "book_sink_failed",
                }[sink_kind]
            )
            assert len(feed.diagnostic_detail) <= 4096
            assert all(c.isprintable() for c in feed.diagnostic_detail)
            if sink_kind != "book":
                assert not feed.book.quality.capture_complete
                assert gaps[0].payload.capture_lost
        finally:
            task.cancel()
            result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert not feed._running and feed.lifecycle == "disconnected"
        assert all(socket.closed for socket in sockets)
        assert gaps[-1].payload.reason_code == "owner_stopped"

    asyncio.run(scenario())


def test_failing_gap_and_diagnostic_sinks_cannot_kill_owner():
    async def scenario():
        recovered = asyncio.Event()
        sockets = []

        def events(batch):
            if feed.epoch == 1 or any(isinstance(e.payload, Gap) for e in batch):
                raise RuntimeError("\x00" * 6000)

        def diagnostic(reason):
            raise RuntimeError("diagnostic sink failure")

        def book(value):
            if value.quality.integrity == "valid":
                recovered.set()
            else:
                raise RuntimeError("invalid-book sink failure")

        def connect(*args, **kwargs):
            socket = FixtureSocket([b"ok"])
            sockets.append(socket)
            return socket

        feed = supervisor(
            adapter=FixtureAdapter(snapshot=False),
            callbacks=FeedCallbacks(events=events, book=book, diagnostic=diagnostic),
            config=fast_config(silence_timeout=1),
            ws_connect=connect,
        )
        task = asyncio.create_task(feed.run())
        try:
            await asyncio.wait_for(recovered.wait(), 2)
            assert not task.done()
            assert feed.epoch >= 2
            assert not feed.capture_complete
            assert not feed.book.quality.capture_complete
        finally:
            task.cancel()
            result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert not feed._running
        assert all(socket.closed for socket in sockets)

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", [object(), "x" * 6000, "bad\nreason", "blocked"])
def test_snapshot_exception_reasons_are_bounded_and_preserve_known_codes(reason):
    async def scenario():
        async def fetch(request):
            raise PublicTransportError(reason)

        events = []
        feed = supervisor(
            http_fetch=fetch, callbacks=FeedCallbacks(events=events.extend)
        )
        feed._start_epoch(True)
        await feed._snapshot(
            feed.adapter.snapshot_request(feed.instrument, feed.key), feed.epoch
        )
        feed._gap(feed._fault_reason)
        assert events[-1].payload.reason_code == (
            "snapshot:blocked"
            if reason == "blocked"
            else "snapshot:invalid_failure_reason"
        )
        assert events[-1].payload.first_missing_ordinal is None
        assert events[-1].payload.last_missing_ordinal is None

    asyncio.run(scenario())


@pytest.mark.parametrize("loss_kind", ["count", "bytes", "capture"])
def test_known_lost_ordinals_include_flushed_queue_not_other_stream(loss_kind):
    async def scenario():
        boundary = ReceiveBoundary()
        raw, events = [], []

        def capture(frame):
            raw.append(frame)
            if loss_kind == "capture" and len(raw) == 2:
                raise RuntimeError("capture failed")

        feed = supervisor(
            callbacks=FeedCallbacks(raw=capture, events=events.extend),
            config=SupervisorConfig(
                queue_count=1 if loss_kind == "count" else 10,
                queue_bytes=2 if loss_kind == "bytes" else 100,
            ),
        )
        other = supervisor()
        feed.boundary = other.boundary = boundary
        feed._start_epoch(False)
        other._start_epoch(False)
        feed._receive(b"ok", "ws", "endpoint", feed.epoch)
        other._receive(b"ok", "ws", "endpoint", other.epoch)
        other_frame = other.queue.get_nowait()
        feed._receive(b"ok", "ws", "endpoint", feed.epoch)
        assert feed._fault.is_set()
        feed._gap(feed._fault_reason)
        gap = events[-1].payload
        assert gap.first_missing_ordinal == raw[0].ingest_ordinal
        assert gap.last_missing_ordinal == raw[1].ingest_ordinal
        assert (
            gap.first_missing_ordinal
            < other_frame.ingest_ordinal
            < gap.last_missing_ordinal
        )
        assert gap.affected_streams == (feed.stream_id,)
        assert gap.capture_lost  # Raw or required normalized delivery was lost.
        assert feed.queue.empty() and feed.queued_bytes == 0
        feed._gap("unknown_disconnect")
        assert events[-1].payload.first_missing_ordinal is None
        assert events[-1].payload.last_missing_ordinal is None

    asyncio.run(scenario())


def test_single_capture_loss_records_exact_ordinal():
    events, raw = [], []

    def capture(frame):
        raw.append(frame)
        raise RuntimeError("capture failed")

    feed = supervisor(callbacks=FeedCallbacks(raw=capture, events=events.extend))
    feed._start_epoch(False)
    feed._receive(b"ok", "ws", "endpoint", feed.epoch)
    feed._gap(feed._fault_reason)
    gap = events[-1].payload
    assert (
        gap.first_missing_ordinal == gap.last_missing_ordinal == raw[0].ingest_ordinal
    )
    assert gap.capture_lost


def test_processing_stages_use_live_origin_without_rewriting_source_receive(
    monkeypatch,
):
    async def scenario():
        delivered = []
        feed = supervisor(callbacks=FeedCallbacks(events=delivered.extend))
        feed._start_epoch(False)
        frame = feed.boundary.frame(
            feed.stream_id, feed.connection_id, feed.epoch, "ws", "endpoint", b"ok"
        )
        origin = frame.receive_mono_ns
        ticks = iter(origin + offset for offset in (100, 200, 300, 400, 500, 600))
        monkeypatch.setattr(time, "monotonic_ns", lambda: next(ticks))
        await feed._process(frame)
        event = delivered[-1]
        assert event.receive_mono_ns == origin
        assert event.receive_wall_ns == frame.receive_wall_ns
        assert (
            event.process_start_mono_ns,
            event.process_end_mono_ns,
            event.publish_mono_ns,
        ) == (origin + 200, origin + 500, origin + 600)
        stages = feed.last_processing
        assert stages.clock_id == frame.clock_id
        assert stages.queue_exit_mono_ns - stages.receive_mono_ns == 100
        assert stages.decode_start_mono_ns - stages.queue_exit_mono_ns == 100
        assert stages.decode_end_mono_ns - stages.decode_start_mono_ns == 100
        assert stages.validation_end_mono_ns - stages.decode_end_mono_ns == 100
        assert feed.book.bids[0].price == Decimal("99")

    asyncio.run(scenario())


def test_quiet_trade_transport_keeps_owner_alive_but_never_invents_a_trade():
    async def scenario():
        delivered = []
        feed = supervisor(
            channel="trades", callbacks=FeedCallbacks(events=delivered.extend)
        )
        feed._start_epoch(False)

        async def process(payload):
            frame = feed.boundary.frame(
                feed.stream_id,
                feed.connection_id,
                feed.epoch,
                "ws",
                "endpoint",
                payload,
            )
            await feed._process(frame)

        await process(b"heartbeat")
        assert not feed._valid
        await process(b"trade1")
        assert feed._valid
        feed.last_valid = 0
        await process(b"heartbeat")
        assert feed.last_valid > 0
        assert [
            event.payload.dedup_key
            for event in delivered
            if isinstance(event.payload, Trade)
        ] == ["trade1"]
        assert delivered[-1].payload == Control("heartbeat")

    asyncio.run(scenario())
