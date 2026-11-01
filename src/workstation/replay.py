"""Bounded deterministic replay over recorded delivery transactions, not timestamps."""

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from itertools import groupby
from typing import Iterator, Protocol

from .book import Book
from .events import EventEnvelope, RawFrame, is_local_control
from .projection import RecordingProjection
from .quality import ClockReading
from .recording import RecordedBatch, RecordingReader, StreamSpec
from .wire import dumps, loads


@dataclass(frozen=True)
class ReplaySourceDescriptor:
    """Replay provenance without pretending an ephemeral source is a recording."""

    source_id: str
    streams: tuple[StreamSpec, ...]
    config: tuple[tuple[str, str], ...] = ()
    synthetic: bool = False
    recording: bool = False
    provenance: str = ""


class ReplaySource(Protocol):
    @property
    def descriptor(self) -> ReplaySourceDescriptor: ...

    @property
    def total_deliveries(self) -> int: ...

    def iter_batches(
        self, *, start_delivery: int = 1, stop_delivery: int | None = None
    ) -> Iterator[RecordedBatch]: ...
    def raw_frame(self, stream_id: str, ordinal: int) -> RawFrame | None: ...
    def resolve_delivery(self, ordinal: int, event_index: int = 0) -> int: ...
    def iter_checkpoints(self, *, before_delivery: int): ...


@dataclass(frozen=True)
class ReplayCursor:
    delivery_index: int = 0
    ordinal: int = 0
    event_index: int = 0


class ReplayClock:
    """Eligibility and pacing never regress; source receive clocks stay intact."""

    def __init__(self) -> None:
        self.speed = Decimal("1")
        self.reset()

    def reset(self) -> None:
        self.virtual_mono_ns = 0
        self.reading: ClockReading | None = None
        self.source_reading: ClockReading | None = None
        self._frontiers: dict[str, int] = {}
        self.discontinuities = 0

    def set_speed(self, speed: Decimal) -> None:
        if not isinstance(speed, Decimal) or not speed.is_finite() or speed <= 0:
            raise ValueError("replay speed must be a positive finite Decimal")
        self.speed = speed

    def delay_ns(self, event: EventEnvelope) -> int:
        frontier = self._frontiers.get(event.clock_id)
        return 0 if frontier is None else max(0, event.receive_mono_ns - frontier)

    def observe_frontier(self, now: ClockReading) -> int:
        """Advance an owner's clock without inventing a market observation."""
        previous = self._frontiers.get(now.clock_id)
        delta = 0 if previous is None else max(0, now.mono_ns - previous)
        if self.reading is not None and now.clock_id != self.reading.clock_id:
            self.discontinuities += 1
        self.virtual_mono_ns += delta
        frontier = max(now.mono_ns, previous or 0)
        self._frontiers[now.clock_id] = frontier
        self.reading = ClockReading(
            now.clock_id, frontier, now.wall_ns if frontier == now.mono_ns else None
        )
        return delta

    def advance(self, event: EventEnvelope) -> int:
        self.source_reading = ClockReading(
            event.clock_id, event.receive_mono_ns, event.receive_wall_ns
        )
        # Preserve original receive evidence even when a live command or another
        # stream has already advanced the eligibility frontier.
        return self.observe_frontier(
            ClockReading(event.clock_id, event.receive_mono_ns)
        )

    @property
    def state_hash(self) -> str:
        return sha256(
            dumps(
                (
                    self.virtual_mono_ns,
                    self.reading,
                    self.source_reading,
                    self.discontinuities,
                    tuple(sorted(self._frontiers.items())),
                )
            )
        ).hexdigest()


class ReplayPacer:
    """Ephemeral monotonic deadlines, separate from deterministic replay clocks.

    Each interval extends the previous target, not the completion of its work.
    The owner supplies monotonic seconds so reads, observers and lock contention
    consume the interval rather than accumulating as extra playback delay.
    """

    def __init__(self, now: float, speed: Decimal) -> None:
        self.deadline = now
        self.speed = speed

    def advance(self, delay_ns: int) -> None:
        self.deadline += float(Decimal(delay_ns) / Decimal(1_000_000_000) / self.speed)

    def remaining(self, now: float) -> float:
        return max(0.0, self.deadline - now)

    def set_speed(self, speed: Decimal, now: float) -> None:
        # Preserve both the unconsumed source interval and any catch-up debt.
        self.deadline = now + (self.deadline - now) * float(self.speed / speed)
        self.speed = speed


