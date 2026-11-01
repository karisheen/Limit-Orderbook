"""Replay real durable synthetic source transactions; no replay/reader mocks."""

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, replace
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

import pytest

from src.workstation.adapters.hyperliquid import HyperliquidAdapter
from src.workstation.events import (
    BookUpdate,
    Control,
    Gap,
    RawFrame,
    SegmentId,
    SessionId,
    StreamId,
    SubscriptionKey,
    Trade,
    local_control_ref,
    synthetic_envelope,
)
from src.workstation.projection import RecordingProjection
from src.workstation.recording import (
    RecordedBatch,
    Recorder,
    RecorderLimits,
    RecordingError,
    RecordingReader,
    StreamSpec,
)
from src.workstation.replay import (
    DeliveryDigestObserver,
    ReplayClock,
    ReplayCursor,
    ReplaySession,
)
from src.workstation.wire import dumps


D = Decimal
SESSION = "replay-tests"
CLOCK = "replay-test-clock"


@pytest.fixture(scope="module")
def streams():
    adapter = HyperliquidAdapter()
    instruments = adapter.parse_metadata(
        {"collateralToken": 0, "universe": [{"name": "BTC", "szDecimals": 5}]},
        {
            "tokens": [
                {"name": "USDC", "index": 0, "tokenId": "0xusdc", "szDecimals": 8}
            ],
            "universe": [],
        },
        fetch_wall_ns=123,
        payload_hash="synthetic-replay-tests",
    ).instruments
    instrument = next(item for item in instruments if item.native_symbol == "BTC")
    result = []
    for channel in ("book", "trades"):
        key = SubscriptionKey.for_instrument(instrument, channel=channel)
        reducer = adapter.new_reducer(instrument, key)
        result.append(
            StreamSpec(
                StreamId("test-" + channel),
                instrument,
                key,
                reducer.adapter_revision,
                reducer.config_hash,
            )
        )
    return tuple(result)


def book_message(size="1.00000"):
    return {
        "channel": "l2Book",
        "data": {
            "coin": "BTC",
            "time": 1700000000000,
            "levels": [
                [{"px": "60000.0", "sz": size, "n": 1}],
                [{"px": "60001.0", "sz": "2.00000", "n": 1}],
            ],
        },
    }


def trade_message():
    return {
        "channel": "trades",
        "data": [
            {
                "coin": "BTC",
                "side": side,
                "px": "60000.0",
                "sz": "0.00001",
                "time": 1700000000000,
                "tid": tid,
                "hash": "0x0",
                "users": ["buyer", "seller"],
            }
            for tid, side in ((11, "A"), (12, "B"))
        ],
    }


def raw(spec, ordinal, message, *, mono=None, epoch=1):
    return RawFrame(
        SessionId(SESSION),
        spec.stream_id,
        "connection-" + str(epoch),
        epoch,
        CLOCK,
        ordinal,
        1700000000000000000 + ordinal,
        ordinal * 10 if mono is None else mono,
        "ws",
        "wss://api.hyperliquid.xyz/ws",
        json.dumps(message, separators=(",", ":")).encode(),
    )


@dataclass(frozen=True)
class Capture:
    path: Path
    batches: tuple[RecordedBatch, ...]
    state_hash: str
    book_hash: str


def record(
    root,
    specs,
    inputs,
    *,
    checkpoint_interval=2,
    incompatible=None,
    delivered_events=None,
):
    """Use the actual capture projection; raw receive order differs from delivery."""

    async def create():
        recorder = Recorder(
            root,
            session_id=SESSION,
            clock_id=CLOCK,
            streams=specs,
            provenance="synthetic replay regression fixture; not historical market data",
            limits=RecorderLimits(
                min_free_bytes=0, checkpoint_interval_batches=checkpoint_interval
            ),
        )
        projection = RecordingProjection(specs)
        batches = []
        positions = {}
        try:
            for frame in sorted(
                (item for item in inputs if isinstance(item, RawFrame)),
                key=lambda item: item.ingest_ordinal,
            ):
                recorder.append_raw(frame)
            for index, item in enumerate(inputs, 1):
                if isinstance(item, RawFrame):
                    segment, mode = positions.get(
                        item.stream_id, (SegmentId("ws-1"), "ws_snapshot")
                    )
                    events = (
                        projection.process_raw(
                            item, segment_id=segment, source_mode=mode
                        )
                        if delivered_events is None
                        else projection.apply_events(delivered_events[index - 1])
                    )
                else:
                    first = item[0]
                    positions[first.stream_id] = (first.segment_id, first.source_mode)
                    events = projection.apply_events(item)
                assert events, "fixture must produce a complete source transaction"
                batch = RecordedBatch(index, events)
                batches.append(batch)
                recorder.append_events(events)
                if incompatible is not None and index == 3:
                    for checkpoint in projection.checkpoints():
                        if incompatible == "configuration":
                            checkpoint = replace(
                                checkpoint, config_hash="incompatible-config"
                            )
                        auxiliary = (
                            b"[]"
                            if incompatible == "auxiliary"
                            else projection.checkpoint_state()
                        )
                        recorder.append_checkpoint(
                            checkpoint, delivery_index=index, observer_state=auxiliary
                        )
        finally:
            manifest = await recorder.finalize()
        assert manifest.complete, manifest.incomplete_reasons
        return Capture(
            recorder.path, tuple(batches), projection.state_hash, projection.book_hash
        )

    return asyncio.run(create())


