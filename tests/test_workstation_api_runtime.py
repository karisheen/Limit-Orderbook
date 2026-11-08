"""Real-source regressions for owner bootstrap, private creation and capture pins."""

import asyncio
import json
import threading
import time

import pytest

from src.workstation.adapter_catalog import AdapterCatalog
from src.workstation.adapters.base import wrap_decoded
from src.workstation.adapters.hyperliquid import HyperliquidAdapter
from src.workstation.demo import create_demo, demo_source
from src.workstation.demo_memory import MemoryReplaySource
from src.workstation.events import RawFrame, SegmentId
from src.workstation.ingestion import FeedSupervisor
from src.workstation.instruments import MetadataSnapshot
from src.workstation.runtime import ClientSession, RuntimeFailure, TerminalRuntime
from src.workstation.subscriptions import SubscriptionRegistry


@pytest.fixture(scope="module")
def memory_source():
    return MemoryReplaySource.demo()


class GenericSource:
    """Protocol consumer fixture deliberately has no concrete .batches member."""

    def __init__(self, source, entered=None, release=None):
        self.source = source
        self.descriptor = source.descriptor
        self.total_deliveries = source.total_deliveries
        self.entered, self.release = entered, release

    def iter_batches(self, **options):
        if self.entered is not None:
            self.entered.set()
            if not self.release.wait(10):
                raise RuntimeError("test owner was not released")
        return self.source.iter_batches(**options)

    def raw_frame(self, *args):
        return self.source.raw_frame(*args)

    def resolve_delivery(self, *args):
        return self.source.resolve_delivery(*args)

    def iter_checkpoints(self, **options):
        return self.source.iter_checkpoints(**options)


def offline_runtime(source, **options):
    runtime = TerminalRuntime(**options)
    runtime.demo = source
    runtime.demo_id = source.descriptor.source_id
    runtime.demo_ready.set()
    runtime.sources[runtime.demo_id] = {
        "id": runtime.demo_id,
        "mode": "demo",
        "label": "complete synthetic source",
        "status": "ready",
        "synthetic": True,
        "recording": False,
        "duration_ns": "180000000000",
        "streams": 24,
        "reasons": [],
    }
    return runtime


class InitialSourceSupervisor(FeedSupervisor):
    """Production decoder/reducer, one immediate real-schema message, then quiet."""

    async def run(self):
        self._running = True
        self._start_epoch(False)
        try:
            if self.key.channel == "book":
                message = {
                    "channel": "l2Book",
                    "data": {
                        "coin": self.instrument.native_symbol,
                        "time": 1700000000000,
                        "levels": [
                            [{"px": "60000", "sz": "1", "n": 1}],
                            [{"px": "60001", "sz": "2", "n": 1}],
                        ],
                    },
                }
            else:
                message = {
                    "channel": "trades",
                    "data": [
                        {
                            "coin": self.instrument.native_symbol,
                            "side": "B",
                            "px": "60001",
                            "sz": "0.1",
                            "time": 1700000000000,
                            "hash": "synthetic-runtime",
                            "tid": 1,
                        }
                    ],
                }
            self._receive(
                json.dumps(message).encode(),
                "ws",
                "wss://api.hyperliquid.xyz/ws",
                self.epoch,
            )
            frame = self.queue.get_nowait()
            self.queued_bytes -= len(frame.payload)
            await self._process(frame)
            await asyncio.Event().wait()
        finally:
            await self._shutdown()


def live_runtime(tmp_path, *, instruments=None):
    if instruments is None:
        instruments = (
            next(
                spec.instrument
                for spec in demo_source().streams
                if spec.instrument.venue == "hyperliquid"
                and spec.instrument.base.ticker == "BTC"
            ),
        )
    instrument = instruments[0]
    catalog = AdapterCatalog()
    catalog.adapters["hyperliquid"] = HyperliquidAdapter()
    catalog.instruments.apply_metadata(
        MetadataSnapshot("runtime-fixture", 1, "synthetic", instruments)
    )
    registry = SubscriptionRegistry(
        catalog,
        supervisor_factory=InitialSourceSupervisor,
        lock_path=tmp_path / "collector",
    )
    runtime = TerminalRuntime(registry=registry)
    runtime.sources["live"] = {
        "id": "live",
        "mode": "live",
        "label": "native public source fixture",
        "status": "ready",
        "synthetic": False,
        "recording": False,
        "duration_ns": None,
        "streams": 2,
        "reasons": [],
    }
    return runtime, registry, instrument


def test_live_initial_deliveries_and_quiet_join_preserve_truthful_history(tmp_path):
    async def scenario():
        runtime, registry, instrument = live_runtime(tmp_path)
        try:
            first = await runtime.create("live", "live", str(instrument.instrument_id))
            # Acquisition need not wait for a quiet trade channel. Wait for both
            # actual fixture transactions to finish the asynchronous observer.
            async with asyncio.timeout(5):
                while first.delivery < 4:
                    await asyncio.sleep(0.001)
            initial = await first.frame()
            assert initial["health"]["ready"] is True
            assert initial["book"]["bids"][0]["price"] == "60000"
            assert len(initial["analytics"]["tape"]) == 1
            assert first.delivery >= 4  # Both source changes and actual data messages.
            second = await runtime.create("live", "live", str(instrument.instrument_id))
            joined = await second.frame()
            assert joined["health"]["ready"] is True
            assert joined["book"]["asof"] == initial["book"]["asof"]
            assert (
                joined["analytics"]["tape"] == []
            )  # No invented historical trade delivery.
            assert second.delivery == 0
            assert any(
                metric["value"] is not None for metric in joined["analytics"]["metrics"]
            )
            assert all(status.reference_count == 2 for status in registry.snapshot())
        finally:
            await runtime.close()
            await registry.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ("count", "bytes"))
