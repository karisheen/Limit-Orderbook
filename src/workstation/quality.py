"""Quality is evidence, not transport activity. All consumers share these gates."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from .precision import choice, decimal, nonnegative_int, text

if TYPE_CHECKING:
    from .book import Book
    from .instruments import Instrument

SourceMode = Literal["ws_delta", "ws_snapshot", "rest_snapshot", "replay", "demo"]
SOURCE_MODES = ("ws_delta", "ws_snapshot", "rest_snapshot", "replay", "demo")
LIFECYCLES = (
    "starting",
    "waiting_snapshot",
    "synchronizing",
    "live",
    "degraded",
    "stale",
    "resynchronizing",
    "disconnected",
    "unsupported",
    "blocked",
)


@dataclass(frozen=True)
class ClockReading:
    clock_id: str
    mono_ns: int
    wall_ns: int | None = None

    def __post_init__(self):
        text(self.clock_id)
        nonnegative_int(self.mono_ns)
        if self.wall_ns is not None:
            nonnegative_int(self.wall_ns)


@dataclass(frozen=True)
class Coverage:
    subscribed_depth: int | None = None
    retained_bid_levels: int = 0
    retained_ask_levels: int = 0
    worst_bid: Decimal | None = None
    worst_ask: Decimal | None = None
    full_depth: bool = False
    source_aggregation: tuple[tuple[str, str], ...] = ()
    expected_cadence_ns: int | None = None

    def __post_init__(self):
        for retained_levels in (self.retained_bid_levels, self.retained_ask_levels):
            nonnegative_int(retained_levels)
        for coverage_bound in (self.subscribed_depth, self.expected_cadence_ns):
            if coverage_bound is not None and nonnegative_int(coverage_bound) == 0:
                raise ValueError("coverage bound must be positive")
        for name in ("worst_bid", "worst_ask"):
            worst_price = getattr(self, name)
            if worst_price is not None:
                object.__setattr__(self, name, decimal(worst_price, positive=True))
        if type(self.full_depth) is not bool:
            raise ValueError("full_depth must be boolean")
        object.__setattr__(
            self,
            "source_aggregation",
            tuple(tuple(pair) for pair in self.source_aggregation),
        )
        for pair in self.source_aggregation:
            if len(pair) != 2:
                raise ValueError("invalid source aggregation")
            for aggregation_text in pair:
                text(aggregation_text)


@dataclass(frozen=True)
class Capabilities:
    snapshot: bool = True
    deltas: bool = False
    trades: bool = False
    sequence_scope: str | None = None
    checksum_scope: str | None = None
    exact_event_flow: bool = False
    reference_prices: frozenset[str] = frozenset()
    asset_context: frozenset[str] = frozenset()

    def __post_init__(self):
        for value in (self.snapshot, self.deltas, self.trades, self.exact_event_flow):
            if type(value) is not bool:
                raise ValueError("capability must be boolean")
        object.__setattr__(self, "reference_prices", frozenset(self.reference_prices))
        object.__setattr__(self, "asset_context", frozenset(self.asset_context))
        if self.exact_event_flow and not self.deltas:
            raise ValueError("exact event flow requires deltas")


@dataclass(frozen=True)
class Quality:
    lifecycle: str = "waiting_snapshot"
    integrity: str = "unknown"
    transport_alive: bool = False
    source_mode: SourceMode = "ws_snapshot"
    recorded_source_mode: SourceMode | None = None
    last_transport_mono_ns: int | None = None
    last_book_observation_mono_ns: int | None = None
    last_book_change_mono_ns: int | None = None
    last_healthy_observation_mono_ns: int | None = None
    clock_id: str = "unset"
    coverage: Coverage = field(default_factory=Coverage)
    capabilities: Capabilities = field(default_factory=Capabilities)
    continuous_since_ordinal: int | None = None
    reason_codes: tuple[str, ...] = ()
    capture_complete: bool = True

    def __post_init__(self):
        choice(self.lifecycle, LIFECYCLES, "lifecycle")
        choice(self.integrity, ("unknown", "valid", "invalid"), "integrity")
        choice(self.source_mode, SOURCE_MODES, "source mode")
        if self.recorded_source_mode is not None:
            choice(self.recorded_source_mode, SOURCE_MODES, "recorded source mode")
        text(self.clock_id)
        for value in (
            self.last_transport_mono_ns,
            self.last_book_observation_mono_ns,
            self.last_book_change_mono_ns,
            self.last_healthy_observation_mono_ns,
            self.continuous_since_ordinal,
        ):
            if value is not None:
                nonnegative_int(value)
        if not isinstance(self.coverage, Coverage) or not isinstance(
            self.capabilities, Capabilities
        ):
            raise ValueError("invalid quality evidence")
        if (
            type(self.transport_alive) is not bool
            or type(self.capture_complete) is not bool
        ):
            raise ValueError("quality flags must be boolean")
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        for reason in self.reason_codes:
            text(reason)


@dataclass(frozen=True)
class EligibilityPolicy:
    max_age_ns: int = 5_000_000_000
    max_asof_skew_ns: int = 1_000_000_000
    allow_snapshot_quotes: bool = True
    require_execution_access: bool = False
    required_capabilities: frozenset[str] = frozenset()
    version: str = "eligibility-v1"

    def __post_init__(self):
        nonnegative_int(self.max_age_ns)
        nonnegative_int(self.max_asof_skew_ns)
        text(self.version)
        object.__setattr__(
            self, "required_capabilities", frozenset(self.required_capabilities)
        )
        if not self.required_capabilities <= {
            "snapshot",
            "deltas",
            "trades",
            "exact_event_flow",
            "sequence",
            "checksum",
        }:
            raise ValueError("unknown required capability")


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    reasons: tuple[str, ...]
    evaluated_at_mono_ns: int
    policy_version: str

    def __post_init__(self):
        nonnegative_int(self.evaluated_at_mono_ns)
        text(self.policy_version)
        object.__setattr__(self, "reasons", tuple(self.reasons))
        if type(self.eligible) is not bool or self.eligible != (not self.reasons):
            raise ValueError("eligibility and reasons disagree")


def evaluate(
    book: "Book",
    instrument: "Instrument",
    purpose: str,
    now: ClockReading,
    policy: EligibilityPolicy = EligibilityPolicy(),
) -> Eligibility:
    choice(
        purpose,
        ("display", "comparison", "depth_walk", "event_ofi", "alert", "paper_fill"),
        "purpose",
    )
    reasons: list[str] = []
    quality = book.quality
    if (book.instrument_id, book.metadata_version) != (
        instrument.instrument_id,
        instrument.metadata_version,
    ):
        reasons.append("instrument_mismatch")
    if not instrument.streamable:
        reasons.append("instrument_" + instrument.status)
    if instrument.metadata_status != "verified" and purpose in (
        "comparison",
        "depth_walk",
        "paper_fill",
    ):
        reasons.append("instrument_metadata_unverified")
    readable_observation = (
        quality.integrity == "valid"
        and quality.last_book_observation_mono_ns is not None
        and quality.last_healthy_observation_mono_ns is not None
    )
    if instrument.data_access in ("blocked", "unsupported") or (
        instrument.data_access == "unknown" and not readable_observation
    ):
        reasons.append("data_access_" + instrument.data_access)
    if policy.require_execution_access and instrument.execution_access != "available":
        reasons.append("execution_access_" + instrument.execution_access)
    if quality.integrity != "valid":
        reasons.append("integrity_" + quality.integrity)
    if quality.lifecycle not in ("live", "degraded"):
        reasons.append("lifecycle_" + quality.lifecycle)
    if quality.lifecycle == "degraded" and quality.source_mode != "rest_snapshot":
        reasons.append("degraded")
    if not quality.transport_alive:
        reasons.append("transport_dead")
    if quality.clock_id != now.clock_id or book.asof.clock_id != now.clock_id:
        reasons.append("clock_mismatch")
    else:
        observed = quality.last_healthy_observation_mono_ns
        if observed is None:
            reasons.append("no_healthy_observation")
        elif observed > now.mono_ns or book.asof.receive_mono_ns > now.mono_ns:
            reasons.append("clock_regression")
        elif now.mono_ns - observed > policy.max_age_ns:
            reasons.append("stale")
    mode = quality.recorded_source_mode or quality.source_mode
    if not policy.allow_snapshot_quotes and mode in ("ws_snapshot", "rest_snapshot"):
        reasons.append("snapshot_quotes_disabled")
    if not book.bids or not book.asks:
        reasons.append("empty_side")
    elif book.bids[0].price >= book.asks[0].price:
        reasons.append("crossed_or_locked")
    required = policy.required_capabilities | (
        {"exact_event_flow"} if purpose == "event_ofi" else set()
    )
    for capability in sorted(required):
        supported = (
            getattr(quality.capabilities, capability + "_scope", None)
            if capability in ("sequence", "checksum")
            else getattr(quality.capabilities, capability)
        )
        if not supported:
            reasons.append("missing_" + capability)
    if purpose == "event_ofi" and (
        quality.continuous_since_ordinal is None
        or mode in ("rest_snapshot", "ws_snapshot")
    ):
        reasons.append("no_event_continuity")
    return Eligibility(
        not reasons, tuple(dict.fromkeys(reasons)), now.mono_ns, policy.version
    )


@dataclass(frozen=True)
class CohortEligibility:
    eligible: bool
    reasons: tuple[str, ...]
    members: tuple[tuple[str, Eligibility], ...]

    def __post_init__(self):
        object.__setattr__(self, "reasons", tuple(self.reasons))
        object.__setattr__(
            self, "members", tuple(tuple(member) for member in self.members)
        )
        if self.eligible != (
            not self.reasons and all(item.eligible for _, item in self.members)
        ):
            raise ValueError("inconsistent cohort eligibility")


def evaluate_cohort(
    books: tuple["Book", ...],
    instruments: tuple["Instrument", ...],
    now: ClockReading,
    policy: EligibilityPolicy = EligibilityPolicy(),
) -> CohortEligibility:
    """Exact native-quote cohorts only. Conversion is a separate explicit cost-engine operation."""
    if len(books) != len(instruments):
        raise ValueError("one metadata record is required per book")
    members = tuple(
        (book.instrument_id, evaluate(book, instrument, "comparison", now, policy))
        for book, instrument in zip(books, instruments, strict=True)
    )
    reasons = []
    if not books:
        reasons.append("empty_cohort")
    identities = {
        (
            item.base.asset_id,
            item.quote.asset_id,
            item.market_type,
            item.settlement.asset_id if item.settlement else None,
            item.expiry_wall_ns,
            item.quantity_unit,
            item.contract_multiplier,
            item.contract_value_currency,
            item.inverse,
        )
        for item in instruments
    }
    if len(identities) > 1:
        reasons.append("incompatible_instruments")
    if len({book.instrument_id for book in books}) != len(books):
        reasons.append("duplicate_instrument")
    if books and len({book.asof.clock_id for book in books} | {now.clock_id}) != 1:
        reasons.append("clock_mismatch")
    elif (
        books
        and max(book.asof.receive_mono_ns for book in books)
        - min(book.asof.receive_mono_ns for book in books)
        > policy.max_asof_skew_ns
    ):
        reasons.append("asof_skew")
    return CohortEligibility(
        not reasons and all(item.eligible for _, item in members),
        tuple(reasons),
        members,
    )


@dataclass(frozen=True)
class TradeEvidence:
    """Independent public trade-channel evidence; never a fabricated book."""

    instrument_id: str
    metadata_version: str
    stream_id: str
    clock_id: str
    connection_epoch: int
    established_mono_ns: int | None = None
    last_validated_mono_ns: int | None = None
    healthy: bool = False
    reason_codes: tuple[str, ...] = ()
    continuity_scope: str = "validated_public_channel_not_matching_engine"

    def __post_init__(self):
        for value in (
            self.instrument_id,
            self.metadata_version,
            self.stream_id,
            self.clock_id,
            self.continuity_scope,
        ):
            text(value)
        nonnegative_int(self.connection_epoch)
        for clock_ns in (self.established_mono_ns, self.last_validated_mono_ns):
            if clock_ns is not None:
                nonnegative_int(clock_ns)
        if type(self.healthy) is not bool:
            raise ValueError("invalid trade evidence health")
        if self.healthy and (
            self.established_mono_ns is None or self.last_validated_mono_ns is None
        ):
            raise ValueError("healthy trade evidence requires establishment")
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        for reason in self.reason_codes:
            text(reason)


def evaluate_trades(
    evidence: TradeEvidence | None,
    instrument: "Instrument",
    now: ClockReading,
    policy: EligibilityPolicy = EligibilityPolicy(),
) -> Eligibility:
    """Gate actual validated trades, independently of book availability."""
    reasons: list[str] = []
    if not instrument.streamable:
        reasons.append("instrument_" + instrument.status)
    established = evidence is not None and evidence.established_mono_ns is not None
    if instrument.data_access in ("blocked", "unsupported") or (
        instrument.data_access == "unknown" and not established
    ):
        reasons.append("data_access_" + instrument.data_access)
    if evidence is None:
        reasons.append("no_trade_evidence")
    else:
        if (evidence.instrument_id, evidence.metadata_version) != (
            instrument.instrument_id,
            instrument.metadata_version,
        ):
            reasons.append("instrument_mismatch")
        if not established:
            reasons.append("trade_history_unanchored")
        if not evidence.healthy:
            reasons.append("trade_channel_unhealthy")
        reasons.extend(evidence.reason_codes)
        if evidence.clock_id != now.clock_id:
            reasons.append("clock_mismatch")
        elif evidence.last_validated_mono_ns is None:
            reasons.append("no_validated_trade_observation")
        elif evidence.last_validated_mono_ns > now.mono_ns:
            reasons.append("clock_regression")
        elif now.mono_ns - evidence.last_validated_mono_ns > policy.max_age_ns:
            reasons.append("stale_trade_channel")
    return Eligibility(
        not reasons, tuple(dict.fromkeys(reasons)), now.mono_ns, policy.version
    )


@dataclass(frozen=True)
class ContextEvidence:
    """Independent validated reference/context evidence, never book health."""

    instrument_id: str
    metadata_version: str
    stream_id: str
    channel: str
    clock_id: str
    last_validated_mono_ns: int | None = None
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self):
        for value in (
            self.instrument_id,
            self.metadata_version,
            self.stream_id,
            self.clock_id,
        ):
            text(value)
        choice(self.channel, ("reference", "context"), "context channel")
        if self.last_validated_mono_ns is not None:
            nonnegative_int(self.last_validated_mono_ns)
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        for reason in self.reason_codes:
            text(reason)


def evaluate_context(
    evidence: ContextEvidence | None,
    instrument: "Instrument",
    now: ClockReading | None,
    policy: EligibilityPolicy = EligibilityPolicy(),
) -> Eligibility:
    """Reference freshness does not imply executable depth or trade continuity."""
    reasons: list[str] = []
    if not instrument.streamable:
        reasons.append("instrument_" + instrument.status)
    established = evidence is not None and evidence.last_validated_mono_ns is not None
    if instrument.data_access in ("blocked", "unsupported") or (
        instrument.data_access == "unknown" and not established
    ):
        reasons.append("data_access_" + instrument.data_access)
    if now is None:
        reasons.append("clock_unavailable")
    if evidence is None:
        reasons.append("no_context_evidence")
    else:
        if (evidence.instrument_id, evidence.metadata_version) != (
            instrument.instrument_id,
            instrument.metadata_version,
        ):
            reasons.append("instrument_mismatch")
        reasons.extend(evidence.reason_codes)
        if evidence.last_validated_mono_ns is None:
            reasons.append("no_validated_context_observation")
        if now is not None:
            if evidence.clock_id != now.clock_id:
                reasons.append("clock_mismatch")
            elif evidence.last_validated_mono_ns is not None:
                if evidence.last_validated_mono_ns > now.mono_ns:
                    reasons.append("clock_regression")
                elif now.mono_ns - evidence.last_validated_mono_ns > policy.max_age_ns:
                    reasons.append("stale_context_channel")
    return Eligibility(
        not reasons,
        tuple(dict.fromkeys(reasons)),
        now.mono_ns if now is not None else 0,
        policy.version,
    )
