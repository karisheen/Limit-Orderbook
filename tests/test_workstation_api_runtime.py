"""Real-source regressions for owner bootstrap, private creation and capture pins."""

import asyncio
import json
import threading
import time

import pytest

from src.workstation.adapter_catalog import AdapterCatalog
from src.workstation.adapters.hyperliquid import HyperliquidAdapter
from src.workstation.demo import create_demo, demo_source
from src.workstation.demo_memory import MemoryReplaySource
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
                        "coin": "BTC",
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
                            "coin": "BTC",
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


def live_runtime(tmp_path):
    instrument = next(
        spec.instrument
        for spec in demo_source().streams
        if spec.instrument.venue == "hyperliquid"
        and spec.instrument.base.ticker == "BTC"
    )
    catalog = AdapterCatalog()
    catalog.adapters["hyperliquid"] = HyperliquidAdapter()
    catalog.instruments.apply_metadata(
        MetadataSnapshot("runtime-fixture", 1, "synthetic", (instrument,))
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
