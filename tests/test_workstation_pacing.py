"""Deterministic scheduling with real normalized/raw deliveries, not wall deadlines."""

import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.workstation import replay as replay_module
from src.workstation import runtime as runtime_module
from src.workstation.adapters.hyperliquid import HyperliquidAdapter
from src.workstation.demo_memory import MemoryReplaySource
from src.workstation.events import RawFrame, SegmentId, StreamId, SubscriptionKey
from src.workstation.projection import RecordingProjection
from src.workstation.recording import RecordedBatch, StreamSpec
from src.workstation.replay import (
    DeliveryDigestObserver,
    ReplaySession,
    ReplaySourceDescriptor,
)


D = Decimal


@pytest.fixture
def source_factory():
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
        payload_hash="synthetic-pacing-tests",
    )
    instrument = next(
        item for item in metadata.instruments if item.native_symbol == "BTC"
    )
    key = SubscriptionKey.for_instrument(instrument)
    reducer = adapter.new_reducer(instrument, key)
    spec = StreamSpec(
        StreamId("pacing-book"),
        instrument,
        key,
        reducer.adapter_revision,
        reducer.config_hash,
    )

    def make(timestamps=(1, 2, 3, 4)):
        projection = RecordingProjection((spec,))
        batches, frames = [], {}
        for ordinal, timestamp in enumerate(timestamps, 1):
            payload = json.dumps(
                {
                    "channel": "l2Book",
                    "data": {
                        "coin": "BTC",
                        "time": 1700000000000,
                        "levels": [
                            [{"px": "60000.0", "sz": str(ordinal), "n": 1}],
                            [{"px": "60001.0", "sz": "2.00000", "n": 1}],
                        ],
                    },
                }
            ).encode()
            frame = RawFrame(
                "pacing-tests",
                spec.stream_id,
                "connection-1",
                1,
                "pacing-clock",
                ordinal,
                1700000000000000000 + ordinal,
                timestamp * 1_000_000_000,
                "ws",
                "wss://api.hyperliquid.xyz/ws",
                payload,
            )
            events = projection.process_raw(
                frame, segment_id=SegmentId("ws-1"), source_mode="ws_snapshot"
            )
            batches.append(RecordedBatch(ordinal, events))
            frames[(str(spec.stream_id), ordinal)] = frame
        return MemoryReplaySource(
            ReplaySourceDescriptor(
                "pacing-tests",
                (spec,),
                synthetic=True,
                provenance="synthetic pacing regression fixture",
            ),
            tuple(batches),
            frames,
            {
                (event.ingest_ordinal, event.event_index): batch.delivery_index
                for batch in batches
                for event in batch.events
            },
            (max(timestamps) - min(timestamps)) * 1_000_000_000,
        )

    return make


class ManualTime:
    """Only the consuming module sees this clock; asyncio itself keeps real time."""

    def __init__(self):
        self.now = 0.0
        self.on_wait = None

    def time(self):
        return self.now

    async def sleep(self, delay):
        self.now += delay
        await asyncio.sleep(0)

    async def wait_for(self, awaitable, timeout):
        if self.on_wait is not None:
            callback, self.on_wait = self.on_wait, None
            callback(timeout)
            return await awaitable
        awaitable.close()
        self.now += timeout
        await asyncio.sleep(0)
        raise asyncio.TimeoutError

    def install(self, monkeypatch, module):
        monkeypatch.setattr(
            module,
            "asyncio",
            SimpleNamespace(
                Event=asyncio.Event,
                get_running_loop=lambda: self,
                gather=asyncio.gather,
                sleep=self.sleep,
                wait_for=self.wait_for,
                TimeoutError=asyncio.TimeoutError,
                CancelledError=asyncio.CancelledError,
            ),
        )


class TimedDigest(DeliveryDigestObserver):
    def __init__(self, timing, processing=0.25):
        self.timing, self.processing = timing, processing
        super().__init__()

    def reset(self):
        super().reset()
        self.deliveries, self.times, self.readings = [], [], []

    def on_batch(self, batch, books, clock):
        super().on_batch(batch, books, clock)
        self.deliveries.append(batch.delivery_index)
        self.times.append(self.timing.now)
        self.readings.append(clock.source_reading)
        self.timing.now += self.processing


def assert_matches_unpaced(session, source):
    digest = DeliveryDigestObserver()
    reference = ReplaySession(source, observers=(digest,))
    try:
        reference.step(source.total_deliveries)
        assert session.cursor == reference.cursor
        assert session.clock.state_hash == reference.clock.state_hash
        assert session.state_hash == reference.state_hash
        assert session.book_hash == reference.book_hash
        book = session.projection.books()[0]
        assert book.bids[0].price == D("60000")
        assert book.bids[0].quantity == D(source.total_deliveries)
    finally:
        reference.close()


