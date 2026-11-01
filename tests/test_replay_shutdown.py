"""Production owner shutdown must finish captured input before WebSocket close."""

import asyncio
from functools import partial
import json
import threading
from types import SimpleNamespace

import pytest

from src.workstation import ingestion
from src.workstation.adapter_catalog import AdapterCatalog
from src.workstation.adapters.hyperliquid import HyperliquidAdapter
from src.workstation.events import BookUpdate, Control, Gap, SubscriptionKey
from src.workstation.ingestion import FeedSupervisor, SupervisorConfig
from src.workstation.recording import Recorder, RecorderLimits, RecordingReader
from src.workstation.replay import ReplaySession
from src.workstation.subscriptions import RecorderBinding, SubscriptionRegistry


def catalog_fixture():
    adapter = HyperliquidAdapter()
    metadata = adapter.parse_metadata(
        {"collateralToken": 0, "universe": [{"name": "BTC", "szDecimals": 5}]},
        {
            "tokens": [
                {"name": "USDC", "index": 0, "tokenId": "0xusdc", "szDecimals": 8}
            ],
            "universe": [],
        },
        fetch_wall_ns=123,
        payload_hash="synthetic-shutdown-fixture",
    )
    catalog = AdapterCatalog()
    catalog.adapters["hyperliquid"] = adapter
    catalog.instruments.apply_metadata(metadata)
    (instrument,) = metadata.instruments
    return catalog, instrument


def snapshot(price):
    return json.dumps(
        {
            "channel": "l2Book",
            "data": {
                "coin": "BTC",
                "time": 1700000000000,
                "levels": [
                    [{"px": str(price), "sz": "1.00000", "n": 1}],
                    [{"px": str(price + 1), "sz": "2.00000", "n": 2}],
                ],
            },
        },
        separators=(",", ":"),
    ).encode()


class ClosingSocket:
    """Only the network transport is fake; close is an explicit scheduling fence."""

    def __init__(self, connector):
        self.connector = connector
        self.incoming = asyncio.Queue()
        self.close_entered = asyncio.Event()
        self.allow_close = asyncio.Event()
        self.closed = False

    async def __aenter__(self):
        self.connector.opened.put_nowait(self)
        return self

    async def __aexit__(self, *args):
        self.close_entered.set()
        if not self.connector.cleanup:
            await self.allow_close.wait()
        self.closed = True

    async def send(self, payload):
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.incoming.get()


class SocketConnector:
    def __init__(self):
        self.opened = asyncio.Queue()
        self.sockets = []
        self.cleanup = False

    def __call__(self, endpoint, **options):
        socket = ClosingSocket(self)
        self.sockets.append(socket)
        return socket

    def release(self):
        self.cleanup = True
        for socket in self.sockets:
            socket.allow_close.set()


class DecodeGate:
    """Hold a real decoder/reducer worker while the real reader fills its queue."""

    def __init__(self, owner, monkeypatch):
        self.loop = asyncio.get_running_loop()
        self.decode_reduce = owner._decode_reduce
        self.decoded = []
        self.arm()
        monkeypatch.setattr(owner, "_decode_reduce", self.decode)

    def arm(self):
        self.entered = asyncio.Event()
        self.release = threading.Event()

    def decode(self, frame):
        self.loop.call_soon_threadsafe(self.entered.set)
        if not self.release.wait(10):
            raise TimeoutError("test did not release native decoder")
        result = self.decode_reduce(frame)
        self.decoded.append(frame.ingest_ordinal)
        return result


def assert_normalized_once(events, raws):
    assert [
        event.ingest_ordinal
        for event in events
        if isinstance(event.payload, BookUpdate)
    ] == [frame.ingest_ordinal for frame in raws]


def assert_final_boundary(events, raws):
    assert_normalized_once(events, raws)
    gaps = [event for event in events if isinstance(event.payload, Gap)]
    assert len(gaps) == 1
    assert gaps[0] == events[-1]
    gap = gaps[0].payload
    assert gap.continuity_lost
    assert not gap.capture_lost
    assert gap.first_missing_ordinal is None
    assert gap.last_missing_ordinal is None
    assert gap.affected_streams == (raws[0].stream_id,)
    assert gaps[0].connection_epoch == raws[0].connection_epoch


def replay_trajectory(reader, mode):
    replay = ReplaySession(reader, mode=mode)
    trajectory = []
    try:
        while delivered := replay.step():
            trajectory.append(
                (
                    delivered,
                    replay.cursor,
                    replay.projection.state_hash,
                    replay.book_hash,
                    replay.clock.state_hash,
                )
            )
    finally:
        replay.close()
    return trajectory


