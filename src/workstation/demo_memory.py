"""Bounded full-scenario synthetic replay, without files or a pretend manifest."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterator, Mapping

from .demo import demo_source
from .events import RawFrame, SegmentId, StreamId
from .projection import RecordingProjection
from .quality import SourceMode
from .recording import RecordedBatch
from .replay import ReplaySourceDescriptor


@dataclass(frozen=True)
class MemoryReplaySource:
    descriptor: ReplaySourceDescriptor
    batches: tuple[RecordedBatch, ...]
    raw_frames: Mapping[tuple[str, int], RawFrame]
    positions: Mapping[tuple[int, int], int]
    duration_ns: int

    @property
    def total_deliveries(self) -> int:
        return len(self.batches)

    @classmethod
    def demo(cls, *, duration_seconds: int = 180) -> "MemoryReplaySource":
        source = demo_source(duration_seconds=duration_seconds)
        projection = RecordingProjection(source.streams)
        batches: list[RecordedBatch] = []
        frames: dict[tuple[str, int], RawFrame] = {}
        segments: dict[StreamId, tuple[SegmentId, SourceMode]] = {}
        raw_bytes = 0
        for item in source.inputs():
            if isinstance(item, RawFrame):
                raw_bytes += len(item.payload)
                if raw_bytes > 64 * 1024 * 1024:
                    raise ValueError("synthetic source exceeds memory byte budget")
                frames[(str(item.stream_id), item.ingest_ordinal)] = item
                segment, mode = segments[item.stream_id]
                events = projection.process_raw(
                    item, segment_id=segment, source_mode=mode
                )
            else:
                first = item[0]
                segments[first.stream_id] = (first.segment_id, first.source_mode)
                events = projection.apply_batch(RecordedBatch(len(batches) + 1, item))
            if events:
                batches.append(RecordedBatch(len(batches) + 1, events))
                if len(batches) > 100_000:
                    raise ValueError("synthetic source exceeds delivery budget")
        return cls(
            ReplaySourceDescriptor(
                source.session_id,
                source.streams,
                (
                    ("fixture_sha256", source.fixture_hash),
                    ("fixture_version", "3"),
                    ("positioning_scenario", "ramp-signed-v1"),
                    ("duration_seconds", str(duration_seconds)),
                ),
                synthetic=True,
                recording=False,
                provenance=source.provenance,
            ),
            tuple(batches),
            MappingProxyType(frames),
            MappingProxyType(
                {
                    (event.ingest_ordinal, event.event_index): batch.delivery_index
                    for batch in batches
                    for event in batch.events
                }
            ),
            duration_seconds * 1_000_000_000,
        )

    def iter_batches(
        self, *, start_delivery: int = 1, stop_delivery: int | None = None
    ) -> Iterator[RecordedBatch]:
        stop = (
            len(self.batches)
            if stop_delivery is None
            else min(stop_delivery, len(self.batches))
        )
        for index in range(max(1, start_delivery) - 1, stop):
            yield self.batches[index]

    def raw_frame(self, stream_id: str, ordinal: int) -> RawFrame | None:
        return self.raw_frames.get((str(stream_id), ordinal))

    def resolve_delivery(self, ordinal: int, event_index: int = 0) -> int:
        try:
            return self.positions[(ordinal, event_index)]
        except KeyError as exc:
            raise ValueError("seek target is not a source event") from exc

    def iter_checkpoints(self, *, before_delivery: int):
        return iter(())