def test_live_history_retention_evicts_oldest_and_presents_latest_without_future_events(
    tmp_path, boundary
):
    import httpx

    from src.workstation.api import create_app

    count_limit, byte_limit = 4096, 8 * 1024 * 1024
    events_per_delivery = 1 if boundary == "count" else 512

    async def scenario():
        runtime, registry, instrument = live_runtime(tmp_path)
        try:
            first = await runtime.create("live", "live", str(instrument.instrument_id))
            async with asyncio.timeout(5):
                while first.delivery < 4:
                    await asyncio.sleep(0.001)
            # A quiet join starts with real book state but no invented history.
            session = await runtime.create(
                "live", "live", str(instrument.instrument_id)
            )
            assert session.delivery == 0
            spec = next(
                spec for spec in session.streams if spec.key.channel == "trades"
            )
            adapter = HyperliquidAdapter()
            clock = session.live_clock.reading
            assert clock is not None

            def batch(delivery):
                # Fake only received wire data; retain the native decoder,
                # envelopes, live observer, analytics and API serialization.
                raw = RawFrame(
                    "history-retention",
                    spec.stream_id,
                    "history-trades",
                    1,
                    clock.clock_id,
                    delivery,
                    1700000000000000000 + delivery,
                    clock.mono_ns + delivery,
                    "ws",
                    adapter.ws_endpoint(instrument, spec.key),
                    json.dumps(
                        {
                            "channel": "trades",
                            "data": [
                                {
                                    "coin": instrument.native_symbol,
                                    "side": "B",
                                    "px": "60001",
                                    "sz": "0.1",
                                    "time": 1700000000000 + delivery,
                                    "tid": delivery * 1000 + index,
                                    "hash": f"history-{delivery}-{index}",
                                }
                                for index in range(events_per_delivery)
                            ],
                        }
                    ).encode(),
                )
                decoded = adapter.decode(raw, instrument, spec.key)
                assert decoded.error is None, decoded.error
                events = wrap_decoded(
                    raw,
                    instrument,
                    spec.key,
                    decoded,
                    SegmentId("history-retention"),
                    "ws_snapshot",
                )
                assert len(events) == events_per_delivery
                return events

            def fill_history():
                sizes = []

                def apply(events):
                    sizes.append(len(repr(events)))
                    session._apply_live(events, session.live_books, sizes[-1])
                    assert len(session.history) <= count_limit
                    assert session.history_bytes <= byte_limit
                    assert session.history[-1].delivery_index == session.delivery
                    assert session.history[-1].events == events

                while True:
                    events = batch(session.delivery + 1)
                    size = len(repr(events))
                    assert size < byte_limit
                    if boundary == "count":
                        # Ensure this really exercises the delivery-count bound,
                        # not an earlier byte eviction.
                        assert session.history_bytes + size < byte_limit
                        at_boundary = session.delivery == count_limit
                    else:
                        assert session.delivery < count_limit
                        at_boundary = session.history_bytes + size > byte_limit
                    if at_boundary:
                        break
                    apply(events)

                assert session.history_floor == 1
                assert session.history[0].delivery_index == 1
                assert len(session.history) == session.delivery
                assert session.history_bytes == sum(sizes)
                if boundary == "count":
                    assert len(session.history) == count_limit
                else:
                    assert session.history_bytes <= byte_limit
                    assert session.history_bytes + size > byte_limit

                apply(events)
                assert session.history_floor > 1
                # Continue after overflow: retaining latest is an ongoing
                # guarantee, not just a one-time eviction.
                apply(batch(session.delivery + 1))
                apply(batch(session.delivery + 1))
                retained_bytes = sum(sizes)
                evicted = 0
                while len(sizes) - evicted > count_limit or retained_bytes > byte_limit:
                    retained_bytes -= sizes[evicted]
                    evicted += 1
                assert session.history_floor == evicted + 1
                assert [entry.delivery_index for entry in session.history] == list(
                    range(evicted + 1, session.delivery + 1)
                )
                assert list(session.history_sizes) == sizes[evicted:]
                assert session.history_bytes == retained_bytes
                if boundary == "count":
                    assert evicted == 3
                    assert len(session.history) == count_limit
                else:
                    assert len(session.history) < count_limit
                for entry in session.history:
                    assert all(
                        event.ingest_ordinal == entry.delivery_index
                        for event in entry.events
                    )

            async with session.lock:
                await session.work(fill_history)

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app(runtime)),
                base_url="http://127.0.0.1",
            ) as client:

                async def history(start, end):
                    response = await client.get(
                        "/api/v1/history",
                        params={
                            "session_id": session.id,
                            "generation": str(session.generation),
                            "start_delivery": start,
                            "end_delivery": end,
                            "limit": 1000,
                        },
                    )
                    assert response.status_code == 200
                    result = response.json()
                    assert result["generation"] == str(session.generation)
                    assert result["cursor"] == str(session.delivery)
                    return result

                def assert_delivery(result, delivery):
                    events = result["events"]
                    assert len(events) == events_per_delivery
                    assert [event["payload"]["trade_id"] for event in events] == [
                        str(delivery * 1000 + index)
                        for index in range(events_per_delivery)
                    ]
                    assert all(
                        event["ingest_ordinal"] == str(delivery)
                        and event["key"]["instrument_id"]
                        == str(instrument.instrument_id)
                        and event["payload"]["price"] == "60001"
                        and event["payload"]["quantity"] == "0.1"
                        for event in events
                    )

                floor = session.history_floor
                assert 1 < floor < session.delivery
                lost_prefix = await history(1, floor)
                assert_delivery(lost_prefix, floor)
                assert lost_prefix["truncated"] is True
                evicted_only = await history(1, floor - 1)
                assert evicted_only["events"] == []
                assert evicted_only["truncated"] is True

                # Later retained batches must not leak into a bounded query.
                bounded = await history(floor, floor)
                assert_delivery(bounded, floor)
                assert bounded["truncated"] is False
                assert bounded["events"] == lost_prefix["events"]

                latest = await history(session.delivery, 10**18)
                assert_delivery(latest, session.delivery)
                assert latest["truncated"] is False
                future = await history(session.delivery + 1, 10**18)
                assert future["events"] == []
                assert future["truncated"] is False
        finally:
            await runtime.close()
            await registry.close()

    asyncio.run(scenario())


def test_live_failed_initialization_releases_temporary_sink_and_owners(
    tmp_path, monkeypatch
):
    def fail(self, *args):
        raise ValueError("deliberate observer configuration rejection")

    monkeypatch.setattr(ClientSession, "_initialize_live", fail)

    async def scenario():
        runtime, registry, instrument = live_runtime(tmp_path)
        try:
            with pytest.raises(ValueError):
                await runtime.create("live", "live", str(instrument.instrument_id))
            assert runtime.sessions == {}
            assert runtime.reservations == 0
            assert registry.snapshot() == ()
            assert registry._processed_sinks == []
        finally:
            await runtime.close()
            await registry.close()

    asyncio.run(scenario())


def test_creation_is_private_capacity_reserved_and_generic_source_supported(
    memory_source,
):
    async def scenario():
        entered, release = threading.Event(), threading.Event()
        runtime = offline_runtime(
            GenericSource(memory_source, entered, release), max_sessions=1
        )
        creator = asyncio.create_task(runtime.create())
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            assert runtime.sessions == {}
            assert runtime.reservations == 1
            with pytest.raises(RuntimeFailure) as failure:
                await runtime.create()
            assert failure.value.status == 503
            await runtime.remove("unpublished-id")
            release.set()
            session = await creator
            frame = await session.frame()
            assert frame["book"] is not None
            assert frame["replay"]["total_deliveries"] == str(
                memory_source.total_deliveries
            )
            assert runtime.reservations == 0
        finally:
            release.set()
            await asyncio.gather(creator, return_exceptions=True)
            await runtime.close()

    asyncio.run(scenario())


def test_shutdown_joins_private_initializer_before_executor_disposal(memory_source):
    async def scenario():
        entered, release = threading.Event(), threading.Event()
        runtime = offline_runtime(GenericSource(memory_source, entered, release))
        creator = asyncio.create_task(runtime.create())
        assert await asyncio.to_thread(entered.wait, 5)
        closing = asyncio.create_task(runtime.close())
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        await closing
        outcome = await asyncio.gather(creator, return_exceptions=True)
        assert isinstance(outcome[0], asyncio.CancelledError)
        assert runtime.sessions == {} and runtime.reservations == 0
        assert not runtime.initializers

    asyncio.run(scenario())


