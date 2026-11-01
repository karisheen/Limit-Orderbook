"""Offline, unfunded directional experiments over the native replay pipeline.

Labels and delayed taker estimates use the last book AVAILABLE at their exact
receive-clock deadline. A later delivery never supplies an earlier arrival book.
Each observation is independent: these are not portfolio returns or fills.
"""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, fields, is_dataclass, replace
from decimal import Decimal, localcontext
from hashlib import sha256
import heapq
import json
from pathlib import Path
import sqlite3
from time import time_ns
from uuid import uuid4
from typing import get_args

from .analytics import AnalyticsConfig, AnalyticsEngine
from .book import Book
from .costs import FeeSchedule, walk_book
from .precision import (
    DECIMAL_CONTEXT,
    DERIVED_DECIMAL_POLICY,
    decimal,
    nonnegative_int,
    text,
)
from .quality import ClockReading, EligibilityPolicy, evaluate
from .recording import RecordedBatch, RecordingReader, StreamSpec
from .replay import ReplayClock, ReplaySession, ReplaySource
from .wire import dumps

ZERO = Decimal(0)
BPS = Decimal(10000)
SECOND = 1_000_000_000
VERSION = "research-v1"
HYPOTHESES = ("depth_band_imbalance", "event_ofi", "executed_trade_flow")


@dataclass(frozen=True)
class ResearchScenario:
    name: str = "base"
    latency_ns: int = 0
    extra_fee_bps: Decimal = ZERO
    extra_slippage_bps: Decimal = ZERO

    def __post_init__(self):
        text(self.name)
        if len(self.name) > 80:
            raise ValueError("scenario name too long")
        nonnegative_int(self.latency_ns)
        for name in ("extra_fee_bps", "extra_slippage_bps"):
            object.__setattr__(
                self, name, decimal(getattr(self, name), nonnegative=True)
            )


