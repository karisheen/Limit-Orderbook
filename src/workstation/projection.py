"""Single-owner deterministic downstream projection shared by capture and replay.

The recorder owns this projection on its writer thread: it never snapshots a live
supervisor's concurrently mutating reducer. Raw replay uses the live pure decode
and normalized evidence helpers; normalized replay omits only source decoding.
"""

from collections import OrderedDict
from dataclasses import replace
from hashlib import sha256
from typing import TYPE_CHECKING, Iterable

from .adapter_catalog import create_adapter
from .adapters.base import Adapter
from .book import Book, BookCheckpoint, BookReducer, ReducerState
from .events import Control, EventEnvelope, Gap, RawFrame, SegmentId, StreamId
from .ingestion import decode_frame, make_snapshot_reducer, normalize_events
from .quality import SourceMode
from .wire import dumps, loads

if TYPE_CHECKING:
    from .recording import RecordedBatch, StreamSpec

_Auxiliary = tuple[
    int,
    int,
    str,
    tuple[tuple[str, str, int, str, bool, str], ...],
    tuple[tuple[str, str, str, str], ...],
]


def adapter_for_stream(spec: "StreamSpec") -> Adapter:
    """Select only built-in adapters; manifests cannot name executable classes."""
    config = dict(spec.adapter_config)
    if spec.key.venue == "hyperliquid":
        if set(config) - {"dex"}:
            raise ValueError("unsupported Hyperliquid adapter configuration")
        from .adapters.hyperliquid import HyperliquidAdapter

        adapter: Adapter = HyperliquidAdapter(dex=config.get("dex", ""))
    else:
        if config:
            raise ValueError("unsupported adapter configuration")
        adapter = create_adapter(spec.key.venue)
    prototype = adapter.new_reducer(spec.instrument, spec.key)
    if (prototype.adapter_revision, prototype.config_hash) != (
        spec.adapter_revision,
        spec.config_hash,
    ):
        raise ValueError("incompatible recorded adapter revision/configuration")
    return adapter