def test_reaper_preserves_active_owner_and_refreshes_completed_access(memory_source):
    async def scenario():
        runtime = offline_runtime(memory_source, orphan_seconds=1)
        try:
            session = await runtime.create()
            cutoff = time.monotonic()
            async with session.activity(), session.lock:
                session.last_access = cutoff - 10
                await runtime.remove(session.id, idle_before=cutoff)
                assert session.id in runtime.sessions
            await runtime.remove(session.id, idle_before=cutoff)
            assert session.id in runtime.sessions
            session.last_access = cutoff - 10
            await runtime.remove(session.id, idle_before=cutoff)
            assert session.id not in runtime.sessions
        finally:
            await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "replacement,active", [(False, False), (True, False), (False, True), (True, True)]
)
def test_catalog_capture_identity_never_substitutes_removed_or_replaced_capture(
    tmp_path, replacement, active
):
    async def scenario():
        with await create_demo(tmp_path / "original", duration_seconds=1) as capture:
            path = capture.reader.path
        with await create_demo(tmp_path / "replacement", duration_seconds=2) as capture:
            replacement_path = capture.reader.path
        runtime = TerminalRuntime(captures=(path,))
        try:
            await runtime.start()
            await runtime.demo_task
            source_id = next(
                key
                for key, value in runtime.sources.items()
                if value["mode"] == "replay"
            )
            session = await runtime.create("replay", source_id) if active else None
            path.rename(path.with_name("moved-original"))
            if replacement:
                replacement_path.rename(path)
            with pytest.raises(RuntimeFailure) as failure:
                if active:
                    await session.frame()
                else:
                    await runtime.create("replay", source_id)
            assert failure.value.status == 503
            assert runtime.sources[source_id]["status"] == "unavailable"
            assert runtime.reservations == 0
            if active:
                unavailable = await session.frame()
                assert unavailable["status"] == "unavailable"
                assert unavailable["book"] is None and unavailable["analytics"] is None
            else:
                assert runtime.sessions == {}
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_failed_websocket_handshake_releases_session_and_live_owners(tmp_path):
    from src.workstation.api import create_app

    async def scenario():
        runtime, registry, instrument = live_runtime(tmp_path)
        try:
            session = await runtime.create(
                "live", "live", str(instrument.instrument_id)
            )
            app = create_app(runtime)

            async def receive():
                return {"type": "websocket.connect"}

            async def disconnected_send(message):
                assert message["type"] == "websocket.accept"
                raise OSError("peer disconnected during handshake")

            scope = {
                "type": "websocket",
                "asgi": {"version": "3.0", "spec_version": "2.4"},
                "scheme": "ws",
                "path": "/api/v1/stream",
                "root_path": "",
                "query_string": f"session_id={session.id}".encode(),
                "headers": [
                    (b"host", b"127.0.0.1"),
                    (b"origin", b"http://127.0.0.1:8000"),
                ],
                "server": ("127.0.0.1", 8000),
                "client": ("127.0.0.1", 23456),
                "subprotocols": [],
                "state": {},
            }
            with pytest.raises(OSError):
                await app(scope, receive, disconnected_send)
            assert runtime.sessions == {}
            assert registry.snapshot() == ()
            assert registry._processed_sinks == []
        finally:
            await runtime.close()
            await registry.close()

    asyncio.run(scenario())


def test_level_cancelled_websocket_joins_workers_and_preserves_other_live_owner(
    tmp_path,
):
    import anyio

    from src.workstation.api import create_app

    async def scenario():
        runtime, registry, instrument = live_runtime(tmp_path)
        release_worker = threading.Event()
        worker_started = threading.Event()
        worker_finished = threading.Event()
        owner_task = None
        try:
            session = await runtime.create(
                "live", "live", str(instrument.instrument_id)
            )
            key = next(
                spec.key for spec in session.streams if spec.key.channel == "book"
            )
            independent = await registry.acquire("independent-headless-owner", key)
            frame_sent = anyio.Event()
            receives = asyncio.Queue()
            receives.put_nowait({"type": "websocket.connect"})

            def bounded_history_operation():
                result = session._history(0, 10**18, 100)
                worker_started.set()
                try:
                    if not release_worker.wait(10):
                        raise RuntimeError("history worker was not released")
                    return result
                finally:
                    worker_finished.set()

            async def run_owned_history():
                async with session.activity(), session.lock:
                    await session.work(bounded_history_operation)

            owner_task = asyncio.create_task(run_owned_history())
            assert await asyncio.to_thread(worker_started.wait, 5)

            async def receive():
                return await receives.get()

            async def send(message):
                if message["type"] == "websocket.send":
                    frame_sent.set()
                    await asyncio.Event().wait()

            scope = {
                "type": "websocket",
                "asgi": {"version": "3.0", "spec_version": "2.4"},
                "scheme": "ws",
                "path": "/api/v1/stream",
                "root_path": "",
                "query_string": f"session_id={session.id}".encode(),
                "headers": [
                    (b"host", b"127.0.0.1"),
                    (b"origin", b"http://127.0.0.1:8000"),
                ],
                "server": ("127.0.0.1", 8000),
                "client": ("127.0.0.1", 23456),
                "subprotocols": [],
                "state": {},
            }
            app = create_app(runtime)
            async with anyio.create_task_group() as group:
                group.start_soon(app, scope, receive, send)
                await frame_sent.wait()
                group.cancel_scope.cancel()
                release_worker.set()
            await owner_task
            assert worker_finished.is_set()
            assert runtime.sessions == {}
            assert session.closed and session.active_work == 0
            assert session.live_task is not None and session.live_task.done()
            assert registry._processed_sinks == []
            retained = registry.snapshot()
            assert len(retained) == 1
            assert retained[0].key == independent.key
            assert retained[0].reference_count == 1
            await registry.release(independent.lease_id)
            assert registry.snapshot() == ()
        finally:
            release_worker.set()
            if owner_task is not None:
                await asyncio.gather(owner_task, return_exceptions=True)
            await runtime.close()
            await registry.close()

    asyncio.run(scenario())