@pytest.fixture
def capture(tmp_path, streams):
    book, trades = streams
    return record(
        tmp_path,
        streams,
        (
            raw(book, 1, book_message()),
            raw(trades, 3, trade_message()),
            raw(book, 2, book_message("3.00000")),
            raw(trades, 4, trade_message()),
            raw(book, 5, book_message("4.00000")),
        ),
    )


@contextmanager
def replay(capture, **options):
    with RecordingReader(capture.path) as reader:
        session = ReplaySession(reader, **options)
        try:
            yield session
        finally:
            session.close()


def fingerprint(session):
    return (
        session.cursor,
        session.state_hash,
        session.book_hash,
        session.clock.state_hash,
    )


def test_step_preserves_delivery_order_not_receive_or_exchange_order(capture):
    with replay(capture) as session:
        assert session.status == "paused"
        assert session.cursor == ReplayCursor()
        assert session.books() == ()
        delivered, clocks = [], []
        for _ in capture.batches:
            (batch,) = session.step()
            delivered.append(batch)
            clocks.append(session.clock.virtual_mono_ns)
            assert session.status == "paused"
        assert [batch.events[0].ingest_ordinal for batch in delivered] == [
            1,
            3,
            2,
            4,
            5,
        ]
        assert [batch.delivery_index for batch in delivered] == [1, 2, 3, 4, 5]
        assert clocks == [0, 20, 20, 30, 40]
        assert tuple(delivered) == capture.batches
        assert session.book_hash == capture.book_hash
        assert session.state_hash == capture.state_hash
        assert session.step() == ()
        assert session.status == "finished"
    with RecordingReader(capture.path) as reader:
        assert [frame.ingest_ordinal for frame in reader.iter_raw()] == [1, 2, 3, 4, 5]


@pytest.mark.parametrize("mode", ["raw", "normalized"])
def test_step_and_seek_apply_whole_source_transaction(capture, mode):
    with replay(capture, mode=mode) as session:
        first, trades = session.step(2)
        assert first.delivery_index == 1
        assert len(trades.events) == 2
        assert all(isinstance(event.payload, Trade) for event in trades.events)
        assert session.cursor == ReplayCursor(2, 3, 1)
        expected = fingerprint(session)
        session.step(3)
        assert session.seek(3, event_index=0) == ReplayCursor(2, 3, 1)
        assert fingerprint(session) == expected
        assert session.seek(3, event_index=1) == ReplayCursor(2, 3, 1)
        assert fingerprint(session) == expected
        # Ordinal 2 was received first but delivered AFTER ordinal 3.
        assert session.seek(2) == ReplayCursor(3, 2, 0)
        assert session.books()[0].bids[0].quantity == D("3")
        assert session.seek(0) == ReplayCursor()
        assert session.books() == ()
        assert session.clock.virtual_mono_ns == 0
        assert session.status == "paused"
        assert session.step()[0] == capture.batches[0]


@pytest.mark.parametrize("count", [0, -1, 100001, True, 1.5])
def test_step_rejects_invalid_count_without_advancing(capture, count):
    with replay(capture) as session:
        before = fingerprint(session)
        with pytest.raises(ValueError, match="step count"):
            session.step(count)
        assert fingerprint(session) == before


@pytest.mark.parametrize(
    "ordinal,event_index", [(-1, 0), (True, 0), (1, -1), (1, True), (1.5, 0)]
)
def test_seek_rejects_invalid_positions(capture, ordinal, event_index):
    with replay(capture) as session:
        with pytest.raises(ValueError, match="seek position"):
            session.seek(ordinal, event_index)
        assert session.cursor == ReplayCursor()


def test_seek_rejects_absent_transaction_and_absent_event(capture):
    with replay(capture) as session:
        for ordinal, event_index in ((999, 0), (3, 2)):
            with pytest.raises(RecordingError, match="not a captured transaction"):
                session.seek(ordinal, event_index)


@pytest.mark.parametrize(
    "speed", [D("0"), D("-1"), D("NaN"), D("Infinity"), 2, 1.0, "2"]
)
def test_speed_requires_positive_finite_decimal(speed):
    clock = ReplayClock()
    with pytest.raises(ValueError, match="positive finite Decimal"):
        clock.set_speed(speed)
    assert clock.speed == D("1")


def test_clock_preserves_original_readings_across_discontinuities(streams):
    clock = ReplayClock()
    instrument = streams[0].instrument

    def event(clock_id, mono, wall):
        return synthetic_envelope(
            instrument,
            Control("heartbeat"),
            clock_id=clock_id,
            mono_ns=mono,
            receive_wall_ns=wall,
        )

    readings = [
        event("a", 100, 900),
        event("a", 90, 800),
        event("a", 120, 700),
        event("b", 3, 600),
        event("b", 8, 500),
        event("a", 125, 400),
    ]
    deltas = [0, 0, 20, 0, 5, 5]
    virtual = [0, 0, 20, 20, 25, 30]
    frontiers = [100, 100, 120, 3, 8, 125]
    initial_hash = clock.state_hash
    for reading, delta, total, frontier in zip(readings, deltas, virtual, frontiers):
        before = clock.state_hash
        assert clock.delay_ns(reading) == delta
        assert clock.state_hash == before
        assert clock.advance(reading) == delta
        assert clock.virtual_mono_ns == total
        assert clock.reading.clock_id == reading.clock_id
        assert clock.reading.mono_ns == frontier
        assert clock.reading.wall_ns is None
        assert clock.source_reading.clock_id == reading.clock_id
        assert clock.source_reading.mono_ns == reading.receive_mono_ns
        assert clock.source_reading.wall_ns == reading.receive_wall_ns
    assert clock.discontinuities == 2
    clock.set_speed(D("1000000"))
    state = clock.state_hash
    clock.set_speed(D("0.125"))
    assert clock.state_hash == state  # Speed is pacing, not recorded evidence.
    clock.reset()
    assert clock.state_hash == initial_hash
    assert clock.speed == D("0.125")
    assert clock.reading is None
    assert clock.source_reading is None
    assert clock.discontinuities == 0