class RecordingProjection:
    """Bounded stream state; caller serializes access at delivery boundaries."""

    def __init__(
        self,
        streams: Iterable["StreamSpec"] = (),
        *,
        dedup_capacity: int = 50_000,
        poll_interval: float = 2.0,
    ):
        if type(dedup_capacity) is not int or dedup_capacity < 1:
            raise ValueError("invalid dedup capacity")
        if not 0 < poll_interval < float("inf"):
            raise ValueError("invalid polling interval")
        self.dedup_capacity = dedup_capacity
        self.poll_interval = poll_interval
        self.specs: dict[StreamId, StreamSpec] = {}
        self.adapters: dict[StreamId, Adapter] = {}
        self.reducers: dict[StreamId, BookReducer] = {}
        self.modes: dict[StreamId, SourceMode] = {}
        self.dedup: OrderedDict[tuple[str, str, str, str], None] = OrderedDict()
        self._checkpoint_cache: dict[StreamId, BookCheckpoint] = {}
        for spec in streams:
            self.register_stream(spec)

    def register_stream(self, spec: "StreamSpec") -> None:
        if spec.stream_id in self.specs:
            if self.specs[spec.stream_id] != spec:
                raise ValueError("stream metadata replacement is forbidden")
            return
        self.adapters[spec.stream_id] = adapter_for_stream(spec)
        self.specs[spec.stream_id] = spec

    def _start(
        self,
        stream_id: StreamId,
        epoch: int,
        segment_id: SegmentId,
        mode: SourceMode,
        *,
        capture_complete: bool = True,
    ) -> BookReducer:
        spec, adapter = self.specs[stream_id], self.adapters[stream_id]
        if mode == "rest_snapshot":
            reducer = make_snapshot_reducer(
                adapter,
                spec.instrument,
                spec.key,
                epoch=epoch,
                segment_id=segment_id,
                poll_interval=self.poll_interval,
            )
        else:
            reducer = adapter.new_reducer(spec.instrument, spec.key)
            reducer.reset(epoch, segment_id)
        reducer.quality = replace(reducer.quality, capture_complete=capture_complete)
        self.reducers[stream_id], self.modes[stream_id] = reducer, mode
        return reducer

    def _ensure(self, event: EventEnvelope) -> BookReducer:
        spec = self.specs.get(event.stream_id)
        if (
            spec is None
            or spec.key != event.key
            or (spec.instrument.metadata_version != event.instrument_metadata_version)
        ):
            raise ValueError("unregistered or incompatible event stream")
        reducer = self.reducers.get(event.stream_id)
        if isinstance(event.payload, Control) and event.payload.kind == "source_change":
            if reducer is not None and event.connection_epoch <= reducer.epoch:
                raise ValueError("nonadvancing recorded source epoch")
            return self._start(
                event.stream_id,
                event.connection_epoch,
                event.segment_id,
                event.source_mode,
                capture_complete=(
                    (reducer.quality.capture_complete if reducer else True)
                    and event.payload.detail != "capture_incomplete"
                ),
            )
        if reducer is None:
            reducer = self._start(
                event.stream_id,
                event.connection_epoch,
                event.segment_id,
                event.source_mode,
            )
        return reducer

    def apply_batch(self, batch: "RecordedBatch") -> tuple[EventEnvelope, ...]:
        return self.apply_events(batch.events)

    def apply_events(
        self,
        events: tuple[EventEnvelope, ...],
        *,
        decoded: bool = False,
        recorded_events: tuple[EventEnvelope, ...] | None = None,
    ) -> tuple[EventEnvelope, ...]:
        if not events:
            return ()
        first = events[0]
        self._checkpoint_cache.pop(first.stream_id, None)
        reducer = self._ensure(first)
        # Live source controls announce the already-reset reducer, not a second
        # invalidation of its pristine state. Preserve their observer delivery.
        if isinstance(first.payload, Control) and first.payload.kind == "source_change":
            if len(events) != 1:
                raise ValueError("source change must be a separate transaction")
            return events
        reduction = (
            reducer.invalidate(first)
            if len(events) == 1 and isinstance(first.payload, Gap)
            else reducer.apply(events)
        )
        return normalize_events(
            events,
            reduction.events,
            self.dedup,
            self.dedup_capacity,
            recorded_events=recorded_events if decoded else events,
        )

    def process_raw(
        self,
        frame: RawFrame,
        *,
        segment_id: SegmentId,
        source_mode: SourceMode,
        recorded_events: tuple[EventEnvelope, ...] | None = None,
    ) -> tuple[EventEnvelope, ...]:
        spec = self.specs[frame.stream_id]
        events = decode_frame(
            self.adapters[frame.stream_id],
            spec.instrument,
            spec.key,
            frame,
            SegmentId(segment_id),
            source_mode,
        )
        return self.apply_events(events, decoded=True, recorded_events=recorded_events)

    def books(self) -> tuple[Book, ...]:
        return tuple(
            reducer.book
            for _, reducer in sorted(self.reducers.items())
            if reducer.book is not None
        )

    def _checkpoints(self) -> dict[StreamId, BookCheckpoint]:
        for stream, reducer in self.reducers.items():
            if stream not in self._checkpoint_cache:
                self._checkpoint_cache[stream] = reducer.checkpoint()
        return self._checkpoint_cache

    def checkpoints(self) -> tuple[BookCheckpoint, ...]:
        return tuple(
            checkpoint
            for _, checkpoint in sorted(self._checkpoints().items())
            if checkpoint.stream_id is not None
        )

    def checkpoint_state(self) -> bytes:
        streams = []
        checkpoints = self._checkpoints()
        for stream_id, reducer in sorted(self.reducers.items()):
            checkpoint = checkpoints[stream_id]
            streams.append(
                (
                    str(stream_id),
                    self.modes[stream_id],
                    reducer.epoch,
                    str(reducer.segment_id),
                    reducer.quality.capture_complete,
                    checkpoint.state_hash if checkpoint.stream_id is not None else "",
                )
            )
        return dumps(
            (
                1,
                self.dedup_capacity,
                str(self.poll_interval),
                tuple(streams),
                tuple(self.dedup),
            ),
            canonical=False,
        )

    def restore(
        self,
        checkpoints: Iterable[BookCheckpoint],
        auxiliary: bytes,
    ) -> None:
        version, capacity, interval, streams, dedup = loads(auxiliary, _Auxiliary)
        if (
            version != 1
            or capacity != self.dedup_capacity
            or interval != str(self.poll_interval)
        ):
            raise ValueError("incompatible projection checkpoint")
        if len(dedup) > capacity or len(set(dedup)) != len(dedup):
            raise ValueError("invalid checkpoint dedup history")
        saved = {checkpoint.stream_id: checkpoint for checkpoint in checkpoints}
        required = {StreamId(row[0]) for row in streams if row[5]}
        if set(saved) != required or len({row[0] for row in streams}) != len(streams):
            raise ValueError("incomplete projection checkpoint group")
        candidate = RecordingProjection(
            self.specs.values(),
            dedup_capacity=self.dedup_capacity,
            poll_interval=self.poll_interval,
        )
        for stream, mode, epoch, segment, complete, state_hash in streams:
            stream_id = StreamId(stream)
            if mode not in (
                "ws_delta",
                "ws_snapshot",
                "rest_snapshot",
                "demo",
                "replay",
            ):
                raise ValueError("invalid checkpoint source mode")
            reducer = candidate._start(
                stream_id,
                epoch,
                SegmentId(segment),
                mode,
                capture_complete=complete,
            )
            if state_hash:
                checkpoint = saved[stream_id]
                if checkpoint.state_hash != state_hash:
                    raise ValueError("checkpoint group state mismatch")
                reducer.restore(checkpoint)
        candidate.dedup.update((identity, None) for identity in dedup)
        self.reducers, self.modes, self.dedup = (
            candidate.reducers,
            candidate.modes,
            candidate.dedup,
        )
        self._checkpoint_cache.clear()

    @property
    def book_hash(self) -> str:
        return sha256(dumps(self.books())).hexdigest()

    @property
    def state_hash(self) -> str:
        # Last source-envelope payload/checksum differs legitimately after dedup
        # and accepted checksum annotation. It is diagnostic, not future state:
        # identity/order, protocol buffers, quality, levels and dedup are retained.
        states = tuple(
            (
                str(stream),
                self.modes[stream],
                replace(
                    loads(reducer.checkpoint().reducer_state, ReducerState),
                    last_event=None,
                ),
            )
            for stream, reducer in sorted(self.reducers.items())
        )
        return sha256(dumps((states, tuple(self.dedup)))).hexdigest()
