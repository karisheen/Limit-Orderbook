"""Offline durable recorder leases and deterministic client lifecycle races."""

import asyncio
from dataclasses import replace
import json
import threading
from types import SimpleNamespace

import pytest

from src.workstation import ingestion
from src.workstation.adapter_catalog import AdapterCatalog
from src.workstation.adapters.hyperliquid import HyperliquidAdapter
from src.workstation.events import BookUpdate, Control, Gap, SubscriptionKey
from src.workstation.ingestion import FeedSupervisor, SupervisorConfig
from src.workstation.quality import ClockReading, evaluate
from src.workstation.recording import Recorder, RecorderLimits, RecordingReader
from src.workstation.replay import ReplaySession
from src.workstation.subscriptions import RecorderBinding, SubscriptionRegistry


def catalog_fixture():
    adapter = HyperliquidAdapter()
    metadata = adapter.parse_metadata(
        {
            "collateralToken": 0,
            "universe": [
                {"name": "BTC", "szDecimals": 5},
                {"name": "ETH", "szDecimals": 4},
            ],
        },
        {
            "tokens": [
                {"name": "USDC", "index": 0, "tokenId": "0xusdc", "szDecimals": 8}
            ],
            "universe": [],
        },
        fetch_wall_ns=123,
        payload_hash="synthetic-binding-fixture",
    )
    catalog = AdapterCatalog()
    catalog.adapters["hyperliquid"] = adapter
    catalog.instruments.apply_metadata(metadata)
    instruments = {item.native_symbol: item for item in metadata.instruments}
    return catalog, instruments


def snapshot(symbol="BTC", price=60000):
    return json.dumps(
        {
            "channel": "l2Book",
            "data": {
                "coin": symbol,
                "time": 1700000000000,
                "levels": [
                    [{"px": str(price), "sz": "1.00000", "n": 1}],
                    [{"px": str(price + 1), "sz": "2.00000", "n": 2}],
                ],
            },
        },
        separators=(",", ":"),
    ).encode()


