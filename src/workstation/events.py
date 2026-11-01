"""Immutable receive-order events. Wall clocks never determine replay ordering."""

from dataclasses import dataclass, replace
from decimal import Decimal
from hashlib import sha256
from typing import TypeAlias

from .instruments import Identifier, Instrument, InstrumentId, AssetId
from .precision import MAX_MARKET_DATA_BYTES, choice, decimal, nonnegative_int, text
from .quality import SOURCE_MODES, SourceMode

SCHEMA_VERSION = 1
_EMPTY_PAYLOAD_SHA256 = sha256(b"").hexdigest()


class SessionId(Identifier):
    pass


class SegmentId(Identifier):
    pass


class StreamId(Identifier):
    pass


class ClientId(Identifier):
    pass


class LeaseId(Identifier):
    pass


class WallNs(int):
    def __new__(cls, value: int):
        return super().__new__(cls, nonnegative_int(value))


class MonoNs(WallNs):
    pass


class Ordinal(WallNs):
    pass


@dataclass(frozen=True)
class SubscriptionKey:
    venue: str
    api_family: str
    instrument_id: InstrumentId
    channel: str = "book"
    depth: int | None = None
    aggregation: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        for value in (self.venue, self.api_family, self.channel):
            text(value)
        object.__setattr__(self, "instrument_id", InstrumentId(self.instrument_id))
        if self.depth is not None and not 1 <= nonnegative_int(self.depth) <= 100_000:
            raise ValueError("invalid subscription depth")
        pairs = tuple(tuple(pair) for pair in self.aggregation)
        for pair in pairs:
            if len(pair) != 2:
                raise ValueError("invalid aggregation pair")
            for value in pair:
                text(value)
        if len({pair[0] for pair in pairs}) != len(pairs):
            raise ValueError("duplicate aggregation key")
        object.__setattr__(self, "aggregation", tuple(sorted(pairs)))

    @classmethod
    def for_instrument(cls, instrument: Instrument, **options):
        return cls(
            instrument.venue, instrument.api_family, instrument.instrument_id, **options
        )


@dataclass(frozen=True)
class RawFrame:
    session_id: SessionId
    stream_id: StreamId
    connection_id: str
    connection_epoch: int
    clock_id: str
    ingest_ordinal: int
    receive_wall_ns: int
    receive_mono_ns: int
    transport: str
    endpoint_id: str
    payload: bytes
    payload_sha256: str = ""

    def __post_init__(self):
        for name, cls in (("session_id", SessionId), ("stream_id", StreamId)):
            object.__setattr__(self, name, cls(getattr(self, name)))
        for identity_text in (
            self.connection_id,
            self.clock_id,
            self.transport,
            self.endpoint_id,
        ):
            text(identity_text)
        for counter in (
            self.connection_epoch,
            self.ingest_ordinal,
            self.receive_wall_ns,
            self.receive_mono_ns,
        ):
            nonnegative_int(counter)
        if (
            not isinstance(self.payload, bytes)
            or len(self.payload) > MAX_MARKET_DATA_BYTES
        ):
            raise ValueError("invalid raw payload or message size")
        digest = sha256(self.payload).hexdigest()
        if self.payload_sha256 and self.payload_sha256 != digest:
            raise ValueError("raw payload hash mismatch")
        object.__setattr__(self, "payload_sha256", digest)

    def ref(self) -> "RawRef":
        """Stable logical stream reference, independent of rotated storage paths."""
        return RawRef(
            f"raw:{sha256(self.stream_id.encode()).hexdigest()}",
            self.ingest_ordinal,
            self.payload_sha256,
        )


@dataclass(frozen=True)
class SequenceInfo:
    scope: str
    first: str | None = None
    last: str | None = None
    previous: str | None = None

    def __post_init__(self):
        text(self.scope)
        for value in (self.first, self.last, self.previous):
            if value is not None:
                text(value)


@dataclass(frozen=True)
class ChecksumInfo:
    algorithm: str
    supplied: str
    calculated: str | None = None
    scope: str = "book"
    result: str = "unchecked"

    def __post_init__(self):
        for value in (self.algorithm, self.supplied, self.scope):
            text(value)
        if self.calculated is not None:
            text(self.calculated)
        choice(self.result, ("unchecked", "match", "mismatch"), "checksum result")
        if self.result == "match" and self.calculated != self.supplied:
            raise ValueError("inconsistent checksum match")
        if self.result == "mismatch" and (
            self.calculated is None or self.calculated == self.supplied
        ):
            raise ValueError("inconsistent checksum mismatch")