@dataclass(frozen=True)
class ResearchConfig:
    stream_ids: tuple[str, ...] = ()
    sample_interval_ns: int = SECOND
    feature_window_ns: int = 60 * SECOND
    depth_band_bps: Decimal = Decimal(25)
    thresholds: tuple[Decimal, ...] = (ZERO, Decimal("0.25"), Decimal("0.5"))
    horizons_ns: tuple[int, ...] = (SECOND, 5 * SECOND, 30 * SECOND, 60 * SECOND)
    train_fraction: Decimal = Decimal("0.5")
    validation_fraction: Decimal = Decimal("0.25")
    embargo_ns: int = 60 * SECOND
    min_samples: int = 30
    base_quantity: Decimal = Decimal("0.001")
    fees: tuple[tuple[str, FeeSchedule], ...] = ()
    scenarios: tuple[ResearchScenario, ...] = (
        ResearchScenario(),
        ResearchScenario(
            "fee_slippage", extra_fee_bps=Decimal(2), extra_slippage_bps=Decimal(2)
        ),
        ResearchScenario("latency_1s", latency_ns=SECOND),
    )
    max_observations: int = 20_000
    policy: EligibilityPolicy = EligibilityPolicy()

    def __post_init__(self):
        for name in (
            "sample_interval_ns",
            "feature_window_ns",
            "min_samples",
            "max_observations",
        ):
            if nonnegative_int(getattr(self, name)) == 0:
                raise ValueError("research bounds must be positive")
        nonnegative_int(self.embargo_ns)
        if self.max_observations > 100_000:
            raise ValueError("observation bound exceeds 100000")
        for name in ("stream_ids", "thresholds", "horizons_ns", "fees", "scenarios"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        for name in (
            "depth_band_bps",
            "train_fraction",
            "validation_fraction",
            "base_quantity",
        ):
            object.__setattr__(self, name, decimal(getattr(self, name), positive=True))
        object.__setattr__(
            self,
            "thresholds",
            tuple(decimal(v, nonnegative=True) for v in self.thresholds),
        )
        if not 0 < self.depth_band_bps < BPS:
            raise ValueError("invalid depth band")
        if self.train_fraction + self.validation_fraction >= 1:
            raise ValueError("test partition must be nonempty")
        if not 1 <= len(self.thresholds) <= 16 or len(set(self.thresholds)) != len(
            self.thresholds
        ):
            raise ValueError("require 1..16 distinct thresholds")
        if not 1 <= len(self.horizons_ns) <= 8 or len(set(self.horizons_ns)) != len(
            self.horizons_ns
        ):
            raise ValueError("require 1..8 distinct horizons")
        for horizon in self.horizons_ns:
            if nonnegative_int(horizon) == 0:
                raise ValueError("horizons must be positive")
        if not 1 <= len(self.scenarios) <= 8 or any(
            not isinstance(s, ResearchScenario) for s in self.scenarios
        ):
            raise ValueError("require 1..8 scenarios")
        if len({s.name for s in self.scenarios}) != len(self.scenarios):
            raise ValueError("duplicate scenario")
        if (
            self.max_observations * len(self.horizons_ns) * len(self.scenarios)
            > 500_000
        ):
            raise ValueError("research label bound exceeds 500000")
        if len(self.stream_ids) > 32 or len(set(self.stream_ids)) != len(
            self.stream_ids
        ):
            raise ValueError("invalid stream selection")
        if len(self.fees) > 32 or len({i for i, _ in self.fees}) != len(self.fees):
            raise ValueError("invalid fee selection")
        for instrument, fee in self.fees:
            text(instrument)
            if not isinstance(fee, FeeSchedule):
                raise ValueError("explicit native FeeSchedule required")
        if not isinstance(self.policy, EligibilityPolicy):
            raise ValueError("invalid eligibility policy")


@dataclass(frozen=True)
class _ResearchDerived:
    """Use the native p80 derived policy, without loosening input-money bounds."""

    def __post_init__(self):
        for item in fields(self):
            value = getattr(self, item.name)
            if item.type is Decimal or Decimal in get_args(item.type):
                if value is not None:
                    object.__setattr__(
                        self, item.name, decimal(value, policy=DERIVED_DECIMAL_POLICY)
                    )
                elif item.type is Decimal:
                    raise ValueError("missing derived value: " + item.name)


@dataclass(frozen=True)
class ResearchFeature(_ResearchDerived):
    hypothesis: str
    value: Decimal | None
    unit: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ResearchObservation(_ResearchDerived):
    observation_id: int
    stream_id: str
    instrument_id: str
    venue: str
    delivery_index: int
    clock_id: str
    mono_ns: int
    elapsed_ns: int
    regime: str
    mid: Decimal | None
    features: tuple[ResearchFeature, ...]
    continuity: tuple[str, ...]


@dataclass(frozen=True)
class ResearchReceipt(_ResearchDerived):
    delivery_index: int
    book_ordinal: int | None
    mono_ns: int
    buy_price: Decimal | None
    sell_price: Decimal | None
    mid: Decimal | None
    reasons: tuple[str, ...]
    buy_fee_quote: Decimal | None = None
    sell_fee_quote: Decimal | None = None
    buy_depth_slippage_bps: Decimal | None = None
    sell_depth_slippage_bps: Decimal | None = None


@dataclass(frozen=True)
class ResearchLabel(_ResearchDerived):
    observation_id: int
    horizon_ns: int
    scenario: str
    end_elapsed_ns: int
    entry: ResearchReceipt
    exit: ResearchReceipt
    gross_long_bps: Decimal | None
    net_long_bps: Decimal | None
    net_short_bps: Decimal | None
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ResearchStatistics(_ResearchDerived):
    eligible_samples: int
    selected_samples: int
    labeled_samples: int
    net_samples: int
    nonoverlapping_samples: int
    mean_gross_bps: Decimal | None
    expected_net_payoff_bps: Decimal | None
    hit_rate: Decimal | None
    average_win_bps: Decimal | None
    average_loss_bps: Decimal | None
    no_trade_bps: Decimal
    matched_long_bps: Decimal | None
    matched_short_bps: Decimal | None
    exclusions: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class ResearchTrial:
    hypothesis: str
    threshold: Decimal
    horizon_ns: int
    scenario: str
    split: str
    group: str
    statistics: ResearchStatistics


@dataclass(frozen=True)
class ResearchSelection:
    hypothesis: str
    horizon_ns: int
    threshold: Decimal | None
    reason: str


@dataclass(frozen=True)
class ResearchReport:
    attempt_id: str
    version: str
    source_id: str
    source_hash: str
    provenance: str
    synthetic: bool
    config: ResearchConfig
    config_hash: str
    observation_hash: str
    label_hash: str
    duration_ns: int
    train_end_ns: int
    validation_end_ns: int
    observation_count: int
    label_count: int
    split_counts: tuple[tuple[str, int], ...]
    exclusions: tuple[tuple[str, int], ...]
    trials: tuple[ResearchTrial, ...]
    selections: tuple[ResearchSelection, ...]
    outcome: str
    limitations: tuple[str, ...]
    turnover: Decimal | None = None
    drawdown: Decimal | None = None
    portfolio_unavailable_reasons: tuple[str, ...] = (
        "no_sequential_funded_portfolio_model",
    )
    report_hash: str = ""


@dataclass(frozen=True)
class ResearchAttempt:
    attempt_id: str
    started_wall_ns: int
    finished_wall_ns: int | None
    status: str
    source: str
    config_hash: str
    report_hash: str | None
    failure: str | None


# Private exact codec: fixed allowlist, never arbitrary class importing or pickle.
_RECORDS = {
    cls.__name__: cls
    for cls in (
        ResearchConfig,
        ResearchScenario,
        ResearchFeature,
        ResearchObservation,
        ResearchReceipt,
        ResearchLabel,
        ResearchStatistics,
        ResearchTrial,
        ResearchSelection,
        ResearchReport,
        ResearchAttempt,
        FeeSchedule,
        EligibilityPolicy,
    )
}


def _encode(value):
    if is_dataclass(value):
        return {
            "type": type(value).__name__,
            "fields": {f.name: _encode(getattr(value, f.name)) for f in fields(value)},
        }
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, (tuple, frozenset)):
        values = sorted(value) if isinstance(value, frozenset) else value
        return [_encode(item) for item in values]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    raise TypeError("unsupported research value")