@pytest.mark.parametrize("mode", ["raw", "normalized"])
@pytest.mark.parametrize("speed", [None, D("0.5"), D("1"), D("1000000")])
def test_async_run_to_end_preserves_hashes_at_all_speeds(capture, mode, speed):
    observer = DeliveryDigestObserver()
    with replay(capture, mode=mode, observers=(observer,)) as session:
        asyncio.run(session.run_to_end(speed))
        assert session.status == "finished"
        assert session.cursor == ReplayCursor(5, 5, 0)
        assert session.book_hash == capture.book_hash
        assert session.projection.state_hash == capture.state_hash
        assert observer.batch_count == 5
        assert observer.event_count == 7
        digest = "0" * 64
        for batch in capture.batches:
            digest = sha256(
                bytes.fromhex(digest)
                + dumps(
                    (batch.delivery_index, batch.events),
                    canonical=False,
                )
            ).hexdigest()
        assert observer.digest == digest
        finished = fingerprint(session)
        asyncio.run(session.run_to_end(speed))
        assert fingerprint(session) == finished


def test_pause_interrupts_wait_and_speed_change_resumes_without_skipping(
    tmp_path, streams
):
    book = streams[0]
    capture = record(
        tmp_path,
        (book,),
        (
            raw(book, 1, book_message(), mono=0),
            raw(book, 2, book_message("3.00000"), mono=10**15),
        ),
        checkpoint_interval=1000,
    )

    async def scenario(session):
        task = asyncio.create_task(session.play(D("1")))
        try:
            await asyncio.sleep(0)  # First transaction applies before the first yield.
            assert session.cursor.delivery_index == 1
            await asyncio.sleep(0)  # Enter the long recorded pacing interval.
            session.pause()
            await asyncio.wait_for(task, timeout=1)
            assert session.status == "paused"
            assert session.cursor.delivery_index == 1
            task = asyncio.create_task(session.play(D("1")))
            await asyncio.sleep(0)
            session.set_speed(D("1e30"))
            await asyncio.wait_for(task, timeout=1)
            assert session.status == "finished"
            assert session.cursor.delivery_index == 2
            assert session.clock.speed == D("1e30")
            assert session.book_hash == capture.book_hash
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    with replay(capture) as session:
        asyncio.run(scenario(session))


def test_play_rejects_second_owner_and_step_interrupts_first(capture):
    async def scenario(session):
        task = asyncio.create_task(session.play(D("1e-30")))
        try:
            await asyncio.sleep(0)
            assert session.cursor.delivery_index == 1
            with pytest.raises(RuntimeError, match="playback owner"):
                await session.play()
            with pytest.raises(RuntimeError, match="playback owner"):
                await session.run_to_end()
            (batch,) = session.step()
            assert batch.delivery_index == 2
            await asyncio.wait_for(task, timeout=1)
            assert session.status == "paused"
            assert session.cursor.delivery_index == 2
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    with replay(capture) as session:
        asyncio.run(scenario(session))


@pytest.mark.parametrize("mode", ["raw", "normalized"])
def test_backward_seek_resets_observer_and_reconstructs_only_past(capture, mode):
    observer = DeliveryDigestObserver()
    with replay(capture, mode=mode, observers=(observer,)) as session:
        session.step(2)
        past, past_observer = fingerprint(session), observer.checkpoint()
        asyncio.run(session.run_to_end())
        future, future_observer = fingerprint(session), observer.checkpoint()
        assert past_observer != future_observer
        session.seek(3, 0)
        assert session.checkpoint_status == "observer_history_recomputed"
        assert observer.batch_count == 2
        assert observer.event_count == 3
        assert observer.checkpoint() == past_observer
        assert fingerprint(session) == past
        assert session.books()[0].bids[0].quantity == D("1")
        asyncio.run(session.run_to_end())
        assert fingerprint(session) == future
        assert observer.checkpoint() == future_observer
        session.seek(0)
        assert observer.batch_count == observer.event_count == 0
        assert observer.digest == "0" * 64
        assert session.clock.reading is None


def test_delivery_observer_checkpoint_round_trip_and_validation(capture):
    observer = DeliveryDigestObserver()
    with replay(capture, observers=(observer,)) as session:
        session.step(2)
        restored = DeliveryDigestObserver()
        restored.restore(observer.checkpoint())
        assert restored.checkpoint() == observer.checkpoint()
        for state in (
            ("2", 2, 3, observer.digest),
            ("1", -1, 3, observer.digest),
            ("1", 2, 1, observer.digest),
            ("1", 2, 3, "g" * 64),
        ):
            with pytest.raises(ValueError):
                restored.restore(dumps(state))
        restored.reset()
        assert restored.batch_count == restored.event_count == 0
        assert restored.digest == "0" * 64