@dataclass(frozen=True)
class RawRef:
    file_id: str
    frame_ordinal: int
    payload_sha256: str

    def __post_init__(self):
        text(self.file_id)
        nonnegative_int(self.frame_ordinal)
        if len(self.payload_sha256) != 64 or any(
            c not in "0123456789abcdef" for c in self.payload_sha256
        ):
            raise ValueError("invalid SHA256")


def local_control_ref(stream_id: StreamId, ordinal: int) -> RawRef:
    """Explicit supervisor evidence origin; no public frame is claimed."""
    return RawRef(
        f"local:{sha256(stream_id.encode()).hexdigest()}",
        ordinal,
        _EMPTY_PAYLOAD_SHA256,
    )


@dataclass(frozen=True)
class LevelChange:
    side: str
    price: Decimal
    quantity: Decimal
    quantity_semantics: str = "absolute"
    exchange_event_wall_ns: int | None = None

    def __post_init__(self):
        choice(self.side, ("bid", "ask"), "book side")
        choice(
            self.quantity_semantics, ("absolute", "incremental"), "quantity semantics"
        )
        object.__setattr__(self, "price", decimal(self.price, positive=True))
        object.__setattr__(
            self,
            "quantity",
            decimal(self.quantity, nonnegative=self.quantity_semantics == "absolute"),
        )
        if self.exchange_event_wall_ns is not None:
            nonnegative_int(self.exchange_event_wall_ns)


@dataclass(frozen=True)
class BookUpdate:
    kind: str
    changes: tuple[LevelChange, ...]
    subscribed_depth: int | None = None
    reset_reason: str | None = None

    def __post_init__(self):
        choice(self.kind, ("snapshot", "delta"), "book update")
        object.__setattr__(self, "changes", tuple(self.changes))
        if len(self.changes) > 200_000 or any(
            not isinstance(change, LevelChange) for change in self.changes
        ):
            raise ValueError("invalid book changes")
        if (
            self.subscribed_depth is not None
            and not 1 <= nonnegative_int(self.subscribed_depth) <= 100_000
        ):
            raise ValueError("invalid subscribed depth")
        if self.reset_reason is not None:
            text(self.reset_reason)


@dataclass(frozen=True)
class Trade:
    trade_id: str | None
    dedup_key: str
    price: Decimal
    quantity: Decimal
    quantity_unit: str = "base"
    taker_side: str = "unknown"
    side_provenance: str = "unknown"
    is_subscription_history: bool = False

    def __post_init__(self):
        if self.trade_id is not None:
            text(self.trade_id)
        text(self.dedup_key)
        text(self.side_provenance)
        object.__setattr__(self, "price", decimal(self.price, positive=True))
        object.__setattr__(self, "quantity", decimal(self.quantity, positive=True))
        choice(self.quantity_unit, ("base", "contracts"), "quantity unit")
        choice(self.taker_side, ("buy", "sell", "unknown"), "taker side")
        if type(self.is_subscription_history) is not bool:
            raise ValueError("history flag must be boolean")


@dataclass(frozen=True)
class ReferencePrice:
    kind: str
    price: Decimal
    currency: AssetId
    interval_ns: int | None = None

    def __post_init__(self):
        choice(self.kind, ("mid", "mark", "oracle"), "reference price")
        object.__setattr__(self, "price", decimal(self.price, positive=True))
        object.__setattr__(self, "currency", AssetId(self.currency))
        if self.interval_ns is not None and nonnegative_int(self.interval_ns) == 0:
            raise ValueError("interval must be positive")