def test_busy_presentation_keeps_completed_evidence_but_fences_generation(
    memory_source,
):
    async def scenario():
        runtime = offline_runtime(memory_source)
        try:
            session = await runtime.create("demo")
            first = await session.frame()
            assert first["book"] is not None and first["replay"] is not None
            async with session.lock:
                busy = await session.frame()
                assert busy["book"] == first["book"]
                assert busy["analytics"] == first["analytics"]
                assert busy["replay"] == first["replay"]
                assert busy["status"] == "paused"
                assert not busy["health"]["ready"]
                assert "last_completed_presentation" in busy["health"]["reasons"]
                for status in ("seeking", "unavailable", "error"):
                    session.status = status
                    invalid = await session.frame()
                    assert invalid["book"] is None
                    assert invalid["analytics"] is None
                    assert invalid["replay"] is None
                session.status = "paused"
                session.generation += 1
                changed = await session.frame()
                assert changed["book"] is None
                assert changed["analytics"] is None
                assert changed["replay"] is None
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_bounded_frames_preserve_native_depth_costs_and_complete_metrics():
    from src.workstation.api import CostRequest
    from src.workstation.projection import RecordingProjection
    from src.workstation.recording import RecordedBatch
    from src.workstation.replay import ReplaySourceDescriptor

    spec = next(
        item
        for item in demo_source().streams
        if item.instrument.venue == "coinbase"
        and item.instrument.base.ticker == "BTC"
        and item.key.channel == "book"
    )
    projection = RecordingProjection((spec,))
    batches, frames = [], {}
    for ordinal in (1, 2):
        updates = (
            [
                {
                    "side": side,
                    "price_level": str(price),
                    "new_quantity": "1",
                    "event_time": "2025-01-01T00:00:00Z",
                }
                for side, prices in (
                    ("bid", range(60000, 59750, -1)),
                    ("offer", range(60001, 60251)),
                )
                for price in prices
            ]
            if ordinal == 1
            else [
                {
                    "side": "bid",
                    "price_level": str(price),
                    "new_quantity": "2",
                    "event_time": "2025-01-01T00:01:00Z",
                }
                for price in range(60000, 59900, -1)
            ]
        )
        frame = RawFrame(
            "presentation-fixture",
            spec.stream_id,
            "connection",
            1,
            "presentation-clock",
            ordinal,
            1735689600000000000 + (ordinal - 1) * 60_000_000_000,
            1_000_000_000 + (ordinal - 1) * 60_000_000_000,
            "ws",
            "wss://advanced-trade-ws.coinbase.com",
            json.dumps(
                {
                    "channel": "l2_data",
                    "sequence_num": ordinal - 1,
                    "events": [
                        {
                            "type": "snapshot" if ordinal == 1 else "update",
                            "product_id": spec.instrument.native_symbol,
                            "updates": updates,
                        }
                    ],
                }
            ).encode(),
        )
        events = projection.process_raw(
            frame, segment_id=SegmentId("ws-1"), source_mode="ws_delta"
        )
        batches.append(RecordedBatch(ordinal, events))
        frames[(str(spec.stream_id), ordinal)] = frame
    source = MemoryReplaySource(
        ReplaySourceDescriptor(
            "presentation-fixture",
            (spec,),
            synthetic=True,
            provenance="synthetic full-depth presentation regression",
        ),
        tuple(batches),
        frames,
        {
            (event.ingest_ordinal, event.event_index): batch.delivery_index
            for batch in batches
            for event in batch.events
        },
        60_000_000_000,
    )

    async def scenario():
        runtime = offline_runtime(source)
        try:
            session = await runtime.create(
                instrument_id=str(spec.instrument.instrument_id)
            )
            frame = await session.frame()
            assert len(frame["book"]["bids"]) == len(frame["book"]["asks"]) == 100
            assert frame["book"]["asks"][-1]["price"] == "60100"
            assert frame["book"]["presentation"] == {
                "depth_limit": 100,
                "bids_omitted": 150,
                "asks_omitted": 150,
            }
            assert frame["book"]["quality"]["coverage"]["retained_ask_levels"] == "250"
            assert len(frame["analytics"]["display_evidence"]) == 100
            assert frame["analytics"]["presentation"]["evidence_omitted"] == 200
            metric = next(
                item
                for item in frame["analytics"]["metrics"]
                if item["name"] == "displayed_replenishment"
            )
            assert metric["value"] == "100"
            request = CostRequest(
                session_id=session.id,
                generation=str(session.generation),
                side="buy",
                base_quantity="150",
                fee_bps="0",
                fee_provenance="Explicit regression assumption",
                fee_date="2025-01-01",
            )
            result = await session.work(session._costs, request)
            assert result["estimate"]["filled_base_quantity"] == "150"
            assert result["estimate"]["unfilled_base_quantity"] == "0"
            assert result["estimate"]["notional_quote"] == "9011325"
            assert result["estimate"]["partial"] is False
            assert result["estimate"]["consumed_levels"][-1]["price"] == "60150"
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_live_suspended_peer_does_not_reject_active_selection(tmp_path):
    from dataclasses import replace

    async def scenario():
        active = next(
            spec.instrument
            for spec in demo_source().streams
            if spec.instrument.venue == "hyperliquid"
            and spec.instrument.base.ticker == "BTC"
        )
        suspended = replace(
            next(
                spec.instrument
                for spec in demo_source().streams
                if spec.instrument.venue == "binance-global"
                and spec.instrument.base.ticker == "BTC"
            ),
            status="suspended",
        )
        runtime, registry, _ = live_runtime(tmp_path, instruments=(active, suspended))
        try:
            session = await runtime.create("live", "live", str(active.instrument_id))
            assert session.instrument_id == active.instrument_id
            assert suspended.instrument_id not in {
                status.key.instrument_id for status in registry.snapshot()
            }
            with pytest.raises(ValueError, match="product_suspended"):
                await runtime.create("live", "live", str(suspended.instrument_id))
            assert tuple(runtime.sessions) == (session.id,)
        finally:
            await runtime.close()

    asyncio.run(scenario())


@pytest.fixture
def cohort_product():
    from dataclasses import replace

    from src.workstation.instruments import AssetIdentity

    template = next(
        spec.instrument
        for spec in demo_source().streams
        if spec.instrument.instrument_id == "binance-global:spot-v3:spot:BTCUSDT"
    )

    def make(
        venue,
        quote="USD",
        market_type="spot",
        *,
        suffix="",
        ticker=None,
        base="BTC",
        status="active",
    ):
        symbol = f"{base}{quote}{suffix}"
        return replace(
            template,
            instrument_id=f"{venue}:cohort:{market_type}:{symbol}",
            venue=venue,
            native_symbol=symbol,
            market_type=market_type,
            base=AssetIdentity(base, base),
            quote=AssetIdentity(quote, ticker or quote),
            status=status,
        )

    return make


@pytest.mark.parametrize("primary_quote", ("USD", "USDT", "USDC"))
def test_live_cohort_allows_only_exact_or_canonical_usd_family(
    cohort_product, primary_quote
):
    from src.workstation.runtime import _live_cohort

    selected = cohort_product("primary", primary_quote)
    eligible = tuple(
        cohort_product(f"peer-{quote}", quote) for quote in ("USD", "USDC", "USDT")
    )
    unrelated = tuple(
        cohort_product(f"other-{index}", quote, ticker="USD")
        for index, quote in enumerate(
            ("BRL", "AED", "EUR", "ARS", "AUD", "USD1", "venue:USD", "usd", "BTC")
        )
    )
    inputs = (*unrelated, *eligible, selected)
    cohort = _live_cohort(selected, inputs)
    assert cohort == (selected, *eligible)
    assert _live_cohort(selected, tuple(reversed(inputs))) == cohort
    for actual, original in zip(cohort, (selected, *eligible)):
        assert actual is original


@pytest.mark.parametrize("primary_quote", ("venue:EUR", "BRL", "ETH", "usd", "USD1"))
def test_live_cohort_non_usd_quotes_require_exact_asset_identity(
    cohort_product, primary_quote
):
    from src.workstation.runtime import _live_cohort

    selected = cohort_product("primary", primary_quote, ticker="SAME")
    exact = cohort_product("exact", primary_quote, ticker="DIFFERENT")
    unrelated = tuple(
        cohort_product(f"other-{index}", quote, ticker="SAME")
        for index, quote in enumerate(("USD", "USDT", "USDC", "other:EUR", "EUR"))
    )
    assert _live_cohort(selected, (*unrelated, exact, selected)) == (selected, exact)


@pytest.mark.parametrize("status", ("suspended", "delisted", "unavailable", "unknown"))
def test_live_cohort_filters_inactive_and_other_base_products(cohort_product, status):
    from src.workstation.runtime import _live_cohort

    selected = cohort_product("primary")
    inactive = cohort_product("peer", status=status)
    active = cohort_product("peer", "USDT")
    other_base = cohort_product("other-base", base="ETH")
    assert _live_cohort(selected, (inactive, other_base, active)) == (selected, active)