@pytest.mark.parametrize("mode", ["raw", "normalized"])
def test_checkpoint_seek_restores_complete_state_clock_and_dedup(capture, mode):
    with replay(capture, mode=mode) as expected:
        expected.step(2)
        at_checkpoint = fingerprint(expected)
        expected.step()
        after_checkpoint = fingerprint(expected)
        expected.step(2)
        final = fingerprint(expected)
    with replay(capture, mode=mode) as session:
        with RecordingReader(capture.path) as reader:
            assert any(item.delivery_index == 2 for item in reader.iter_checkpoints())
        session.seek(3)
        assert session.checkpoint_status == "restored"
        assert fingerprint(session) == at_checkpoint
        session.seek(2)
        assert session.checkpoint_status == "restored"
        assert fingerprint(session) == after_checkpoint
        (duplicates,) = session.step()
        assert all(
            isinstance(event.payload, Control)
            and event.payload.reason_code == "duplicate_trade"
            for event in duplicates.events
        )
        asyncio.run(session.run_to_end())
        assert fingerprint(session) == final


@pytest.mark.parametrize("incompatible", ["configuration", "auxiliary"])
@pytest.mark.parametrize("mode", ["raw", "normalized"])
def test_incompatible_checkpoint_falls_back_or_rejects_explicitly(
    tmp_path, streams, incompatible, mode
):
    book, trades = streams
    capture = record(
        tmp_path,
        streams,
        (
            raw(book, 1, book_message()),
            raw(trades, 2, trade_message()),
            raw(book, 3, book_message("3.00000")),
            raw(trades, 4, trade_message()),
        ),
        checkpoint_interval=1000,
        incompatible=incompatible,
    )
    with replay(capture, mode=mode) as expected:
        expected.step(3)
        at_target = fingerprint(expected)
    with replay(capture, mode=mode, checkpoint_policy="fallback") as session:
        session.seek(3)
        assert session.checkpoint_status.startswith("incompatible:")
        assert fingerprint(session) == at_target
        asyncio.run(session.run_to_end())
        assert session.state_hash == capture.state_hash
    with replay(capture, mode=mode, checkpoint_policy="reject") as session:
        with pytest.raises(ValueError, match="incompatible:"):
            session.seek(3)
        assert session.checkpoint_status.startswith("incompatible:")
        assert session.cursor == ReplayCursor()
        assert session.books() == ()


@pytest.mark.parametrize("mode", ["raw", "normalized"])
def test_duplicate_trades_remain_raw_evidence_but_not_delivered_trades(capture, mode):
    with replay(capture, mode=mode) as session:
        batches = session.step(5)
        trades = [
            event.payload
            for batch in batches
            for event in batch.events
            if isinstance(event.payload, Trade)
        ]
        duplicates = [
            event
            for batch in batches
            for event in batch.events
            if isinstance(event.payload, Control)
            and event.payload.reason_code == "duplicate_trade"
        ]
        assert len(trades) == len(duplicates) == 2
        assert len({trade.dedup_key for trade in trades}) == 2
        assert [event.event_index for event in duplicates] == [0, 1]
        assert all(event.ingest_ordinal == 4 for event in duplicates)
        original = session.reader.raw_frame("test-trades", 3)
        duplicate = session.reader.raw_frame("test-trades", 4)
        assert original.payload == duplicate.payload
        assert original.payload_sha256 == duplicate.payload_sha256


@pytest.mark.parametrize("mode", ["raw", "normalized"])
def test_recording_and_replay_preserve_lexical_decimal_precision(capture, mode):
    with replay(capture, mode=mode) as session:
        (batch,) = session.step()
        update = batch.events[0].payload
        assert isinstance(update, BookUpdate)
        bid = next(level for level in update.changes if level.side == "bid")
        assert str(bid.price) == "60000.0"
        assert str(bid.quantity) == "1.00000"
        frame = session.reader.raw_frame("test-book", 1)
        assert b'"px":"60000.0"' in frame.payload
        assert b'"sz":"1.00000"' in frame.payload
        session.seek(2)
        assert session.checkpoint_status == "restored"
        assert str(session.books()[0].bids[0].quantity) == "3.00000"


@pytest.mark.parametrize(
    "options",
    [
        {"mode": "other"},
        {"checkpoint_policy": "ignore"},
        {"observers": (DeliveryDigestObserver(), DeliveryDigestObserver())},
    ],
)
def test_invalid_replay_configuration_is_rejected(capture, options):
    with RecordingReader(capture.path) as reader:
        with pytest.raises(ValueError):
            ReplaySession(reader, **options)


@pytest.fixture(params=["binance-global", "hyperliquid"])
def source_boundaries(tmp_path, request):
    from src.workstation.demo import demo_source

    source = demo_source(duration_seconds=2)
    spec = next(
        item
        for item in source.streams
        if item.key.venue == request.param
        and item.key.channel == "book"
        and item.instrument.base.ticker == "BTC"
    )
    inputs = []
    for item in source.inputs():
        if isinstance(item, RawFrame):
            if item.stream_id == spec.stream_id:
                inputs.append(
                    replace(item, session_id=SessionId(SESSION), clock_id=CLOCK)
                )
        elif item[0].stream_id == spec.stream_id:
            inputs.append(
                tuple(
                    replace(event, session_id=SessionId(SESSION), clock_id=CLOCK)
                    for event in item
                )
            )
    return record(tmp_path, (spec,), tuple(inputs))


