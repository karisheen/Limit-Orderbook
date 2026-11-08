"""Bounded non-executable references, independently qualified from market depth."""

from dataclasses import dataclass
from decimal import Decimal, localcontext
from hashlib import sha256

from .book import Book
from .costs import FXConversion, FXRate, convert_fx
from .events import (
    AssetContext,
    Control,
    EventEnvelope,
    EventEnvelopeRef,
    Gap,
    ReferencePrice,
)
from .instruments import Instrument
from .precision import (
    DECIMAL_CONTEXT,
    DERIVED_DECIMAL_POLICY,
    decimal,
    nonnegative_int,
    text,
)
from .quality import (
    ClockReading,
    ContextEvidence,
    Eligibility,
    EligibilityPolicy,
    evaluate,
    evaluate_context,
)
from .recording import RecordedBatch, StreamSpec
from .replay import ReplayClock
from .wire import dumps, loads

MAX_REFERENCE_STREAMS = 64
MAX_CONTEXT_STREAMS = 64
MAX_INSTRUMENTS = 64
MAX_BASIS_ROWS = MAX_INSTRUMENTS * 3
MAX_STREAMS = 256
POLICY = EligibilityPolicy()
REFERENCE_LABEL = "reference_only_not_executable"


@dataclass(frozen=True)
class ReferenceObservation:
    instrument: Instrument
    stream_id: str
    value: ReferencePrice | None
    asof: EventEnvelopeRef | None
    eligibility: Eligibility
    label: str = REFERENCE_LABEL


@dataclass(frozen=True)
class AssetContextObservation:
    instrument: Instrument
    stream_id: str
    value: AssetContext | None
    asof: EventEnvelopeRef | None
    eligibility: Eligibility
    field_reasons: tuple[tuple[str, tuple[str, ...]], ...]
    funding_rate_unit: str = "fraction_per_funding_interval"
    open_interest_unit_provenance: str = "adapter_declared_not_independently_verified"
    label: str = REFERENCE_LABEL