@pytest.mark.parametrize(
    ("primary_quote", "quotes", "preferred"),
    (
        ("USD", ("USDT", "USDC", "USD"), "USD"),
        ("USDT", ("USDT", "USDC", "USD"), "USDT"),
        ("USDC", ("USDT", "USDC", "USD"), "USDC"),
        ("USD", ("USDT", "USDC"), "USDT"),
        ("USDT", ("USDC", "USD"), "USDC"),
        ("USDC", ("USD",), "USD"),
    ),
)
def test_live_cohort_ranks_exact_quote_then_family_then_native_id(
    cohort_product, primary_quote, quotes, preferred
):
    from src.workstation.runtime import _live_cohort

    selected = cohort_product("primary", primary_quote, suffix="z")
    same_group = cohort_product("primary", primary_quote, suffix="A")
    peers = tuple(
        cohort_product("peer", quote, suffix=suffix)
        for quote in quotes
        for suffix in ("a", "A")
    )
    expected = next(
        item
        for item in peers
        if item.quote.asset_id == preferred and item.native_symbol.endswith("A")
    )
    inputs = (same_group, *peers)
    cohort = _live_cohort(selected, inputs)
    assert cohort == (selected, expected)
    assert cohort[0] is selected
    assert cohort[1] is expected
    assert _live_cohort(selected, tuple(reversed(inputs))) == cohort


def test_live_cohort_multi_quote_venues_cannot_starve_market_groups(cohort_product):
    from src.workstation.runtime import _live_cohort

    groups = (
        ("coinbase", "spot"),
        ("kraken", "spot"),
        ("binance-us", "spot"),
        ("binance-global", "spot"),
        ("binance-global", "perpetual"),
        ("hyperliquid", "spot"),
        ("hyperliquid", "perpetual"),
        ("okx", "spot"),
        ("okx", "perpetual"),
        ("bitget", "spot"),
        ("bitget", "perpetual"),
        ("kucoin", "spot"),
        ("kucoin", "perpetual"),
    )
    selected = cohort_product("coinbase", suffix="selected")
    inputs = tuple(
        cohort_product(venue, quote, market, suffix=suffix)
        for venue, market in groups
        for quote in ("USD", "USDT", "USDC", "BRL", "EUR", "AED", "AUD", "USD1")
        for suffix in ("A", "B")
    )
    cohort = _live_cohort(selected, inputs)
    assert cohort[0] is selected
    assert {(item.venue, item.market_type) for item in cohort} == set(groups)
    assert len(cohort) == len(groups)
    assert all(item.quote.asset_id == "USD" for item in cohort)
    assert _live_cohort(selected, tuple(reversed(inputs))) == cohort


@pytest.mark.parametrize("selected_market", ("spot", "perpetual"))
def test_live_cohort_bound_retains_exact_primary_and_prioritizes_selected_market(
    cohort_product, selected_market
):
    from src.workstation.runtime import _live_cohort

    selected = cohort_product("zz-primary", market_type=selected_market, suffix="z")
    same_group = cohort_product("zz-primary", market_type=selected_market, suffix="A")
    inputs = (
        same_group,
        *(
            cohort_product(f"venue-{index:02}", quote, market)
            for index in range(20)
            for market in ("spot", "perpetual")
            for quote in ("USDT", "USDC", "USD")
        ),
    )
    cohort = _live_cohort(selected, inputs)
    assert len(cohort) == 16
    assert cohort[0] is selected
    assert [item.venue for item in cohort[1:]] == [
        f"venue-{index:02}" for index in range(15)
    ]
    assert all(item.market_type == selected_market for item in cohort)
    assert all(item.quote.asset_id == "USD" for item in cohort)
    assert len({(item.venue, item.market_type) for item in cohort}) == len(cohort)
    assert _live_cohort(selected, tuple(reversed(inputs))) == cohort


@pytest.mark.parametrize("departed", (True, False))
def test_pending_paper_arrival_during_live_owner_replacement(
    tmp_path, monkeypatch, departed
):
    from dataclasses import replace

    from src.workstation.api import PaperConfigureRequest, PaperOrderInput, PaperQuery

    clock = [time.monotonic_ns(), time.time_ns()]
    monkeypatch.setattr(time, "monotonic_ns", lambda: clock[0])
    monkeypatch.setattr(time, "time_ns", lambda: clock[1])

    async def scenario():
        original = next(
            spec.instrument
            for spec in demo_source().streams
            if spec.instrument.venue == "hyperliquid"
            and spec.instrument.market_type == "spot"
        )
        other = replace(
            original,
            instrument_id="hyperliquid:info:spot:@999999",
            native_symbol="@999999",
            native_asset_id="999999",
            base=replace(original.base, asset_id="regression:OTHER", ticker="OTHER"),
        )
        runtime, registry, _ = live_runtime(tmp_path, instruments=(original, other))
        try:
            session = await runtime.create("live", "live", str(original.instrument_id))
            for _ in range(100):
                frame = await session.frame()
                if frame["health"]["ready"]:
                    break
                await asyncio.sleep(0.01)
            assert frame["health"]["ready"]
            identity = {"session_id": session.id, "generation": str(session.generation)}
            configured = await session.work(
                session._paper_configure,
                PaperConfigureRequest(
                    **identity,
                    balances=[
                        {
                            "venue": original.venue,
                            "asset_id": original.quote.asset_id,
                            "available": "100000",
                        }
                    ],
                    fees=[
                        {
                            "instrument_id": original.instrument_id,
                            "fee_bps": "5",
                            "fee_provenance": "explicit regression assumption",
                            "fee_date": "2025-01-01",
                            "fee_asset_id": original.quote.asset_id,
                        }
                    ],
                    order_latency_ns="1000000000",
                ),
            )
            submitted = await session.work(
                session._paper_order,
                PaperOrderInput(
                    **identity,
                    instrument_id=original.instrument_id,
                    side="buy",
                    quantity="1",
                ),
            )
            assert submitted["snapshot"]["orders"][0]["status"] == "pending"
            actual_replace = registry.replace

            async def delayed_replace(client_id, keys, generation, **options):
                # Advance the quiet source frontier while ownership replacement
                # is suspended, without feed events or a wall-time-dependent test.
                await asyncio.sleep(0)
                clock[0] += 1_050_000_000
                clock[1] += 1_050_000_000
                return await actual_replace(client_id, keys, generation, **options)

            monkeypatch.setattr(registry, "replace", delayed_replace)
            selected = other if departed else original
            await runtime.select(
                session, str(session.generation), selected.instrument_id
            )
            result = await session.work(
                session._paper_query,
                PaperQuery(session_id=session.id, generation=str(session.generation)),
            )
            snapshot = result["snapshot"]
            assert snapshot["run"]["run_id"] == configured["snapshot"]["run"]["run_id"]
            order = snapshot["orders"][0]
            if departed:
                assert snapshot["balances"] == submitted["snapshot"]["balances"]
                assert snapshot["fills"] == []
                assert order["status"] == "cancelled"
                assert "instrument_not_observed" in order["reasons"]
                assert "ioc_remainder_cancelled" in order["reasons"]
            else:
                assert order["status"] == "filled"
                assert len(snapshot["fills"]) == 1
        finally:
            await runtime.close()

    asyncio.run(scenario())


def live_trade_events(session, instrument, ordinal, mono_ns):
    spec = next(spec for spec in session.streams if spec.key.channel == "trades")
    adapter = HyperliquidAdapter()
    raw = RawFrame(
        "batch-ownership",
        spec.stream_id,
        "batch-trades",
        1,
        session.live_clock.reading.clock_id,
        ordinal,
        1700000000000000000 + ordinal,
        mono_ns,
        "ws",
        adapter.ws_endpoint(instrument, spec.key),
        json.dumps(
            {
                "channel": "trades",
                "data": [
                    {
                        "coin": instrument.native_symbol,
                        "side": "B",
                        "px": "60001",
                        "sz": "0.1",
                        "time": 1700000000000 + ordinal,
                        "tid": ordinal,
                        "hash": f"batch-{ordinal}",
                    }
                ],
            }
        ).encode(),
    )
    decoded = adapter.decode(raw, instrument, spec.key)
    assert decoded.error is None
    return wrap_decoded(
        raw,
        instrument,
        spec.key,
        decoded,
        SegmentId("batch-ownership"),
        "ws_snapshot",
    )