@pytest.mark.parametrize("mode", ["raw", "normalized"])
def test_source_epochs_restore_ws_and_rest_strategy_without_relabeling(
    source_boundaries, mode
):
    capture = source_boundaries
    with replay(capture, mode=mode) as session:
        checkpoints = []
        saw_gap = saw_rest = saw_ws_recovery = False
        for expected in capture.batches:
            (delivered,) = session.step()
            assert delivered == expected
            first = delivered.events[0]
            if (
                isinstance(first.payload, Control)
                and first.payload.kind == "source_change"
            ):
                # A new epoch must not leak the previous segment's book.
                assert session.books() == ()
            elif isinstance(first.payload, Gap):
                saw_gap = True
                (book,) = session.books()
                assert book.quality.integrity == "invalid"
                assert book.quality.continuous_since_ordinal is None
                assert first.payload.reason_code in book.quality.reason_codes
                assert book.quality.capture_complete
            elif any(
                isinstance(event.payload, BookUpdate) for event in delivered.events
            ):
                (book,) = session.books()
                assert book.segment_id == first.segment_id
                assert book.connection_epoch == first.connection_epoch
                assert book.quality.clock_id == first.clock_id
                assert book.quality.source_mode == first.source_mode
                assert book.quality.source_mode not in ("demo", "replay")
                assert book.quality.capture_complete
                if first.source_mode == "rest_snapshot":
                    saw_rest = True
                    assert first.connection_epoch == 2
                    assert book.quality.integrity == "valid"
                    assert not book.quality.capabilities.deltas
                    assert not book.quality.capabilities.exact_event_flow
                    assert book.quality.capabilities.sequence_scope is None
                    assert book.quality.capabilities.checksum_scope is None
                elif first.connection_epoch == 3:
                    saw_ws_recovery = True
                    assert book.quality.capabilities.deltas == (
                        first.key.venue == "binance-global"
                    )
            checkpoints.append((first.ingest_ordinal, fingerprint(session)))
        assert saw_gap and saw_rest and saw_ws_recovery
        assert session.state_hash == capture.state_hash
        assert session.book_hash == capture.book_hash
        # Includes Binance's unsynchronized snapshot checkpoint before the first
        # bridging diff, plus gap and pristine new-epoch checkpoint boundaries.
        for ordinal, expected in reversed(checkpoints):
            session.seek(ordinal)
            assert fingerprint(session) == expected
        asyncio.run(session.run_to_end(D("1000000000")))
        assert session.state_hash == capture.state_hash


@pytest.mark.parametrize("mode", ["raw", "normalized"])
def test_gap_preserves_known_missing_range_without_inventing_history(
    tmp_path, streams, mode
):
    book = streams[0]
    snapshot = raw(book, 1, book_message())
    gap = synthetic_envelope(
        book.instrument,
        Gap(
            "synthetic_network_loss",
            first_missing_ordinal=6,
            last_missing_ordinal=9,
            continuity_lost=True,
            capture_lost=False,
            affected_streams=(book.stream_id,),
        ),
        ordinal=10,
        epoch=1,
        connection_id=snapshot.connection_id,
        mono_ns=100,
        session_id=SessionId(SESSION),
        stream_id=book.stream_id,
        key=book.key,
        clock_id=CLOCK,
        segment_id=SegmentId("ws-1"),
        source_mode="ws_snapshot",
    )
    capture = record(
        tmp_path,
        (book,),
        (snapshot, (gap,)),
        checkpoint_interval=1000,
    )
    with replay(capture, mode=mode) as session:
        session.step()
        (before,) = session.books()
        (delivered,) = session.step()
        assert delivered.events == (gap,)
        (after,) = session.books()
        assert after.bids == before.bids
        assert after.asks == before.asks
        assert after.quality.integrity == "invalid"
        assert after.quality.capture_complete
        assert after.quality.continuous_since_ordinal is None
        assert "synthetic_network_loss" in after.quality.reason_codes
        assert session.reader.manifest.complete
        assert session.reader.raw_frame(book.stream_id, 10) is None
        assert session.reader.query_history(start_ordinal=6, end_ordinal=9) == ()
        assert session.seek(10) == ReplayCursor(2, 10, 0)
        assert session.checkpoint_status == "restored"
        assert session.books() == (after,)
        session.seek(1)
        assert session.books() == (before,)


@pytest.mark.parametrize("mode", ["raw", "normalized"])
def test_trade_dedup_survives_source_epoch_and_backward_seek(tmp_path, streams, mode):
    trades = streams[1]
    source_change = synthetic_envelope(
        trades.instrument,
        Control("source_change", "ws_connect"),
        ordinal=2,
        epoch=2,
        mono_ns=20,
        session_id=SessionId(SESSION),
        stream_id=trades.stream_id,
        key=trades.key,
        clock_id=CLOCK,
        segment_id=SegmentId("ws-2"),
        source_mode="ws_snapshot",
    )
    capture = record(
        tmp_path,
        (trades,),
        (
            raw(trades, 1, trade_message()),
            (source_change,),
            raw(trades, 3, trade_message(), epoch=2),
        ),
    )
    with replay(capture, mode=mode) as session:
        original, boundary, duplicate = session.step(3)
        assert all(isinstance(event.payload, Trade) for event in original.events)
        assert boundary.events == (source_change,)
        assert all(
            isinstance(event.payload, Control)
            and event.payload.reason_code == "duplicate_trade"
            for event in duplicate.events
        )
        assert all(
            event.connection_epoch == 2 and event.segment_id == "ws-2"
            for event in duplicate.events
        )
        final = fingerprint(session)
        session.seek(2)
        assert session.step() == (duplicate,)
        assert fingerprint(session) == final
        session.seek(0)
        assert session.step() == (original,)  # No future dedup cache survives reset.