@dataclass(frozen=True)
class AssetContext:
    mark: Decimal | None = None
    oracle: Decimal | None = None
    funding_rate: Decimal | None = None
    funding_interval_ns: int | None = None
    open_interest: Decimal | None = None
    open_interest_unit: str | None = None

    def __post_init__(self):
        for name in ("mark", "oracle", "funding_rate", "open_interest"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(
                    self,
                    name,
                    decimal(
                        value,
                        positive=name in ("mark", "oracle"),
                        nonnegative=name == "open_interest",
                    ),
                )
        if (
            self.funding_interval_ns is not None
            and nonnegative_int(self.funding_interval_ns) == 0
        ):
            raise ValueError("funding interval must be positive")
        if self.open_interest is not None and self.open_interest_unit is None:
            raise ValueError("open interest requires units")
        if self.open_interest_unit is not None:
            text(self.open_interest_unit)


@dataclass(frozen=True)
class Control:
    kind: str
    reason_code: str = "none"
    detail: str = ""

    def __post_init__(self):
        choice(
            self.kind,
            (
                "ack",
                "heartbeat",
                "rejection",
                "reset",
                "disconnect",
                "source_change",
                "clock_tick",
            ),
            "control",
        )
        text(self.reason_code)
        if not isinstance(self.detail, str) or len(self.detail) > 4096:
            raise ValueError("control detail too large")


@dataclass(frozen=True)
class Gap:
    reason_code: str
    first_missing_ordinal: int | None = None
    last_missing_ordinal: int | None = None
    continuity_lost: bool = True
    capture_lost: bool = False
    affected_streams: tuple[StreamId, ...] = ()

    def __post_init__(self):
        text(self.reason_code)
        for value in (self.first_missing_ordinal, self.last_missing_ordinal):
            if value is not None:
                nonnegative_int(value)
        if (
            self.first_missing_ordinal is not None
            and self.last_missing_ordinal is not None
            and self.first_missing_ordinal > self.last_missing_ordinal
        ):
            raise ValueError("inverted missing ordinal range")
        if (
            type(self.continuity_lost) is not bool
            or type(self.capture_lost) is not bool
        ):
            raise ValueError("gap flags must be boolean")
        object.__setattr__(
            self,
            "affected_streams",
            tuple(StreamId(value) for value in self.affected_streams),
        )


EventPayload: TypeAlias = (
    BookUpdate | Trade | ReferencePrice | AssetContext | Control | Gap
)


@dataclass(frozen=True)
class EventEnvelopeRef:
    session_id: SessionId
    ingest_ordinal: int
    event_index: int
    exchange_event_wall_ns: int | None
    receive_wall_ns: int
    receive_mono_ns: int
    clock_id: str

    def __post_init__(self):
        object.__setattr__(self, "session_id", SessionId(self.session_id))
        text(self.clock_id)
        for value in (
            self.ingest_ordinal,
            self.event_index,
            self.receive_wall_ns,
            self.receive_mono_ns,
        ):
            nonnegative_int(value)
        if self.exchange_event_wall_ns is not None:
            nonnegative_int(self.exchange_event_wall_ns)


@dataclass(frozen=True)
class EventEnvelope:
    session_id: SessionId
    segment_id: SegmentId
    stream_id: StreamId
    key: SubscriptionKey
    instrument_metadata_version: str
    connection_id: str
    connection_epoch: int
    clock_id: str
    ingest_ordinal: int
    receive_wall_ns: int
    receive_mono_ns: int
    payload: EventPayload
    event_index: int = 0
    exchange_event_wall_ns: int | None = None
    exchange_time_kind: str | None = None
    process_start_mono_ns: int | None = None
    process_end_mono_ns: int | None = None
    publish_mono_ns: int | None = None
    source_mode: SourceMode = "ws_snapshot"
    sequence: SequenceInfo | None = None
    checksum: ChecksumInfo | None = None
    raw_ref: RawRef | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self):
        for name, cls in (
            ("session_id", SessionId),
            ("segment_id", SegmentId),
            ("stream_id", StreamId),
        ):
            object.__setattr__(self, name, cls(getattr(self, name)))
        for identity_text in (
            self.instrument_metadata_version,
            self.connection_id,
            self.clock_id,
        ):
            text(identity_text)
        for counter in (
            self.connection_epoch,
            self.ingest_ordinal,
            self.receive_wall_ns,
            self.receive_mono_ns,
            self.event_index,
        ):
            nonnegative_int(counter)
        if (
            self.schema_version != SCHEMA_VERSION
            or type(self.schema_version) is not int
        ):
            raise ValueError("unsupported event schema")
        choice(self.source_mode, SOURCE_MODES, "source mode")
        if not isinstance(self.key, SubscriptionKey) or not isinstance(
            self.payload,
            (BookUpdate, Trade, ReferencePrice, AssetContext, Control, Gap),
        ):
            raise ValueError("invalid event key/payload")
        for evidence, evidence_type in (
            (self.sequence, SequenceInfo),
            (self.checksum, ChecksumInfo),
            (self.raw_ref, RawRef),
        ):
            if evidence is not None and not isinstance(evidence, evidence_type):
                raise ValueError("invalid event evidence")
        if self.exchange_event_wall_ns is not None:
            nonnegative_int(self.exchange_event_wall_ns)
        if self.exchange_time_kind is not None:
            text(self.exchange_time_kind)
            if self.exchange_event_wall_ns is None:
                raise ValueError("exchange time kind without exchange time")
        previous = self.receive_mono_ns
        for name in ("process_start_mono_ns", "process_end_mono_ns", "publish_mono_ns"):
            processing_time = getattr(self, name)
            if processing_time is not None:
                if nonnegative_int(processing_time) < previous:
                    raise ValueError("monotonic processing times regress")
                previous = processing_time

    @property
    def order_key(self) -> tuple[int, int]:
        return self.ingest_ordinal, self.event_index

    def ref(self) -> EventEnvelopeRef:
        return EventEnvelopeRef(
            self.session_id,
            self.ingest_ordinal,
            self.event_index,
            self.exchange_event_wall_ns,
            self.receive_wall_ns,
            self.receive_mono_ns,
            self.clock_id,
        )