@pytest.mark.parametrize("boundary", ("count", "bytes"))
def test_live_inflight_ownership_remains_charged_until_cancelled_worker_joins(
    tmp_path, monkeypatch, boundary
):
    async def scenario():
        runtime, registry, instrument = live_runtime(tmp_path)
        entered, release = threading.Event(), threading.Event()
        try:
            first = await runtime.create("live", "live", str(instrument.instrument_id))
            async with asyncio.timeout(5):
                while first.delivery < 4:
                    await asyncio.sleep(0.001)
            session = await runtime.create(
                "live", "live", str(instrument.instrument_id)
            )
            session.live_task.cancel()
            await asyncio.gather(session.live_task, return_exceptions=True)
            events = live_trade_events(
                session, instrument, 10, session.live_clock.reading.mono_ns + 1
            )
            charge = len(repr(events))
            if boundary == "count":
                session.live_queue = asyncio.Queue(maxsize=1)
            else:
                monkeypatch.setattr("src.workstation.runtime.LIVE_QUEUE_BYTES", charge)
            original = session._apply_live_batch

            def stalled(batch, bootstrap=False):
                entered.set()
                if not release.wait(10):
                    raise RuntimeError("owner was not released")
                original(batch, bootstrap)

            monkeypatch.setattr(session, "_apply_live_batch", stalled)
            session.offer(events, ())
            session.live_task = asyncio.create_task(session.consume_live())
            assert await asyncio.to_thread(entered.wait, 5)
            session.offer(events, ())
            frame = await session.frame()
            assert frame["status"] == "unavailable"
            assert frame["health"]["ready"] is False
            assert "analytical_queue_overflow" in session.error
            session.live_task.cancel()
            await asyncio.sleep(0)
            assert session.live_bytes == charge
            assert session.live_inflight == 1
            release.set()
            await asyncio.gather(session.live_task, return_exceptions=True)
            assert session.live_bytes == 0
            assert session.live_inflight == 0
        finally:
            release.set()
            await runtime.close()
            await registry.close()

    asyncio.run(scenario())


def test_live_batched_owner_preserves_order_and_complete_observer_state(tmp_path):
    from types import SimpleNamespace

    from src.workstation.precision import DERIVED_DECIMAL_POLICY
    from src.workstation.visuals import CheckpointState, digest
    from src.workstation.wire import loads

    async def scenario():
        runtime, registry, instrument = live_runtime(tmp_path)
        try:
            first = await runtime.create("live", "live", str(instrument.instrument_id))
            async with asyncio.timeout(5):
                while first.delivery < 4:
                    await asyncio.sleep(0.001)
            owners = [
                await runtime.create("live", "live", str(instrument.instrument_id))
                for _ in range(2)
            ]
            for owner in owners:
                owner.live_task.cancel()
                await asyncio.gather(owner.live_task, return_exceptions=True)
            batched, serial = owners
            start = max(owner.live_clock.reading.mono_ns for owner in owners)
            deliveries = [
                live_trade_events(batched, instrument, i, start + i)
                for i in range(1, 66)
            ]
            for events in deliveries:
                batched.offer(events, ())
                await serial.work(serial._apply_live, events, (), len(repr(events)))
            batched.live_task = asyncio.create_task(batched.consume_live())
            async with asyncio.timeout(5):
                while batched.delivery < len(deliveries) or batched.live_bytes:
                    await asyncio.sleep(0.001)

            def checkpoint(owner):
                return tuple(observer.checkpoint() for observer in owner._observers())

            batched_state = await batched.work(checkpoint, batched)
            serial_state = await serial.work(checkpoint, serial)
            # Independent live clients deliberately have different source fences.
            # Keep exact context, alert and financial state equality; only the
            # visual source/scope IDs and their derived segment IDs may differ.
            assert batched_state[1:] == serial_state[1:]
            assert batched.source_identity != serial.source_identity
            axes = []
            market_states = []
            for owner, state in zip(owners, (batched_state, serial_state), strict=True):
                visual = loads(
                    state[0], CheckpointState, decimal_policy=DERIVED_DECIMAL_POLICY
                )
                *market, axis = visual
                market_states.append(market)
                axes.append(axis)
                assert axis[2] == owner.source_identity == owner.visuals.source_identity
                assert axis[4] == owner.visuals.clock_scope
                assert axis[10]
                assert axis[4] == digest(
                    (
                        axis[0],
                        axis[2],
                        owner.live_clock.reading.clock_id,
                        axis[3],
                        axis[10][0][2],
                    )
                )
                for segment_id, stamp, reference, _cause, _change in axis[10]:
                    assert segment_id == digest((axis[4], stamp, reference))
            assert market_states[0] == market_states[1]
            left, right = axes
            assert left[4] != right[4]
            assert {segment[0] for segment in left[10]}.isdisjoint(
                segment[0] for segment in right[10]
            )
            # Preserve projection policy, epoch, clock key, retained bounds,
            # truncation/change flags, every receive pair and unapplied evidence.
            assert left[:2] == right[:2]
            assert left[3] == right[3]
            assert left[5:10] == right[5:10]
            assert [segment[1:] for segment in left[10]] == [
                segment[1:] for segment in right[10]
            ]
            assert left[11:] == right[11:]

            request = SimpleNamespace(
                start_ns=str(start),
                end_ns=str(start + len(deliveries)),
                price_step="1",
                price_min=None,
                price_offset_rows=0,
                recenter=False,
                bucket_ns="1000000000",
                rows=32,
                max_columns=180,
                after_column=None,
            )
            views = []
            for owner, axis, state in zip(
                owners, axes, (batched_state, serial_state), strict=True
            ):
                snapshot = await owner.work(owner.visuals.query_snapshot)
                view = await owner.work(snapshot.query, request, 0)
                projected = view["time_axis"]
                assert projected.pop("source_identity") == owner.source_identity
                assert projected.pop("scope_id") == axis[4]
                assert projected["segments"]
                assert [segment.pop("id") for segment in projected["segments"]] == [
                    segment[0] for segment in axis[10]
                ]
                views.append(view)
                other = serial if owner is batched else batched
                assert not await other.work(other.visuals.accept_query, snapshot)
                with pytest.raises(
                    ValueError, match="incompatible visualization clock"
                ):
                    await other.work(other.visuals.restore, state[0])
            assert views[0] == views[1]
            for owner in owners:
                assert owner.delivery == len(deliveries)
                assert [batch.events for batch in owner.history] == deliveries
                assert [batch.delivery_index for batch in owner.history] == list(
                    range(1, len(deliveries) + 1)
                )
                assert owner.status == "live"
                assert owner.live_queue.empty()
                assert owner.live_bytes == 0
                assert owner.live_inflight == 0
        finally:
            await runtime.close()
            await registry.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_cutover", (False, True))