def scoped_trade_capture(root, spec, decisions, *, corruption=None):
    """Record real decoded trades with the live registry's delivered decisions.

    Other, unrecorded streams may have populated or evicted the shared live
    dedup cache. The capture contains the actual decision, not a fabricated seed
    or fabricated intervening market history.
    """
    from src.workstation.ingestion import decode_frame

    adapter = HyperliquidAdapter()
    frames, deliveries = [], []
    for ordinal, excluded in enumerate(decisions, 1):
        frame = raw(spec, ordinal, trade_message())
        frames.append(frame)
        events = decode_frame(
            adapter, spec.instrument, spec.key, frame, SegmentId("ws-1"), "ws_snapshot"
        )
        assert len(events) == 2
        assert all(isinstance(event.payload, Trade) for event in events)
        if excluded:
            events = tuple(
                replace(
                    event,
                    payload=Control(
                        "ack",
                        "duplicate_trade",
                        event.payload.dedup_key[:4096],
                    ),
                )
                for event in events
            )
        if corruption == "detail":
            events = (
                replace(
                    events[0],
                    payload=replace(
                        events[0].payload,
                        detail="wrong-trade-identity",
                    ),
                ),
                *events[1:],
            )
        elif corruption == "provenance":
            events = (
                replace(events[0], connection_id="not-the-captured-connection"),
                *events[1:],
            )
        deliveries.append(events)
    return record(root, (spec,), tuple(frames), delivered_events=tuple(deliveries))


def test_scoped_capture_first_trade_can_be_excluded_without_captured_history(
    tmp_path, streams
):
    capture = scoped_trade_capture(tmp_path, streams[1], (True,))
    results = []
    for mode in ("raw", "normalized"):
        observer = DeliveryDigestObserver()
        with replay(capture, mode=mode, observers=(observer,)) as session:
            assert session.cursor == ReplayCursor()
            assert session.reader.manifest.raw_count == 1
            assert session.reader.manifest.batch_count == 1
            (batch,) = session.step()
            assert batch == capture.batches[0]
            assert all(
                isinstance(event.payload, Control)
                and event.payload.reason_code == "duplicate_trade"
                for event in batch.events
            )
            assert session.projection.state_hash == capture.state_hash
            assert observer.event_count == 2
            results.append(fingerprint(session))
            session.seek(0)
            assert session.step() == (batch,)
            assert fingerprint(session) == results[-1]
    assert results[0] == results[1]


def test_scoped_capture_accepts_repeat_after_unrecorded_shared_cache_eviction(
    tmp_path, streams
):
    capture = scoped_trade_capture(tmp_path, streams[1], (False, False))
    results = []
    for mode in ("raw", "normalized"):
        with replay(capture, mode=mode) as session:
            first, second = session.step(2)
            assert all(
                isinstance(event.payload, Trade)
                for batch in (first, second)
                for event in batch.events
            )
            assert tuple(event.payload.dedup_key for event in first.events) == tuple(
                event.payload.dedup_key for event in second.events
            )
            assert (first, second) == capture.batches
            assert session.state_hash == capture.state_hash
            results.append(fingerprint(session))
            session.seek(1)
            assert session.step() == (second,)
            assert fingerprint(session) == results[-1]
            session.seek(2)
            assert session.checkpoint_status == "restored"
            assert fingerprint(session) == results[-1]
    assert results[0] == results[1]


@pytest.mark.parametrize(
    "corruption,match",
    [
        ("detail", "recorded duplicate identity differs from source"),
        ("provenance", None),
    ],
)
def test_scoped_duplicate_decision_does_not_bypass_raw_evidence_verification(
    tmp_path,
    streams,
    corruption,
    match,
):
    capture = scoped_trade_capture(tmp_path, streams[1], (True,), corruption=corruption)
    if corruption == "provenance":
        with pytest.raises(RecordingError):
            RecordingReader(capture.path)
    # Duplicate identities require re-decoding. For forged provenance, also
    # exercise replay's independent defense with eager reader checks disabled.
    with RecordingReader(capture.path, verify=corruption != "provenance") as reader:
        session = ReplaySession(reader, mode="raw")
        try:
            with pytest.raises(ValueError, match=match):
                session.step()
            assert session.cursor == ReplayCursor()
        finally:
            session.close()