class ReplayObserver(Protocol):
    """Future windows/alerts/paper must implement reset, never retain future state."""

    observer_id: str
    version: str

    def reset(self) -> None: ...
    def on_batch(
        self,
        batch: RecordedBatch,
        books: tuple[Book, ...],
        clock: ReplayClock,
    ) -> None: ...
    def checkpoint(self) -> bytes: ...
    def restore(self, state: bytes) -> None: ...


class DeliveryDigestObserver:
    """Real bounded audit observer: hashes exact delivered evidence in order."""

    observer_id = "delivery_digest"
    version = "1"

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.batch_count = 0
        self.event_count = 0
        self.digest = "0" * 64

    def on_batch(
        self,
        batch: RecordedBatch,
        books: tuple[Book, ...],
        clock: ReplayClock,
    ) -> None:
        self.digest = sha256(
            bytes.fromhex(self.digest)
            + dumps((batch.delivery_index, batch.events), canonical=False)
        ).hexdigest()
        self.batch_count += 1
        self.event_count += len(batch.events)

    def checkpoint(self) -> bytes:
        return dumps((self.version, self.batch_count, self.event_count, self.digest))

    def restore(self, state: bytes) -> None:
        version, batches, events, digest = loads(state, tuple[str, int, int, str])
        if (
            version != self.version
            or batches < 0
            or events < batches
            or len(digest) != 64
        ):
            raise ValueError("incompatible delivery observer checkpoint")
        if len(bytes.fromhex(digest)) != 32:
            raise ValueError("invalid delivery digest")
        self.batch_count, self.event_count, self.digest = batches, events, digest