@dataclass(frozen=True)
class ReferenceBasis:
    spot_instrument_id: str
    perpetual_instrument_id: str
    reference_kind: str
    quote_asset_id: str
    spot_mid: Decimal | None
    perpetual_reference: Decimal | None
    perpetual_quote_asset_id: str
    proceeds_in_spot_quote: Decimal | None
    funding_cost_in_spot_quote: Decimal | None
    proceeds_basis_bps: Decimal | None
    funding_basis_bps: Decimal | None
    proceeds_conversion: FXConversion | None
    funding_conversion: FXConversion | None
    spot_asof: EventEnvelopeRef | None
    perpetual_asof: EventEnvelopeRef | None
    reasons: tuple[str, ...]
    label: str = "reference_basis_not_executable_arbitrage"
    cost_scope: str = (
        "explicit_fx_spread_and_conversion_fee_only_not_trading_fees_or_funding_pnl"
    )

    def __post_init__(self):
        for name in (
            "spot_mid",
            "perpetual_reference",
            "proceeds_in_spot_quote",
            "funding_cost_in_spot_quote",
            "proceeds_basis_bps",
            "funding_basis_bps",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(
                    self, name, decimal(value, policy=DERIVED_DECIMAL_POLICY)
                )
        object.__setattr__(self, "reasons", tuple(self.reasons))


@dataclass(frozen=True)
class ContextSnapshot:
    instrument_id: str
    now: ClockReading | None
    watchlist: tuple[ReferenceObservation, ...]
    contexts: tuple[AssetContextObservation, ...]
    basis: tuple[ReferenceBasis, ...]
    reasons: tuple[str, ...]


class _Channel:
    """Private mutable owner state, deliberately not a persisted wire record."""

    __slots__ = ("observation", "last_event", "reasons")

    def __init__(
        self,
        observation: EventEnvelope | None = None,
        last_event: EventEnvelope | None = None,
        reasons: tuple[str, ...] = (),
    ) -> None:
        self.observation = observation
        self.last_event = last_event
        self.reasons = reasons


_Checkpoint = tuple[
    str,
    str,
    int,
    tuple[tuple[str, EventEnvelope | None, EventEnvelope | None, tuple[str, ...]], ...],
]


def _identity(event: EventEnvelope):
    return event.session_id, event.segment_id, event.clock_id, event.connection_epoch


def _related(left: Instrument, right: Instrument) -> bool:
    # Tickers only discover candidate explanations. They NEVER establish equivalence.
    return (
        left.base.asset_id == right.base.asset_id
        or left.base.ticker == right.base.ticker
        or left.base.reference_asset_id == right.base.asset_id
        or right.base.reference_asset_id == left.base.asset_id
    )


def _same_underlying(left: Instrument, right: Instrument) -> bool:
    # Venue-native codes differ (BTC, XXBT, or absent). Canonical asset IDs
    # establish equivalence; wrapped/native tokens have distinct canonical IDs.
    return left.base.asset_id == right.base.asset_id


class ContextEngine:
    """Latest observations only; reset/restore never retain future context.

    Owned by the same worker as books. A new source identity replaces its channel;
    a channel control invalidates only that channel (plus explicit gap targets).
    Heartbeats/acks never refresh a price. No executable bid/ask is synthesized.
    """

    observer_id = "context_reference"
    version = "1"

    def __init__(self, streams: tuple[StreamSpec, ...]):
        self.streams = tuple(streams)
        if len(self.streams) > MAX_STREAMS:
            raise ValueError("context stream bound exceeded")
        self._specs = {str(spec.stream_id): spec for spec in self.streams}
        if len(self._specs) != len(self.streams):
            raise ValueError("duplicate context stream identity")
        self._instruments: dict[str, dict[str, Instrument]] = {}
        for spec in self.streams:
            key = str(spec.instrument.instrument_id)
            versions = self._instruments.setdefault(key, {})
            previous = versions.setdefault(
                spec.instrument.metadata_version, spec.instrument
            )
            if previous != spec.instrument:
                raise ValueError("ambiguous context instrument metadata version")
        if len(self._instruments) > MAX_INSTRUMENTS:
            raise ValueError("context instrument bound exceeded")
        for channel, maximum in (
            ("reference", MAX_REFERENCE_STREAMS),
            ("context", MAX_CONTEXT_STREAMS),
        ):
            if sum(s.key.channel == channel for s in self.streams) > maximum:
                raise ValueError("context channel bound exceeded")
        self._book_instruments = {
            (spec.key, spec.instrument.metadata_version): spec.instrument
            for spec in self.streams
            if spec.key.channel == "book"
        }
        self._signature = sha256(
            dumps(tuple((str(s.stream_id), s.instrument, s.key) for s in self.streams))
        ).hexdigest()
        self.reset()

    def reset(self) -> None:
        self.delivery_index = -1
        self._channels = {
            str(s.stream_id): _Channel()
            for s in self.streams
            if s.key.channel in ("reference", "context")
        }

    def on_batch(
        self, batch: RecordedBatch, books: tuple[Book, ...], clock: ReplayClock
    ) -> None:
        nonnegative_int(batch.delivery_index)
        if batch.delivery_index <= self.delivery_index:
            raise ValueError("context delivery must advance; reset before seek")
        for event in batch.events:
            spec = self._specs.get(str(event.stream_id))
            if spec is None:
                continue
            if (
                event.key != spec.key
                or event.instrument_metadata_version != spec.instrument.metadata_version
            ):
                raise ValueError("context event identity mismatch")
            state = self._channels.get(str(event.stream_id))
            payload = event.payload
            if isinstance(payload, Gap):
                for target in payload.affected_streams:
                    affected = self._channels.get(str(target))
                    if affected is not None:
                        affected.reasons = (
                            "context_continuity_reset",
                            payload.reason_code,
                        )
            if state is None:
                continue
            previous = state.last_event
            if previous is not None and _identity(previous) != _identity(event):
                state = self._channels[str(event.stream_id)] = _Channel()
                previous = None
            if (
                previous is not None
                and event.receive_mono_ns < previous.receive_mono_ns
            ):
                state.reasons = ("context_clock_regression",)
                continue
            state.last_event = event
            if isinstance(payload, Gap) or (
                isinstance(payload, Control)
                and payload.kind
                in ("reset", "disconnect", "source_change", "rejection")
            ):
                state.reasons = ("context_continuity_reset", payload.reason_code)
                continue
            if isinstance(payload, (ReferencePrice, AssetContext)):
                expected = (
                    ReferencePrice if spec.key.channel == "reference" else AssetContext
                )
                if not isinstance(payload, expected):
                    state.reasons = ("context_payload_channel_mismatch",)
                elif (
                    isinstance(payload, ReferencePrice)
                    and payload.currency != spec.instrument.quote.asset_id
                ):
                    state.reasons = ("reference_currency_mismatch",)
                else:
                    state.observation = event
                    state.reasons = ()
        self.delivery_index = batch.delivery_index

    def checkpoint(self) -> bytes:
        return dumps(
            (
                self.version,
                self._signature,
                self.delivery_index,
                tuple(
                    (key, state.observation, state.last_event, state.reasons)
                    for key, state in self._channels.items()
                ),
            )
        )

    def restore(self, state: bytes) -> None:
        version, signature, delivery, rows = loads(state, _Checkpoint)
        if version != self.version or signature != self._signature or delivery < -1:
            raise ValueError("incompatible context checkpoint")
        if (
            len(rows) != len(self._channels)
            or {row[0] for row in rows} != self._channels.keys()
        ):
            raise ValueError("incompatible context checkpoint channels")
        channels = {}
        for key, observation, last_event, reasons in rows:
            spec = self._specs[key]
            for event in (observation, last_event):
                if event is not None and (
                    str(event.stream_id) != key
                    or event.key != spec.key
                    or event.instrument_metadata_version
                    != spec.instrument.metadata_version
                ):
                    raise ValueError("invalid context checkpoint event")
            if observation is not None and not isinstance(
                observation.payload,
                ReferencePrice if spec.key.channel == "reference" else AssetContext,
            ):
                raise ValueError("invalid context checkpoint observation")
            channels[key] = _Channel(observation, last_event, reasons)
        self._channels, self.delivery_index = channels, delivery

    def _gate(
        self, spec: StreamSpec, state: _Channel, now: ClockReading | None
    ) -> Eligibility:
        event = state.observation
        evidence = ContextEvidence(
            str(spec.instrument.instrument_id),
            spec.instrument.metadata_version,
            str(spec.stream_id),
            spec.key.channel,
            event.clock_id if event is not None else "unobserved",
            event.receive_mono_ns if event is not None else None,
            state.reasons,
        )
        return evaluate_context(evidence, spec.instrument, now, POLICY)

    def snapshot(
        self,
        instrument_id: str,
        *,
        books: tuple[Book, ...],
        now: ClockReading | None,
        fx_rates: tuple[FXRate, ...] = (),
    ) -> ContextSnapshot:
        text(instrument_id)
        selected = self._instruments.get(instrument_id)
        watchlist = []
        contexts = []
        for key, state in self._channels.items():
            spec = self._specs[key]
            event = state.observation
            value = event.payload if event is not None else None
            asof = event.ref() if event is not None else None
            gate = self._gate(spec, state, now)
            if spec.key.channel == "reference":
                if value is not None and not isinstance(value, ReferencePrice):
                    raise ValueError("invalid reference observation payload")
                watchlist.append(
                    ReferenceObservation(spec.instrument, key, value, asof, gate)
                )
            elif selected is not None and (
                spec.instrument.instrument_id == instrument_id
                or any(_related(item, spec.instrument) for item in selected.values())
            ):
                if value is not None and not isinstance(value, AssetContext):
                    raise ValueError("invalid asset context observation payload")
                fields = (
                    "mark",
                    "oracle",
                    "funding_rate",
                    "funding_interval_ns",
                    "open_interest",
                )
                field_reasons = tuple(
                    (
                        field,
                        gate.reasons
                        + (
                            ("missing_" + field,)
                            if value is None or getattr(value, field) is None
                            else ()
                        )
                        + (
                            ("missing_funding_interval_ns",)
                            if field == "funding_rate"
                            and value is not None
                            and value.funding_interval_ns is None
                            else ()
                        ),
                    )
                    for field in fields
                )
                contexts.append(
                    AssetContextObservation(
                        spec.instrument, key, value, asof, gate, field_reasons
                    )
                )
        reasons = []
        if selected is None:
            reasons.append("unknown_instrument")
        if now is None:
            reasons.append("clock_unavailable")
        if not any(row.instrument.instrument_id == instrument_id for row in watchlist):
            reasons.append("reference_channel_not_subscribed")
        if not any(row.instrument.instrument_id == instrument_id for row in contexts):
            reasons.append("context_channel_not_subscribed")
        basis = []
        if selected is not None:
            for candidate_id, candidates in self._instruments.items():
                if candidate_id == instrument_id:
                    continue
                # Versions only discover related pairs. Evidence below chooses
                # its own stream-pinned metadata, never a catalogue replacement.
                pair = next(
                    (
                        (left, right) if left.market_type == "spot" else (right, left)
                        for left in selected.values()
                        for right in candidates.values()
                        if {left.market_type, right.market_type}
                        == {"spot", "perpetual"}
                        and _related(left, right)
                    ),
                    None,
                )
                if pair is None:
                    continue
                spot, perp = pair
                for kind in ("mark", "oracle", "mid"):
                    basis.append(self._basis(spot, perp, kind, books, now, fx_rates))
        if not basis:
            reasons.append("no_related_spot_perpetual_pair")
        return ContextSnapshot(
            instrument_id,
            now,
            tuple(watchlist),
            tuple(contexts),
            tuple(basis),
            tuple(reasons),
        )

    def _basis(
        self,
        spot: Instrument,
        perp: Instrument,
        kind: str,
        books: tuple[Book, ...],
        now: ClockReading | None,
        fx_rates: tuple[FXRate, ...],
    ) -> ReferenceBasis:
        reasons = []
        if now is None:
            reasons.append("clock_unavailable")
        candidates = [b for b in books if b.instrument_id == spot.instrument_id]
        book = candidates[0] if len(candidates) == 1 else None
        if not candidates:
            reasons.append("missing_spot_book")
        elif len(candidates) != 1:
            reasons.append("ambiguous_spot_book")
        if book is not None:
            book_instrument = self._book_instruments.get(
                (book.key, book.metadata_version)
            )
            if book_instrument is None:
                reasons.append("spot_book_identity_mismatch")
                book = None
            else:
                spot = book_instrument
        if book is not None and now is not None:
            reasons.extend(evaluate(book, spot, "comparison", now, POLICY).reasons)
        channel = "reference" if kind == "mid" else "context"
        sources = [
            (self._specs[key], state)
            for key, state in self._channels.items()
            if self._specs[key].key.instrument_id == perp.instrument_id
            and self._specs[key].key.channel == channel
        ]
        event = None
        price: Decimal | None = None
        if not sources:
            reasons.append("perpetual_" + channel + "_channel_not_subscribed")
        elif len(sources) != 1:
            reasons.append("ambiguous_perpetual_reference")
        else:
            spec, state = sources[0]
            perp = spec.instrument
            if perp.metadata_status != "verified":
                reasons.append("instrument_metadata_unverified")
            reasons.extend(self._gate(spec, state, now).reasons)
            event = state.observation
            if event is not None:
                payload = event.payload
                if isinstance(payload, ReferencePrice):
                    price = payload.price if payload.kind == kind else None
                elif isinstance(payload, AssetContext):
                    price = payload.mark if kind == "mark" else payload.oracle
                else:
                    raise ValueError("invalid perpetual reference observation payload")
        if not _same_underlying(spot, perp):
            reasons.append("incompatible_underlying_identity")
        if spot.market_type != "spot" or perp.market_type != "perpetual":
            reasons.append("incompatible_market_type")
        if (
            spot.quantity_unit != "base"
            or perp.quantity_unit != "base"
            or perp.inverse
            or perp.contract_multiplier != 1
            or perp.reference_multiplier not in (None, Decimal(1))
        ):
            reasons.append("unsupported_reference_units")
        if price is None:
            reasons.append("missing_perpetual_" + kind)
        if event is not None and book is not None:
            if book.asof.clock_id != event.clock_id:
                reasons.append("clock_mismatch")
            elif (
                abs(book.asof.receive_mono_ns - event.receive_mono_ns)
                > POLICY.max_asof_skew_ns
            ):
                reasons.append("asof_skew")
        spot_mid = proceeds_value = funding_value = proceeds_bps = funding_bps = None
        proceeds = funding = None
        with localcontext(DECIMAL_CONTEXT):
            if book is not None and book.bids and book.asks:
                spot_mid = (book.bids[0].price + book.asks[0].price) / 2
            if not reasons:
                # Each absent input contributes a gate reason above.
                assert book is not None and now is not None
                assert event is not None and price is not None
                assert spot_mid is not None
                proceeds = convert_fx(
                    price,
                    perp.quote.asset_id,
                    spot.quote.asset_id,
                    rates=fx_rates,
                    now=now,
                    policy=POLICY,
                    reference_mono_ns=event.receive_mono_ns,
                )
                funding = convert_fx(
                    price,
                    spot.quote.asset_id,
                    perp.quote.asset_id,
                    rates=fx_rates,
                    now=now,
                    policy=POLICY,
                    required_input=True,
                    reference_mono_ns=event.receive_mono_ns,
                )
                reasons.extend(proceeds.reasons)
                reasons.extend(funding.reasons)
                for conversion in (proceeds, funding):
                    if (
                        conversion.rate is not None
                        and abs(
                            conversion.rate.asof.mono_ns - book.asof.receive_mono_ns
                        )
                        > POLICY.max_asof_skew_ns
                    ):
                        reasons.append("fx_asof_skew")
                if not reasons:
                    proceeds_value, funding_value = (
                        proceeds.target_amount,
                        funding.source_amount,
                    )
                    # Successful conversions retain both exact currency amounts.
                    assert proceeds_value is not None and funding_value is not None
                    proceeds_bps = (proceeds_value / spot_mid - 1) * 10_000
                    funding_bps = (funding_value / spot_mid - 1) * 10_000
        return ReferenceBasis(
            str(spot.instrument_id),
            str(perp.instrument_id),
            kind,
            str(spot.quote.asset_id),
            spot_mid,
            price,
            str(perp.quote.asset_id),
            proceeds_value,
            funding_value,
            proceeds_bps,
            funding_bps,
            proceeds,
            funding,
            book.asof if book is not None else None,
            event.ref() if event is not None else None,
            tuple(dict.fromkeys(reasons)),
        )