def _decode(value):
    if isinstance(value, list):
        return tuple(_decode(v) for v in value)
    if isinstance(value, dict):
        if set(value) == {"decimal"}:
            return Decimal(value["decimal"])
        if set(value) == {"type", "fields"} and value["type"] in _RECORDS:
            return _RECORDS[value["type"]](
                **{k: _decode(v) for k, v in value["fields"].items()}
            )
        raise ValueError("invalid research record")
    return value


def _record_hash(records):
    digest = sha256()
    for record in records:
        digest.update(_json(record).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _json(value):
    return json.dumps(
        _encode(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def _hash(value):
    return sha256(_json(value).encode()).hexdigest()


def _limit(limit):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("query limit must be 1..1000")


class ResearchStore:
    """Single-worker SQLite connection; immutable attempts, atomic final publication.

    A killed worker leaves a visible `running` attempt, never a fabricated success.
    No retention/deletion or favorable-outcome filtering is provided.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self._db.close()
            raise ValueError("unsupported research schema")
        with self._db:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, started TEXT NOT NULL, finished TEXT, status TEXT NOT NULL, source TEXT NOT NULL, config_hash TEXT NOT NULL, config TEXT NOT NULL, report_hash TEXT, failure TEXT, report TEXT)"
            )
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS observations (attempt TEXT NOT NULL REFERENCES attempts(id), ordinal INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(attempt,ordinal))"
            )
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS labels (attempt TEXT NOT NULL REFERENCES attempts(id), ordinal INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(attempt,ordinal))"
            )
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS sources (attempt TEXT PRIMARY KEY REFERENCES attempts(id), payload TEXT NOT NULL)"
            )
            self._db.execute("PRAGMA user_version=1")

    def close(self) -> None:
        self._db.close()

    def list_attempts(self, limit: int = 100) -> tuple[ResearchAttempt, ...]:
        _limit(limit)
        rows = self._db.execute(
            "SELECT id,started,finished,status,source,config_hash,report_hash,failure FROM attempts ORDER BY rowid DESC LIMIT ?",
            (limit,),
        )
        return tuple(
            ResearchAttempt(r[0], int(r[1]), int(r[2]) if r[2] else None, *r[3:])
            for r in rows
        )

    def get_attempt(self, attempt_id: str) -> ResearchAttempt | None:
        row = self._db.execute(
            "SELECT id,started,finished,status,source,config_hash,report_hash,failure FROM attempts WHERE id=?",
            (attempt_id,),
        ).fetchone()
        return (
            None
            if row is None
            else ResearchAttempt(
                row[0], int(row[1]), int(row[2]) if row[2] else None, *row[3:]
            )
        )

    def get_report(self, attempt_id: str) -> ResearchReport | None:
        row = self._db.execute(
            "SELECT report FROM attempts WHERE id=?", (attempt_id,)
        ).fetchone()
        return _decode(json.loads(row[0])) if row and row[0] else None

    def get_config(self, attempt_id: str) -> ResearchConfig | None:
        row = self._db.execute(
            "SELECT config FROM attempts WHERE id=?", (attempt_id,)
        ).fetchone()
        return _decode(json.loads(row[0])) if row else None

    def get_source(self, attempt_id: str) -> tuple[tuple[str, str], ...] | None:
        row = self._db.execute(
            "SELECT payload FROM sources WHERE attempt=?", (attempt_id,)
        ).fetchone()
        return _decode(json.loads(row[0])) if row else None

    def _bind_source(self, attempt, descriptor, source_hash):
        evidence = (
            ("source_id", descriptor.source_id),
            ("provenance", descriptor.provenance),
            ("synthetic", str(descriptor.synthetic)),
            ("source_hash", source_hash),
            ("streams_hash", sha256(dumps(descriptor.streams)).hexdigest()),
        )
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO sources VALUES(?,?)", (attempt, _json(evidence))
            )
            self._db.execute(
                "UPDATE attempts SET source=? WHERE id=? AND status='running'",
                (descriptor.source_id, attempt),
            )

    def list_observations(
        self, attempt_id: str, *, limit: int = 100, offset: int = 0
    ) -> tuple[ResearchObservation, ...]:
        return self._evidence("observations", attempt_id, limit, offset)

    def list_labels(
        self, attempt_id: str, *, limit: int = 100, offset: int = 0
    ) -> tuple[ResearchLabel, ...]:
        return self._evidence("labels", attempt_id, limit, offset)

    def _evidence(self, table, attempt_id, limit, offset):
        _limit(limit)
        nonnegative_int(offset)
        rows = self._db.execute(
            f"SELECT payload FROM {table} WHERE attempt=? ORDER BY ordinal LIMIT ? OFFSET ?",
            (attempt_id, limit, offset),
        )
        return tuple(_decode(json.loads(row[0])) for row in rows)

    def _start(self, source, config):
        attempt = uuid4().hex
        with self._db:
            self._db.execute(
                "INSERT INTO attempts(id,started,status,source,config_hash,config) VALUES(?,?,?,?,?,?)",
                (
                    attempt,
                    str(time_ns()),
                    "running",
                    source[:4096],
                    _hash(config),
                    _json(config),
                ),
            )
        return attempt

    def _finish(self, attempt, report, failure, observer):
        with self._db:
            if observer is not None:
                for table, values in (
                    ("observations", observer.observations),
                    ("labels", observer.labels),
                ):
                    self._db.executemany(
                        f"INSERT INTO {table} VALUES(?,?,?)",
                        ((attempt, i, _json(v)) for i, v in enumerate(values)),
                    )
            cursor = self._db.execute(
                "UPDATE attempts SET finished=?,status=?,failure=?,report=?,report_hash=? WHERE id=? AND status='running'",
                (
                    str(time_ns()),
                    "failed" if failure else "completed",
                    failure,
                    _json(report) if report else None,
                    report.report_hash if report else None,
                    attempt,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("attempt already finalized")


def _continuity(book):
    return (
        str(book.segment_id),
        str(book.connection_epoch),
        book.asof.clock_id,
        str(book.quality.continuous_since_ordinal),
    )


def _split(observation, config, train_end, validation_end):
    """All variants share a split. Closed outcome intervals touching a boundary purge.

    Later partitions embargo [boundary,boundary+embargo). Causal feature warmup
    from earlier partitions is allowed; feature fitting never uses later labels.
    """
    start = observation.elapsed_ns
    end = start + max(config.horizons_ns) + max(s.latency_ns for s in config.scenarios)
    if start < train_end:
        return "purged" if end >= train_end else "train"
    if start < train_end + config.embargo_ns:
        return "embargoed"
    if start < validation_end:
        return "purged" if end >= validation_end else "validation"
    if start < validation_end + config.embargo_ns:
        return "embargoed"
    return "test"


class _ResearchObserver:
    observer_id = "research"
    version = VERSION

    def __init__(self, streams: tuple[StreamSpec, ...], config: ResearchConfig):
        self.streams, self.config = streams, config
        self.specs = {
            str(s.stream_id): s
            for s in streams
            if s.key.channel == "book"
            and (not config.stream_ids or str(s.stream_id) in config.stream_ids)
        }
        if (
            not self.specs
            or len(self.specs) > 32
            or set(config.stream_ids) - self.specs.keys()
        ):
            raise ValueError("select 1..32 known book streams")
        group_bound = 4 + len({s.instrument.venue for s in self.specs.values()})
        trial_bound = (
            len(HYPOTHESES)
            * len(config.thresholds)
            * len(config.horizons_ns)
            * len(config.scenarios)
            * 3
            * group_bound
        )
        if trial_bound > 50_000:
            raise ValueError("research trial bound exceeds 50000")
        self.analytics = AnalyticsEngine(
            streams,
            AnalyticsConfig(
                window_ns=config.feature_window_ns,
                retention_ns=max(config.feature_window_ns, 300 * SECOND),
                depth_bps=(config.depth_band_bps,),
                policy=config.policy,
            ),
        )
        self.reset()

    def reset(self):
        self.analytics.reset()
        self.observations: list[ResearchObservation] = []
        self.labels: list[ResearchLabel] = []
        self.pending: list[tuple[int, int, str, int, int, int]] = []
        self.entries: dict[tuple[int, int], ResearchReceipt] = {}
        self.last_books: dict[str, Book] = {}
        self.last_sample: dict[str, int] = {}
        self.delivery = 0
        self.clock_id: str | None = None
        self.serial = 0
        self.exclusions: Counter[str] = Counter()

    def checkpoint(self):
        raise ValueError("research observer requires replay from prefix")

    def restore(self, state):
        raise ValueError("research observer requires replay from prefix")

    def _receipt(self, observation, book, mono_ns, delivery):
        reasons = []
        if book is None:
            reasons.append("no_book_at_arrival")
        elif _continuity(book) != observation.continuity:
            reasons.append("continuity_changed")
        else:
            reasons.extend(
                evaluate(
                    book,
                    self.specs[observation.stream_id].instrument,
                    "depth_walk",
                    ClockReading(observation.clock_id, mono_ns),
                    self.config.policy,
                ).reasons
            )
            if not book.quality.capture_complete:
                reasons.append("capture_incomplete")
        if reasons:
            return ResearchReceipt(
                delivery,
                book.asof.ingest_ordinal if book else None,
                mono_ns,
                None,
                None,
                None,
                tuple(dict.fromkeys(reasons)),
            )
        instrument = self.specs[observation.stream_id].instrument
        fee = dict(self.config.fees).get(str(instrument.instrument_id), FeeSchedule())
        prices = []
        fees, slippage = [], []
        for side in ("buy", "sell"):
            estimate = walk_book(
                book,
                instrument,
                side,
                base_quantity=self.config.base_quantity,
                fees=fee,
                policy=self.config.policy,
                now=ClockReading(observation.clock_id, mono_ns),
            )
            side_reasons = list(estimate.reasons)
            if (
                estimate.partial
                or estimate.filled_base_quantity != self.config.base_quantity
            ):
                side_reasons.append("partial_or_unfilled_order")
            if estimate.effective_price is None:
                side_reasons.append("unavailable_effective_price")
            reasons.extend(side_reasons)
            prices.append(estimate.effective_price if not side_reasons else None)
            fees.append(estimate.fee_quote_equivalent)
            slippage.append(estimate.slippage_bps)
        return ResearchReceipt(
            delivery,
            book.asof.ingest_ordinal,
            mono_ns,
            prices[0],
            prices[1],
            (book.bids[0].price + book.asks[0].price) / 2,
            tuple(dict.fromkeys(reasons)),
            fees[0],
            fees[1],
            slippage[0],
            slippage[1],
        )

    def _schedule(self, deadline, kind, observation, scenario, horizon=0):
        self.serial += 1
        heapq.heappush(
            self.pending,
            (
                deadline,
                self.serial,
                kind,
                observation.observation_id,
                scenario,
                horizon,
            ),
        )

    def _settle(self, now, books, delivery, inclusive):
        while self.pending and (
            self.pending[0][0] <= now if inclusive else self.pending[0][0] < now
        ):
            deadline, _, kind, oid, scenario_index, horizon = heapq.heappop(
                self.pending
            )
            observation = self.observations[oid]
            receipt = self._receipt(
                observation, books.get(observation.stream_id), deadline, delivery
            )
            key = (oid, scenario_index)
            if kind == "entry":
                self.entries[key] = receipt
                continue
            entry = self.entries[key]
            scenario = self.config.scenarios[scenario_index]
            gross = (
                (receipt.mid / observation.mid - 1) * BPS
                if receipt.mid is not None and observation.mid is not None
                else None
            )
            stress = 2 * (scenario.extra_fee_bps + scenario.extra_slippage_bps)
            long = (
                (receipt.sell_price - entry.buy_price) / observation.mid * BPS - stress
                if receipt.sell_price is not None
                and entry.buy_price is not None
                and observation.mid is not None
                else None
            )
            short = (
                (entry.sell_price - receipt.buy_price) / observation.mid * BPS - stress
                if entry.sell_price is not None
                and receipt.buy_price is not None
                and observation.mid is not None
                else None
            )
            reasons = tuple(dict.fromkeys((*entry.reasons, *receipt.reasons)))
            self.labels.append(
                ResearchLabel(
                    oid,
                    horizon,
                    scenario.name,
                    observation.elapsed_ns + scenario.latency_ns + horizon,
                    entry,
                    receipt,
                    gross,
                    long,
                    short,
                    reasons,
                )
            )
            if horizon == max(self.config.horizons_ns):
                del self.entries[key]

    def _censor(self, reason):
        for _, _, kind, oid, scenario_index, horizon in sorted(self.pending):
            if kind == "entry":
                continue
            observation = self.observations[oid]
            scenario = self.config.scenarios[scenario_index]
            missing = ResearchReceipt(
                self.delivery,
                None,
                observation.mono_ns + scenario.latency_ns + horizon,
                None,
                None,
                None,
                (reason,),
            )
            entry = self.entries.get(
                (oid, scenario_index),
                replace(missing, mono_ns=observation.mono_ns + scenario.latency_ns),
            )
            self.labels.append(
                ResearchLabel(
                    oid,
                    horizon,
                    scenario.name,
                    observation.elapsed_ns + scenario.latency_ns + horizon,
                    entry,
                    missing,
                    None,
                    None,
                    None,
                    (reason,),
                )
            )
        self.pending.clear()
        self.entries.clear()

    def on_batch(
        self, batch: RecordedBatch, books: tuple[Book, ...], clock: ReplayClock
    ):
        now = clock.reading
        if now is None:
            return
        with localcontext(DECIMAL_CONTEXT):
            if self.clock_id is not None and self.clock_id != now.clock_id:
                self._censor("clock_discontinuity")
                self.last_books.clear()
                self.last_sample.clear()
            self.clock_id = now.clock_id
            # Strictly earlier timers see only the preceding delivered prefix.
            self._settle(now.mono_ns, self.last_books, self.delivery, False)
            self.analytics.on_batch(batch, books, clock)
            self.delivery = batch.delivery_index
            by_key = {book.key: book for book in books}
            current = {
                sid: by_key[spec.key]
                for sid, spec in self.specs.items()
                if spec.key in by_key
            }
            # Any observed quality break invalidates pending cross-gap outcomes,
            # even if a later snapshot restores healthy-looking quotes.
            for sid, prior in self.last_books.items():
                book = current.get(sid)
                if (
                    book is None
                    or _continuity(prior) != _continuity(book)
                    or not book.quality.capture_complete
                    or not evaluate(
                        book,
                        self.specs[sid].instrument,
                        "display",
                        now,
                        self.config.policy,
                    ).eligible
                ):
                    self._invalidate_stream(sid, "book_continuity_or_quality_break")
            self.last_books = current
            self._settle(now.mono_ns, current, self.delivery, True)
            touched = {str(event.stream_id) for event in batch.events}
            for sid, spec in self.specs.items():
                if sid not in touched:
                    continue
                last = self.last_sample.get(sid)
                if (
                    last is not None
                    and now.mono_ns - last < self.config.sample_interval_ns
                ):
                    continue
                if len(self.observations) >= self.config.max_observations:
                    raise ValueError("research_observation_budget_exceeded")
                self.last_sample[sid] = now.mono_ns
                metrics = {
                    m.name: m
                    for m in self.analytics.snapshot(
                        spec.instrument.instrument_id, now, key=spec.key
                    ).metrics
                }
                band = format(self.config.depth_band_bps, "f")
                bid, ask = (
                    metrics[f"depth_bid_{band}bps_base"],
                    metrics[f"depth_ask_{band}bps_base"],
                )
                depth_reasons = (*bid.reasons, *ask.reasons)
                depth = None
                if bid.value is not None and ask.value is not None:
                    if bid.value + ask.value:
                        depth = (bid.value - ask.value) / (bid.value + ask.value)
                    else:
                        depth_reasons = (*depth_reasons, "empty_depth_band")
                ofi = metrics["event_ofi"]
                buy, sell, unknown = (
                    metrics[f"trade_{side}_notional"]
                    for side in ("buy", "sell", "unknown")
                )
                flow_reasons = (*buy.reasons, *sell.reasons, *unknown.reasons)
                flow = None
                if unknown.value is not None and unknown.value > 0:
                    flow_reasons = (*flow_reasons, "unknown_aggressor_flow")
                if (
                    not flow_reasons
                    and buy.value is not None
                    and sell.value is not None
                ):
                    if buy.value + sell.value:
                        flow = (buy.value - sell.value) / (buy.value + sell.value)
                    else:
                        flow_reasons = ("no_executed_flow",)
                features: tuple[ResearchFeature, ...] = (
                    ResearchFeature(
                        HYPOTHESES[0],
                        depth,
                        "base_depth_ratio",
                        tuple(dict.fromkeys(depth_reasons)),
                    ),
                    ResearchFeature(HYPOTHESES[1], ofi.value, ofi.unit, ofi.reasons),
                    ResearchFeature(
                        HYPOTHESES[2],
                        flow,
                        "known_side_notional_ratio",
                        tuple(dict.fromkeys(flow_reasons)),
                    ),
                )
                book = current.get(sid)
                if book is not None and not book.quality.capture_complete:
                    features = tuple(
                        replace(
                            f, value=None, reasons=(*f.reasons, "capture_incomplete")
                        )
                        for f in features
                    )
                spread = metrics["spread_bps"].value
                regime = (
                    "unknown"
                    if spread is None
                    else "spread_le_10bps"
                    if spread <= 10
                    else "spread_gt_10bps"
                )
                observation = ResearchObservation(
                    len(self.observations),
                    sid,
                    str(spec.instrument.instrument_id),
                    spec.instrument.venue,
                    batch.delivery_index,
                    now.clock_id,
                    now.mono_ns,
                    clock.virtual_mono_ns,
                    regime,
                    metrics["mid"].value,
                    features,
                    _continuity(book) if book else (),
                )
                self.observations.append(observation)
                for feature in features:
                    self.exclusions.update(feature.reasons)
                for index, scenario in enumerate(self.config.scenarios):
                    self._schedule(
                        now.mono_ns + scenario.latency_ns, "entry", observation, index
                    )
                    for horizon in self.config.horizons_ns:
                        self._schedule(
                            now.mono_ns + scenario.latency_ns + horizon,
                            "exit",
                            observation,
                            index,
                            horizon,
                        )
            self._settle(now.mono_ns, current, self.delivery, True)

    def _invalidate_stream(self, stream_id, reason):
        kept: list[tuple[int, int, str, int, int, int]] = []
        removed: list[tuple[int, int, str, int, int, int]] = []
        for timer in self.pending:
            (
                removed if self.observations[timer[3]].stream_id == stream_id else kept
            ).append(timer)
        self.pending = removed
        # Preserve entry receipts belonging to other streams.
        other_entries = {
            key: value
            for key, value in self.entries.items()
            if self.observations[key[0]].stream_id != stream_id
        }
        self._censor(reason)
        self.entries = other_entries
        self.pending = kept
        heapq.heapify(self.pending)


def _mean(values):
    return sum(values, ZERO) / len(values) if values else None


def _statistics(rows, hypothesis, threshold):
    eligible = selected = labeled = nonoverlap = 0
    gross, net, longs, shorts = [], [], [], []
    exclusions: Counter[str] = Counter()
    occupied: dict[str, int] = {}
    for observation, label in rows:
        feature = next(f for f in observation.features if f.hypothesis == hypothesis)
        if feature.value is None:
            exclusions.update(feature.reasons or ("feature_unavailable",))
            continue
        eligible += 1
        if feature.value == 0 or abs(feature.value) < threshold:
            continue
        selected += 1
        direction = 1 if feature.value > 0 else -1
        exclusions.update(label.reasons)
        if label.gross_long_bps is not None:
            gross.append(direction * label.gross_long_bps)
            labeled += 1
        value = label.net_long_bps if direction == 1 else label.net_short_bps
        if value is not None:
            net.append(value)
            if observation.elapsed_ns > occupied.get(observation.stream_id, -1):
                nonoverlap += 1
                occupied[observation.stream_id] = label.end_elapsed_ns
            if label.net_long_bps is not None:
                longs.append(label.net_long_bps)
            if label.net_short_bps is not None:
                shorts.append(label.net_short_bps)
    wins, losses = [v for v in net if v > 0], [v for v in net if v < 0]
    return ResearchStatistics(
        eligible,
        selected,
        labeled,
        len(net),
        nonoverlap,
        _mean(gross),
        _mean(net),
        Decimal(len(wins)) / len(net) if net else None,
        _mean(wins),
        _mean(losses),
        ZERO,
        _mean(longs),
        _mean(shorts),
        tuple(sorted(exclusions.items())),
    )


def _report(attempt, descriptor, source_hash, duration, observer):
    config = observer.config
    train_end = int(duration * config.train_fraction)
    validation_end = int(
        duration * (config.train_fraction + config.validation_fraction)
    )
    splits = {
        o.observation_id: _split(o, config, train_end, validation_end)
        for o in observer.observations
    }
    split_counts = Counter(splits.values())
    groups: dict[str, Callable[[ResearchObservation], bool]] = {"all": lambda o: True}
    for venue in sorted({o.venue for o in observer.observations}):

        def matches_venue(observation: ResearchObservation, venue: str = venue) -> bool:
            return observation.venue == venue

        groups["venue:" + venue] = matches_venue
    for regime in sorted({o.regime for o in observer.observations}):

        def matches_regime(
            observation: ResearchObservation, regime: str = regime
        ) -> bool:
            return observation.regime == regime

        groups["regime:" + regime] = matches_regime
    buckets: dict[
        tuple[str, int, str], list[tuple[ResearchObservation, ResearchLabel]]
    ] = {}
    for label in observer.labels:
        observation = observer.observations[label.observation_id]
        split = splits[label.observation_id]
        if split not in ("train", "validation", "test"):
            continue
        buckets.setdefault((split, label.horizon_ns, label.scenario), []).append(
            (observation, label)
        )
    trials = []
    index = {}
    for hypothesis in HYPOTHESES:
        for threshold in config.thresholds:
            for horizon in config.horizons_ns:
                for scenario in config.scenarios:
                    for split in ("train", "validation", "test"):
                        rows = sorted(
                            buckets.get((split, horizon, scenario.name), ()),
                            key=lambda row: row[0].observation_id,
                        )
                        for group, matches in groups.items():
                            stats = _statistics(
                                (row for row in rows if matches(row[0])),
                                hypothesis,
                                threshold,
                            )
                            trial = ResearchTrial(
                                hypothesis,
                                threshold,
                                horizon,
                                scenario.name,
                                split,
                                group,
                                stats,
                            )
                            trials.append(trial)
                            if group == "all":
                                index[
                                    (
                                        hypothesis,
                                        threshold,
                                        horizon,
                                        scenario.name,
                                        split,
                                    )
                                ] = stats
    selections = []
    sufficient = positive = False
    for hypothesis in HYPOTHESES:
        for horizon in config.horizons_ns:
            candidates = []
            for threshold in config.thresholds:
                train = index[
                    hypothesis, threshold, horizon, config.scenarios[0].name, "train"
                ]
                validation = index[
                    hypothesis,
                    threshold,
                    horizon,
                    config.scenarios[0].name,
                    "validation",
                ]
                if (
                    min(
                        train.net_samples,
                        validation.net_samples,
                        train.nonoverlapping_samples,
                        validation.nonoverlapping_samples,
                    )
                    < config.min_samples
                ):
                    continue
                if (
                    train.expected_net_payoff_bps is not None
                    and train.expected_net_payoff_bps > 0
                    and validation.expected_net_payoff_bps is not None
                ):
                    candidates.append(
                        (validation.expected_net_payoff_bps, -threshold, threshold)
                    )
            if not candidates:
                selections.append(
                    ResearchSelection(
                        hypothesis,
                        horizon,
                        None,
                        "insufficient_support_or_no_positive_train_payoff",
                    )
                )
                continue
            payoff, _, selected = max(candidates)
            selections.append(
                ResearchSelection(
                    hypothesis, horizon, selected, "validation_selected_test_untouched"
                )
            )
            test = index[
                hypothesis, selected, horizon, config.scenarios[0].name, "test"
            ]
            supported = (
                min(test.net_samples, test.nonoverlapping_samples) >= config.min_samples
            )
            sufficient |= supported
            positive |= (
                supported
                and payoff > 0
                and test.expected_net_payoff_bps is not None
                and test.expected_net_payoff_bps > 0
            )
    # Distinguish adequate negative experiments from genuinely insufficient data.
    support = any(
        all(
            min(
                index[h, t, n, config.scenarios[0].name, s].net_samples,
                index[h, t, n, config.scenarios[0].name, s].nonoverlapping_samples,
            )
            >= config.min_samples
            for s in ("train", "validation", "test")
        )
        for h in HYPOTHESES
        for t in config.thresholds
        for n in config.horizons_ns
    )
    sufficient |= support
    outcome = (
        "insufficient_evidence"
        if descriptor.synthetic or not sufficient
        else "unvalidated_positive_markout"
        if positive
        else "no_reliable_edge"
    )
    exclusions = observer.exclusions.copy()
    for label in observer.labels:
        exclusions.update(label.reasons)
    report = ResearchReport(
        attempt_id=attempt,
        version=VERSION,
        source_id=descriptor.source_id,
        source_hash=source_hash,
        provenance=descriptor.provenance,
        synthetic=descriptor.synthetic,
        config=config,
        config_hash=_hash(config),
        observation_hash=_record_hash(observer.observations),
        label_hash=_record_hash(
            sorted(
                observer.labels,
                key=lambda label: (
                    label.observation_id,
                    label.horizon_ns,
                    label.scenario,
                ),
            )
        ),
        duration_ns=duration,
        train_end_ns=train_end,
        validation_end_ns=validation_end,
        observation_count=len(observer.observations),
        label_count=len(observer.labels),
        split_counts=tuple(sorted(split_counts.items())),
        exclusions=tuple(sorted(exclusions.items())),
        trials=tuple(trials),
        selections=tuple(selections),
        outcome=outcome,
        limitations=(
            "unfunded_directional_markouts_not_realizable_portfolio_returns",
            "independent_taker_roundtrips_no_shared_inventory_or_market_impact",
            "short_direction_does_not_establish_borrow_or_margin_access",
            "fees_are_sourced_assumptions_unknown_fees_exclude_net",
            "exact_deadline_last_available_book_no_future_arrival_book",
            "closed_outcome_intervals_purged_and_later_partition_embargoed",
            "all_candidate_tests_reported_once_selection_uses_train_validation_only",
            "fixed_spread_regimes_no_full_sample_fitting",
            "synthetic_data_cannot_establish_edge"
            if descriptor.synthetic
            else "observational_markouts_do_not_establish_predictive_edge",
        ),
    )
    return replace(
        report, report_hash=_hash(replace(report, attempt_id="", report_hash=""))
    )


def run_research(
    source: ReplaySource | Path, config: ResearchConfig, store: ResearchStore
) -> ResearchReport:
    """Run synchronously on the owning worker; every begun attempt remains visible.

    Paths are verified and retention-locked throughout both scan and replay.
    Non-recording sources must explicitly declare synthetic provenance. Source
    scan discovers split boundaries and hashes evidence, never computes features.
    """
    if not isinstance(config, ResearchConfig):
        raise ValueError("ResearchConfig required")
    source_name = (
        str(source) if isinstance(source, (Path, str)) else "synthetic_replay_source"
    )
    attempt = store._start(source_name, config)
    reader = None
    observer = None
    session = None
    try:
        replay_source: RecordingReader | ReplaySource
        if isinstance(source, (Path, str)):
            reader = RecordingReader(Path(source), verify=True)
            replay_source = reader
        elif isinstance(source, RecordingReader):
            # Never accept a caller's possibly-unverified reader as verified input.
            reader = RecordingReader(source.path, verify=True)
            replay_source = reader
        else:
            replay_source = source
            if (
                not source.descriptor.synthetic
                or source.descriptor.recording
                or not source.descriptor.provenance
            ):
                raise ValueError(
                    "memory research requires explicit synthetic provenance"
                )
        probe = ReplaySession(replay_source)
        descriptor = probe.source
        if reader is not None and (
            "synthetic" in descriptor.provenance.lower()
            or "fixture_sha256" in dict(descriptor.config)
        ):
            # A durable demo is still invented evidence; recording=True alone
            # must never upgrade its scientific provenance to real market data.
            descriptor = replace(descriptor, synthetic=True)
        total_deliveries = probe.total_deliveries
        probe.close()
        store._bind_source(attempt, descriptor, "")
        digest = sha256(
            dumps(
                (
                    descriptor.source_id,
                    descriptor.streams,
                    descriptor.config,
                    descriptor.synthetic,
                    descriptor.provenance,
                )
            )
        )
        if reader is not None:
            digest.update(
                json.dumps(
                    reader.manifest.to_dict(), sort_keys=True, separators=(",", ":")
                ).encode()
            )
        clock = ReplayClock()
        scanned = 0
        scan_digest = sha256()
        for batch in replay_source.iter_batches():
            digest.update(dumps(batch))
            scan_digest.update(dumps(batch))
            clock.advance(batch.events[0])
            scanned += 1
        if scanned != total_deliveries:
            raise ValueError("source delivery count changed")
        store._bind_source(attempt, descriptor, digest.hexdigest())
        observer = _ResearchObserver(descriptor.streams, config)
        session = ReplaySession(replay_source, observers=(observer,))
        replay_digest = sha256()
        while True:
            delivered = session.step()
            if not delivered:
                break
            replay_digest.update(dumps(delivered[0]))
        if replay_digest.digest() != scan_digest.digest():
            raise ValueError("source changed during research")
        observer._censor("end_of_source")
        with localcontext(DECIMAL_CONTEXT):
            report = _report(
                attempt, descriptor, digest.hexdigest(), clock.virtual_mono_ns, observer
            )
        store._finish(attempt, report, None, observer)
        return report
    except BaseException as exc:
        store._finish(
            attempt, None, (type(exc).__name__ + ": " + str(exc))[:4096], observer
        )
        raise
    finally:
        if session is not None:
            session.close()
        if reader is not None:
            reader.close()