def test_live_selection_drains_new_source_while_departed_collectors_close(
    tmp_path, monkeypatch, cancel_cutover
):
    async def scenario():
        instruments = {
            spec.instrument.base.ticker: spec.instrument
            for spec in demo_source().streams
            if spec.instrument.venue == "hyperliquid"
            and spec.instrument.base.ticker in ("BTC", "SOL")
        }
        original, selected = instruments["BTC"], instruments["SOL"]
        runtime, registry, _ = live_runtime(tmp_path, instruments=(original, selected))
        entered, release = asyncio.Event(), asyncio.Event()
        finish = registry._finish_entry
        selection = None

        async def held_close(key, entry):
            if key.instrument_id == original.instrument_id:
                entered.set()
                await release.wait()
            await finish(key, entry)

        try:
            session = await runtime.create("live", None, original.instrument_id)
            monkeypatch.setattr(registry, "_finish_entry", held_close)
            selection = asyncio.create_task(
                runtime.select(session, str(session.generation), selected.instrument_id)
            )
            await asyncio.wait_for(entered.wait(), 5)
            feed = next(
                entry.supervisor
                for key, entry in registry._entries.items()
                if key.instrument_id == selected.instrument_id and key.channel == "book"
            )
            # The fixture owns its initial epoch/snapshot. Do not inject a
            # second decoder transaction until that owner has completed it.
            async with asyncio.timeout(5):
                while not feed.processed_frames:
                    await asyncio.sleep(0.001)
            for index in range(300):
                message = {
                    "channel": "l2Book",
                    "data": {
                        "coin": selected.native_symbol,
                        "time": 1700000000001 + index,
                        "levels": [
                            [{"px": "60000", "sz": str(index + 2), "n": 1}],
                            [{"px": "60001", "sz": "2", "n": 1}],
                        ],
                    },
                }
                feed._receive(
                    json.dumps(message).encode(),
                    "ws",
                    "wss://api.hyperliquid.xyz/ws",
                    feed.epoch,
                )
                raw = feed.queue.get_nowait()
                feed.queued_bytes -= len(raw.payload)
                await feed._process(raw)
            assert session.status == "loading"
            assert session.telemetry["overflows"] == 0
            async with asyncio.timeout(5):
                while session.live_queue.qsize() or session.live_inflight:
                    await asyncio.sleep(0.001)
            assert not selection.done()
            if cancel_cutover:
                selection.cancel()
            release.set()
            if cancel_cutover:
                with pytest.raises(asyncio.CancelledError):
                    await selection
                assert not registry.snapshot()
            else:
                frame = await selection
                assert frame["selection"]["instrument_id"] == selected.instrument_id
                assert frame["book"]["bids"][0]["quantity"] == "301"
            assert session.live_bytes == 0
            assert not any(
                status.key.instrument_id == original.instrument_id
                and status.key.channel in ("book", "trades")
                for status in registry.snapshot()
            )
        finally:
            release.set()
            if selection is not None:
                await asyncio.gather(selection, return_exceptions=True)
            await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_query", (False, True))
def test_visual_query_snapshot_does_not_block_live_ingestion_or_outlive_close(
    tmp_path, monkeypatch, cancel_query
):
    import httpx

    from src.workstation.api import create_app
    from src.workstation.visuals import VisualHistory

    async def scenario():
        runtime, registry, instrument = live_runtime(tmp_path)
        entered, release = threading.Event(), threading.Event()
        original_query = VisualHistory.query
        captured = {}
        request = None
        removal = None

        def held_query(history, options, generation):
            captured["now"] = history.clock.mono_ns
            entered.set()
            if not release.wait(15):
                raise RuntimeError("test visualization was not released")
            return original_query(history, options, generation)

        try:
            session = await runtime.create("live", "live", instrument.instrument_id)
            async with asyncio.timeout(5):
                while session.delivery < 4:
                    await asyncio.sleep(0.001)
            monkeypatch.setattr(VisualHistory, "query", held_query)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app(runtime)),
                base_url="http://127.0.0.1",
            ) as client:
                request = asyncio.create_task(
                    client.post(
                        "/api/v1/visualization",
                        json={
                            "session_id": session.id,
                            "generation": str(session.generation),
                        },
                    )
                )
                assert await asyncio.to_thread(entered.wait, 5)
                feed = next(
                    entry.supervisor
                    for key, entry in registry._entries.items()
                    if key.instrument_id == instrument.instrument_id
                    and key.channel == "book"
                )
                for index in range(300):
                    message = {
                        "channel": "l2Book",
                        "data": {
                            "coin": instrument.native_symbol,
                            "time": 1700000000001 + index,
                            "levels": [
                                [{"px": "60000", "sz": str(index + 2), "n": 1}],
                                [{"px": "60001", "sz": "2", "n": 1}],
                            ],
                        },
                    }
                    feed._receive(
                        json.dumps(message).encode(),
                        "ws",
                        "wss://api.hyperliquid.xyz/ws",
                        feed.epoch,
                    )
                    raw = feed.queue.get_nowait()
                    feed.queued_bytes -= len(raw.payload)
                    await feed._process(raw)
                assert session.status == "live"
                assert session.telemetry["overflows"] == 0
                async with asyncio.timeout(5):
                    while session.live_queue.qsize() or session.live_inflight:
                        await asyncio.sleep(0.001)
                frame = await session.frame()
                assert frame["book"]["bids"][0]["quantity"] == "301"
                assert not request.done()
                if cancel_query:
                    request.cancel()
                    removal = asyncio.create_task(runtime.remove(session.id))
                    await asyncio.sleep(0)
                    assert not request.done()
                    assert not removal.done()
                release.set()
                if cancel_query:
                    with pytest.raises(asyncio.CancelledError):
                        await request
                    await removal
                    assert session.closed
                    assert not registry.snapshot()
                else:
                    response = await request
                    assert response.status_code == 200
                    assert response.json()["now_ns"] == str(captured["now"])
                    assert session.visuals.clock.mono_ns > captured["now"]
                assert session.live_bytes == 0
        finally:
            release.set()
            if request is not None:
                await asyncio.gather(request, return_exceptions=True)
            if removal is not None:
                await asyncio.gather(removal, return_exceptions=True)
            await runtime.close()

    asyncio.run(scenario())


def test_catalog_serialization_cannot_stall_live_ingestion(tmp_path, monkeypatch):
    import httpx

    from src.workstation import api as api_module, runtime as runtime_module

    async def scenario():
        runtime, registry, instrument = live_runtime(tmp_path)
        runtime.source_instruments["live"] = (instrument,)
        entered, release, progressed = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        before_release = []
        original_dto = runtime_module.dto
        request = None

        def held_catalog(value):
            if isinstance(value, tuple) and value == (instrument,):
                entered.set()
                if not release.wait(15):
                    raise RuntimeError("test catalog was not released")
            return original_dto(value)

        def release_guard():
            if entered.wait(10):
                before_release.append(progressed.wait(5))
            release.set()

        guard = threading.Thread(target=release_guard)
        guard.start()
        try:
            session = await runtime.create("live", "live", instrument.instrument_id)
            async with asyncio.timeout(5):
                while session.delivery < 4:
                    await asyncio.sleep(0.001)
            monkeypatch.setattr(runtime_module, "dto", held_catalog)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=api_module.create_app(runtime)),
                base_url="http://127.0.0.1",
            ) as client:
                request = asyncio.create_task(
                    client.get("/api/v1/instruments", params={"source_id": "live"})
                )
                assert await asyncio.to_thread(entered.wait, 10)
                feed = next(
                    entry.supervisor
                    for key, entry in registry._entries.items()
                    if key.instrument_id == instrument.instrument_id
                    and key.channel == "book"
                )
                feed._receive(
                    json.dumps(
                        {
                            "channel": "l2Book",
                            "data": {
                                "coin": instrument.native_symbol,
                                "time": 1700000000001,
                                "levels": [
                                    [{"px": "60000", "sz": "7", "n": 1}],
                                    [{"px": "60001", "sz": "2", "n": 1}],
                                ],
                            },
                        }
                    ).encode(),
                    "ws",
                    "wss://api.hyperliquid.xyz/ws",
                    feed.epoch,
                )
                raw = feed.queue.get_nowait()
                feed.queued_bytes -= len(raw.payload)
                await feed._process(raw)
                async with asyncio.timeout(5):
                    while session.live_queue.qsize() or session.live_inflight:
                        await asyncio.sleep(0.001)
                frame = await session.frame()
                assert frame["book"]["bids"][0]["quantity"] == "7"
                progressed.set()
                response = await request
                assert response.status_code == 200
                assert response.json()["instruments"][0]["instrument_id"] == str(
                    instrument.instrument_id
                )
                await asyncio.to_thread(guard.join, 10)
                assert before_release == [True]
                assert session.telemetry["overflows"] == 0
        finally:
            progressed.set()
            release.set()
            await asyncio.to_thread(guard.join, 10)
            if request is not None:
                await asyncio.gather(request, return_exceptions=True)
            await runtime.close()

    asyncio.run(scenario())


