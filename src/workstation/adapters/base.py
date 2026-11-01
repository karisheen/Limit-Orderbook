"""Pure public-feed adapter contracts; network execution belongs to supervision."""

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Protocol

from ..book import BookReducer
from ..events import (
    AssetContext,
    BookUpdate,
    ChecksumInfo,
    Control,
    EventEnvelope,
    EventPayload,
    Gap,
    RawFrame,
    ReferencePrice,
    SegmentId,
    SequenceInfo,
    SubscriptionKey,
    Trade,
)
from ..instruments import Instrument, MetadataSnapshot
from ..precision import nonnegative_int, text
from ..quality import Capabilities, SourceMode


_RFC3339 = re.compile(
    r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})\Z",
    re.ASCII,
)


def parse_rfc3339_ns(value: str | None) -> int | None:
    """Parse supplied exchange timestamps without floating point or lost nanos."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("invalid RFC3339 timestamp")
    match = _RFC3339.fullmatch(value)
    if match is None:
        raise ValueError("invalid RFC3339 timestamp")
    whole, fraction, offset = match.groups()
    if offset != "Z" and (int(offset[1:3]) > 23 or int(offset[4:6]) > 59):
        raise ValueError("invalid RFC3339 offset")
    instant = datetime.fromisoformat(whole + ("+00:00" if offset == "Z" else offset))
    elapsed = instant - datetime(1970, 1, 1, tzinfo=timezone.utc)
    result = (elapsed.days * 86400 + elapsed.seconds) * 1_000_000_000
    result += int((fraction or "").ljust(9, "0"))
    return nonnegative_int(result, "exchange timestamp")


@dataclass(frozen=True)
class TransportRequest:
    method: str
    endpoint_id: str
    body: bytes | None = None
    headers: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        if self.method not in ("GET", "POST", "WS"):
            raise ValueError("unsupported public transport method")
        text(self.endpoint_id)
        if self.body is not None and (
            not isinstance(self.body, bytes) or len(self.body) > 4_194_304
        ):
            raise ValueError("invalid request body")
        object.__setattr__(self, "headers", tuple(tuple(pair) for pair in self.headers))
        for pair in self.headers:
            if len(pair) != 2:
                raise ValueError("invalid header")
            for value in pair:
                text(value)
            if pair[0].lower() in ("authorization", "cookie", "x-api-key"):
                raise ValueError("public adapters must not supply credentials")


@dataclass(frozen=True)
class DecodedEvent:
    payload: EventPayload
    sequence: SequenceInfo | None = None
    checksum: ChecksumInfo | None = None
    exchange_event_wall_ns: int | None = None
    exchange_time_kind: str | None = None

    def __post_init__(self):
        if not isinstance(
            self.payload,
            (BookUpdate, Trade, ReferencePrice, AssetContext, Control, Gap),
        ):
            raise ValueError("invalid decoded payload")
        if self.sequence is not None and not isinstance(self.sequence, SequenceInfo):
            raise ValueError("invalid decoded sequence")
        if self.checksum is not None and not isinstance(self.checksum, ChecksumInfo):
            raise ValueError("invalid decoded checksum")
        if self.exchange_event_wall_ns is not None:
            nonnegative_int(self.exchange_event_wall_ns)
        if self.exchange_time_kind is not None:
            text(self.exchange_time_kind)
            if self.exchange_event_wall_ns is None:
                raise ValueError("exchange time kind without time")


@dataclass(frozen=True)
class DecodeError:
    code: str
    raw_hash: str
    detail: str

    def __post_init__(self):
        text(self.code)
        if len(self.raw_hash) != 64 or any(
            c not in "0123456789abcdef" for c in self.raw_hash
        ):
            raise ValueError("invalid raw SHA256")
        if not isinstance(self.detail, str) or len(self.detail) > 4096:
            raise ValueError("decode error detail too long")


@dataclass(frozen=True)
class DecodeResult:
    events: tuple[DecodedEvent, ...] = ()
    controls: tuple[Control, ...] = ()
    error: DecodeError | None = None

    def __post_init__(self):
        object.__setattr__(self, "events", tuple(self.events))
        object.__setattr__(self, "controls", tuple(self.controls))
        if any(not isinstance(event, DecodedEvent) for event in self.events):
            raise ValueError("invalid decoded events")
        if any(not isinstance(control, Control) for control in self.controls):
            raise ValueError("invalid decoded controls")
        if self.error is not None:
            if not isinstance(self.error, DecodeError) or self.events or self.controls:
                raise ValueError("decode failure cannot contain partial events")


class Adapter(Protocol):
    async def discover(self) -> MetadataSnapshot: ...
    def capabilities(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> Capabilities: ...
    def subscription_requests(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> tuple[TransportRequest, ...]: ...
    def snapshot_request(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> TransportRequest | None: ...
    def ws_endpoint(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> str | None: ...
    def decode(
        self, frame: RawFrame, instrument: Instrument, key: SubscriptionKey
    ) -> DecodeResult: ...
    def new_reducer(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> BookReducer: ...


def wrap_decoded(
    frame: RawFrame,
    instrument: Instrument,
    key: SubscriptionKey,
    result: DecodeResult,
    segment_id: SegmentId,
    source_mode: SourceMode,
) -> tuple[EventEnvelope, ...]:
    """Attach receive provenance without inventing exchange/process/publish timestamps.

    Sequence-bearing ack/heartbeat payloads belong in ordered ``events`` as
    DecodedEvent(Control(...), sequence=...). Unsequenced ``controls`` append after
    events; decoders needing interleaving put every payload in events instead.
    """
    if result.error is not None:
        raise ValueError(f"decode failed: {result.error.code}")
    if (key.instrument_id, key.venue, key.api_family) != (
        instrument.instrument_id,
        instrument.venue,
        instrument.api_family,
    ):
        raise ValueError("decoded subscription identity mismatch")
    decoded = result.events + tuple(
        DecodedEvent(control) for control in result.controls
    )
    return tuple(
        EventEnvelope(
            session_id=frame.session_id,
            segment_id=segment_id,
            stream_id=frame.stream_id,
            key=key,
            instrument_metadata_version=instrument.metadata_version,
            connection_id=frame.connection_id,
            connection_epoch=frame.connection_epoch,
            clock_id=frame.clock_id,
            ingest_ordinal=frame.ingest_ordinal,
            event_index=index,
            receive_wall_ns=frame.receive_wall_ns,
            receive_mono_ns=frame.receive_mono_ns,
            payload=event.payload,
            exchange_event_wall_ns=event.exchange_event_wall_ns,
            exchange_time_kind=event.exchange_time_kind,
            source_mode=source_mode,
            sequence=event.sequence,
            checksum=event.checksum,
        )
        for index, event in enumerate(decoded)
    )
