"""Bounded local alerts over admitted native evidence; never order instructions.

Commands take effect *after* their delivery cutoff. The retained command log is
intent, while rules, latches and fires are a replay-prefix projection. Thresholds
are absolute metric levels (a depth shock is a low-depth condition), not an
unstated percentage change or a predictive trading signal.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
from typing import TYPE_CHECKING

from .analytics import (
    AnalyticsEngine,
    AnalyticsSnapshot,
    DisplayEvidence,
    MetricResult,
    MetricWindow,
    TapePrint,
)
from .book import Book, Level
from .costs import FeeSchedule, PairEstimate, StressAssumptions, compare_pair
from .events import EventEnvelopeRef, SubscriptionKey
from .instruments import Instrument
from .precision import (
    DECIMAL_CONTEXT,
    DERIVED_DECIMAL_POLICY,
    decimal,
    nonnegative_int,
    text,
)
from .quality import ClockReading, Eligibility, Quality, TradeEvidence, evaluate
from .wire import dumps, loads

if TYPE_CHECKING:
    from .recording import RecordedBatch, StreamSpec
    from .replay import ReplayClock

MAX_ALERT_RULES = 64
MAX_ALERT_EVENTS = 256
MAX_ALERT_COMMANDS = 4096
MAX_ALERT_EVIDENCE = 200
MAX_ALERT_PRINT_IDENTITIES = 20_000
ALERT_KINDS = (
    "spread_shock",
    "depth_shock",
    "large_executed_print",
    "event_ofi",
    "best_quote_imbalance",
    "persistent_displayed_liquidity",
    "net_cost",
)
_ROLLING_KINDS = ("event_ofi", "persistent_displayed_liquidity")


def alert_unit(instrument: Instrument, kind: str) -> str:
    """Native threshold denomination shared by rule validation and API forms."""
    if kind not in ALERT_KINDS:
        raise ValueError("invalid alert kind")
    if kind == "spread_shock":
        return "bps"
    if kind == "best_quote_imbalance":
        return "ratio"
    if kind == "depth_shock":
        return str(instrument.base.asset_id)
    if kind == "net_cost":
        return str(instrument.quote.asset_id)
    return instrument.quantity_unit + ":" + str(instrument.instrument_id)


def _records(value, cls, limit):
    result = tuple(value)
    if len(result) > limit or any(not isinstance(item, cls) for item in result):
        raise ValueError("alert record type or retention bound")
    return result


def _reasons(value):
    result = tuple(value)
    for item in result:
        text(item)
    if len(result) > 128 or len(set(result)) != len(result):
        raise ValueError("invalid alert reasons")
    return result


def _evidence_limit(metric=None, displayed=(), pair=None, references=()):
    """Reject an oversized evidence graph rather than retain a hidden history."""
    if metric is not None and (
        len(metric.references) > MAX_ALERT_EVIDENCE
        or len(metric.trade_references) > MAX_ALERT_EVIDENCE
    ):
        return "metric_evidence_limit"
    if any(len(row.references) > MAX_ALERT_EVIDENCE for row in displayed):
        return "display_evidence_limit"
    if pair is not None and any(
        len(rows) > MAX_ALERT_EVIDENCE
        for rows in (
            pair.buy.consumed_levels,
            pair.sell.consumed_levels,
            pair.fx_conversions,
            pair.inventory,
            pair.reasons,
            pair.assumptions,
            pair.feasibility_reasons,
            pair.buy.reasons,
            pair.buy.assumptions,
            pair.sell.reasons,
            pair.sell.assumptions,
        )
    ):
        return "pair_evidence_limit"
    if len(references) > MAX_ALERT_EVIDENCE:
        return "reference_evidence_limit"
    return None


@dataclass(frozen=True)
class AlertRule:
    rule_id: str
    key: SubscriptionKey
    kind: str
    direction: str
    threshold: Decimal
    rearm_threshold: Decimal
    unit: str
    window_ns: int = 0
    cooldown_ns: int = 0
    depth_side: str = "bid"
    depth_bps: Decimal = Decimal(10)
    sell_key: SubscriptionKey | None = None
    base_quantity: Decimal | None = None
    buy_fees: FeeSchedule = FeeSchedule()
    sell_fees: FeeSchedule = FeeSchedule()
    stress: StressAssumptions = StressAssumptions()

    def __post_init__(self):
        text(self.rule_id)
        text(self.unit)
        if not isinstance(self.key, SubscriptionKey) or self.key.channel != "book":
            raise ValueError("alert requires an exact book key")
        if self.kind not in ALERT_KINDS or self.direction not in ("above", "below"):
            raise ValueError("invalid alert kind or direction")
        for name in ("threshold", "rearm_threshold"):
            object.__setattr__(self, name, decimal(getattr(self, name)))
        if (self.direction == "above" and self.rearm_threshold >= self.threshold) or (
            self.direction == "below" and self.rearm_threshold <= self.threshold
        ):
            raise ValueError("rearm threshold must lie beyond the non-trigger side")
        nonnegative_int(self.window_ns)
        nonnegative_int(self.cooldown_ns)
        if (self.kind in _ROLLING_KINDS) != (self.window_ns > 0):
            raise ValueError(
                "rolling alerts require a window; instantaneous alerts use zero"
            )
        if self.depth_side not in ("bid", "ask"):
            raise ValueError("invalid depth side")
        object.__setattr__(self, "depth_bps", decimal(self.depth_bps, positive=True))
        if self.depth_bps >= 10000:
            raise ValueError("invalid depth band")
        if not isinstance(self.buy_fees, FeeSchedule) or not isinstance(
            self.sell_fees, FeeSchedule
        ):
            raise ValueError("alert requires typed fee schedules")
        if not isinstance(self.stress, StressAssumptions):
            raise ValueError("alert requires typed cost assumptions")
        if self.kind == "net_cost":
            if (
                not isinstance(self.sell_key, SubscriptionKey)
                or self.sell_key.channel != "book"
            ):
                raise ValueError("net cost requires an exact sell book key")
            if self.base_quantity is None:
                raise ValueError("net cost requires a base quantity")
            object.__setattr__(
                self, "base_quantity", decimal(self.base_quantity, positive=True)
            )
        elif self.sell_key is not None or self.base_quantity is not None:
            raise ValueError("pair inputs only apply to net cost alerts")


@dataclass(frozen=True)
class AlertPutRequest:
    rule: AlertRule

    def __post_init__(self):
        if not isinstance(self.rule, AlertRule):
            raise ValueError("expected alert rule")


@dataclass(frozen=True)
class AlertRemoveRequest:
    rule_id: str

    def __post_init__(self):
        text(self.rule_id)


@dataclass(frozen=True)
class AlertBookEvidence:
    key: SubscriptionKey
    metadata_version: str
    asof: EventEnvelopeRef
    best_bid: Level | None
    best_ask: Level | None
    quality: Quality

    def __post_init__(self):
        if not isinstance(self.key, SubscriptionKey) or self.key.channel != "book":
            raise ValueError("invalid supporting book key")
        text(self.metadata_version)
        if not isinstance(self.asof, EventEnvelopeRef) or not isinstance(
            self.quality, Quality
        ):
            raise ValueError("invalid supporting book evidence")
        for level in (self.best_bid, self.best_ask):
            if level is not None and not isinstance(level, Level):
                raise ValueError("invalid supporting quote")

    @classmethod
    def from_book(cls, book: Book) -> AlertBookEvidence:
        return cls(
            book.key,
            book.metadata_version,
            book.asof,
            book.bids[0] if book.bids else None,
            book.asks[0] if book.asks else None,
            book.quality,
        )


@dataclass(frozen=True)
class AlertEvaluation:
    rule: AlertRule
    delivery_index: int
    now: ClockReading | None
    value: Decimal | None
    eligibility: Eligibility
    window: MetricWindow | None
    armed: bool
    cooldown_remaining_ns: int
    condition_met: bool
    reasons: tuple[str, ...] = ()
    references: tuple[EventEnvelopeRef, ...] = ()
    metric: MetricResult | None = None
    prints: tuple[TapePrint, ...] = ()
    displayed: tuple[DisplayEvidence, ...] = ()
    pair: PairEstimate | None = None
    trade_evidence: TradeEvidence | None = None
    persistence_ns: int = 0
    book_evidence: AlertBookEvidence | None = None

    def __post_init__(self):
        if not isinstance(self.rule, AlertRule) or not isinstance(
            self.eligibility, Eligibility
        ):
            raise ValueError("invalid alert evaluation")
        if self.now is not None and not isinstance(self.now, ClockReading):
            raise ValueError("invalid alert clock")
        if self.window is not None and not isinstance(self.window, MetricWindow):
            raise ValueError("invalid alert window")
        for name, cls in (
            ("metric", MetricResult),
            ("pair", PairEstimate),
            ("trade_evidence", TradeEvidence),
            ("book_evidence", AlertBookEvidence),
        ):
            if getattr(self, name) is not None and not isinstance(
                getattr(self, name), cls
            ):
                raise ValueError("invalid alert evidence")
        nonnegative_int(self.delivery_index)
        nonnegative_int(self.cooldown_remaining_ns)
        nonnegative_int(self.persistence_ns)
        for name in ("armed", "condition_met"):
            if type(getattr(self, name)) is not bool:
                raise ValueError("invalid alert flag")
        if self.value is not None:
            object.__setattr__(
                self, "value", decimal(self.value, policy=DERIVED_DECIMAL_POLICY)
            )
        if self.eligibility.eligible != (self.value is not None):
            raise ValueError("alert eligibility must describe a known value")
        expected_condition = (
            False
            if self.value is None
            else (
                self.value >= self.rule.threshold
                if self.rule.direction == "above"
                else self.value <= self.rule.threshold
            )
        )
        if self.condition_met != expected_condition:
            raise ValueError("alert condition must match its exact threshold")
        if self.value is not None and (self.now is None or self.window is None):
            raise ValueError("eligible alert requires a clock and window")
        if (
            self.metric is not None
            and self.value is not None
            and (self.metric.value != self.value or self.metric.unit != self.rule.unit)
        ):
            raise ValueError("alert metric evidence mismatch")
        if (
            self.pair is not None
            and self.value is not None
            and (
                not self.pair.eligible
                or self.pair.net != self.value
                or str(self.pair.numeraire) != self.rule.unit
            )
        ):
            raise ValueError("alert pair evidence mismatch")
        object.__setattr__(self, "reasons", _reasons(self.reasons))
        for name, evidence_cls in (
            ("references", EventEnvelopeRef),
            ("prints", TapePrint),
            ("displayed", DisplayEvidence),
        ):
            object.__setattr__(
                self,
                name,
                _records(getattr(self, name), evidence_cls, MAX_ALERT_EVIDENCE),
            )
        evidence_limit = _evidence_limit(
            self.metric, self.displayed, self.pair, self.references
        )
        if evidence_limit is not None:
            raise ValueError(evidence_limit)


@dataclass(frozen=True)
class AlertEvent:
    event_id: int
    evaluation: AlertEvaluation

    def __post_init__(self):
        if nonnegative_int(self.event_id) == 0:
            raise ValueError("alert event id must be positive")
        if (
            not isinstance(self.evaluation, AlertEvaluation)
            or not self.evaluation.condition_met
        ):
            raise ValueError("alert fire needs eligible supporting evidence")
        if not self.evaluation.armed or self.evaluation.cooldown_remaining_ns:
            raise ValueError("alert fire must be armed and outside cooldown")


@dataclass(frozen=True)
class AlertSnapshot:
    rules: tuple[AlertRule, ...]
    evaluations: tuple[AlertEvaluation, ...]
    events: tuple[AlertEvent, ...]
    delivery_index: int
    now: ClockReading | None
    dropped_events: int = 0
    reasons: tuple[str, ...] = ()

    def __post_init__(self):
        for name, cls, limit in (
            ("rules", AlertRule, MAX_ALERT_RULES),
            ("evaluations", AlertEvaluation, MAX_ALERT_RULES),
            ("events", AlertEvent, MAX_ALERT_EVENTS),
        ):
            object.__setattr__(self, name, _records(getattr(self, name), cls, limit))
        nonnegative_int(self.delivery_index)
        nonnegative_int(self.dropped_events)
        if self.now is not None and not isinstance(self.now, ClockReading):
            raise ValueError("invalid snapshot clock")
        if len({r.rule_id for r in self.rules}) != len(self.rules):
            raise ValueError("duplicate alert rule")
        if tuple(e.rule for e in self.evaluations) != self.rules:
            raise ValueError("alert evaluations must match visible rules")
        if any(e.evaluation.delivery_index > self.delivery_index for e in self.events):
            raise ValueError("future alert event")
        if any(a.event_id >= b.event_id for a, b in zip(self.events, self.events[1:])):
            raise ValueError("unordered alert events")
        if any(
            e.delivery_index != self.delivery_index or e.now != self.now
            for e in self.evaluations
        ):
            raise ValueError("alert evaluation cutoff mismatch")
        object.__setattr__(self, "reasons", _reasons(self.reasons))


@dataclass(frozen=True)
class AlertCommand:
    sequence: int
    delivery_index: int
    rule_id: str
    rule: AlertRule | None

    def __post_init__(self):
        if nonnegative_int(self.sequence) == 0:
            raise ValueError("alert command sequence must be positive")
        nonnegative_int(self.delivery_index)
        text(self.rule_id)
        if self.rule is not None and (
            not isinstance(self.rule, AlertRule) or self.rule.rule_id != self.rule_id
        ):
            raise ValueError("alert command identity mismatch")


@dataclass(frozen=True)
class AlertLatch:
    rule_id: str
    armed: bool = True
    last_fire: ClockReading | None = None

    def __post_init__(self):
        text(self.rule_id)
        if type(self.armed) is not bool or (
            self.last_fire is not None and not isinstance(self.last_fire, ClockReading)
        ):
            raise ValueError("invalid alert latch")


@dataclass(frozen=True)
class AlertPrintIdentity:
    instrument_id: str
    clock_id: str
    session_id: str
    dedup_key: str

    def __post_init__(self):
        for name in ("instrument_id", "clock_id", "session_id", "dedup_key"):
            text(getattr(self, name))


@dataclass(frozen=True)
class AlertCheckpoint:
    version: str
    streams_hash: str
    config_hash: str
    commands: tuple[AlertCommand, ...]
    applied_commands: int
    snapshot: AlertSnapshot
    latches: tuple[AlertLatch, ...]
    next_event_id: int
    seen_prints: tuple[AlertPrintIdentity, ...]
    dedup_overflow: bool

    def __post_init__(self):
        for name in ("version", "streams_hash", "config_hash"):
            text(getattr(self, name))
        object.__setattr__(
            self, "commands", _records(self.commands, AlertCommand, MAX_ALERT_COMMANDS)
        )
        object.__setattr__(
            self, "latches", _records(self.latches, AlertLatch, MAX_ALERT_RULES)
        )
        object.__setattr__(
            self,
            "seen_prints",
            _records(self.seen_prints, AlertPrintIdentity, MAX_ALERT_PRINT_IDENTITIES),
        )
        if (
            len(set(self.seen_prints)) != len(self.seen_prints)
            or type(self.dedup_overflow) is not bool
        ):
            raise ValueError("invalid alert dedup history")
        nonnegative_int(self.applied_commands)
        if not isinstance(self.snapshot, AlertSnapshot) or self.applied_commands > len(
            self.commands
        ):
            raise ValueError("invalid alert checkpoint projection")
        if nonnegative_int(self.next_event_id) == 0:
            raise ValueError("invalid next alert event id")
        if any(c.sequence != i + 1 for i, c in enumerate(self.commands)) or any(
            a.delivery_index > b.delivery_index
            for a, b in zip(self.commands, self.commands[1:])
        ):
            raise ValueError("unordered alert commands")
        if self.applied_commands != sum(
            c.delivery_index <= self.snapshot.delivery_index for c in self.commands
        ):
            raise ValueError("alert command cutoff mismatch")
        if tuple(latch.rule_id for latch in self.latches) != tuple(
            r.rule_id for r in self.snapshot.rules
        ):
            raise ValueError("alert latch projection mismatch")
        rules: dict[str, AlertRule] = {}
        for command in self.commands[: self.applied_commands]:
            if command.rule is None:
                rules.pop(command.rule_id, None)
            else:
                rules[command.rule_id] = command.rule
        if tuple(rules.values()) != self.snapshot.rules:
            raise ValueError("alert rule projection mismatch")
        if any(e.event_id >= self.next_event_id for e in self.snapshot.events):
            raise ValueError("invalid alert event sequence")


class AlertEngine:
    observer_id = "alerts"
    version = "1"

    def __init__(self, streams: tuple[StreamSpec, ...], analytics: AnalyticsEngine):
        self._commands: list[AlertCommand] = []
        self._install_streams(streams, analytics)
        self.reset()

    def _install_streams(self, streams, analytics):
        streams = tuple(streams)
        if streams != analytics.streams:
            raise ValueError("alerts require the same streams as their analytics owner")
        self.streams, self.analytics = streams, analytics
        self._trade_keys = {
            s.stream_id: s.key for s in streams if s.key.channel in ("trade", "trades")
        }
        self._specs = {s.key: s for s in streams if s.key.channel == "book"}
        self._ambiguous = {
            s.key
            for s in streams
            if s.key.channel == "book" and sum(t.key == s.key for t in streams) > 1
        }

    def reset(self) -> None:
        self.delivery_index = 0
        self.now: ClockReading | None = None
        self._rules: dict[str, AlertRule] = {}
        self._latches: dict[str, AlertLatch] = {}
        self._evaluations: dict[str, AlertEvaluation] = {}
        self._events: deque[AlertEvent] = deque(maxlen=MAX_ALERT_EVENTS)
        self._dropped_events = 0
        self._next_event_id = 1
        self._applied = 0
        self._seen_prints: dict[AlertPrintIdentity, None] = {}
        self._dedup_overflow = False
        self._apply_commands()

    def replace_streams(
        self, streams: tuple[StreamSpec, ...], analytics: AnalyticsEngine
    ) -> None:
        self._install_streams(streams, analytics)
        # No old metric or retained healthy book is a current observation. The
        # latches and cooldown timestamps belong to the unchanged live clock.
        self._evaluations = {
            r.rule_id: self._unavailable(
                r,
                "awaiting_observation"
                if r.key in self._specs
                else "instrument_not_observed",
            )
            for r in self._rules.values()
        }

    def _mutation(self, delivery_index):
        nonnegative_int(delivery_index)
        if delivery_index != self.delivery_index:
            raise ValueError("alert command requires current delivery cutoff")
        if self._applied != len(self._commands):
            raise ValueError("alert_future_commands_fork_required")
        if len(self._commands) >= MAX_ALERT_COMMANDS:
            raise ValueError("alert_command_history_limit")

    def put_rule(self, rule: AlertRule, *, delivery_index: int) -> AlertRule:
        if not isinstance(rule, AlertRule):
            raise ValueError("expected alert rule")
        self._mutation(delivery_index)
        spec = self._specs.get(rule.key)
        if spec is not None:
            if rule.unit != alert_unit(spec.instrument, rule.kind):
                raise ValueError("threshold_unit_mismatch")
        if rule.rule_id not in self._rules and len(self._rules) >= MAX_ALERT_RULES:
            raise ValueError("alert_rule_limit")
        self._commands.append(
            AlertCommand(len(self._commands) + 1, delivery_index, rule.rule_id, rule)
        )
        self._apply_commands()
        return rule

    def remove_rule(self, rule_id: str, *, delivery_index: int) -> None:
        text(rule_id)
        self._mutation(delivery_index)
        if rule_id not in self._rules:
            raise ValueError("unknown_alert_rule")
        self._commands.append(
            AlertCommand(len(self._commands) + 1, delivery_index, rule_id, None)
        )
        self._apply_commands()

    def _apply_commands(self, cutoff=None):
        while self._applied < len(self._commands):
            command = self._commands[self._applied]
            if command.delivery_index > (
                self.delivery_index if cutoff is None else cutoff
            ):
                break
            if command.rule is None:
                self._rules.pop(command.rule_id, None)
                self._latches.pop(command.rule_id, None)
                self._evaluations.pop(command.rule_id, None)
            else:
                self._rules[command.rule_id] = command.rule
                if command.rule_id not in self._latches:
                    self._latches[command.rule_id] = AlertLatch(command.rule_id)
                self._evaluations[command.rule_id] = self._unavailable(
                    command.rule, "awaiting_observation"
                )
            self._applied += 1

    def _remaining(self, rule):
        last = self._latches[rule.rule_id].last_fire
        if last is None or self.now is None or last.clock_id != self.now.clock_id:
            return 0
        return max(0, last.mono_ns + rule.cooldown_ns - self.now.mono_ns)

    def _unavailable(
        self,
        rule,
        *reasons,
        metric=None,
        pair=None,
        trade_evidence=None,
        book_evidence=None,
    ):
        evidence_limit = _evidence_limit(metric=metric, pair=pair)
        if evidence_limit is not None:
            reasons = (*reasons, evidence_limit)
            metric = pair = None
        return AlertEvaluation(
            rule,
            self.delivery_index,
            self.now,
            None,
            Eligibility(
                False,
                tuple(dict.fromkeys(reasons)),
                self.now.mono_ns if self.now else 0,
                self.analytics.config.policy.version,
            ),
            metric.window if metric else None,
            self._latches[rule.rule_id].armed,
            self._remaining(rule),
            False,
            metric=metric,
            pair=pair,
            trade_evidence=trade_evidence,
            book_evidence=book_evidence,
            persistence_ns=self.analytics.config.persistence_ns
            if rule.kind == "persistent_displayed_liquidity"
            else 0,
        )

    def _evaluation(
        self,
        rule,
        value,
        unit,
        window,
        *,
        references=(),
        metric=None,
        prints=(),
        displayed=(),
        pair=None,
        trade_evidence=None,
        book_evidence=None,
    ):
        if self.now is None:
            return self._unavailable(rule, "clock_unavailable")
        evidence_limit = _evidence_limit(metric, displayed, pair, references)
        if evidence_limit is not None:
            return self._unavailable(
                rule,
                evidence_limit,
                trade_evidence=trade_evidence,
                book_evidence=book_evidence,
            )
        if rule.unit != unit:
            return self._unavailable(
                rule, "threshold_unit_mismatch", metric=metric, pair=pair
            )
        met = (
            value >= rule.threshold
            if rule.direction == "above"
            else value <= rule.threshold
        )
        return AlertEvaluation(
            rule,
            self.delivery_index,
            self.now,
            value,
            Eligibility(
                True, (), self.now.mono_ns, self.analytics.config.policy.version
            ),
            window,
            self._latches[rule.rule_id].armed,
            self._remaining(rule),
            met,
            references=tuple(references),
            metric=metric,
            prints=prints,
            displayed=displayed,
            pair=pair,
            trade_evidence=trade_evidence,
            book_evidence=book_evidence,
            persistence_ns=self.analytics.config.persistence_ns
            if rule.kind == "persistent_displayed_liquidity"
            else 0,
        )

    def _observe(self, rule, batch, books, snapshots, admitted):
        if self.now is None:
            return (self._unavailable(rule, "clock_unavailable"),)
        spec = self._specs.get(rule.key)
        if spec is None:
            return (self._unavailable(rule, "instrument_not_observed"),)
        if rule.key in self._ambiguous:
            return (self._unavailable(rule, "ambiguous_book_key"),)
        if rule.kind == "large_executed_print":
            evidence, gate = self.analytics.trade_evidence(
                str(rule.key.instrument_id), self.now, key=rule.key
            )
            if not gate.eligible:
                return (
                    self._unavailable(rule, *gate.reasons, trade_evidence=evidence),
                )
            if not self.analytics.admitted_tape_complete(batch):
                return (self._unavailable(rule, "admitted_tape_incomplete"),)
            if self._dedup_overflow:
                return (self._unavailable(rule, "trade_dedup_history_incomplete"),)
            prints = tuple(
                p
                for p in admitted
                if self._trade_keys[p.stream_id].instrument_id == rule.key.instrument_id
            )
            if not prints:
                return (self._unavailable(rule, "no_new_executed_print"),)
            # Never use a rolling tape maximum: each admitted non-history print
            # is evaluated once, in actual delivery/event order.
            return tuple(
                self._evaluation(
                    rule,
                    p.trade.quantity,
                    spec.instrument.quantity_unit + ":" + str(rule.key.instrument_id),
                    MetricWindow(
                        p.asof.receive_mono_ns,
                        p.asof.receive_mono_ns,
                        p.asof.clock_id,
                        "executed_print",
                    ),
                    references=(p.asof,),
                    prints=(p,),
                    trade_evidence=evidence,
                )
                for p in prints
            )
        book = books.get(rule.key)
        if book is None:
            return (self._unavailable(rule, "no_book"),)
        gate = evaluate(
            book, spec.instrument, "display", self.now, self.analytics.config.policy
        )
        reasons = gate.reasons + (
            () if book.quality.capture_complete else ("capture_incomplete",)
        )
        if reasons:
            return (
                self._unavailable(
                    rule, *reasons, book_evidence=AlertBookEvidence.from_book(book)
                ),
            )
        if rule.kind == "net_cost":
            sell_spec, sell_book = (
                self._specs.get(rule.sell_key),
                books.get(rule.sell_key),
            )
            if sell_spec is None or sell_book is None:
                return (self._unavailable(rule, "sell_instrument_not_observed"),)
            if rule.sell_key in self._ambiguous:
                return (self._unavailable(rule, "ambiguous_sell_book_key"),)
            if not sell_book.quality.capture_complete:
                return (self._unavailable(rule, "sell_capture_incomplete"),)
            pair = compare_pair(
                book,
                spec.instrument,
                sell_book,
                sell_spec.instrument,
                base_quantity=rule.base_quantity,
                buy_fees=rule.buy_fees,
                sell_fees=rule.sell_fees,
                policy=self.analytics.config.policy,
                now=self.now,
                stress=rule.stress,
            )
            reasons = pair.reasons
            if pair.unfilled_base_quantity:
                reasons = (*reasons, "requested_quantity_unfilled")
            if not pair.eligible or pair.net is None or reasons:
                return (
                    self._unavailable(
                        rule, *(reasons or ("net_cost_unavailable",)), pair=pair
                    ),
                )
            return (
                self._evaluation(
                    rule,
                    pair.net,
                    str(pair.numeraire),
                    MetricWindow(
                        min(book.asof.receive_mono_ns, sell_book.asof.receive_mono_ns),
                        self.now.mono_ns,
                        self.now.clock_id,
                        "latest_comparable_books",
                    ),
                    references=(book.asof, sell_book.asof),
                    pair=pair,
                ),
            )
        if (
            rule.kind in _ROLLING_KINDS
            and rule.window_ns != self.analytics.config.window_ns
        ):
            return (self._unavailable(rule, "unsupported_window"),)
        if (
            rule.kind == "depth_shock"
            and rule.depth_bps not in self.analytics.config.depth_bps
        ):
            return (self._unavailable(rule, "unsupported_depth_band"),)
        snapshot = snapshots.get(rule.key)
        if snapshot is None:
            snapshot = self.analytics.snapshot(
                str(rule.key.instrument_id), self.now, key=rule.key
            )
            snapshots[rule.key] = snapshot
        metric_name = {
            "spread_shock": "spread_bps",
            "best_quote_imbalance": "signed_imbalance",
            "event_ofi": "event_ofi",
            "persistent_displayed_liquidity": "displayed_persistence",
            "depth_shock": f"depth_{rule.depth_side}_{format(rule.depth_bps, 'f')}bps_base",
        }[rule.kind]
        # Use the configured band's spelling, since Decimal('10.0') == Decimal('10').
        if rule.kind == "depth_shock":
            band = next(
                b for b in self.analytics.config.depth_bps if b == rule.depth_bps
            )
            metric_name = f"depth_{rule.depth_side}_{format(band, 'f')}bps_base"
        metric = next(m for m in snapshot.metrics if m.name == metric_name)
        if not metric.eligibility.eligible or metric.value is None:
            return (
                self._unavailable(
                    rule, *(metric.reasons or ("metric_unavailable",)), metric=metric
                ),
            )
        displayed = (
            tuple(
                d
                for d in snapshot.display_evidence
                if d.kind == "displayed_persistence"
            )
            if rule.kind == "persistent_displayed_liquidity"
            else ()
        )
        if len(displayed) > MAX_ALERT_EVIDENCE:
            return (self._unavailable(rule, "display_evidence_limit", metric=metric),)
        if rule.kind == "persistent_displayed_liquidity":
            with localcontext(DECIMAL_CONTEXT):
                if sum((d.quantity for d in displayed), Decimal(0)) != metric.value:
                    return (
                        self._unavailable(
                            rule, "display_evidence_incomplete", metric=metric
                        ),
                    )
        return (
            self._evaluation(
                rule,
                metric.value,
                metric.unit,
                metric.window,
                references=metric.references,
                metric=metric,
                displayed=displayed,
                book_evidence=AlertBookEvidence.from_book(book),
            ),
        )

    def _new_prints(self, batch):
        if self._dedup_overflow:
            return ()
        if not self.analytics.admitted_tape_complete(batch):
            self._dedup_overflow = True
            return ()
        admitted = []
        for item in self.analytics.admitted_tape(batch):
            key = self._trade_keys[item.stream_id]
            identity = AlertPrintIdentity(
                str(key.instrument_id),
                item.asof.clock_id,
                str(item.asof.session_id),
                item.trade.dedup_key,
            )
            if identity in self._seen_prints:
                continue
            if len(self._seen_prints) == MAX_ALERT_PRINT_IDENTITIES:
                # Eviction would allow an old public trade id to fire again.
                # Stop rather than silently weaken deduplication guarantees.
                self._dedup_overflow = True
                return ()
            self._seen_prints[identity] = None
            if not item.trade.is_subscription_history:
                admitted.append(item)
        return tuple(admitted)

    def on_batch(
        self, batch: RecordedBatch, books: tuple[Book, ...], clock: ReplayClock
    ) -> None:
        if batch.delivery_index <= self.delivery_index:
            raise ValueError("alert delivery must advance; reset before rewind")
        if clock.reading is None:
            raise ValueError("alert delivery needs a clock reading")
        changed_clock = (
            self.now is not None and self.now.clock_id != clock.reading.clock_id
        )
        if (
            self.now is not None
            and not changed_clock
            and clock.reading.mono_ns < self.now.mono_ns
        ):
            raise ValueError("alert clock regression")
        self.delivery_index, self.now = batch.delivery_index, clock.reading
        self._apply_commands(batch.delivery_index - 1)
        if changed_clock:
            self._seen_prints.clear()
            self._dedup_overflow = False
        if self._rules:
            book_map = {b.key: b for b in books}
            snapshots: dict[SubscriptionKey, AnalyticsSnapshot] = {}
            admitted = (
                self._new_prints(batch)
                if any(r.kind == "large_executed_print" for r in self._rules.values())
                else ()
            )
            for rule in self._rules.values():
                if changed_clock:
                    self._latches[rule.rule_id] = AlertLatch(rule.rule_id)
                    self._evaluations[rule.rule_id] = self._unavailable(
                        rule, "clock_discontinuity"
                    )
                    continue
                for evaluation in self._observe(
                    rule, batch, book_map, snapshots, admitted
                ):
                    latch = self._latches[rule.rule_id]
                    # Earlier prints in this atomic delivery may have fired or
                    # rearmed; recompute state rather than reuse prebuilt latches.
                    if evaluation.eligibility.eligible:
                        rearm = (
                            evaluation.value <= rule.rearm_threshold
                            if rule.direction == "above"
                            else evaluation.value >= rule.rearm_threshold
                        )
                        if rearm:
                            latch = replace(latch, armed=True)
                        self._latches[rule.rule_id] = latch
                    evaluation = replace(
                        evaluation,
                        armed=latch.armed,
                        cooldown_remaining_ns=self._remaining(rule),
                    )
                    if (
                        evaluation.condition_met
                        and latch.armed
                        and not evaluation.cooldown_remaining_ns
                    ):
                        if len(self._events) == MAX_ALERT_EVENTS:
                            self._dropped_events += 1
                        self._events.append(AlertEvent(self._next_event_id, evaluation))
                        self._next_event_id += 1
                        self._latches[rule.rule_id] = AlertLatch(
                            rule.rule_id, False, self.now
                        )
                    latch = self._latches[rule.rule_id]
                    status = (() if latch.armed else ("awaiting_rearm",)) + (
                        ("cooldown",) if self._remaining(rule) else ()
                    )
                    self._evaluations[rule.rule_id] = replace(
                        evaluation,
                        armed=latch.armed,
                        cooldown_remaining_ns=self._remaining(rule),
                        reasons=status,
                    )
        self._apply_commands()

    def snapshot(self) -> AlertSnapshot:
        return AlertSnapshot(
            tuple(self._rules.values()),
            tuple(self._evaluations[r] for r in self._rules),
            tuple(self._events),
            self.delivery_index,
            self.now,
            self._dropped_events,
            (("event_history_truncated",) if self._dropped_events else ())
            + (("trade_dedup_history_incomplete",) if self._dedup_overflow else ()),
        )

    def checkpoint(self) -> bytes:
        return dumps(
            AlertCheckpoint(
                self.version,
                self.analytics.streams_hash,
                self.analytics.config_hash,
                tuple(self._commands),
                self._applied,
                self.snapshot(),
                tuple(self._latches[r] for r in self._rules),
                self._next_event_id,
                tuple(self._seen_prints),
                self._dedup_overflow,
            ),
            decimal_policy=DERIVED_DECIMAL_POLICY,
        )

    def restore(self, state: bytes) -> None:
        saved = loads(state, AlertCheckpoint, decimal_policy=DERIVED_DECIMAL_POLICY)
        if (saved.version, saved.streams_hash, saved.config_hash) != (
            self.version,
            self.analytics.streams_hash,
            self.analytics.config_hash,
        ):
            raise ValueError("incompatible alert checkpoint")
        commands = tuple(self._commands)
        common = min(len(commands), len(saved.commands))
        if commands[:common] != saved.commands[:common]:
            raise ValueError("conflicting alert command history")
        # An old replay checkpoint cannot erase subsequently configured intent.
        self._commands = list(
            commands if len(commands) >= len(saved.commands) else saved.commands
        )
        snapshot = saved.snapshot
        self.delivery_index, self.now = snapshot.delivery_index, snapshot.now
        self._rules = {r.rule_id: r for r in snapshot.rules}
        self._evaluations = {e.rule.rule_id: e for e in snapshot.evaluations}
        self._latches = {latch.rule_id: latch for latch in saved.latches}
        self._events = deque(snapshot.events, maxlen=MAX_ALERT_EVENTS)
        self._dropped_events, self._next_event_id = (
            snapshot.dropped_events,
            saved.next_event_id,
        )
        self._applied = saved.applied_commands
        self._seen_prints = dict.fromkeys(saved.seen_prints)
        self._dedup_overflow = saved.dedup_overflow
        self._apply_commands()
