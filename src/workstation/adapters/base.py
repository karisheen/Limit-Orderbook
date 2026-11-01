"""Pure public-feed adapter contracts; network execution belongs to supervision."""

from dataclasses import dataclass
from datetime import datetime, timezone
from ipaddress import ip_address
import re
from typing import Protocol
from urllib.parse import parse_qsl, unquote, urlsplit

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
from ..precision import exact_json_loads, nonnegative_int, text
from ..quality import Capabilities, SourceMode


_RFC3339 = re.compile(
    r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})\Z",
    re.ASCII,
)

_PUBLIC_HEADERS = frozenset(("accept", "content-type", "user-agent"))
_PUBLIC_QUERY_FIELDS = frozenset(
    (
        "symbol",
        "limit",
        "pair",
        "count",
        "category",
        "product_type",
        "offset",
        "cursor",
        "product_id",
        "streams",
        "stream",
    )
)
_PUBLIC_BODY_FIELDS = frozenset(
    (
        "type",
        "coin",
        "dex",
        "nSigFigs",
        "mantissa",
        "fast",
        "method",
        "subscription",
        "params",
        "channel",
        "symbol",
        "snapshot",
        "depth",
        "product_ids",
        "op",
        "args",
        "req_id",
        "id",
        "subscribe",
    )
)
_PUBLIC_OPERATIONS = frozenset(("subscribe", "unsubscribe", "ping", "pong"))
_PUBLIC_TYPES = frozenset(
    (
        "subscribe",
        "unsubscribe",
        "ping",
        "pong",
        "spotMeta",
        "meta",
        "metaAndAssetCtxs",
        "perpDexs",
        "l2Book",
        "allMids",
        "spotMetaAndAssetCtxs",
        "trades",
        "activeAssetCtx",
    )
)


def validate_public_endpoint(endpoint: str, *, websocket: bool = False) -> None:
    """Validate public URI syntax before either transport can open a connection."""
    text(endpoint, "public endpoint")
    if (
        any(not c.isascii() or c.isspace() or ord(c) == 127 for c in endpoint)
        or "\\" in endpoint
        or "#" in endpoint
        or re.search(r"%(?![0-9A-Fa-f]{2})", endpoint)
    ):
        raise ValueError("invalid public endpoint")
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != ("wss" if websocket else "https")
        or not parsed.hostname
        or "@" in parsed.netloc
        or (parsed.port is not None and not 0 < parsed.port <= 65535)
    ):
        raise ValueError("public endpoint must be secure and credential-free")
    host = parsed.hostname
    try:
        ip_address(host)
    except ValueError:
        if len(host) > 253 or any(
            not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in host.rstrip(".").split(".")
        ):
            raise ValueError("invalid public endpoint host") from None
    decoded_path = unquote(parsed.path, errors="strict")
    if any(ord(c) < 32 or ord(c) == 127 or c == "\\" for c in decoded_path):
        raise ValueError("invalid public endpoint path")
    for name, value in parse_qsl(
        parsed.query,
        keep_blank_values=True,
        strict_parsing=True,
        max_num_fields=32,
        errors="strict",
    ):
        if name not in _PUBLIC_QUERY_FIELDS:
            raise ValueError("unsupported public query parameter")
        text(value, "public query value")
        if any(ord(c) == 127 for c in value):
            raise ValueError("invalid public query value")


def _validate_public_body(body: bytes) -> None:
    value = exact_json_loads(body, max_bytes=4_194_304)
    if not isinstance(value, dict):
        raise ValueError("public request body must be a JSON object")
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if any(key not in _PUBLIC_BODY_FIELDS for key in item):
                raise ValueError("unsupported public request field")
            for key in ("method", "op"):
                if key in item and (
                    not isinstance(item[key], str)
                    or item[key].lower() not in _PUBLIC_OPERATIONS
                ):
                    raise ValueError("unsupported public request operation")
            if "type" in item and (
                not isinstance(item["type"], str) or item["type"] not in _PUBLIC_TYPES
            ):
                raise ValueError("unsupported public request type")
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


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
        validate_public_endpoint(self.endpoint_id, websocket=self.method == "WS")
        if self.body is not None and (
            not isinstance(self.body, bytes) or len(self.body) > 4_194_304
        ):
            raise ValueError("invalid request body")
        if self.body is not None:
            if self.method == "GET":
                raise ValueError("public GET requests must not supply a body")
            _validate_public_body(self.body)
        object.__setattr__(self, "headers", tuple(tuple(pair) for pair in self.headers))
        for pair in self.headers:
            if len(pair) != 2:
                raise ValueError("invalid header")
            for value in pair:
                text(value)
            if pair[0].lower() not in _PUBLIC_HEADERS:
                raise ValueError("unsupported public request header")


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