class ControlledSupervisor(FeedSupervisor):
    """Native adapter/reducer path with explicit, network-free source scheduling."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.started = asyncio.Queue()
        self.commands = asyncio.Queue()
        self.stopping = asyncio.Event()
        self.stop_requested = asyncio.Event()
        self.allow_stop = asyncio.Event()
        self.allow_stop.set()

    def request_stop(self):
        super().request_stop()
        self.stop_requested.set()

    async def run(self):
        self._running = True
        self.stop_requested.clear()
        self._start_epoch(False)
        self.started.put_nowait(self.epoch)
        try:
            while True:
                payload, delivered = await self.commands.get()
                try:
                    self._receive(
                        payload, "ws", "wss://api.hyperliquid.xyz/ws", self.epoch
                    )
                    frame = self.queue.get_nowait()
                    self.queued_bytes -= len(frame.payload)
                    await self._process(frame)
                except BaseException as exc:
                    if not delivered.done():
                        delivered.set_exception(exc)
                    raise
                else:
                    delivered.set_result(frame)
        finally:
            self.stopping.set()
            await self.allow_stop.wait()
            await self._shutdown()

    async def emit(self, payload):
        delivered = asyncio.get_running_loop().create_future()
        self.commands.put_nowait((payload, delivered))
        return await asyncio.wait_for(delivered, 5)


def registry_fixture(tmp_path, *, max_clients=1024, config=None):
    catalog, instruments = catalog_fixture()
    registry = SubscriptionRegistry(
        catalog,
        config=config,
        supervisor_factory=ControlledSupervisor,
        lock_path=tmp_path / "collector",
        max_clients=max_clients,
    )
    return registry, instruments


def recorder_fixture(tmp_path, registry, **options):
    return Recorder(
        tmp_path / "captures",
        session_id=registry.session_id,
        clock_id=registry.clock_id,
        config=registry.recording_config,
        limits=RecorderLimits(min_free_bytes=0, checkpoint_interval_batches=1),
        provenance="synthetic Hyperliquid source-schema binding fixture",
        **options,
    )


class DecodeBarrier:
    """Pause a real decode worker without blocking the event-loop owner."""

    def __init__(self, owner, monkeypatch):
        self.entered = asyncio.Event()
        self.release = threading.Event()
        loop = asyncio.get_running_loop()
        decode_reduce = owner._decode_reduce

        def blocked(frame):
            loop.call_soon_threadsafe(self.entered.set)
            if not self.release.wait(5):
                raise TimeoutError("test did not release decode worker")
            return decode_reduce(frame)

        monkeypatch.setattr(owner, "_decode_reduce", blocked)


def assert_complete_drain(recorder, manifest, raws, instrument):
    assert manifest.complete, manifest.incomplete_reasons
    assert manifest.raw_count == len(raws)
    with RecordingReader(recorder.path) as reader:
        assert tuple(reader.iter_raw()) == tuple(raws)
        events = tuple(
            event for batch in reader.iter_batches() for event in batch.events
        )
        updates = tuple(
            event for event in events if isinstance(event.payload, BookUpdate)
        )
        assert tuple(event.ingest_ordinal for event in updates) == tuple(
            frame.ingest_ordinal for frame in raws
        )
        for event in updates:
            assert (
                event.receive_mono_ns
                <= event.process_start_mono_ns
                <= event.process_end_mono_ns
                <= event.publish_mono_ns
            )
        assert isinstance(events[-1].payload, Gap)
        assert events[-1].payload.reason_code == "owner_stopped"
        assert all(
            not event.payload.capture_lost
            for event in events
            if isinstance(event.payload, Gap)
        )
        trajectories = []
        for mode in ("raw", "normalized"):
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
                    if any(
                        isinstance(event.payload, BookUpdate)
                        for event in delivered[0].events
                    ):
                        (book,) = replay.books()
                        eligible = evaluate(
                            book, instrument, "comparison", replay.clock.reading
                        )
                        assert eligible.eligible, eligible.reasons
                assert all(
                    not evaluate(
                        book, instrument, "comparison", replay.clock.reading
                    ).eligible
                    for book in replay.books()
                )
            finally:
                replay.close()
            trajectories.append(trajectory)
        assert trajectories[0] == trajectories[1]


def test_durable_binding_restarts_existing_owner_and_survives_browser_close(tmp_path):
    async def scenario():
        registry, instruments = registry_fixture(tmp_path)
        key = SubscriptionKey.for_instrument(instruments["BTC"])
        other_key = SubscriptionKey.for_instrument(instruments["ETH"])
        recorder = recorder_fixture(tmp_path, registry)
        try:
            queue = registry.presentation_queue("tab-a")
            first = await registry.acquire("tab-a", key)
            second = await registry.acquire("tab-b", key)
            owner = registry._entries[first.key].supervisor
            epoch = await asyncio.wait_for(owner.started.get(), 5)
            prehistory = await owner.emit(snapshot(price=59000))
            assert (await queue.get()).book.quality.integrity == "valid"
            other = await registry.acquire("unselected", other_key)
            other_owner = registry._entries[other.key].supervisor
            await asyncio.wait_for(other_owner.started.get(), 5)

            binding = await RecorderBinding.start(registry, recorder, (key, first.key))
            (lease,) = binding.leases
            assert lease.stream_id == first.stream_id == second.stream_id
            assert lease.key.depth == 20
            spec = registry.describe(lease)
            assert spec.instrument == instruments["BTC"]
            assert spec.key == lease.key
            assert spec.adapter_config == (("dex", ""),)
            assert (await queue.get()).book.quality.integrity == "invalid"
            assert await asyncio.wait_for(owner.started.get(), 5) == epoch + 1
            assert registry.snapshot()[0].reference_count == 3

            first_raw = await owner.emit(snapshot(price=60000))
            await registry.release_client("tab-a")
            await registry.release_client("tab-b")
            assert queue.closed
            assert registry._entries[lease.key].leases == {lease.lease_id}
            second_raw = await owner.emit(snapshot(price=60002))
            await other_owner.emit(snapshot("ETH", 3000))
            manifest = await binding.stop()
            assert await binding.stop() is manifest
            assert manifest.complete, manifest.incomplete_reasons
            assert manifest.raw_count == 2
            assert manifest.checkpoint_count > 0
            assert manifest.streams == (spec,)
            assert lease.key not in registry._entries
            assert not registry._raw_sinks and not registry._event_sinks
            assert binding.client_id not in registry._client_locks
            with RecordingReader(recorder.path) as reader:
                raws = tuple(reader.iter_raw())
                assert raws == (first_raw, second_raw)
                assert prehistory not in raws
                batches = tuple(reader.iter_batches())
                events = tuple(event for batch in batches for event in batch.events)
                assert all(event.stream_id == lease.stream_id for event in events)
                assert isinstance(events[0].payload, Control)
                assert events[0].payload.kind == "source_change"
                assert events[0].connection_epoch == epoch + 1
                assert (
                    sum(isinstance(event.payload, BookUpdate) for event in events) == 2
                )
                assert isinstance(events[-1].payload, Gap)
                assert events[-1].payload.reason_code == "owner_stopped"
                assert tuple(reader.iter_checkpoints())
        finally:
            await registry.close()
            await recorder.finalize()

    asyncio.run(scenario())


def test_fresh_binding_registers_before_first_source_event(tmp_path):
    async def scenario():
        registry, instruments = registry_fixture(tmp_path)
        recorder = recorder_fixture(tmp_path, registry)
        key = SubscriptionKey.for_instrument(instruments["BTC"])
        try:
            binding = await RecorderBinding.start(registry, recorder, (key,))
            owner = registry._entries[binding.leases[0].key].supervisor
            await asyncio.wait_for(owner.started.get(), 5)
            await owner.emit(snapshot())
            manifest = await binding.stop()
            assert manifest.complete, manifest.incomplete_reasons
            with RecordingReader(recorder.path) as reader:
                first = next(reader.iter_batches()).events[0]
                assert first.payload.kind == "source_change"
                assert first.stream_id == manifest.streams[0].stream_id
        finally:
            await registry.close()
            await recorder.finalize()

    asyncio.run(scenario())


def test_client_capacity_retires_generations_and_failed_operations(tmp_path):
    async def scenario():
        registry, instruments = registry_fixture(tmp_path, max_clients=1)
        key = SubscriptionKey.for_instrument(instruments["BTC"])
        try:
            for index in range(12):
                client = f"tab-{index}"
                queue = registry.presentation_queue(client)
                await registry.replace(client, frozenset({key}), 10)
                await registry.release_client(client)
                assert queue.closed
                assert not registry._client_locks
                assert not registry._generations
                assert not registry._client_operations
            with pytest.raises(LookupError):
                await registry.acquire("bad-key", replace(key, instrument_id="missing"))
            with pytest.raises(ValueError, match="capacity"):
                registry.presentation_queue("bad-queue", capacity=0)
            assert not registry._client_locks
            lease = await registry.acquire("last", key)
            await registry.release(lease.lease_id)
            assert not registry._client_locks
        finally:
            await registry.close()

    asyncio.run(scenario())


def test_client_lock_survives_queued_operations_and_cancelled_waiter(tmp_path):
    async def scenario():
        registry, instruments = registry_fixture(tmp_path, max_clients=1)
        key = SubscriptionKey.for_instrument(instruments["BTC"])
        try:
            lease = await registry.acquire("client", key)
            owner = registry._entries[lease.key].supervisor
            await asyncio.wait_for(owner.started.get(), 5)
            owner.allow_stop.clear()
            original_lock = registry._client_locks[lease.client_id]
            releasing = asyncio.create_task(registry.release_client("client"))
            await asyncio.wait_for(owner.stopping.wait(), 5)
            acquiring = asyncio.create_task(registry.acquire("client", key))
            cancelled = asyncio.create_task(registry.release_client("client"))
            await asyncio.sleep(0)
            assert registry._client_operations[lease.client_id] == 3
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled
            assert registry._client_operations[lease.client_id] == 2
            assert registry._client_locks[lease.client_id] is original_lock
            with pytest.raises(ValueError, match="capacity"):
                await registry.acquire("other", key)
            owner.allow_stop.set()
            await releasing
            replacement = await acquiring
            assert replacement.stream_id != lease.stream_id
            assert replacement.generation == 1
            assert registry._client_locks[lease.client_id] is original_lock
            await registry.release_client("client")
            assert not registry._client_locks
            assert not registry._generations
        finally:
            for entry in registry._entries.values():
                entry.supervisor.allow_stop.set()
            await registry.close()

    asyncio.run(scenario())


def test_stop_cancellation_joins_owner_finalizes_and_preserves_other_lease(tmp_path):
    async def scenario():
        registry, instruments = registry_fixture(tmp_path)
        key = SubscriptionKey.for_instrument(instruments["BTC"])
        recorder = recorder_fixture(tmp_path, registry)
        try:
            binding = await RecorderBinding.start(registry, recorder, (key,))
            browser = await registry.acquire("browser", key)
            owner = registry._entries[browser.key].supervisor
            await asyncio.wait_for(owner.started.get(), 5)
            await owner.emit(snapshot())
            owner.allow_stop.clear()
            stopping = asyncio.create_task(binding.stop())
            await asyncio.wait_for(owner.stopping.wait(), 5)
            stopping.cancel()
            await asyncio.sleep(0)
            assert not stopping.done()
            owner.allow_stop.set()
            with pytest.raises(asyncio.CancelledError):
                await stopping
            manifest = await binding.stop()
            assert manifest.complete, manifest.incomplete_reasons
            assert registry.describe(browser).stream_id == browser.stream_id
            assert await asyncio.wait_for(owner.started.get(), 5) == 2
            await owner.emit(snapshot(price=61000))
            with RecordingReader(recorder.path) as reader:
                assert len(tuple(reader.iter_raw())) == 1
        finally:
            for entry in registry._entries.values():
                entry.supervisor.allow_stop.set()
            await registry.close()
            await recorder.finalize()

    asyncio.run(scenario())


def test_binding_rejects_mismatched_provenance_and_config_without_leases(tmp_path):
    async def scenario():
        registry, instruments = registry_fixture(
            tmp_path, config=SupervisorConfig(dedup_capacity=123)
        )
        key = SubscriptionKey.for_instrument(instruments["BTC"])
        recorder = Recorder(
            tmp_path / "captures",
            session_id=registry.session_id,
            clock_id=registry.clock_id,
            limits=RecorderLimits(min_free_bytes=0),
        )
        try:
            with pytest.raises(ValueError, match="config"):
                await RecorderBinding.start(registry, recorder, (key,))
            recorder.clock_id = "different-clock"
            with pytest.raises(ValueError, match="session/clock"):
                await RecorderBinding.start(registry, recorder, (key,))
            assert not registry.snapshot()
            assert not registry._client_locks
            assert not registry._raw_sinks and not registry._event_sinks
        finally:
            await registry.close()
            await recorder.finalize()

    asyncio.run(scenario())


def test_cancelled_attach_restarts_existing_owner_without_leaking_recorder(tmp_path):
    async def scenario():
        registry, instruments = registry_fixture(tmp_path)
        key = SubscriptionKey.for_instrument(instruments["BTC"])
        recorder = recorder_fixture(tmp_path, registry)
        try:
            browser = await registry.acquire("browser", key)
            owner = registry._entries[browser.key].supervisor
            await asyncio.wait_for(owner.started.get(), 5)
            await owner.emit(snapshot())
            queue = registry.presentation_queue("browser")
            owner.allow_stop.clear()
            attaching = asyncio.create_task(
                RecorderBinding.start(registry, recorder, (key,), "capture")
            )
            await asyncio.wait_for(owner.stopping.wait(), 5)
            invalid = await asyncio.wait_for(queue.get(), 5)
            assert invalid.book.quality.integrity == "invalid"
            assert invalid.book.quality.reason_codes == ("recorder_attach",)
            attaching.cancel()
            await asyncio.sleep(0)
            assert not attaching.done()
            owner.allow_stop.set()
            with pytest.raises(asyncio.CancelledError):
                await attaching
            assert await asyncio.wait_for(owner.started.get(), 5) == 2
            assert registry.describe(browser).stream_id == browser.stream_id
            assert "capture" not in registry._client_locks
            assert not registry._raw_sinks and not registry._event_sinks
            manifest = await recorder.finalize()
            assert not manifest.complete
            assert "recorder_attach_failed" in manifest.incomplete_reasons
            await owner.emit(snapshot(price=62000))
        finally:
            for entry in registry._entries.values():
                entry.supervisor.allow_stop.set()
            await registry.close()
            await recorder.finalize()

    asyncio.run(scenario())


def test_stop_drains_captured_queue_without_inflight_worker(tmp_path):
    async def scenario():
        registry, instruments = registry_fixture(tmp_path)
        key = SubscriptionKey.for_instrument(instruments["BTC"])
        recorder = recorder_fixture(tmp_path, registry)
        try:
            binding = await RecorderBinding.start(registry, recorder, (key,))
            lease = binding.leases[0]
            owner = registry._entries[lease.key].supervisor
            await asyncio.wait_for(owner.started.get(), 5)
            owner._receive(
                snapshot(), "ws", "wss://api.hyperliquid.xyz/ws", owner.epoch
            )
            manifest = await binding.stop()
            assert manifest.complete, manifest.incomplete_reasons
            assert manifest.raw_count == 1
            with pytest.raises(ValueError, match="current"):
                registry.describe(lease)
            with RecordingReader(recorder.path) as reader:
                (raw,) = reader.iter_raw()
                events = tuple(
                    event for batch in reader.iter_batches() for event in batch.events
                )
                assert [
                    event.ingest_ordinal
                    for event in events
                    if isinstance(event.payload, BookUpdate)
                ] == [raw.ingest_ordinal]
                assert isinstance(events[-1].payload, Gap)
                assert not events[-1].payload.capture_lost
        finally:
            await registry.close()
            await recorder.finalize()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["stop", "close", "attach"])
def test_graceful_owner_boundary_drains_inflight_and_queue(
    tmp_path, monkeypatch, operation
):
    async def scenario():
        registry, instruments = registry_fixture(tmp_path)
        instrument = instruments["BTC"]
        key = SubscriptionKey.for_instrument(instrument)
        recorder = recorder_fixture(tmp_path, registry)
        second_recorder = None
        barrier = None
        pending = []
        try:
            binding = await RecorderBinding.start(registry, recorder, (key,))
            owner = registry._entries[binding.leases[0].key].supervisor
            await asyncio.wait_for(owner.started.get(), 5)
            raws, published = [], []
            registry.subscribe_raw(raws.append)
            publish = owner.callbacks.book

            def observe(book):
                published.append(book)
                publish(book)

            owner.callbacks = replace(owner.callbacks, book=observe)
            barrier = DecodeBarrier(owner, monkeypatch)
            pending.append(asyncio.create_task(owner.emit(snapshot())))
            await asyncio.wait_for(barrier.entered.wait(), 5)
            for price in (60002, 60004):
                owner._receive(
                    snapshot(price=price),
                    "ws",
                    "wss://api.hyperliquid.xyz/ws",
                    owner.epoch,
                )
            assert len(raws) == 3
            assert owner.queue.qsize() == 2
            assert owner._pending_ordinal == raws[0].ingest_ordinal
            if operation == "attach":
                second_recorder = recorder_fixture(tmp_path / "second", registry)
                closing = asyncio.create_task(
                    RecorderBinding.start(registry, second_recorder, (key,))
                )
            else:
                closing = asyncio.create_task(
                    binding.stop() if operation == "stop" else registry.close()
                )
            pending.append(closing)
            await asyncio.wait_for(owner.stop_requested.wait(), 5)
            assert not closing.done()
            # Captures arriving after the ownership fence must not be admitted.
            cutoff_ordinal = registry.boundary.ordinal
            owner._receive(
                snapshot(price=70000), "ws", "wss://api.hyperliquid.xyz/ws", owner.epoch
            )
            assert registry.boundary.ordinal == cutoff_ordinal
            assert len(raws) == 3
            assert owner.queue.qsize() == 2
            barrier.release.set()
            result = await asyncio.wait_for(asyncio.shield(closing), 5)
            await asyncio.gather(pending[0], return_exceptions=True)
            if operation == "attach":
                await asyncio.wait_for(owner.started.get(), 5)
                assert not owner._stop_requested
                attached_manifest = await result.stop()
                assert attached_manifest.complete, attached_manifest.incomplete_reasons
                assert attached_manifest.raw_count == 0
            manifest = await binding.stop()
            valid_books = [
                book for book in published if book.quality.integrity == "valid"
            ]
            assert len(valid_books) == 3
            for book in valid_books:
                eligible = evaluate(
                    book,
                    instrument,
                    "comparison",
                    ClockReading(
                        registry.clock_id, book.quality.last_book_observation_mono_ns
                    ),
                )
                assert eligible.eligible, eligible.reasons
            assert published[-1].quality.integrity == "invalid"
            assert_complete_drain(recorder, manifest, raws, instrument)
        finally:
            if barrier is not None:
                barrier.release.set()
            await asyncio.gather(*pending, return_exceptions=True)
            await registry.close()
            await recorder.finalize()
            if second_recorder is not None:
                await second_recorder.finalize()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["fault", "deadline", "deadline_single"])
@pytest.mark.parametrize("operation", ["stop", "close"])
def test_owner_drain_distinguishes_capture_loss_from_slow_completion(
    tmp_path, monkeypatch, failure, operation
):
    async def scenario():
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
        registry, instruments = registry_fixture(
            tmp_path, config=SupervisorConfig(stop_drain_timeout=0.01)
        )
        key = SubscriptionKey.for_instrument(instruments["BTC"])
        recorder = recorder_fixture(tmp_path, registry)
        barrier = None
        pending = []
        try:
            binding = await RecorderBinding.start(registry, recorder, (key,))
            owner = registry._entries[binding.leases[0].key].supervisor
            await asyncio.wait_for(owner.started.get(), 5)
            raws = []
            registry.subscribe_raw(raws.append)
            barrier = DecodeBarrier(owner, monkeypatch)
            pending.append(asyncio.create_task(owner.emit(snapshot())))
            await asyncio.wait_for(barrier.entered.wait(), 5)
            queued_prices = () if failure == "deadline_single" else (60002, 60004)
            for price in queued_prices:
                owner._receive(
                    snapshot(price=price),
                    "ws",
                    "wss://api.hyperliquid.xyz/ws",
                    owner.epoch,
                )
            assert owner.queue.qsize() == len(queued_prices)
            if failure == "fault":
                owner._set_fault("test_fault")
            closing = asyncio.create_task(
                binding.stop() if operation == "stop" else registry.close()
            )
            pending.append(closing)
            await asyncio.wait_for(owner.stop_requested.wait(), 5)
            assert not closing.done()
            if failure != "fault":
                clock[0] += 1.0
            barrier.release.set()
            await asyncio.wait_for(asyncio.shield(closing), 5)
            await asyncio.gather(pending[0], return_exceptions=True)
            manifest = await binding.stop()
            if failure == "deadline_single":
                assert owner.capture_complete
                assert owner.shutdown_slow
                assert owner.reason == "shutdown_slow"
                assert len(raws) == 1
                assert_complete_drain(recorder, manifest, raws, instruments["BTC"])
                return
            assert not manifest.complete
            assert manifest.raw_count == 3
            assert not owner.capture_complete
            with RecordingReader(recorder.path, allow_incomplete=True) as reader:
                assert tuple(reader.iter_raw()) == tuple(raws)
                events = tuple(
                    event for batch in reader.iter_batches() for event in batch.events
                )
                normalized_ordinals = [
                    event.ingest_ordinal
                    for event in events
                    if isinstance(event.payload, BookUpdate)
                ]
                assert normalized_ordinals == (
                    [] if failure == "fault" else [raws[0].ingest_ordinal]
                )
                gap = events[-1].payload
                assert isinstance(gap, Gap)
                assert gap.capture_lost
                first_missing = raws[0] if failure == "fault" else raws[1]
                assert gap.first_missing_ordinal == first_missing.ingest_ordinal
                assert gap.last_missing_ordinal == raws[-1].ingest_ordinal
        finally:
            if barrier is not None:
                barrier.release.set()
            await asyncio.gather(*pending, return_exceptions=True)
            await registry.close()
            await recorder.finalize()

    asyncio.run(scenario())


@pytest.mark.parametrize("close_first", [False, True])
def test_registry_close_and_binding_stop_share_owner_join(tmp_path, close_first):
    async def scenario():
        registry, instruments = registry_fixture(tmp_path)
        recorder = recorder_fixture(tmp_path, registry)
        key = SubscriptionKey.for_instrument(instruments["BTC"])
        try:
            binding = await RecorderBinding.start(registry, recorder, (key,))
            owner = registry._entries[binding.leases[0].key].supervisor
            await asyncio.wait_for(owner.started.get(), 5)
            await owner.emit(snapshot())
            calls = [registry.close(), binding.stop()]
            if not close_first:
                calls.reverse()
            await asyncio.wait_for(asyncio.gather(*calls), 5)
            manifest = await binding.stop()
            assert manifest.complete, manifest.incomplete_reasons
            assert not registry.snapshot()
            assert not registry._client_locks
            assert not registry._client_operations
            assert not registry._raw_sinks and not registry._event_sinks
            with RecordingReader(recorder.path) as reader:
                assert len(tuple(reader.iter_raw())) == 1
        finally:
            await registry.close()
            await recorder.finalize()

    asyncio.run(scenario())


def test_attach_records_duplicate_trade_evidence_without_prior_history(tmp_path):
    async def scenario():
        registry, instruments = registry_fixture(tmp_path)
        recorder = recorder_fixture(tmp_path, registry)
        key = SubscriptionKey.for_instrument(instruments["BTC"], channel="trades")
        payload = json.dumps(
            {
                "channel": "trades",
                "data": [
                    {
                        "coin": "BTC",
                        "side": "A",
                        "px": "60000.0",
                        "sz": "0.00001",
                        "time": 1700000000000,
                        "tid": 77,
                        "hash": "0x0",
                        "users": ["buyer", "seller"],
                    }
                ],
            }
        ).encode()
        try:
            browser = await registry.acquire("browser", key)
            owner = registry._entries[browser.key].supervisor
            await asyncio.wait_for(owner.started.get(), 5)
            await owner.emit(payload)
            binding = await RecorderBinding.start(registry, recorder, (key,))
            await asyncio.wait_for(owner.started.get(), 5)
            await owner.emit(payload)
            manifest = await binding.stop()
            assert manifest.complete, manifest.incomplete_reasons
            with RecordingReader(recorder.path) as reader:
                assert any(
                    isinstance(event.payload, Control)
                    and event.payload.reason_code == "duplicate_trade"
                    for batch in reader.iter_batches()
                    for event in batch.events
                )
        finally:
            await registry.close()
            await recorder.finalize()

    asyncio.run(scenario())


def test_processed_delivery_is_atomic_across_streams_resets_and_owner_close(tmp_path):
    async def scenario():
        registry, instruments = registry_fixture(tmp_path)
        observed = []

        def observe(events, books):
            observed.append((events, books))

        registry.subscribe_processed(observe)
        try:
            leases = {
                name: await registry.acquire(
                    "observer", SubscriptionKey.for_instrument(instrument)
                )
                for name, instrument in instruments.items()
            }
            owners = {
                name: registry._entries[lease.key].supervisor
                for name, lease in leases.items()
            }
            for owner in owners.values():
                await owner.started.get()
            await owners["BTC"].emit(snapshot("BTC", 60000))
            await owners["ETH"].emit(snapshot("ETH", 3000))
            await owners["BTC"].emit(snapshot("BTC", 60005))
            updates = [
                {book.instrument_id: str(book.bids[0].price) for book in books}
                for events, books in observed
                if isinstance(events[0].payload, BookUpdate)
            ]
            btc, eth = (instruments[name].instrument_id for name in ("BTC", "ETH"))
            assert updates == [
                {btc: "60000"},
                {btc: "60000", eth: "3000"},
                {btc: "60005", eth: "3000"},
            ]
            owners["BTC"]._start_epoch(False)
            events, books = observed[-1]
            assert events[0].payload.kind == "source_change"
            assert {book.instrument_id for book in books} == {eth}
            await owners["BTC"].emit(snapshot("BTC", 60007))
            await registry.release(leases["BTC"].lease_id)
            events, books = observed[-1]
            assert isinstance(events[0].payload, Gap)
            closed_book = next(book for book in books if book.instrument_id == btc)
            assert closed_book.bids[0].price == 60007
            assert closed_book.quality.integrity == "invalid"
            assert {book.instrument_id for book in registry.books()} == {eth}
        finally:
            await registry.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("failed_consumer", ["events", "processed"])
def test_failed_consumer_cannot_hide_completed_book_or_leave_current_evidence(
    tmp_path, failed_consumer
):
    async def scenario():
        registry, instruments = registry_fixture(tmp_path)
        observed = []

        def fail(events, *unused):
            if isinstance(events[0].payload, BookUpdate):
                raise RuntimeError("consumer failed")

        if failed_consumer == "events":
            registry.subscribe_events(fail)
        else:
            registry.subscribe_processed(fail)
        registry.subscribe_processed(
            lambda events, books: observed.append((events, books))
        )
        try:
            lease = await registry.acquire(
                "observer", SubscriptionKey.for_instrument(instruments["BTC"])
            )
            owner = registry._entries[lease.key].supervisor
            await owner.started.get()
            with pytest.raises(ingestion.FeedFault):
                await owner.emit(snapshot("BTC", 60000))
            events, books = next(
                item for item in observed if isinstance(item[0][0].payload, BookUpdate)
            )
            assert books[0].bids[0].price == 60000
            assert books[0].quality.capture_complete == (failed_consumer == "processed")
            invalidations = [
                (events, books)
                for events, books in observed
                if isinstance(events[0].payload, Gap)
            ]
            assert invalidations[0][1][0].quality.integrity == "invalid"
            assert invalidations[0][0][0].payload.capture_lost == (
                failed_consumer == "events"
            )
            assert owner.capture_complete == (failed_consumer == "processed")
        finally:
            await registry.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("queued_count", [1, 2])
def test_shutdown_processed_failure_only_loses_unconsumed_required_input(
    tmp_path, queued_count
):
    async def scenario():
        registry, instruments = registry_fixture(tmp_path)
        recorder = recorder_fixture(tmp_path, registry)

        def fail(events, books):
            if isinstance(events[0].payload, BookUpdate):
                raise RuntimeError("analytical consumer failed during drain")

        registry.subscribe_processed(fail)
        binding = await RecorderBinding.start(
            registry,
            recorder,
            frozenset((SubscriptionKey.for_instrument(instruments["BTC"]),)),
        )
        try:
            owner = next(iter(registry._entries.values())).supervisor
            await owner.started.get()
            for index in range(queued_count):
                owner._receive(
                    snapshot("BTC", 60000 + index),
                    "ws",
                    "wss://api.hyperliquid.xyz/ws",
                    owner.epoch,
                )
            await binding.stop()
            manifest = await recorder.finalize()
            assert manifest.complete == (queued_count == 1)
            with RecordingReader(recorder.path, allow_incomplete=True) as reader:
                batches = tuple(reader.iter_batches())
                updates = [
                    event
                    for batch in batches
                    for event in batch.events
                    if isinstance(event.payload, BookUpdate)
                ]
                assert [event.payload.changes[0].price for event in updates] == [60000]
                gaps = [
                    event.payload
                    for batch in batches
                    for event in batch.events
                    if isinstance(event.payload, Gap)
                ]
                assert gaps[-1].capture_lost == (queued_count == 2)
                if queued_count == 1:
                    assert gaps[-1].first_missing_ordinal is None
                else:
                    frames = tuple(reader.iter_raw())
                    assert gaps[-1].first_missing_ordinal == frames[-1].ingest_ordinal
                    assert gaps[-1].last_missing_ordinal == frames[-1].ingest_ordinal
        finally:
            await binding.stop()
            await registry.close()
            await recorder.finalize()

    asyncio.run(scenario())