@pytest.mark.parametrize("mode", ["normalized", "raw"])
@pytest.mark.parametrize("speed", [D("1"), D("2")])
def test_replay_deadlines_include_read_and_observer_time(
    monkeypatch, source_factory, mode, speed
):
    timing = ManualTime()
    timing.install(monkeypatch, replay_module)
    source = source_factory()
    observer = TimedDigest(timing)
    session = ReplaySession(source, mode=mode, observers=(observer,))
    peek = session._peek

    def slow_read():
        timing.now += 0.1
        return peek()

    monkeypatch.setattr(session, "_peek", slow_read)
    try:
        asyncio.run(session.play(speed))
        assert observer.times == pytest.approx(
            [0.1, 1 / float(speed), 2 / float(speed), 3 / float(speed)]
        )
        assert observer.deliveries == [1, 2, 3, 4]
        assert session.status == "finished"
        assert_matches_unpaced(session, source)
    finally:
        session.close()


@pytest.mark.parametrize("processing", [0.25, 1.5])
def test_catchup_yields_without_skipping_or_regressing_source_clocks(
    monkeypatch, source_factory, processing
):
    timing = ManualTime()
    timing.install(monkeypatch, replay_module)
    source = source_factory((1, 3, 2, 4))
    observer = TimedDigest(timing, processing)
    session = ReplaySession(source, observers=(observer,))
    prefixes = []

    async def scenario():
        finished = False

        async def observe():
            while not finished:
                prefixes.append(tuple(observer.deliveries))
                await asyncio.sleep(0)

        watcher = asyncio.create_task(observe())
        try:
            await session.play()
        finally:
            finished = True
            await watcher

    try:
        asyncio.run(scenario())
        assert observer.deliveries == [1, 2, 3, 4]
        assert {(1,), (1, 2), (1, 2, 3)}.issubset(set(prefixes))
        expected = [0, 2, 2.25, 3] if processing == 0.25 else [0, 2, 3.5, 5]
        assert observer.times == pytest.approx(expected)
        assert [reading.mono_ns for reading in observer.readings] == [
            1_000_000_000,
            3_000_000_000,
            2_000_000_000,
            4_000_000_000,
        ]
        assert session.clock.virtual_mono_ns == 3_000_000_000
        assert_matches_unpaced(session, source)
    finally:
        session.close()


@pytest.mark.parametrize(
    "speeds, times",
    [
        (("2", "4"), [0, 2, 3]),
        (("0.5", "1"), [0, 4.25, 8.25]),
    ],
)
def test_speed_changes_reschedule_only_remaining_source_interval(
    monkeypatch, source_factory, speeds, times
):
    timing = ManualTime()
    timing.install(monkeypatch, replay_module)
    source = source_factory((1, 5, 9))
    observer = TimedDigest(timing, 0)
    session = ReplaySession(source, observers=(observer,))

    def change_twice(timeout):
        timing.now += 1
        session.set_speed(D(speeds[0]))
        timing.now += 0.5
        session.set_speed(D(speeds[1]))
        # The interrupt may be handled later; rate changes take effect at the
        # setter's timestamp, not when the waiting playback task wakes up.
        timing.now += 0.25

    timing.on_wait = change_twice
    try:
        asyncio.run(session.play())
        assert observer.times == pytest.approx(times)
        assert observer.deliveries == [1, 2, 3]
        assert_matches_unpaced(session, source)
    finally:
        session.close()


@pytest.mark.parametrize("action", ["pause", "seek", "reset"])
def test_interrupt_restarts_with_fresh_deadlines(monkeypatch, source_factory, action):
    timing = ManualTime()
    timing.install(monkeypatch, replay_module)
    source = source_factory()
    observer = TimedDigest(timing, 0)
    session = ReplaySession(source, observers=(observer,))

    def interrupt(timeout):
        timing.now += timeout / 2
        if action == "pause":
            session.pause()
        else:
            session.seek_delivery(2 if action == "seek" else 0)

    timing.on_wait = interrupt
    try:
        asyncio.run(session.play())
        expected_cursor = {"pause": 1, "seek": 2, "reset": 0}[action]
        assert session.cursor.delivery_index == expected_cursor
        assert session.status == "paused"
        timing.now += 100
        resumed_at = timing.now
        first_new = len(observer.times)
        asyncio.run(session.play())
        expected_delay = 0 if action == "reset" else 1
        assert observer.times[first_new] == pytest.approx(resumed_at + expected_delay)
        assert observer.deliveries == [1, 2, 3, 4]
        assert_matches_unpaced(session, source)
    finally:
        session.close()