@pytest.mark.parametrize("mode", ["raw", "normalized"])
@pytest.mark.parametrize("detail,complete", [("", True), ("capture_incomplete", False)])
def test_source_change_carries_pre_capture_loss_into_snapshot_quality(
    tmp_path,
    streams,
    mode,
    detail,
    complete,
):
    book = streams[0]
    boundary = synthetic_envelope(
        book.instrument,
        Control("source_change", "ws_connect", detail),
        ordinal=1,
        epoch=1,
        mono_ns=10,
        session_id=SessionId(SESSION),
        stream_id=book.stream_id,
        key=book.key,
        clock_id=CLOCK,
        segment_id=SegmentId("ws-1"),
        source_mode="ws_snapshot",
    )
    next_boundary = replace(
        boundary,
        payload=Control("source_change", "ws_connect"),
        ingest_ordinal=3,
        raw_ref=local_control_ref(book.stream_id, 3),
        receive_mono_ns=30,
        receive_wall_ns=30,
        connection_epoch=2,
        segment_id=SegmentId("ws-2"),
    )
    capture = record(
        tmp_path,
        (book,),
        (
            (boundary,),
            raw(book, 2, book_message()),
            (next_boundary,),
            raw(book, 4, book_message("3.00000"), epoch=2),
        ),
    )
    assert not any(
        isinstance(event.payload, Gap)
        for batch in capture.batches
        for event in batch.events
    )
    with replay(capture, mode=mode) as session:
        session.step()
        assert session.books() == ()
        assert (
            session.projection.reducers[book.stream_id].quality.capture_complete
            is complete
        )
        session.step()
        (snapshot,) = session.books()
        assert snapshot.quality.integrity == "valid"
        assert snapshot.quality.capture_complete is complete
        at_snapshot = fingerprint(session)
        session.step()
        assert session.books() == ()
        assert (
            session.projection.reducers[book.stream_id].quality.capture_complete
            is complete
        )
        session.step()
        (recovered,) = session.books()
        assert recovered.connection_epoch == 2
        assert recovered.quality.integrity == "valid"
        assert recovered.quality.capture_complete is complete
        session.seek(2)
        assert session.checkpoint_status == "restored"
        assert fingerprint(session) == at_snapshot
        assert session.books()[0].quality.capture_complete is complete


@pytest.mark.parametrize("mode", ["raw", "normalized"])
def test_clock_highwater_keeps_newer_book_eligible_after_older_cross_stream_delivery(
    tmp_path,
    streams,
    mode,
):
    from src.workstation.quality import evaluate

    book, trades = streams
    capture = record(
        tmp_path,
        streams,
        (
            raw(book, 2, book_message(), mono=20),
            raw(trades, 1, trade_message(), mono=10),
        ),
    )
    with replay(capture, mode=mode) as session:
        session.step()
        (observed,) = session.books()
        before = evaluate(
            observed, book.instrument, "comparison", session.clock.reading
        )
        assert before.eligible, before.reasons
        assert before.evaluated_at_mono_ns == 20
        (older,) = session.step()
        assert older.events[0].receive_mono_ns == 10
        assert session.clock.source_reading.mono_ns == 10
        assert session.clock.reading.mono_ns == 20
        assert session.clock.reading.wall_ns is None
        assert session.books() == (observed,)
        after = evaluate(observed, book.instrument, "comparison", session.clock.reading)
        assert after.eligible, after.reasons
        assert after == before
        # The original delivery reading is retained as evidence, but using it
        # for eligibility would falsely invalidate the previously observed book.
        source_time_result = evaluate(
            observed,
            book.instrument,
            "comparison",
            session.clock.source_reading,
        )
        assert "clock_regression" in source_time_result.reasons
        state = fingerprint(session)
        session.seek(1)
        assert session.checkpoint_status == "restored"
        assert fingerprint(session) == state
        restored = evaluate(
            session.books()[0], book.instrument, "comparison", session.clock.reading
        )
        assert restored == before


def record_control_evidence(root, spec, events, *, frames=()):
    """Persist evidence directly, including intentionally missing raw inputs."""

    async def create():
        recorder = Recorder(
            root,
            session_id=SESSION,
            clock_id=CLOCK,
            streams=(spec,),
            provenance="synthetic control-provenance regression fixture",
            limits=RecorderLimits(min_free_bytes=0),
        )
        try:
            for frame in frames:
                recorder.append_raw(frame)
            recorder.append_events(events)
        finally:
            manifest = await recorder.finalize()
        return recorder.path, manifest

    return asyncio.run(create())


@pytest.fixture(params=["ack", "heartbeat"])
def public_control(streams, request):
    from src.workstation.ingestion import decode_frame

    spec = streams[0]
    message = (
        {
            "channel": "subscriptionResponse",
            "data": {
                "method": "subscribe",
                "subscription": {"type": "l2Book", "coin": "BTC"},
            },
        }
        if request.param == "ack"
        else {"channel": "pong"}
    )
    frame = raw(spec, 1, message)
    events = decode_frame(
        HyperliquidAdapter(),
        spec.instrument,
        spec.key,
        frame,
        SegmentId("ws-1"),
        "ws_snapshot",
    )
    assert len(events) == 1
    assert isinstance(events[0].payload, Control)
    assert events[0].payload.kind == request.param
    assert events[0].raw_ref == frame.ref()
    return spec, frame, events


def test_public_adapter_controls_require_their_original_raw_frame(
    tmp_path, public_control
):
    spec, frame, events = public_control
    path, manifest = record_control_evidence(tmp_path, spec, events)
    assert not manifest.complete
    assert manifest.raw_count == 0
    assert "missing_required_raw_evidence" in manifest.incomplete_reasons
    with pytest.raises(RecordingError):
        RecordingReader(path)
    with pytest.raises(RecordingError):
        RecordingReader(path, allow_incomplete=True)
    with RecordingReader(path, verify=False, allow_incomplete=True) as reader:
        session = ReplaySession(reader, mode="raw")
        try:
            with pytest.raises(
                ValueError, match="missing raw frame for source transaction"
            ):
                session.step()
            assert session.cursor == ReplayCursor()
        finally:
            session.close()