def test_native_candle_query_releases_owner_and_rejects_obsolete_generation(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    import src.workstation.candles as candles

    async def scenario():
        runtime, registry, instrument = live_runtime(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()

        async def fetch(request, **kwargs):
            entered.set()
            await release.wait()
            return b"[]"

        monkeypatch.setattr(candles, "fetch_public", fetch)
        query = None
        try:
            session = await runtime.create("live", "live", instrument.instrument_id)
            request = SimpleNamespace(
                session_id=session.id,
                generation=str(session.generation),
                interval="1h",
                limit=300,
            )
            query = asyncio.create_task(runtime.candles(request))
            await asyncio.wait_for(entered.wait(), 5)
            assert not session.lock.locked() and not session.actions.locked()
            # Completed observer work remains available while HTTP is stalled.
            async with session.lock:
                assert await session.work(lambda: session.visuals is not None)
            async with session.actions, session.lock:
                session.generation += 1
            release.set()
            with pytest.raises(RuntimeFailure, match="obsolete"):
                await query
            assert runtime.candle_service.active == 0
            assert session.active_requests == 0
        finally:
            release.set()
            if query is not None:
                await asyncio.gather(query, return_exceptions=True)
            await runtime.close()
            await registry.close()

    asyncio.run(scenario())


def test_cancelled_native_candle_query_releases_capacity_without_cache(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    import src.workstation.candles as candles

    async def scenario():
        runtime, registry, instrument = live_runtime(tmp_path)
        entered = asyncio.Event()

        async def fetch(request, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(candles, "fetch_public", fetch)
        query = None
        try:
            session = await runtime.create("live", "live", instrument.instrument_id)
            query = asyncio.create_task(
                runtime.candles(
                    SimpleNamespace(
                        session_id=session.id,
                        generation=str(session.generation),
                        interval="1h",
                        limit=300,
                    )
                )
            )
            await asyncio.wait_for(entered.wait(), 5)
            query.cancel()
            with pytest.raises(asyncio.CancelledError):
                await query
            assert runtime.candle_service.active == 0
            assert not runtime.candle_service.cache
            assert session.active_requests == 0
            assert not session.lock.locked() and not session.actions.locked()
        finally:
            if query is not None:
                query.cancel()
                await asyncio.gather(query, return_exceptions=True)
            await runtime.close()
            await registry.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_capture", (False, True))
def test_capture_disk_work_keeps_live_delivery_and_lifecycle_fence(
    tmp_path, monkeypatch, cancel_capture
):
    from types import SimpleNamespace

    async def scenario():
        runtime, registry, instrument = live_runtime(tmp_path)
        entered, release = threading.Event(), threading.Event()
        capture = selection = None
        create = runtime.captures._create

        def held_create(*args):
            entered.set()
            if not release.wait(15):
                raise RuntimeError("capture disk worker was not released")
            return create(*args)

        async def drain(session):
            async with asyncio.timeout(5):
                while session.live_queue.qsize() or session.live_inflight:
                    await asyncio.sleep(0.001)

        try:
            session = await runtime.create("live", "live", instrument.instrument_id)
            async with asyncio.timeout(5):
                while session.delivery < 4:
                    await asyncio.sleep(0.001)
            await drain(session)
            before = session.delivery
            generation = str(session.generation)
            monkeypatch.setattr(runtime.captures, "_create", held_create)
            capture = asyncio.create_task(
                runtime.recording_action(
                    SimpleNamespace(
                        session_id=session.id, generation=generation, scope="instrument"
                    )
                )
            )
            assert await asyncio.to_thread(entered.wait, 5)
            if cancel_capture:
                capture.cancel()
                await asyncio.sleep(0)
                assert not capture.done()

            async def select_after_capture():
                async with session.activity(), session.actions:
                    return await runtime.select(
                        session, generation, instrument.instrument_id
                    )

            selection = asyncio.create_task(select_after_capture())
            feed = next(
                entry.supervisor
                for key, entry in registry._entries.items()
                if key.instrument_id == instrument.instrument_id
                and key.channel == "book"
            )
            # More than the unchanged queue count bound, delivered through the
            # native decoder/reducer while real recorder creation is pending.
            # Drain in bounded chunks, not by releasing the capture operation.
            for index in range(300):
                feed._receive(
                    json.dumps(
                        {
                            "channel": "l2Book",
                            "data": {
                                "coin": instrument.native_symbol,
                                "time": 1700000000001 + index,
                                "levels": [
                                    [{"px": "60000", "sz": str(index + 2), "n": 1}],
                                    [{"px": "60001", "sz": "2", "n": 1}],
                                ],
                            },
                        }
                    ).encode(),
                    "ws",
                    "wss://api.hyperliquid.xyz/ws",
                    feed.epoch,
                )
                raw = feed.queue.get_nowait()
                feed.queued_bytes -= len(raw.payload)
                await feed._process(raw)
                if index % 16 == 15:
                    await drain(session)
                    boundary = await session.frame()
                    assert boundary["book"]["bids"][0]["quantity"] == str(index + 2)
            await drain(session)
            frame = await session.frame()
            assert frame["status"] == "live"
            assert frame["book"]["bids"][0]["quantity"] == "301"
            assert session.delivery == before + 300
            assert [
                batch.delivery_index
                for batch in session.history
                if batch.delivery_index > before
            ] == list(range(before + 1, before + 301))
            assert session.live_bytes == session.live_inflight == 0
            assert session.telemetry["overflows"] == 0
            assert not capture.done() and not selection.done()
            assert str(session.generation) == generation

            release.set()
            if cancel_capture:
                with pytest.raises(asyncio.CancelledError):
                    await capture
            else:
                result = await capture
                assert result["recording_id"] in runtime.captures.active
            selected = await selection
            assert selected["generation"] == str(int(generation) + 1)
            # Cancellation joins the actual attachment before selection is
            # allowed to change its frozen scope; it never abandons a recorder.
            assert len(runtime.captures.active) == 1
            active = next(iter(runtime.captures.active.values()))
            assert active.owner == session.id
        finally:
            release.set()
            for task in (capture, selection):
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)
            await runtime.close()
            await registry.close()

    asyncio.run(scenario())