class ReplaySession:
    """Synchronous step/seek and interruptible async pacing over bounded iterators.

    All methods belong to one event loop/owner. `pause` interrupts a pending wait;
    `step` and `seek` pause first. A source transaction is never partially applied.
    """

    def __init__(
        self,
        reader: RecordingReader | ReplaySource,
        *,
        mode: str = "normalized",
        observers: tuple[ReplayObserver, ...] = (),
        checkpoint_policy: str = "fallback",
    ):
        if mode not in ("raw", "normalized"):
            raise ValueError("replay mode must be raw or normalized")
        if checkpoint_policy not in ("fallback", "reject"):
            raise ValueError("invalid checkpoint policy")
        if len({observer.observer_id for observer in observers}) != len(observers):
            raise ValueError("duplicate observer identity")
        self.reader, self.mode = reader, mode
        if isinstance(reader, RecordingReader):
            manifest = reader.manifest
            self.source = ReplaySourceDescriptor(
                str(manifest.session_id),
                manifest.streams,
                manifest.config,
                recording=True,
                provenance=manifest.provenance,
            )
        else:
            self.source = reader.descriptor
        self.observers, self.checkpoint_policy = tuple(observers), checkpoint_policy
        self.clock = ReplayClock()
        self.cursor = ReplayCursor()
        self.status = "paused"
        self.checkpoint_status = "not_used"
        self._interrupt = asyncio.Event()
        self._playing = False
        self._pacer: ReplayPacer | None = None
        self._generation = 0
        self._iterator: Iterator[RecordedBatch] | None = None
        self._pending: RecordedBatch | None = None
        self._reset()

    @property
    def total_deliveries(self) -> int:
        return (
            self.reader.manifest.batch_count
            if isinstance(self.reader, RecordingReader)
            else self.reader.total_deliveries
        )

    def _new_projection(self) -> RecordingProjection:
        config = dict(self.source.config)
        projection = RecordingProjection(
            self.source.streams,
            dedup_capacity=int(config.get("dedup_capacity", "50000")),
            poll_interval=float(config.get("poll_interval", "2.0")),
        )
        return projection

    def _close_iterator(self) -> None:
        if self._iterator is not None:
            close = getattr(self._iterator, "close", None)
            if close is not None:
                close()
        self._iterator, self._pending = None, None

    def _reset(self) -> None:
        self._close_iterator()
        self.projection = self._new_projection()
        self.cursor = ReplayCursor()
        self.clock.reset()
        for observer in self.observers:
            observer.reset()
        self._iterator = iter(self.reader.iter_batches(start_delivery=1))

    def pause(self) -> None:
        self._generation += 1
        self.status = "paused"
        self._interrupt.set()

    def set_speed(self, speed: Decimal) -> None:
        self.clock.set_speed(speed)
        if self._pacer is not None:
            self._pacer.set_speed(speed, asyncio.get_running_loop().time())
        self._interrupt.set()

    def _peek(self) -> RecordedBatch | None:
        if self._pending is None and self._iterator is not None:
            self._pending = next(self._iterator, None)
        return self._pending

    def _apply(self, batch: RecordedBatch) -> RecordedBatch:
        events = batch.events
        if not events:
            raise ValueError("empty recorded delivery transaction")
        if batch.delivery_index != self.cursor.delivery_index + 1:
            raise ValueError("noncontiguous delivery index")
        first = events[0]
        if self.mode == "raw":
            frame = self.reader.raw_frame(first.stream_id, first.ingest_ordinal)
            if frame is None:
                # Only explicitly marked supervisor evidence has no raw input.
                # Public controls and observed protocol pongs require their frame.
                if not all(is_local_control(event) for event in events):
                    raise ValueError("missing raw frame for source transaction")
                normalized = self.projection.apply_batch(batch)
            else:
                reference = frame.ref()
                if any(event.raw_ref != reference for event in events):
                    raise ValueError("source transaction raw reference mismatch")
                normalized = self.projection.process_raw(
                    frame,
                    segment_id=first.segment_id,
                    source_mode=first.source_mode,
                    recorded_events=events,
                )
            if dumps(normalized, canonical=False) != dumps(events, canonical=False):
                raise ValueError("raw decode differs from recorded normalized delivery")
        else:
            normalized = self.projection.apply_batch(batch)
        delivered = RecordedBatch(batch.delivery_index, normalized)
        self.clock.advance(first)
        last = events[-1]
        self.cursor = ReplayCursor(
            batch.delivery_index, last.ingest_ordinal, last.event_index
        )
        self._pending = None
        for observer in self.observers:
            observer.on_batch(delivered, self.books(), self.clock)
        return delivered

    def step(self, count: int = 1) -> tuple[RecordedBatch, ...]:
        if type(count) is not int or not 1 <= count <= 100_000:
            raise ValueError("step count must be between 1 and 100000")
        self.pause()
        delivered = []
        for _ in range(count):
            batch = self._peek()
            if batch is None:
                self.status = "finished"
                break
            delivered.append(self._apply(batch))
        return tuple(delivered)

    def next_delay_ns(self) -> int | None:
        """Owner-only lookahead for off-loop paced delivery; never exposes evidence."""
        batch = self._peek()
        return None if batch is None else self.clock.delay_ns(batch.events[0])

    def seek_delivery(self, delivery_index: int) -> ReplayCursor:
        if type(delivery_index) is not int or delivery_index < 0:
            raise ValueError("invalid delivery index")
        if not delivery_index:
            return self.seek(0)
        batches = iter(
            self.reader.iter_batches(
                start_delivery=delivery_index, stop_delivery=delivery_index
            )
        )
        try:
            batch = next(batches, None)
        finally:
            close = getattr(batches, "close", None)
            if close is not None:
                close()
        if batch is None or batch.delivery_index != delivery_index:
            raise ValueError("seek target beyond recorded deliveries")
        last = batch.events[-1]
        return self.seek(last.ingest_ordinal, last.event_index)

    async def play(self, speed: Decimal = Decimal("1")) -> None:
        if self._playing:
            raise RuntimeError("replay already has a playback owner")
        self.clock.set_speed(speed)
        self._playing = True
        self._generation += 1
        generation = self._generation
        self.status = "playing"
        loop = asyncio.get_running_loop()
        pacer = self._pacer = ReplayPacer(loop.time(), self.clock.speed)
        try:
            while self.status == "playing" and generation == self._generation:
                batch = self._peek()
                if batch is None:
                    self.status = "finished"
                    break
                pacer.advance(self.clock.delay_ns(batch.events[0]))
                while self.status == "playing" and generation == self._generation:
                    self._interrupt.clear()
                    remaining = pacer.remaining(loop.time())
                    if not remaining:
                        break
                    try:
                        await asyncio.wait_for(
                            self._interrupt.wait(), timeout=remaining
                        )
                    except asyncio.TimeoutError:
                        pass
                if self.status != "playing" or generation != self._generation:
                    break
                self._apply(batch)
                await asyncio.sleep(0)
        finally:
            self._pacer = None
            self._playing = False
            if self.status == "playing":
                self.status = "paused"

    async def run_to_end(self, speed: Decimal | None = None) -> None:
        if speed is not None:
            await self.play(speed)
            return
        if self._playing:
            raise RuntimeError("replay already has a playback owner")
        self.pause()
        generation = self._generation
        self.status = "playing"
        self._playing = True
        try:
            while generation == self._generation:
                batch = self._peek()
                if batch is None:
                    self.status = "finished"
                    break
                self._apply(batch)
                await asyncio.sleep(0)
        finally:
            self._playing = False
            if self.status == "playing":
                self.status = "paused"

    def seek(self, ordinal: int, event_index: int = 0) -> ReplayCursor:
        if (
            type(ordinal) is not int
            or ordinal < 0
            or type(event_index) is not int
            or event_index < 0
        ):
            raise ValueError("invalid replay seek position")
        self.pause()
        target = (
            0 if ordinal == 0 else self.reader.resolve_delivery(ordinal, event_index)
        )
        self._reset()
        self.checkpoint_status = "not_used"
        if target and not self.observers:
            self._restore_checkpoint(target)
        elif target and self.observers:
            # No persisted state for this exact observer/configuration: bounded
            # replay from the beginning is mandatory, never future-state reuse.
            self.checkpoint_status = "observer_history_recomputed"
        while self.cursor.delivery_index < target:
            batch = self._peek()
            if batch is None:
                raise ValueError("seek target beyond recorded deliveries")
            self._apply(batch)
        self.status = "paused"
        return self.cursor

    def _restore_checkpoint(self, target: int) -> None:
        records = self.reader.iter_checkpoints(before_delivery=target)
        try:
            for delivery, group in groupby(
                records, key=lambda item: item.delivery_index
            ):
                saved = tuple(group)
                if not saved:
                    continue
                try:
                    if any(
                        item.observer_state != saved[0].observer_state for item in saved
                    ):
                        raise ValueError("inconsistent checkpoint auxiliary state")
                    self.projection.restore(
                        (item.checkpoint for item in saved),
                        saved[0].observer_state,
                    )
                except (ValueError, KeyError, TypeError) as exc:
                    self.checkpoint_status = "incompatible:" + str(exc)
                    if self.checkpoint_policy == "reject":
                        raise ValueError(self.checkpoint_status) from exc
                    # Explicit fallback, retaining no partially restored state.
                    self.projection = self._new_projection()
                    return
                self._close_iterator()
                # Pacing frontiers are history-derived too. Scan bounded batches
                # without reducing them; never restore a future clock on seek.
                for batch in self.reader.iter_batches(
                    start_delivery=1, stop_delivery=delivery
                ):
                    self.clock.advance(batch.events[0])
                    last = batch.events[-1]
                    self.cursor = ReplayCursor(
                        batch.delivery_index, last.ingest_ordinal, last.event_index
                    )
                if self.cursor.delivery_index != delivery:
                    raise ValueError("checkpoint delivery is absent")
                self._iterator = iter(
                    self.reader.iter_batches(start_delivery=delivery + 1)
                )
                self.checkpoint_status = "restored"
                return
        finally:
            close = getattr(records, "close", None)
            if close is not None:
                close()

    def books(self) -> tuple[Book, ...]:
        return self.projection.books()

    @property
    def book_hash(self) -> str:
        return self.projection.book_hash

    @property
    def state_hash(self) -> str:
        if not self.observers:
            return self.projection.state_hash
        return sha256(
            dumps(
                (
                    self.projection.state_hash,
                    tuple(
                        (observer.observer_id, observer.version, observer.checkpoint())
                        for observer in self.observers
                    ),
                )
            )
        ).hexdigest()

    def close(self) -> None:
        self.pause()
        self._close_iterator()