_LOCAL_CONTROL_KINDS = frozenset(("source_change", "clock_tick", "reset", "disconnect"))


def is_local_control(event: EventEnvelope) -> bool:
    """Only explicitly marked supervisor controls may lack captured raw input."""
    return (
        event.raw_ref is not None
        and event.raw_ref.file_id.startswith("local:")
        and event.raw_ref.file_id
        == f"local:{sha256(event.stream_id.encode()).hexdigest()}"
        and event.raw_ref.frame_ordinal == event.ingest_ordinal
        and event.raw_ref.payload_sha256 == _EMPTY_PAYLOAD_SHA256
        and (
            isinstance(event.payload, Gap)
            or isinstance(event.payload, Control)
            and event.payload.kind in _LOCAL_CONTROL_KINDS
        )
    )


def synthetic_frame(
    payload: bytes = b"{}",
    *,
    ordinal: int = 1,
    epoch: int = 0,
    mono_ns: int = 1,
    **overrides,
) -> RawFrame:
    frame = RawFrame(
        session_id=SessionId("synthetic-session"),
        stream_id=StreamId("synthetic-stream"),
        connection_id="synthetic-connection",
        connection_epoch=epoch,
        clock_id="synthetic-clock",
        ingest_ordinal=ordinal,
        receive_wall_ns=mono_ns,
        receive_mono_ns=mono_ns,
        transport="synthetic",
        endpoint_id="synthetic",
        payload=payload,
    )
    return replace(frame, **overrides) if overrides else frame


def synthetic_envelope(
    instrument: Instrument,
    payload: EventPayload,
    *,
    ordinal: int = 1,
    epoch: int = 0,
    mono_ns: int | None = None,
    **overrides,
) -> EventEnvelope:
    frame = synthetic_frame(
        ordinal=ordinal, epoch=epoch, mono_ns=ordinal if mono_ns is None else mono_ns
    )
    values = dict(
        session_id=frame.session_id,
        segment_id=SegmentId("synthetic-segment"),
        stream_id=frame.stream_id,
        key=SubscriptionKey.for_instrument(instrument),
        instrument_metadata_version=instrument.metadata_version,
        connection_id=frame.connection_id,
        connection_epoch=epoch,
        clock_id=frame.clock_id,
        ingest_ordinal=ordinal,
        receive_wall_ns=frame.receive_wall_ns,
        receive_mono_ns=frame.receive_mono_ns,
        payload=payload,
        source_mode="demo",
    )
    values.update(overrides)
    if "raw_ref" not in overrides and (
        isinstance(payload, Gap)
        or isinstance(payload, Control)
        and payload.kind in _LOCAL_CONTROL_KINDS
    ):
        values["raw_ref"] = local_control_ref(
            values["stream_id"], values["ingest_ordinal"]
        )
    return EventEnvelope(**values)