@pytest.mark.parametrize("mode", ["raw", "normalized"])
def test_public_adapter_controls_keep_exact_raw_reference(
    tmp_path, public_control, mode
):
    spec, frame, events = public_control
    capture = record(tmp_path, (spec,), (frame,))
    with replay(capture, mode=mode) as session:
        (batch,) = session.step()
        assert batch.events == events
        assert batch.events[0].raw_ref == frame.ref()
        stored = session.reader.raw_frame(spec.stream_id, frame.ingest_ordinal)
        assert stored == frame
        assert stored.payload == frame.payload
        assert stored.payload_sha256 == frame.payload_sha256


@pytest.mark.parametrize("kind", ["reset", "disconnect"])
@pytest.mark.parametrize("mode", ["raw", "normalized"])
def test_explicit_local_reset_and_disconnect_replay_without_raw(
    tmp_path, streams, kind, mode
):
    spec = streams[0]
    event = synthetic_envelope(
        spec.instrument,
        Control(kind),
        ordinal=1,
        epoch=1,
        session_id=SessionId(SESSION),
        stream_id=spec.stream_id,
        key=spec.key,
        clock_id=CLOCK,
        segment_id=SegmentId("ws-1"),
        source_mode="ws_snapshot",
    )
    assert event.raw_ref == local_control_ref(spec.stream_id, 1)
    capture = record(tmp_path, (spec,), ((event,),))
    with replay(capture, mode=mode) as session:
        assert session.reader.manifest.raw_count == 0
        assert session.reader.raw_frame(spec.stream_id, 1) is None
        (batch,) = session.step()
        assert batch.events == (event,)
        assert session.cursor == ReplayCursor(1, 1, 0)


@pytest.mark.parametrize("reference", ["anonymous", "public"])
def test_unmarked_local_looking_control_cannot_substitute_for_missing_raw(
    tmp_path,
    streams,
    reference,
):
    spec = streams[0]
    frame = raw(spec, 1, {"channel": "pong"})
    event = synthetic_envelope(
        spec.instrument,
        Control("reset"),
        ordinal=1,
        epoch=1,
        session_id=SessionId(SESSION),
        stream_id=spec.stream_id,
        key=spec.key,
        clock_id=CLOCK,
        segment_id=SegmentId("ws-1"),
        source_mode="ws_snapshot",
        raw_ref=None if reference == "anonymous" else frame.ref(),
    )
    path, manifest = record_control_evidence(tmp_path, spec, (event,))
    assert not manifest.complete
    assert "missing_required_raw_evidence" in manifest.incomplete_reasons
    with pytest.raises(RecordingError):
        RecordingReader(path, allow_incomplete=True)
    with RecordingReader(path, verify=False, allow_incomplete=True) as reader:
        session = ReplaySession(reader, mode="raw")
        try:
            with pytest.raises(
                ValueError, match="missing raw frame for source transaction"
            ):
                session.step()
        finally:
            session.close()


def test_local_control_marker_cannot_hide_indexed_public_raw(tmp_path, public_control):
    spec, frame, events = public_control
    local = replace(
        events[0],
        payload=Control("reset"),
        raw_ref=local_control_ref(spec.stream_id, frame.ingest_ordinal),
    )
    path, manifest = record_control_evidence(tmp_path, spec, (local,), frames=(frame,))
    assert not manifest.complete
    assert "conflicting_local_raw_origin" in manifest.incomplete_reasons
    with pytest.raises(RecordingError):
        RecordingReader(path, allow_incomplete=True)
    with RecordingReader(path, verify=False, allow_incomplete=True) as reader:
        session = ReplaySession(reader, mode="raw")
        try:
            with pytest.raises(
                ValueError, match="source transaction raw reference mismatch"
            ):
                session.step()
        finally:
            session.close()


@pytest.mark.parametrize("mode", ["raw", "normalized"])
def test_maximum_stream_identity_preserves_public_and_local_evidence(
    tmp_path, streams, mode
):
    spec = replace(streams[0], stream_id=StreamId("s" * 512))
    frame = raw(spec, 1, book_message())
    stopped = synthetic_envelope(
        spec.instrument,
        Control("disconnect"),
        ordinal=2,
        epoch=1,
        session_id=SessionId(SESSION),
        stream_id=spec.stream_id,
        key=spec.key,
        clock_id=CLOCK,
        segment_id=SegmentId("ws-1"),
        connection_id=frame.connection_id,
        receive_mono_ns=20,
        receive_wall_ns=frame.receive_wall_ns + 1,
        source_mode="ws_snapshot",
    )
    capture = record(tmp_path, (spec,), (frame, (stopped,)))
    with replay(capture, mode=mode) as session:
        session.step()
        (book,) = session.books()
        assert book.quality.integrity == "valid"
        assert book.bids[0].price == D("60000.0")
        session.step()
        (stopped_book,) = session.books()
        assert stopped_book.quality.lifecycle == "disconnected"
        assert session.reader.raw_frame(spec.stream_id, 1) == frame