def test_cancel_wait_is_joinable_and_keeps_pending_delivery(
    monkeypatch, source_factory
):
    timing = ManualTime()
    timing.install(monkeypatch, replay_module)
    source = source_factory()
    observer = TimedDigest(timing, 0)
    session = ReplaySession(source, observers=(observer,))

    async def scenario():
        waiting = asyncio.Event()

        async def blocked_wait(awaitable, timeout):
            try:
                waiting.set()
                await asyncio.Future()
            finally:
                awaitable.close()

        monkeypatch.setattr(replay_module.asyncio, "wait_for", blocked_wait)
        task = asyncio.create_task(session.play())
        try:
            await waiting.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert observer.deliveries == [1]
            assert session.status == "paused"
            monkeypatch.setattr(replay_module.asyncio, "wait_for", timing.wait_for)
            await session.play()
            assert observer.deliveries == [1, 2, 3, 4]
            assert_matches_unpaced(session, source)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    try:
        asyncio.run(scenario())
    finally:
        session.close()


@pytest.mark.parametrize("speed", [D("1"), D("2")])
def test_runtime_deadlines_include_owner_reads_locks_and_observers(
    monkeypatch, source_factory, speed
):
    timing = ManualTime()
    timing.install(monkeypatch, runtime_module)
    source = source_factory()
    observer = TimedDigest(timing, 0.1)
    session = ReplaySession(source, observers=(observer,))
    session.set_speed(speed)

    class ContendedLock:
        async def __aenter__(self):
            timing.now += 0.05

        async def __aexit__(self, *args):
            pass

    async def work(fn):
        # This harness replaces executor/lock latency only. Replay, projection,
        # observers and the ClientSession playback method are production code.
        if fn.__name__ == "next_delay_ns":
            timing.now += 0.1
        return fn()

    owner = SimpleNamespace(
        replay=session,
        closed=False,
        status="playing",
        lock=ContendedLock(),
        work=work,
        telemetry={},
        error=None,
    )
    try:
        asyncio.run(runtime_module.ClientSession._play(owner))
        assert owner.status == "ended"
        assert owner.error is None
        assert observer.times == pytest.approx(
            [
                0.2,
                1 / float(speed) + 0.05,
                2 / float(speed) + 0.05,
                3 / float(speed) + 0.05,
            ]
        )
        assert observer.deliveries == [1, 2, 3, 4]
        assert_matches_unpaced(session, source)
    finally:
        session.close()


def test_runtime_cancel_and_speed_restart_keep_next_delivery(
    monkeypatch, source_factory
):
    timing = ManualTime()
    timing.install(monkeypatch, runtime_module)
    source = source_factory()
    observer = TimedDigest(timing, 0)
    session = ReplaySession(source, observers=(observer,))

    async def scenario():
        waiting = asyncio.Event()

        async def work(fn):
            return fn()

        async def blocked_sleep(delay):
            if delay:
                timing.now += delay / 2
                waiting.set()
                await asyncio.Future()
            else:
                await asyncio.sleep(0)

        owner = SimpleNamespace(
            replay=session,
            closed=False,
            status="playing",
            lock=asyncio.Lock(),
            work=work,
            telemetry={},
            error=None,
            play_task=None,
        )
        monkeypatch.setattr(runtime_module.asyncio, "sleep", blocked_sleep)
        task = owner.play_task = asyncio.create_task(
            runtime_module.ClientSession._play(owner)
        )
        try:
            await waiting.wait()
            await runtime_module.ClientSession.stop_play(owner)
            assert task.cancelled()
            assert owner.play_task is None
            assert observer.deliveries == [1]
            assert session.status == "paused"
            # Runtime controls stop/restart the pacing owner. A restarted task
            # anchors a full next interval at its new speed, as before.
            session.set_speed(D("2"))
            monkeypatch.setattr(runtime_module.asyncio, "sleep", timing.sleep)
            await runtime_module.ClientSession._play(owner)
            assert observer.times == pytest.approx([0, 1, 1.5, 2])
            assert observer.deliveries == [1, 2, 3, 4]
            assert owner.status == "ended"
            assert_matches_unpaced(session, source)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    try:
        asyncio.run(scenario())
    finally:
        session.close()