@pytest.mark.parametrize("queued_count", [0, 2])
def test_browser_attach_then_binding_stop_drains_before_transport_close(
    tmp_path, monkeypatch, queued_count
):
    async def scenario():
        # Replace ingestion's reference, never time.monotonic globally: asyncio
        # scheduling and the real receive timestamps must retain their own clocks.
        clock = [100.0]
        real_time = ingestion.time
        monkeypatch.setattr(
            ingestion,
            "time",
            SimpleNamespace(
                monotonic=lambda: clock[0],
                monotonic_ns=real_time.monotonic_ns,
                time_ns=real_time.time_ns,
            ),
        )
        catalog, instrument = catalog_fixture()
        connector = SocketConnector()
        config = SupervisorConfig(stop_drain_timeout=30)
        registry = SubscriptionRegistry(
            catalog,
            config=config,
            supervisor_factory=partial(FeedSupervisor, ws_connect=connector),
            lock_path=tmp_path / "collector",
        )
        recorder = Recorder(
            tmp_path / "captures",
            session_id=registry.session_id,
            clock_id=registry.clock_id,
            config=registry.recording_config,
            limits=RecorderLimits(min_free_bytes=0, checkpoint_interval_batches=1),
            provenance="synthetic Hyperliquid native-schema shutdown fixture",
        )
        events = []
        captured = asyncio.Queue()
        registry.subscribe_events(events.extend)
        registry.subscribe_raw(captured.put_nowait)
        pending = []
        gate = None
        try:
            browser = await registry.acquire(
                "existing-browser", SubscriptionKey.for_instrument(instrument)
            )
            owner = registry._entries[browser.key].supervisor
            assert type(owner) is FeedSupervisor
            gate = DecodeGate(owner, monkeypatch)
            stop_requested = asyncio.Event()
            request_stop = owner.request_stop

            def observe_stop():
                request_stop()
                stop_requested.set()

            monkeypatch.setattr(owner, "request_stop", observe_stop)
            old_events = None
            old_raws = None
            first_epoch = None
            binding = None
            for phase in ("attach", "stop"):
                socket = await asyncio.wait_for(connector.opened.get(), 5)
                epoch = owner.epoch
                if phase == "attach":
                    first_epoch = epoch
                else:
                    assert epoch == first_epoch + 1
                    assert registry._entries[browser.key].supervisor is owner
                    assert owner.capture_complete
                    # Keep the same owner, but let the recorder own the final stop.
                    await registry.release_client("existing-browser")
                    assert registry.snapshot()[0].reference_count == 1
                    gate.arm()
                    stop_requested.clear()

                socket.incoming.put_nowait(snapshot(60000 + epoch * 10))
                await asyncio.wait_for(gate.entered.wait(), 5)
                raws = [await asyncio.wait_for(captured.get(), 5)]
                for index in range(queued_count):
                    socket.incoming.put_nowait(snapshot(60002 + epoch * 10 + index * 2))
                for _ in range(queued_count):
                    raws.append(await asyncio.wait_for(captured.get(), 5))
                assert owner.queue.qsize() == queued_count
                assert owner._pending_ordinal == raws[0].ingest_ordinal
                producers = tuple(owner._producer_tasks)
                assert producers
                closing = asyncio.create_task(
                    RecorderBinding.start(registry, recorder, (browser.key,))
                    if phase == "attach"
                    else binding.stop()
                )
                pending.append(closing)
                await asyncio.wait_for(stop_requested.wait(), 5)
                assert not closing.done()
                gate.release.set()
                await asyncio.wait_for(socket.close_entered.wait(), 5)

                # These assertions are deliberately inside the close handshake,
                # not after _shutdown has had a chance to repair missing work.
                epoch_events = tuple(
                    event for event in events if event.connection_epoch == epoch
                )
                assert_normalized_once(epoch_events, raws)
                assert all(task.done() for task in producers)
                assert owner.queue.empty()
                assert owner.queued_bytes == 0
                assert owner._pending_ordinal is None
                assert owner._first_missing_ordinal is None
                assert owner._last_missing_ordinal is None
                assert owner.capture_complete
                assert not any(isinstance(event.payload, Gap) for event in epoch_events)
                assert not closing.done()
                assert not socket.closed
                assert [
                    ordinal
                    for ordinal in gate.decoded
                    if ordinal in {frame.ingest_ordinal for frame in raws}
                ] == [frame.ingest_ordinal for frame in raws]

                # Transport teardown alone exhausts the drain budget. Required
                # normalization has already finished and must not become loss.
                clock[0] += config.stop_drain_timeout + 1
                socket.allow_close.set()
                result = await asyncio.wait_for(asyncio.shield(closing), 5)
                assert socket.closed
                assert owner.capture_complete
                epoch_events = tuple(
                    event for event in events if event.connection_epoch == epoch
                )
                assert_final_boundary(epoch_events, raws)
                if phase == "attach":
                    binding = result
                    old_events, old_raws = epoch_events, tuple(raws)
                    assert binding.leases[0].stream_id == browser.stream_id
                else:
                    manifest = result
                    assert await binding.stop() is manifest
                    assert manifest.complete, manifest.incomplete_reasons
                    assert manifest.raw_count == len(raws)
                    assert owner.shutdown_slow
                    assert not owner._running
                    assert not registry.snapshot()
                    # A second stop must neither rewrite nor poison the first
                    # epoch's already-published completeness and boundary truth.
                    assert (
                        tuple(
                            event
                            for event in events
                            if event.connection_epoch == first_epoch
                        )
                        == old_events
                    )
                    assert_final_boundary(old_events, old_raws)
                    with RecordingReader(recorder.path) as reader:
                        assert reader.manifest.complete
                        assert tuple(reader.iter_raw()) == tuple(raws)
                        recorded_events = tuple(
                            event
                            for batch in reader.iter_batches()
                            for event in batch.events
                        )
                        assert recorded_events == epoch_events
                        assert isinstance(recorded_events[0].payload, Control)
                        assert recorded_events[0].payload.kind == "source_change"
                        assert recorded_events[0].payload.detail != "capture_incomplete"
                        assert recorded_events[0].connection_epoch == first_epoch + 1
                        assert_final_boundary(recorded_events, raws)
                        assert replay_trajectory(reader, "raw") == replay_trajectory(
                            reader, "normalized"
                        )
        finally:
            connector.release()
            if gate is not None:
                gate.release.set()
            await asyncio.gather(*pending, return_exceptions=True)
            await registry.close()
            await recorder.finalize()

    asyncio.run(scenario())
