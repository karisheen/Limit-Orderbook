"""Durable spot IOC and explicitly modeled isolated perpetual simulation.

Reservations are per (native instrument, side, price). Healthy observed decreases
retire consumed quantity down to the new displayed size; only an observed increase
adds available quantity. Disappearance inside observed coverage then reappearance
is replenishment; moving outside truncated coverage is not. Revision changes,
heartbeats, unchanged snapshots and cohort replacement do not replenish depth.
Reservations survive unsubscription. No queue, passive touch fill or market impact
is inferred. Pending commands compete for balances at arrival, not at submission.

Feed latency delays book availability; decision plus order latency sets arrival.
The run's initial baseline is released only after its configured feed delay.
SQLite stores intent plus a separately queried read-only transition archive.
Reset never adopts archived state: it reconstructs balances, reservations, fills
and P&L from commands and the delivered prefix. Archives preserve live audit
evidence after shutdown, not permission to resume against another source clock.
"""

from bisect import bisect_right
from collections import OrderedDict
from dataclasses import dataclass, field, fields, is_dataclass, replace
from decimal import Decimal, ROUND_FLOOR, localcontext
from hashlib import sha256
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

from .book import Book, Level
from .costs import (
    ConsumedLevel,
    FeeSchedule,
    InventoryBalance,
    _amounts,
    _validate_price,
    walk_book,
)
from .events import EventEnvelopeRef, SubscriptionKey
from .instruments import AssetId, Instrument, InstrumentId, native_order_constraints
from .precision import (
    DECIMAL_CONTEXT,
    DERIVED_DECIMAL_POLICY,
    choice,
    decimal,
    nonnegative_int,
    text,
)
from .quality import ClockReading, EligibilityPolicy, evaluate
from .recording import RecordedBatch, StreamSpec
from .wire import dumps, loads
from .perpetual import (
    PaperRiskModel,
    PaperFundingModel,
    PaperPerpetualPosition,
    PaperFundingPayment,
    PaperFundingCursor,
    PaperLiquidation,
    MAX_PERPETUAL_RECORDS,
    economics_reason,
    settlement_value,
    pnl,
)

PAPER_SCHEMA_VERSION = 2
MAX_COMMANDS = 10_000
MAX_RUNS = 10_000
MAX_RUN_SUMMARY_CACHE_ENTRIES = 1000
MAX_RUN_SUMMARY_CACHE_BYTES = 8 * 1024 * 1024
MAX_INPUTS = 256
MAX_DELAYED_BOOKS = 4096
MAX_DELAYED_BOOK_BYTES = 16 * 1024 * 1024
MAX_RESERVED_PRICES = 100_000
MAX_FILL_LEVELS = 100_000
ZERO = Decimal("0")
SPOT_ASSUMPTIONS: tuple[str, ...] = (
    "spot_only",
    "immediate_or_cancel",
    "no_market_impact",
    "no_passive_fills",
    "conservative_per_price_reservations",
    "average_cost_basis",
    "initial_inventory_basis_unknown",
    "marks_are_gross_best_bid_not_liquidation_proceeds",
)


class PaperError(ValueError):
    """Stable reason code suitable for guarded command endpoints."""


@dataclass(frozen=True)
class PaperFee:
    instrument_id: InstrumentId
    schedule: FeeSchedule

    def __post_init__(self):
        object.__setattr__(self, "instrument_id", InstrumentId(self.instrument_id))
        if not isinstance(self.schedule, FeeSchedule):
            raise ValueError("explicit fee schedule required")


@dataclass(frozen=True)
class PaperConfig:
    balances: tuple[InventoryBalance, ...]
    fees: tuple[PaperFee, ...]
    feed_latency_ns: int = 0
    decision_latency_ns: int = 0
    order_latency_ns: int = 0
    policy: EligibilityPolicy = field(default_factory=EligibilityPolicy)
    risk_models: tuple[PaperRiskModel, ...] = ()
    funding_models: tuple[PaperFundingModel, ...] = ()

    def __post_init__(self):
        for name, kind in (
            ("balances", InventoryBalance),
            ("fees", PaperFee),
            ("risk_models", PaperRiskModel),
            ("funding_models", PaperFundingModel),
        ):
            values = tuple(getattr(self, name))
            if len(values) > MAX_INPUTS or any(not isinstance(v, kind) for v in values):
                raise ValueError("invalid bounded paper configuration")
            object.__setattr__(self, name, values)
        if len({(b.venue, b.asset_id) for b in self.balances}) != len(self.balances):
            raise ValueError("duplicate venue/asset balance")
        if len({f.instrument_id for f in self.fees}) != len(self.fees):
            raise ValueError("duplicate instrument fees")
        for models in (self.risk_models, self.funding_models):
            if len({m.instrument_id for m in models}) != len(models):
                raise ValueError("duplicate instrument model")
        for value in (
            self.feed_latency_ns,
            self.decision_latency_ns,
            self.order_latency_ns,
        ):
            if nonnegative_int(value) > 86_400_000_000_000:
                raise ValueError("latency exceeds one day")
        if not isinstance(self.policy, EligibilityPolicy):
            raise ValueError("invalid eligibility policy")


@dataclass(frozen=True)
class PaperOrderRequest:
    instrument_id: InstrumentId
    side: str
    quantity: Decimal
    order_type: str = "market"
    limit_price: Decimal | None = None
    reduce_only: bool = False

    def __post_init__(self):
        object.__setattr__(self, "instrument_id", InstrumentId(self.instrument_id))
        if type(self.reduce_only) is not bool:
            raise ValueError("reduce_only must be boolean")
        choice(self.side, ("buy", "sell"), "side")
        choice(self.order_type, ("market", "limit"), "order type")
        object.__setattr__(self, "quantity", decimal(self.quantity, positive=True))
        if (self.order_type == "limit") != (self.limit_price is not None):
            raise ValueError("limit price required only for limit orders")
        if self.limit_price is not None:
            object.__setattr__(
                self, "limit_price", decimal(self.limit_price, positive=True)
            )


@dataclass(frozen=True)
class PaperRun:
    run_id: str
    source_id: str
    config: PaperConfig
    created_at: ClockReading
    delivery_index: int
    revision: int
    parent_run_id: str | None = None
    instruments: tuple[Instrument, ...] = ()


@dataclass(frozen=True)
class PaperRunSummary:
    """Validated historical identity, never configuration or resume authority."""

    run_id: str
    source_id: str
    created_at: ClockReading
    delivery_index: int
    revision: int
    parent_run_id: str | None = None


@dataclass(frozen=True)
class PaperCommand:
    sequence: int
    kind: str
    order_id: str
    at: ClockReading
    delivery_index: int
    request: PaperOrderRequest | None = None
    instrument: Instrument | None = None


@dataclass(frozen=True)
class PaperOrder:
    order_id: str
    run_id: str
    request: PaperOrderRequest
    submitted_at: ClockReading
    delivery_index: int
    arrival_at: ClockReading
    status: str
    filled_quantity: Decimal = ZERO
    remaining_quantity: Decimal = ZERO
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class PaperFill:
    fill_id: str
    run_id: str
    order_id: str
    instrument_id: InstrumentId
    venue: str
    side: str
    at: ClockReading
    delivery_index: int
    quantity: Decimal
    notional_quote: Decimal
    quote_asset_id: AssetId
    fee_amount: Decimal
    fee_asset_id: AssetId
    fees: FeeSchedule
    consumed_levels: tuple[ConsumedLevel, ...]
    book_asof: EventEnvelopeRef
    book_key: SubscriptionKey
    book_revision: int


@dataclass(frozen=True)
class PaperBalance:
    venue: str
    asset_id: AssetId
    available: Decimal


@dataclass(frozen=True)
class PaperPosition:
    venue: str
    asset_id: AssetId
    quote_asset_id: AssetId
    instrument_id: InstrumentId
    quantity: Decimal
    cost_basis_quote: Decimal | None
    realized_pnl_quote: Decimal | None
    unrealized_pnl_quote: Decimal | None = None
    mark_price: Decimal | None = None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class PaperSnapshot:
    run: PaperRun | None
    delivery_index: int
    at: ClockReading | None
    balances: tuple[PaperBalance, ...]
    orders: tuple[PaperOrder, ...]
    fills: tuple[PaperFill, ...]
    positions: tuple[PaperPosition, ...]
    reasons: tuple[str, ...]
    assumptions: tuple[str, ...] = SPOT_ASSUMPTIONS
    perpetual_positions: tuple[PaperPerpetualPosition, ...] = ()
    funding_payments: tuple[PaperFundingPayment, ...] = ()
    liquidations: tuple[PaperLiquidation, ...] = ()


@dataclass(frozen=True)
class PaperReservation:
    key: SubscriptionKey
    side: str
    price: Decimal
    displayed: Decimal
    consumed: Decimal


@dataclass(frozen=True)
class PaperDelayedBook:
    available_at: ClockReading
    book: Book


@dataclass(frozen=True)
class PaperCheckpoint:
    run_id: str | None
    revision: int
    applied: int
    delivery_index: int
    at: ClockReading | None
    activated: bool
    books: tuple[Book, ...]
    source_books: tuple[Book, ...]
    delayed: tuple[PaperDelayedBook, ...]
    reservations: tuple[PaperReservation, ...]
    balances: tuple[PaperBalance, ...]
    orders: tuple[PaperOrder, ...]
    fills: tuple[PaperFill, ...]
    positions: tuple[PaperPosition, ...]
    instruments: tuple[Instrument, ...]
    clock_mismatch: bool
    perpetual_positions: tuple[PaperPerpetualPosition, ...] = ()
    funding_payments: tuple[PaperFundingPayment, ...] = ()
    funding_cursors: tuple[PaperFundingCursor, ...] = ()
    liquidations: tuple[PaperLiquidation, ...] = ()
    frozen_reason: str | None = None


def _metadata_retained_bytes(value) -> int:
    size = sys.getsizeof(value)
    if is_dataclass(value):
        size += sys.getsizeof(vars(value))
        size += sum(
            _metadata_retained_bytes(getattr(value, item.name))
            for item in fields(value)
        )
    elif isinstance(value, (tuple, frozenset)):
        size += sum(_metadata_retained_bytes(item) for item in value)
    return size


def _book_retained_bytes(book: Book) -> int:
    """Conservative charge, not serialized size or a claim of measured RSS.

    Each native level is charged for its object/dict, tuple reference and two
    bounded Decimals, even when objects are actually shared between snapshots.
    Metadata is counted recursively, including variable-sized quality reasons,
    aggregation settings and large integer provenance. Shared objects are charged
    repeatedly, conservatively; the level charge avoids scanning native depth.
    """
    metadata = sum(
        _metadata_retained_bytes(getattr(book, item.name))
        for item in fields(book)
        if item.name not in ("bids", "asks")
    )
    return 65_536 + metadata + 768 * (len(book.bids) + len(book.asks))


def _displayed_quantity(
    levels: tuple[Level, ...], price: Decimal, side: str
) -> Decimal | None:
    """Find only consumed prices; never rebuild a full-depth price dictionary."""
    low, high = 0, len(levels)
    while low < high:
        middle = (low + high) // 2
        candidate = levels[middle]
        if candidate.price == price:
            return candidate.quantity
        if (candidate.price < price) if side == "buy" else (candidate.price > price):
            low = middle + 1
        else:
            high = middle
    return None


def _encode(value) -> bytes:
    payload = dumps(value, canonical=False, decimal_policy=DERIVED_DECIMAL_POLICY)
    if len(payload) > 64 * 1024 * 1024:
        raise PaperError("paper_payload_limit")
    return payload


def _decode(payload, expected):
    return loads(
        payload,
        expected,
        max_bytes=64 * 1024 * 1024,
        decimal_policy=DERIVED_DECIMAL_POLICY,
    )


def _later(at: ClockReading, duration: int) -> ClockReading:
    return ClockReading(
        at.clock_id,
        at.mono_ns + duration,
        None if at.wall_ns is None else at.wall_ns + duration,
    )


class PaperStore:
    """Worker-owned commands plus a separately queried read-only transition archive."""

    def __init__(self, path: Path, *, read_only: bool = False):
        self._db = (
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
            if read_only
            else sqlite3.connect(path, timeout=10)
        )
        try:
            if read_only:
                # Historical readers never create, migrate, or change pragmas.
                version = self._db.execute("PRAGMA user_version").fetchone()[0]
                if version != PAPER_SCHEMA_VERSION:
                    raise ValueError("unsupported paper schema")
            else:
                self._db.execute("PRAGMA foreign_keys=ON")
                with self._db:
                    self._db.execute("BEGIN IMMEDIATE")
                    version = self._db.execute("PRAGMA user_version").fetchone()[0]
                    if version not in (0, 1, PAPER_SCHEMA_VERSION):
                        raise ValueError("unsupported paper schema")
                    self._db.execute(
                        "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, revision INTEGER NOT NULL, payload BLOB NOT NULL)"
                    )
                    self._db.execute(
                        "CREATE TABLE IF NOT EXISTS commands (run_id TEXT NOT NULL REFERENCES runs(run_id), sequence INTEGER NOT NULL, payload BLOB NOT NULL, PRIMARY KEY(run_id,sequence))"
                    )
                    self._db.execute(
                        "CREATE INDEX IF NOT EXISTS paper_source ON runs(source_id)"
                    )
                    self._db.execute(
                        "CREATE TABLE IF NOT EXISTS archives (run_id TEXT PRIMARY KEY REFERENCES runs(run_id), revision INTEGER NOT NULL, delivery_index TEXT NOT NULL, clock_id TEXT NOT NULL, mono_ns TEXT NOT NULL, payload BLOB NOT NULL)"
                    )
                    self._db.execute("PRAGMA user_version=2")
        except BaseException:
            self._db.close()
            raise
        self._run_summaries: OrderedDict[str, tuple[bytes, PaperRunSummary, int]] = (
            OrderedDict()
        )
        self._run_summary_bytes = 0

    def list_run_summaries(
        self, *, source_id: str | None = None, limit: int = 100
    ) -> tuple[PaperRunSummary, ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("paper run query limit must be 1..1000")
        if source_id is not None:
            text(source_id)
        query = (
            "SELECT run_id,payload,revision FROM runs"
            + (" WHERE source_id=?" if source_id is not None else "")
            + " ORDER BY rowid DESC LIMIT ?"
        )
        args = (source_id, limit) if source_id is not None else (limit,)
        summaries = []
        for run_id, payload, revision in self._db.execute(query, args):
            encoded = payload.encode("utf-8") if isinstance(payload, str) else payload
            identity = sha256(encoded).digest()
            cached = self._run_summaries.get(run_id)
            if cached is not None and cached[0] == identity:
                summary = cached[1]
                self._run_summaries.move_to_end(run_id)
            else:
                # Validate the ENTIRE durable record, including omitted config
                # and pinned instruments, before caching only its small summary.
                # A payload edit invalidates this proof even at the same revision.
                run = _decode(payload, PaperRun)
                summary = PaperRunSummary(
                    run.run_id,
                    run.source_id,
                    run.created_at,
                    run.delivery_index,
                    run.revision,
                    run.parent_run_id,
                )
                previous = self._run_summaries.pop(run_id, None)
                if previous is not None:
                    self._run_summary_bytes -= previous[2]
                size = (
                    4096
                    + _metadata_retained_bytes(summary)
                    + _metadata_retained_bytes(run_id)
                )
                if size <= MAX_RUN_SUMMARY_CACHE_BYTES:
                    while self._run_summaries and (
                        len(self._run_summaries) >= MAX_RUN_SUMMARY_CACHE_ENTRIES
                        or self._run_summary_bytes + size > MAX_RUN_SUMMARY_CACHE_BYTES
                    ):
                        _key, evicted = self._run_summaries.popitem(last=False)
                        self._run_summary_bytes -= evicted[2]
                    self._run_summaries[run_id] = (identity, summary, size)
                    self._run_summary_bytes += size
            # Commands advance the authoritative revision without rewriting the
            # immutable run payload. Always return this query's database revision.
            summaries.append(replace(summary, revision=revision))
        return tuple(summaries)

    def archived_snapshot(self, run_id: str) -> PaperSnapshot | None:
        """Historical audit only; never used to seed an active engine projection."""
        text(run_id)
        row = self._db.execute(
            "SELECT archives.payload,runs.revision FROM archives JOIN runs USING(run_id) WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        snapshot = _decode(row[0], PaperSnapshot)
        reasons = (*snapshot.reasons, "archived_not_live_projection")
        if snapshot.run.revision != row[1]:
            reasons += ("archive_command_revision_superseded",)
        return replace(snapshot, reasons=tuple(dict.fromkeys(reasons)))

    def _archive(self, snapshot: PaperSnapshot) -> bool:
        if snapshot.run is None or snapshot.at is None:
            return False
        run, at = snapshot.run, snapshot.at
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            current = self._db.execute(
                "SELECT revision FROM runs WHERE run_id=?", (run.run_id,)
            ).fetchone()
            if current is None or current[0] != run.revision:
                return False
            previous = self._db.execute(
                "SELECT revision,delivery_index,clock_id,mono_ns FROM archives WHERE run_id=?",
                (run.run_id,),
            ).fetchone()
            if previous is not None:
                revision, delivery, clock_id, mono = previous
                if run.revision < revision or snapshot.delivery_index < int(delivery):
                    return False
                if snapshot.delivery_index == int(delivery) and (
                    at.clock_id != clock_id or at.mono_ns < int(mono)
                ):
                    return False
                if (
                    run.revision == revision
                    and snapshot.delivery_index == int(delivery)
                    and at.mono_ns == int(mono)
                ):
                    return False
            # Serialize only after transactional stale/backward rejection.
            payload = _encode(snapshot)
            self._db.execute(
                "INSERT INTO archives VALUES(?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET revision=excluded.revision,delivery_index=excluded.delivery_index,clock_id=excluded.clock_id,mono_ns=excluded.mono_ns,payload=excluded.payload",
                (
                    run.run_id,
                    run.revision,
                    str(snapshot.delivery_index),
                    at.clock_id,
                    str(at.mono_ns),
                    payload,
                ),
            )
            return True

    def _load(self, run_id: str) -> tuple[PaperRun, tuple[PaperCommand, ...]]:
        with self._db:
            self._db.execute("BEGIN")
            row = self._db.execute(
                "SELECT payload,revision FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise PaperError("unknown_run")
            run = replace(_decode(row[0], PaperRun), revision=row[1])
            commands = tuple(
                _decode(r[0], PaperCommand)
                for r in self._db.execute(
                    "SELECT payload FROM commands WHERE run_id=? ORDER BY sequence LIMIT ?",
                    (run_id, MAX_COMMANDS + 1),
                )
            )
            if len(commands) != run.revision or len(commands) > MAX_COMMANDS:
                raise PaperError("invalid_command_history")
            return run, commands

    def _revision(self, run_id: str) -> int:
        row = self._db.execute(
            "SELECT revision FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise PaperError("unknown_run")
        return row[0]

    def _create(self, run: PaperRun) -> None:
        payload = _encode(run)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            if self._db.execute("SELECT count(*) FROM runs").fetchone()[0] >= MAX_RUNS:
                raise PaperError("run_limit")
            self._db.execute(
                "INSERT INTO runs VALUES(?,?,?,?)",
                (run.run_id, run.source_id, 0, payload),
            )

    def _append(self, run: PaperRun, command: PaperCommand) -> None:
        if run.revision >= MAX_COMMANDS:
            raise PaperError("command_limit")
        payload = _encode(command)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            changed = self._db.execute(
                "UPDATE runs SET revision=revision+1 WHERE run_id=? AND revision=?",
                (run.run_id, run.revision),
            ).rowcount
            if changed != 1:
                raise PaperError("stale_run_reload_required")
            self._db.execute(
                "INSERT INTO commands VALUES(?,?,?)",
                (run.run_id, command.sequence, payload),
            )

    def close(self) -> None:
        self._db.close()
        self._run_summaries.clear()
        self._run_summary_bytes = 0


class PaperEngine:
    observer_id = "paper"
    version = "paper-v1"

    def __init__(
        self,
        streams: tuple[StreamSpec, ...],
        store: PaperStore,
        source_id: str,
        *,
        run_id: str | None = None,
        archive_enabled: bool = True,
    ):
        text(source_id)
        if len(streams) > MAX_INPUTS:
            raise PaperError("stream_limit")
        self.store, self.source_id = store, source_id
        self._archive_enabled = archive_enabled
        self._streams = tuple(streams)
        self._metadata = {s.instrument.instrument_id: s.instrument for s in streams}
        self._initial_metadata = dict(self._metadata)
        self._active = {s.key for s in streams if s.key.channel == "book"}
        self._run: PaperRun | None
        self._commands: tuple[PaperCommand, ...]
        self._run, self._commands = (
            (None, ()) if run_id is None else store._load(run_id)
        )
        if self._run is not None and self._run.source_id != source_id:
            raise PaperError("run_source_mismatch")
        if self._run is not None:
            for instrument in self._run.instruments:
                self._metadata.setdefault(instrument.instrument_id, instrument)
        self.reset()

    def reset(self) -> None:
        self._metadata = dict(self._initial_metadata)
        if self._run is not None:
            self._metadata.update(
                (item.instrument_id, item) for item in self._run.instruments
            )
        self._rebuild_metadata_versions()
        self._clock_mismatch = False
        self._frozen_reason: str | None = None
        self._archive_dirty = False
        self._at: ClockReading | None = None
        self._delivery = 0
        self._applied = 0
        self._activated = False
        self._books: dict[SubscriptionKey, Book] = {}
        self._source_books: dict[SubscriptionKey, Book] = {}
        self._delayed: list[PaperDelayedBook] = []
        self._delayed_bytes = 0
        self._reservations: dict[
            tuple[InstrumentId, str, Decimal], PaperReservation
        ] = {}
        self._balances: dict[tuple[str, AssetId], Decimal] = {}
        self._orders: dict[str, PaperOrder] = {}
        self._fills: list[PaperFill] = []
        self._fill_level_count = 0
        self._positions: dict[tuple[str, AssetId], PaperPosition] = {}
        self._reset_perpetual()

    def _rebuild_metadata_versions(self) -> None:
        # Bound retained versions to durable command/run evidence and current
        # subscriptions; cohort changes without commands do not grow history.
        instruments = list(self._initial_metadata.values())
        if self._run is not None:
            instruments.extend(self._run.instruments)
        instruments.extend(
            command.instrument
            for command in self._commands
            if command.instrument is not None
        )
        instruments.extend(stream.instrument for stream in self._streams)
        self._metadata_versions = {
            (item.instrument_id, item.metadata_version): item for item in instruments
        }

    def _activate(self, now: ClockReading, delivery: int) -> None:
        if self._run is None or self._activated:
            return
        run = self._run
        if delivery < run.delivery_index:
            return
        if run.created_at.clock_id != now.clock_id:
            return
        if delivery == run.delivery_index and now.mono_ns < run.created_at.mono_ns:
            return
        self._balances = {
            (b.venue, b.asset_id): b.available for b in run.config.balances
        }
        for instrument in sorted(
            self._metadata.values(), key=lambda item: item.instrument_id
        ):
            key = (instrument.venue, instrument.base.asset_id)
            quantity = self._balances.get(key, ZERO)
            if (
                quantity
                and instrument.market_type == "spot"
                and key not in self._positions
            ):
                self._positions[key] = PaperPosition(
                    instrument.venue,
                    instrument.base.asset_id,
                    instrument.quote.asset_id,
                    instrument.instrument_id,
                    quantity,
                    None,
                    ZERO,
                )
        self._activated = True
        baseline = tuple(self._source_books.values())
        # Activation reschedules the observed baseline under this run's latency.
        # The steady-state identity fastpath must not treat it as still scheduled.
        self._source_books.clear()
        self._books.clear()
        self._delayed.clear()
        self._delayed_bytes = 0
        self._reservations.clear()
        for book in baseline:
            self._schedule(book, now)

    @property
    def frozen_reason(self) -> str | None:
        """Constant-time observer/candidate guard; no snapshot allocation."""
        return self._frozen_reason

    def freeze(self, reason: str = "funding_catchup_limit") -> None:
        """Retain the last valid prefix while the owner continues non-paper data."""
        if reason != "funding_catchup_limit":
            raise ValueError("unsupported paper freeze reason")
        self._frozen_reason = reason

    def configure(
        self,
        config: PaperConfig,
        *,
        now: ClockReading,
        delivery_index: int,
        books: tuple[Book, ...] | None = None,
        streams: tuple[StreamSpec, ...] | None = None,
    ) -> str:
        if not isinstance(config, PaperConfig) or not isinstance(now, ClockReading):
            raise ValueError("invalid paper configuration or clock")
        if self._frozen_reason is not None:
            if books is None:
                raise PaperError("frozen_run_requires_current_books")
            if nonnegative_int(delivery_index) < self._delivery:
                raise PaperError("cursor_delivery_mismatch")
        else:
            self._cursor_guard(now, delivery_index)
        if books is not None and (
            len(books) > MAX_INPUTS or any(not isinstance(book, Book) for book in books)
        ):
            raise ValueError("invalid current paper baseline")
        if streams is not None and (
            len(streams) > MAX_INPUTS
            or any(not isinstance(stream, StreamSpec) for stream in streams)
        ):
            raise ValueError("invalid current paper streams")
        metadata = (
            self._metadata
            if streams is None
            else {
                stream.instrument.instrument_id: stream.instrument for stream in streams
            }
        )
        now = self._command_clock(now)
        run = PaperRun(
            uuid4().hex,
            self.source_id,
            config,
            now,
            delivery_index,
            0,
            self._run.run_id if self._run else None,
            tuple(metadata.values()),
        )
        self.store._create(run)
        if streams is not None:
            self._streams = tuple(streams)
            self._initial_metadata = dict(metadata)
            self._active = {
                stream.key for stream in streams if stream.key.channel == "book"
            }
        books = tuple(self._source_books.values()) if books is None else tuple(books)
        self._run, self._commands = run, ()
        self.reset()
        self._delivery, self._at = delivery_index, now
        self._activate(now, delivery_index)
        # A new run is a new simulation; available baseline is not invented history.
        for book in books:
            self._schedule(book, now)
        self._advance(now)
        self._archive_dirty = True
        self._flush_archive()
        return run.run_id

    def _command_clock(self, now: ClockReading) -> ClockReading:
        """Replay frontiers omit wall time; date fees from already received evidence."""
        if now.wall_ns is not None:
            return now
        if (
            self._at is not None
            and self._at.clock_id == now.clock_id
            and self._at.wall_ns is not None
        ):
            return replace(
                now, wall_ns=self._at.wall_ns + now.mono_ns - self._at.mono_ns
            )
        evidence = [
            book.asof
            for book in self._source_books.values()
            if book.asof.clock_id == now.clock_id
            and book.asof.receive_mono_ns <= now.mono_ns
        ]
        if evidence:
            latest = max(evidence, key=lambda item: item.receive_mono_ns)
            return replace(
                now,
                wall_ns=latest.receive_wall_ns + now.mono_ns - latest.receive_mono_ns,
            )
        return now

    def _cursor_guard(self, now: ClockReading, delivery: int) -> None:
        nonnegative_int(delivery)
        if delivery != self._delivery:
            raise PaperError("cursor_delivery_mismatch")
        if self._at is not None and (
            now.clock_id != self._at.clock_id or now.mono_ns < self._at.mono_ns
        ):
            raise PaperError("cursor_clock_mismatch")

    def _mutation_guard(self, now: ClockReading, delivery: int) -> None:
        if self._frozen_reason is not None:
            raise PaperError(self._frozen_reason)
        self._cursor_guard(now, delivery)
        if self._run is None or not self._activated:
            raise PaperError("run_not_active_at_cursor")
        if self._clock_mismatch or now.clock_id != self._run.created_at.clock_id:
            raise PaperError("run_source_clock_mismatch")
        if self.store._revision(self._run.run_id) != self._run.revision:
            raise PaperError("stale_run_reload_required")
        if self._applied < len(self._commands):
            raise PaperError("future_commands_fork_required")

    def submit(
        self, request: PaperOrderRequest, *, now: ClockReading, delivery_index: int
    ) -> PaperOrder:
        self._mutation_guard(now, delivery_index)
        assert self._run is not None  # Established by the mutation guard.
        now = self._command_clock(now)
        if not isinstance(request, PaperOrderRequest):
            raise ValueError("expected PaperOrderRequest")
        instrument = self._metadata.get(request.instrument_id)
        if instrument is None:
            raise PaperError("unknown_instrument")
        if instrument.metadata_status != "verified":
            raise PaperError("instrument_metadata_unverified")
        if instrument.market_type == "perpetual":
            reasons = self._perpetual_inputs(instrument, now)
            if reasons:
                raise PaperError(reasons[0])
        elif (
            instrument.market_type != "spot"
            or instrument.quantity_unit != "base"
            or instrument.inverse
            or instrument.contract_multiplier != 1
            or (
                instrument.settlement is not None
                and instrument.settlement.asset_id != instrument.quote.asset_id
            )
        ):
            raise PaperError("unsupported_perpetual_or_contract_economics")
        if instrument.market_type == "spot" and request.reduce_only:
            raise PaperError("reduce_only_requires_perpetual")
        instrument.validate_quantity(request.quantity)
        if request.limit_price is not None:
            _validate_price(instrument, request.limit_price)
        bounds = native_order_constraints(instrument)
        if (bounds.min_base is not None and request.quantity < bounds.min_base) or any(
            request.quantity > maximum
            for maximum in (
                bounds.max_base,
                bounds.max_market_base
                if request.order_type == "market"
                else bounds.max_limit_base,
            )
            if maximum is not None
        ):
            raise PaperError("native_order_quantity_constraint")
        command = PaperCommand(
            self._run.revision + 1,
            "submit",
            uuid4().hex,
            now,
            delivery_index,
            request,
            instrument,
        )
        self._record(command)
        return self._orders[command.order_id]

    def cancel(
        self, order_id: str, *, now: ClockReading, delivery_index: int
    ) -> PaperOrder:
        self._mutation_guard(now, delivery_index)
        assert self._run is not None  # Established by the mutation guard.
        now = self._command_clock(now)
        if order_id not in self._orders:
            raise PaperError("unknown_order")
        if self._orders[order_id].status != "pending":
            raise PaperError("order_not_pending")
        command = PaperCommand(
            self._run.revision + 1, "cancel", order_id, now, delivery_index
        )
        self._record(command)
        return self._orders[order_id]

    def _record(self, command: PaperCommand) -> None:
        # No projection mutation before the transaction wins its revision compare.
        assert self._run is not None  # Only guarded mutations record commands.
        self._funding_capacity_guard(command.at)
        self.store._append(self._run, command)
        self._run = replace(self._run, revision=command.sequence)
        self._commands += (command,)
        self._apply_command(command)
        self._applied += 1
        self._archive_dirty = True
        self._flush_archive()

    def _apply_command(self, command: PaperCommand) -> None:
        assert self._run is not None  # Commands belong to the loaded/created run.
        if command.instrument is not None:
            self._metadata[command.instrument.instrument_id] = command.instrument
            self._metadata_versions[
                (command.instrument.instrument_id, command.instrument.metadata_version)
            ] = command.instrument
        self._activate(command.at, command.delivery_index)
        self._advance(command.at)
        if command.kind == "submit":
            config = self._run.config
            arrival = _later(
                command.at, config.decision_latency_ns + config.order_latency_ns
            )
            request = command.request
            assert request is not None  # Submit commands always persist a request.
            self._orders[command.order_id] = PaperOrder(
                command.order_id,
                self._run.run_id,
                request,
                command.at,
                command.delivery_index,
                arrival,
                "pending",
                remaining_quantity=request.quantity,
            )
            self._archive_dirty = True
            self._advance(command.at)
        else:
            order = self._orders[command.order_id]
            if order.status == "pending":
                self._orders[command.order_id] = replace(
                    order,
                    status="cancelled",
                    reasons=("user_cancelled_before_arrival",),
                )
                self._archive_dirty = True

    def _replay_commands(self, cutoff: int) -> None:
        if (
            self._run is not None
            and not self._activated
            and cutoff >= self._run.delivery_index
        ):
            self._activate(self._run.created_at, self._run.delivery_index)
            self._advance(self._run.created_at)
        while self._applied < len(self._commands):
            command = self._commands[self._applied]
            if command.delivery_index > cutoff:
                break
            self._apply_command(command)
            self._applied += 1

    def initialize_books(self, books: tuple[Book, ...], now: ClockReading) -> None:
        if self._at is not None:
            raise PaperError("baseline_already_initialized")
        self._funding_capacity_guard(now, command_cutoff=self._delivery)
        self._at = now
        self._activate(now, self._delivery)
        for book in books:
            self._schedule(book, now)
        self._advance(now)
        self._replay_commands(self._delivery)
        self._flush_archive()

    def on_batch(self, batch: RecordedBatch, books: tuple[Book, ...], clock) -> None:
        now = clock.reading
        if now is None:
            return
        if (
            now.wall_ns is None
            and clock.source_reading is not None
            and clock.source_reading.clock_id == now.clock_id
        ):
            source = clock.source_reading
            if source.wall_ns is not None:
                now = replace(
                    now, wall_ns=source.wall_ns + now.mono_ns - source.mono_ns
                )
        self._funding_capacity_guard(now, command_cutoff=batch.delivery_index)
        self._replay_commands(batch.delivery_index - 1)
        if (
            self._at is not None
            and self._at.clock_id == now.clock_id
            and now.mono_ns < self._at.mono_ns
        ):
            # A live query can advance the owner beyond a queued receive stamp.
            # The newly delivered book was not available at that earlier stamp.
            now = self._at
        self._funding_capacity_guard(now)
        # Arrivals strictly before this delivery see only the previous observations.
        self._advance(now, inclusive=False)
        self._delivery = batch.delivery_index
        for book in books:
            self._schedule(book, now)
        self._advance(now)
        self._replay_commands(self._delivery)
        self._flush_archive()

    def replace_streams(
        self,
        streams: tuple[StreamSpec, ...],
        *,
        books: tuple[Book, ...],
        now: ClockReading,
    ) -> None:
        if self._at is not None and now.clock_id != self._at.clock_id:
            raise PaperError("cohort_clock_mismatch")
        self._cursor_guard(now, self._delivery)
        if (
            len(streams) > MAX_INPUTS
            or len(set(self._metadata) | {s.instrument.instrument_id for s in streams})
            > MAX_INPUTS
        ):
            raise PaperError("stream_metadata_limit")
        self._funding_capacity_guard(now)
        self._advance(now, inclusive=False)
        self._streams = tuple(streams)
        self._metadata.update(
            (s.instrument.instrument_id, s.instrument) for s in streams
        )
        self._rebuild_metadata_versions()
        self._active = {s.key for s in streams if s.key.channel == "book"}
        self._books = {
            key: book for key, book in self._books.items() if key in self._active
        }
        self._source_books = {
            key: book for key, book in self._source_books.items() if key in self._active
        }
        self._delayed = [
            item for item in self._delayed if item.book.key in self._active
        ]
        self._delayed_bytes = sum(
            _book_retained_bytes(item.book) for item in self._delayed
        )
        for book in books:
            self._schedule(book, now)
        self._advance(now)
        self._flush_archive()

    def advance_time(self, now: ClockReading) -> None:
        """Live owner only: advance real source time without inventing a delivery."""
        self._cursor_guard(now, self._delivery)
        if self._run is not None:
            self._mutation_guard(now, self._delivery)
        self._advance(self._command_clock(now))
        self._flush_archive()

    def publish_archive(self) -> None:
        """Commit a fully reconstructed candidate before its owner adopts it."""
        self._archive_enabled = True
        self._archive_dirty = True
        self._flush_archive()

    def _flush_archive(self) -> None:
        if (
            not self._archive_enabled
            or not self._archive_dirty
            or not self._activated
            or self._applied < len(self._commands)
        ):
            return
        self.store._archive(self.snapshot(self._at))
        self._archive_dirty = False

    def _schedule(self, book: Book, now: ClockReading) -> None:
        if book.key not in self._active:
            return
        if self._source_books.get(book.key) is book:
            return
        self._source_books[book.key] = book
        existing = self._books.get(book.key)
        if existing == book or any(item.book == book for item in self._delayed):
            return
        latency = (
            self._run.config.feed_latency_ns if self._run and self._activated else 0
        )
        if not latency:
            # Zero-delay observations need no retained queue allocation. All
            # strictly earlier arrivals were settled by the owning caller.
            self._observe(book, now)
            return
        if len(self._delayed) >= MAX_DELAYED_BOOKS:
            raise PaperError("feed_latency_queue_limit")
        charge = _book_retained_bytes(book)
        if self._delayed_bytes + charge > MAX_DELAYED_BOOK_BYTES:
            raise PaperError("feed_latency_byte_limit")
        self._delayed.append(PaperDelayedBook(_later(now, latency), book))
        self._delayed_bytes += charge

    def _observe(self, book: Book, at: ClockReading) -> None:
        self._books[book.key] = book
        observed_instrument = self._metadata_versions.get(
            (book.instrument_id, book.metadata_version)
        )
        if observed_instrument is not None:
            self._metadata[book.instrument_id] = observed_instrument
        if not self._reservations:
            return
        instrument = self._metadata.get(book.instrument_id)
        policy = self._run.config.policy if self._run else EligibilityPolicy()
        if (
            instrument is None
            or not evaluate(book, instrument, "paper_fill", at, policy).eligible
        ):
            return
        for key, old in tuple(self._reservations.items()):
            if key[0] != book.instrument_id:
                continue
            levels = book.asks if old.side == "buy" else book.bids
            quantity = _displayed_quantity(levels, old.price, old.side)
            if quantity is None:
                covered = (
                    book.quality.coverage.full_depth
                    or bool(levels)
                    and (
                        old.price <= levels[-1].price
                        if old.side == "buy"
                        else old.price >= levels[-1].price
                    )
                )
                if covered:
                    del self._reservations[key]
            elif old.displayed != quantity or old.key != book.key:
                self._reservations[key] = PaperReservation(
                    book.key,
                    old.side,
                    old.price,
                    quantity,
                    min(old.consumed, quantity),
                )

    def _advance(self, now: ClockReading, *, inclusive: bool = True) -> None:
        self._funding_capacity_guard(now, inclusive=inclusive)
        if (
            self._run is not None
            and self._activated
            and now.clock_id != self._run.created_at.clock_id
        ):
            self._clock_mismatch = True
        if self._at is not None and self._at.clock_id != now.clock_id:
            self._books.clear()
            self._source_books.clear()
            self._delayed.clear()
            self._delayed_bytes = 0
            for oid, order in self._orders.items():
                if order.status == "pending":
                    self._orders[oid] = replace(
                        order, status="cancelled", reasons=("clock_discontinuity",)
                    )
                    self._archive_dirty = True

        def due(at: ClockReading) -> bool:
            return at.clock_id == now.clock_id and (
                at.mono_ns <= now.mono_ns if inclusive else at.mono_ns < now.mono_ns
            )

        while True:
            observations = [item for item in self._delayed if due(item.available_at)]
            orders = [
                order
                for order in self._orders.values()
                if order.status == "pending" and due(order.arrival_at)
            ]
            book_item = min(
                observations, key=lambda item: item.available_at.mono_ns, default=None
            )
            next_order = min(
                orders, key=lambda item: item.arrival_at.mono_ns, default=None
            )
            funding = self._next_funding()
            if (
                funding is not None
                and due(funding[0])
                and (
                    book_item is None
                    or funding[0].mono_ns < book_item.available_at.mono_ns
                )
                and (
                    next_order is None
                    or funding[0].mono_ns <= next_order.arrival_at.mono_ns
                )
            ):
                self._settle_funding_at(funding[0])
                self._perpetual_risk(funding[0])
                continue
            if book_item is None and next_order is None:
                break
            if book_item is not None and (
                next_order is None
                or book_item.available_at.mono_ns <= next_order.arrival_at.mono_ns
            ):
                # Publish all boundary observations for marks, then charge held
                # exposure before either protective closes or arriving orders.
                for item in observations:
                    if item.available_at.mono_ns != book_item.available_at.mono_ns:
                        continue
                    self._delayed.remove(item)
                    self._delayed_bytes -= _book_retained_bytes(item.book)
                    if item.book.key in self._active:
                        self._observe(item.book, item.available_at)
                self._settle_funding_at(book_item.available_at)
                self._perpetual_risk(book_item.available_at)
            else:
                assert next_order is not None
                with localcontext(DECIMAL_CONTEXT):
                    self._execute(next_order)
                    self._perpetual_risk(next_order.arrival_at)
        self._at = now
        if inclusive:
            self._perpetual_risk(now)

    def _finish(
        self, order: PaperOrder, reasons: tuple[str, ...], quantity: Decimal = ZERO
    ) -> None:
        remaining = order.request.quantity - quantity
        self._orders[order.order_id] = replace(
            order,
            status="filled"
            if not remaining
            else "partial"
            if quantity
            else "cancelled",
            filled_quantity=quantity,
            remaining_quantity=remaining,
            reasons=reasons + (("ioc_remainder_cancelled",) if remaining else ()),
        )
        self._archive_dirty = True

    def _execute(self, order: PaperOrder) -> None:
        assert self._run is not None  # Pending orders are created within a run.
        if (
            self._clock_mismatch
            or order.arrival_at.clock_id != self._run.created_at.clock_id
        ):
            self._finish(order, ("run_source_clock_mismatch",))
            return
        request = order.request
        books = [
            book
            for key, book in self._books.items()
            if key in self._active and book.instrument_id == request.instrument_id
        ]
        if len(books) != 1:
            self._finish(
                order,
                (
                    "instrument_not_observed"
                    if not books
                    else "ambiguous_book_subscription",
                ),
            )
            return
        book = books[0]
        instrument = self._metadata_versions.get(
            (book.instrument_id, book.metadata_version)
        )
        if instrument is None:
            self._finish(order, ("instrument_metadata_unavailable",))
            return
        if instrument.metadata_status != "verified":
            self._finish(order, ("instrument_metadata_unverified",))
            return
        if instrument.market_type == "perpetual":
            self._execute_perpetual(order, book, instrument)
            return
        if (
            instrument.market_type != "spot"
            or instrument.quantity_unit != "base"
            or instrument.inverse
            or instrument.contract_multiplier != 1
            or (
                instrument.settlement is not None
                and instrument.settlement.asset_id != instrument.quote.asset_id
            )
        ):
            self._finish(order, ("unsupported_perpetual_or_contract_economics",))
            return
        gate = evaluate(
            book, instrument, "paper_fill", order.arrival_at, self._run.config.policy
        )
        if not gate.eligible:
            self._finish(order, gate.reasons)
            return
        lot = instrument.lot_size
        assert lot is not None  # Verified instruments require a known native lot.
        fees = next(
            (
                item.schedule
                for item in self._run.config.fees
                if item.instrument_id == request.instrument_id
            ),
            FeeSchedule(),
        )
        fee_asset = fees.asset_id or instrument.quote.asset_id
        if fees.rate is None:
            self._finish(order, ("unknown_fees",))
            return
        assert fees.asof_wall_ns is not None  # Known rates require a dated schedule.
        if order.arrival_at.wall_ns is None:
            self._finish(order, ("fee_date_clock_unavailable",))
            return
        if fees.asof_wall_ns > order.arrival_at.wall_ns:
            self._finish(order, ("fee_date_in_future",))
            return
        if fee_asset not in (instrument.base.asset_id, instrument.quote.asset_id):
            self._finish(order, ("unsupported_fee_asset",))
            return
        base_key = (instrument.venue, instrument.base.asset_id)
        quote_key = (instrument.venue, instrument.quote.asset_id)
        cash, inventory = (
            self._balances.get(quote_key, ZERO),
            self._balances.get(base_key, ZERO),
        )
        quantity = request.quantity
        resource_reason: tuple[str, ...] = ()
        if request.side == "sell":
            cap = inventory / (
                1 + fees.rate if fee_asset == instrument.base.asset_id else 1
            )
            if quantity > cap:
                resource_reason = ("insufficient_inventory",)
            quantity = min(quantity, cap)
        levels = book.asks if request.side == "buy" else book.bids
        available, budget = (
            [],
            cash / (1 + fees.rate if fee_asset == instrument.quote.asset_id else 1),
        )
        remaining = quantity
        for level in levels:
            if request.limit_price is not None and (
                level.price > request.limit_price
                if request.side == "buy"
                else level.price < request.limit_price
            ):
                resource_reason += ("limit_price_reached",)
                break
            reservation = self._reservations.get(
                (book.instrument_id, request.side, level.price)
            )
            consumed = reservation.consumed if reservation else ZERO
            take = min(level.quantity - consumed, remaining)
            if request.side == "buy":
                if take * level.price > budget:
                    resource_reason = ("insufficient_cash",)
                take = min(take, budget / level.price)
            take = (take / lot).to_integral_value(rounding=ROUND_FLOOR) * lot
            if take > 0:
                available.append(Level(level.price, take.normalize()))
                remaining -= take
                budget -= take * level.price
            if remaining <= 0:
                break
        if not available:
            self._finish(order, resource_reason or ("no_unreserved_depth",))
            return
        adjusted = (
            replace(book, asks=tuple(available))
            if request.side == "buy"
            else replace(book, bids=tuple(available))
        )
        estimate = walk_book(
            adjusted,
            instrument,
            request.side,
            base_quantity=request.quantity,
            fees=fees,
            policy=self._run.config.policy,
            now=order.arrival_at,
            order_type=request.order_type,
            limit_price=request.limit_price,
        )
        filled = estimate.filled_base_quantity
        new_prices = sum(
            (book.instrument_id, request.side, level.price) not in self._reservations
            for level in estimate.consumed_levels
        )
        if len(self._reservations) + new_prices > MAX_RESERVED_PRICES:
            self._finish(order, ("reservation_price_limit",))
            return
        if self._fill_level_count + len(estimate.consumed_levels) > MAX_FILL_LEVELS:
            self._finish(order, ("fill_evidence_limit",))
            return
        self._fill_level_count += len(estimate.consumed_levels)
        if not filled or estimate.fee_amount is None:
            self._finish(
                order, tuple(dict.fromkeys((*resource_reason, *estimate.reasons)))
            )
            return
        fee, notional = estimate.fee_amount, estimate.notional_quote
        base_delta = filled if request.side == "buy" else -filled
        quote_delta = -notional if request.side == "buy" else notional
        if fee_asset == instrument.base.asset_id:
            base_delta -= fee
        else:
            quote_delta -= fee
        if inventory + base_delta < 0 or cash + quote_delta < 0:
            raise PaperError("accounting_conservation_violation")
        self._balances[base_key] = inventory + base_delta
        self._balances[quote_key] = cash + quote_delta
        self._account_position(instrument, inventory, base_delta, quote_delta)
        quote_position = self._positions.get(quote_key)
        if quote_position is not None:
            # Quote flows do not imply FX into the asset's previous P&L currency.
            self._positions[quote_key] = replace(
                quote_position,
                quantity=cash + quote_delta,
                cost_basis_quote=None,
                realized_pnl_quote=None,
                reasons=("quote_asset_flow_basis_unavailable",),
            )
        for consumed_level in estimate.consumed_levels:
            key = (book.instrument_id, request.side, consumed_level.price)
            old = self._reservations.get(key)
            if old is None:
                displayed = next(
                    item.quantity
                    for item in levels
                    if item.price == consumed_level.price
                )
                old = PaperReservation(
                    book.key, request.side, consumed_level.price, displayed, ZERO
                )
            self._reservations[key] = replace(
                old, consumed=old.consumed + consumed_level.native_quantity
            )
        self._fills.append(
            PaperFill(
                order.order_id + ":fill",
                order.run_id,
                order.order_id,
                instrument.instrument_id,
                instrument.venue,
                request.side,
                order.arrival_at,
                self._delivery,
                filled,
                notional,
                instrument.quote.asset_id,
                fee,
                fee_asset,
                fees,
                estimate.consumed_levels,
                book.asof,
                book.key,
                book.revision,
            )
        )
        self._finish(
            order, tuple(dict.fromkeys((*resource_reason, *estimate.reasons))), filled
        )

    def _account_position(
        self,
        instrument: Instrument,
        inventory: Decimal,
        base_delta: Decimal,
        quote_delta: Decimal,
    ) -> None:
        key = (instrument.venue, instrument.base.asset_id)
        position = self._positions.get(key)
        basis = (
            position.cost_basis_quote if position else (ZERO if not inventory else None)
        )
        realized = position.realized_pnl_quote if position else ZERO
        reasons = position.reasons if position else ()
        if position and position.quote_asset_id != instrument.quote.asset_id:
            basis = realized = None
            reasons = ("reporting_quote_changed",)
        if base_delta > 0:
            if basis is not None:
                basis -= quote_delta
        else:
            if basis is None:
                realized = None
            else:
                removed_basis = (
                    basis
                    if -base_delta == inventory
                    else basis * (-base_delta) / inventory
                )
                basis -= removed_basis
                if realized is not None:
                    realized += quote_delta - removed_basis
        quantity = inventory + base_delta
        if not quantity:
            basis = ZERO
        self._positions[key] = PaperPosition(
            instrument.venue,
            instrument.base.asset_id,
            instrument.quote.asset_id,
            instrument.instrument_id,
            quantity,
            basis,
            realized,
            reasons=reasons,
        )

    def snapshot(self, now: ClockReading | None) -> PaperSnapshot:
        reasons: list[str] = []
        if self._frozen_reason is not None:
            reasons.extend(("paper_frozen", self._frozen_reason))
        if self._run is None:
            reasons.append("not_configured")
        elif self.store._revision(self._run.run_id) != self._run.revision:
            reasons.append("stale_run_reload_required")
        if self._run is not None and not self._activated:
            reasons.append("run_not_active_at_cursor")
        if self._applied < len(self._commands):
            reasons.append("future_commands_fork_required")
        at = self._at if self._frozen_reason is not None else now or self._at
        if self._run is not None and (
            self._clock_mismatch
            or at is not None
            and at.clock_id != self._run.created_at.clock_id
        ):
            reasons.append("run_source_clock_mismatch")
        positions = []
        with localcontext(DECIMAL_CONTEXT):
            for position in self._positions.values():
                assert self._run is not None  # Positions exist only after activation.
                why = list(position.reasons)
                mark: Decimal | None = None
                unrealized: Decimal | None = None
                instrument = self._metadata[position.instrument_id]
                books = [
                    book
                    for key, book in self._books.items()
                    if key in self._active
                    and book.instrument_id == position.instrument_id
                ]
                if len(books) == 1:
                    instrument = self._metadata_versions.get(
                        (books[0].instrument_id, books[0].metadata_version),
                        instrument,
                    )
                if "stale_run_reload_required" in reasons:
                    why.append("stale_run_reload_required")
                elif "run_source_clock_mismatch" in reasons:
                    why.append("run_source_clock_mismatch")
                elif at is None or len(books) != 1:
                    why.append("instrument_not_observed")
                elif (
                    instrument.base.asset_id != position.asset_id
                    or instrument.quote.asset_id != position.quote_asset_id
                ):
                    why.append("position_native_identity_changed")
                else:
                    gate = evaluate(
                        books[0], instrument, "paper_fill", at, self._run.config.policy
                    )
                    why.extend(gate.reasons)
                    if gate.eligible:
                        mark = books[0].bids[0].price
                if position.cost_basis_quote is None:
                    why.append(
                        "cost_basis_unavailable"
                        if position.reasons
                        else "unknown_initial_cost_basis"
                    )
                elif mark is not None:
                    unrealized = position.quantity * mark - position.cost_basis_quote
                if position.realized_pnl_quote is None:
                    why.append("realized_cost_basis_unavailable")
                positions.append(
                    replace(
                        position,
                        unrealized_pnl_quote=unrealized,
                        mark_price=mark,
                        reasons=tuple(why),
                    )
                )
        balances = tuple(
            PaperBalance(venue, asset, amount)
            for (venue, asset), amount in sorted(self._balances.items())
        )
        return PaperSnapshot(
            self._run,
            self._delivery,
            at,
            balances,
            tuple(self._orders.values()),
            tuple(self._fills),
            tuple(positions),
            tuple(reasons),
            assumptions=(
                "spot_ioc_and_isolated_one_way_perpetual",
                "immediate_or_cancel",
                "no_market_impact",
                "no_passive_fills",
                "conservative_per_price_reservations",
                "initial_inventory_basis_unknown",
                "user_modeled_risk_not_venue_tiers",
                "explicit_modeled_funding",
                "eligible_executable_side_book_proxy_not_exchange_mark",
                "isolated_shortfall_not_socialized",
            )
            if self._run and self._run.config.risk_models
            else SPOT_ASSUMPTIONS,
            perpetual_positions=self._perpetual_snapshot(at, tuple(reasons)),
            funding_payments=tuple(self._funding_payments),
            liquidations=tuple(self._liquidations),
        )

    def checkpoint(self) -> bytes:
        snapshot = self.snapshot(self._at)
        return _encode(
            PaperCheckpoint(
                self._run.run_id if self._run else None,
                self._run.revision if self._run else 0,
                self._applied,
                self._delivery,
                self._at,
                self._activated,
                tuple(self._books.values()),
                tuple(self._source_books.values()),
                tuple(self._delayed),
                tuple(self._reservations.values()),
                snapshot.balances,
                tuple(self._orders.values()),
                tuple(self._fills),
                tuple(self._positions.values()),
                tuple(self._metadata.values()),
                self._clock_mismatch,
                tuple(self._perpetual_positions.values()),
                tuple(self._funding_payments),
                tuple(
                    PaperFundingCursor(iid, through)
                    for iid, through in self._funding_cursors.items()
                ),
                tuple(self._liquidations),
                self._frozen_reason,
            )
        )

    def restore(self, state: bytes) -> None:
        value = _decode(state, PaperCheckpoint)
        if value.run_id != (
            self._run.run_id if self._run else None
        ) or value.revision != (self._run.revision if self._run else 0):
            raise PaperError("checkpoint_run_revision_mismatch")
        self._applied, self._delivery, self._at, self._activated = (
            value.applied,
            value.delivery_index,
            value.at,
            value.activated,
        )
        self._archive_dirty = False
        self._metadata = {item.instrument_id: item for item in value.instruments}
        self._rebuild_metadata_versions()
        self._metadata_versions.update(
            ((item.instrument_id, item.metadata_version), item)
            for item in value.instruments
        )
        self._clock_mismatch = value.clock_mismatch
        self._books = {book.key: book for book in value.books}
        self._source_books = {book.key: book for book in value.source_books}
        self._delayed = list(value.delayed)
        self._delayed_bytes = sum(
            _book_retained_bytes(item.book) for item in self._delayed
        )
        if self._delayed_bytes > MAX_DELAYED_BOOK_BYTES:
            raise PaperError("feed_latency_byte_limit")
        self._reservations = {
            (item.key.instrument_id, item.side, item.price): item
            for item in value.reservations
        }
        self._balances = {
            (item.venue, item.asset_id): item.available for item in value.balances
        }
        self._orders = {item.order_id: item for item in value.orders}
        self._fills = list(value.fills)
        self._fill_level_count = sum(len(fill.consumed_levels) for fill in self._fills)
        self._positions = {
            (item.venue, item.asset_id): item for item in value.positions
        }
        self._restore_perpetual(value)
        self._frozen_reason = value.frozen_reason

    def _reset_perpetual(self) -> None:
        self._perpetual_positions: dict[
            tuple[str, InstrumentId], PaperPerpetualPosition
        ] = {}
        self._funding_payments: list[PaperFundingPayment] = []
        self._funding_cursors: dict[InstrumentId, int] = {}
        self._liquidations: list[PaperLiquidation] = []
        self._liquidation_last: dict[
            InstrumentId,
            tuple[
                PaperPerpetualPosition,
                tuple[Level, ...],
                tuple[PaperReservation, ...],
                tuple[str, ...],
            ],
        ] = {}

    def _perpetual_snapshot(
        self, at: ClockReading | None, global_reasons: tuple[str, ...]
    ) -> tuple[PaperPerpetualPosition, ...]:
        positions = []
        with localcontext(DECIMAL_CONTEXT):
            for position in self._perpetual_positions.values():
                marked = (
                    self._mark_perpetual(position, at) if at is not None else position
                )
                why = list(marked.reasons)
                if (
                    len(self._funding_payments) >= MAX_PERPETUAL_RECORDS
                    and position.quantity
                ):
                    why.append("funding_record_capacity_exhausted")
                if global_reasons:
                    why.extend(global_reasons)
                    marked = replace(
                        marked,
                        mark_price=None,
                        unrealized_pnl=None,
                        equity=None,
                        maintenance_margin=None,
                        shortfall=None,
                    )
                if position.liquidating:
                    why.append("protective_liquidation_residual")
                if position.debt:
                    why.append("isolated_liquidation_debt")
                positions.append(replace(marked, reasons=tuple(dict.fromkeys(why))))
        return tuple(positions)

    def _restore_perpetual(self, value: PaperCheckpoint) -> None:
        self._perpetual_positions = {
            (p.venue, p.instrument_id): p for p in value.perpetual_positions
        }
        self._funding_payments = list(value.funding_payments)
        self._funding_cursors = {
            c.instrument_id: c.through_wall_ns for c in value.funding_cursors
        }
        self._liquidations = list(value.liquidations)
        # Rebuild the attempt cache from the checkpoint's post-attempt state.
        self._liquidation_last.clear()
        if value.at is not None:
            attempted = {r.instrument_id for r in value.liquidations}
            for position in self._perpetual_positions.values():
                if position.liquidating and position.instrument_id in attempted:
                    self._liquidation_last[position.instrument_id] = (
                        self._liquidation_state(position, value.at)
                    )

    def _risk_model(self, iid: InstrumentId) -> PaperRiskModel | None:
        assert self._run is not None
        return next(
            (m for m in self._run.config.risk_models if m.instrument_id == iid), None
        )

    def _funding_model(self, iid: InstrumentId) -> PaperFundingModel | None:
        assert self._run is not None
        return next(
            (m for m in self._run.config.funding_models if m.instrument_id == iid), None
        )

    def _perpetual_inputs(
        self, instrument: Instrument, at: ClockReading
    ) -> tuple[str, ...]:
        assert self._run is not None
        reason = economics_reason(instrument)
        if reason:
            return (reason,)
        risk, funding = (
            self._risk_model(instrument.instrument_id),
            self._funding_model(instrument.instrument_id),
        )
        if risk is None:
            return ("missing_perpetual_risk_model",)
        if funding is None:
            return ("missing_explicit_funding_model",)
        if at.wall_ns is None:
            return ("model_date_clock_unavailable",)
        if funding.mode != "none" and self._run.created_at.wall_ns is None:
            return ("funding_source_clock_anchor_unavailable",)
        if max(risk.asof_wall_ns, funding.asof_wall_ns) > at.wall_ns:
            return ("model_date_in_future",)
        return ()

    def _perpetual_book(
        self, instrument: Instrument, at: ClockReading
    ) -> tuple[Book | None, tuple[str, ...]]:
        assert self._run is not None
        books = [
            b
            for k, b in self._books.items()
            if k in self._active and b.instrument_id == instrument.instrument_id
        ]
        if len(books) != 1:
            return None, (
                "instrument_not_observed"
                if not books
                else "ambiguous_book_subscription",
            )
        book = books[0]
        observed = self._metadata_versions.get(
            (book.instrument_id, book.metadata_version)
        )
        if observed != instrument:
            return None, ("position_metadata_changed",)
        gate = evaluate(book, instrument, "paper_fill", at, self._run.config.policy)
        return (book, ()) if gate.eligible else (None, gate.reasons)

    def _mark_perpetual(
        self, position: PaperPerpetualPosition, at: ClockReading
    ) -> PaperPerpetualPosition:
        if not position.quantity:
            return replace(
                position,
                unrealized_pnl=ZERO,
                equity=ZERO,
                maintenance_margin=ZERO,
                shortfall=position.debt,
                mark_price=None,
            )
        book, reasons = self._perpetual_book(position.instrument, at)
        if book is None or self._clock_mismatch:
            return replace(
                position,
                mark_price=None,
                unrealized_pnl=None,
                equity=None,
                maintenance_margin=None,
                shortfall=None,
                reasons=tuple(
                    dict.fromkeys(
                        (
                            *position.reasons,
                            *(reasons or ("run_source_clock_mismatch",)),
                        )
                    )
                ),
            )
        levels = book.bids if position.quantity > 0 else book.asks
        if not levels:
            return replace(
                position,
                mark_price=None,
                unrealized_pnl=None,
                equity=None,
                maintenance_margin=None,
                shortfall=None,
                reasons=(*position.reasons, "no_mark_depth"),
            )
        mark = levels[0].price
        profit = pnl(position, abs(position.quantity), mark)
        equity = position.isolated_collateral + profit
        risk = self._risk_model(position.instrument_id)
        assert risk is not None  # Opening exposure requires an explicit model.
        maintenance = (
            settlement_value(position.instrument, position.quantity, mark)
            * risk.maintenance_margin_rate
        )
        return replace(
            position,
            mark_price=mark,
            unrealized_pnl=profit,
            equity=equity,
            maintenance_margin=maintenance,
            shortfall=max(ZERO, -equity) + position.debt,
        )

    def _funding_capacity_guard(
        self,
        now: ClockReading,
        *,
        inclusive: bool = True,
        command_cutoff: int | None = None,
    ) -> None:
        """Bound catch-up before projection mutation; never discard due payments."""
        if self._frozen_reason is not None:
            raise PaperError(self._frozen_reason)
        if self._run is None or self._clock_mismatch:
            return
        origin = self._run.created_at
        if origin.wall_ns is None or now.clock_id != origin.clock_id:
            return
        target = origin.wall_ns + now.mono_ns - origin.mono_ns - (0 if inclusive else 1)
        if command_cutoff is not None:
            for index in range(self._applied, len(self._commands)):
                command = self._commands[index]
                if command.delivery_index > command_cutoff:
                    break
                if command.at.clock_id == origin.clock_id:
                    target = max(
                        target, origin.wall_ns + command.at.mono_ns - origin.mono_ns
                    )
        pending: dict[InstrumentId, int] = {}
        for order in self._orders.values():
            if (
                order.status == "pending"
                and order.arrival_at.clock_id == origin.clock_id
            ):
                wall = origin.wall_ns + order.arrival_at.mono_ns - origin.mono_ns
                if wall <= target:
                    iid = order.request.instrument_id
                    pending[iid] = min(pending.get(iid, wall), wall)
        if command_cutoff is not None:
            latency = (
                self._run.config.decision_latency_ns + self._run.config.order_latency_ns
            )
            for index in range(self._applied, len(self._commands)):
                command = self._commands[index]
                if command.delivery_index > command_cutoff:
                    break
                if (
                    command.request is not None
                    and command.at.clock_id == origin.clock_id
                ):
                    wall = (
                        origin.wall_ns + command.at.mono_ns + latency - origin.mono_ns
                    )
                    if wall <= target:
                        iid = command.request.instrument_id
                        pending[iid] = min(pending.get(iid, wall), wall)
        active = {
            p.instrument_id for p in self._perpetual_positions.values() if p.quantity
        }
        capacity = MAX_PERPETUAL_RECORDS - len(self._funding_payments)
        for model in self._run.config.funding_models:
            if model.instrument_id in active:
                through = self._funding_cursors.get(model.instrument_id, origin.wall_ns)
            elif model.instrument_id in pending:
                through = pending[model.instrument_id]
            else:
                continue
            if model.mode == "periodic":
                assert model.first_wall_ns is not None
                assert model.interval_ns is not None
                first = (
                    model.first_wall_ns
                    + max(0, (through - model.first_wall_ns) // model.interval_ns + 1)
                    * model.interval_ns
                )
                count = max(0, (target - first) // model.interval_ns + 1)
            elif model.mode == "events":
                count = max(
                    0,
                    bisect_right(model.events, target, key=lambda e: e.wall_ns)
                    - bisect_right(model.events, through, key=lambda e: e.wall_ns),
                )
            else:
                count = 0
            capacity -= count
            if capacity < 0:
                raise PaperError("funding_catchup_limit")

    def _next_funding(
        self,
    ) -> tuple[ClockReading, PaperFundingModel, Decimal] | None:
        if (
            self._run is None
            or not self._activated
            or self._clock_mismatch
            or self._run.created_at.wall_ns is None
        ):
            return None
        if len(self._funding_payments) >= MAX_PERPETUAL_RECORDS:
            return None
        candidates = []
        positions = {p.instrument_id: p for p in self._perpetual_positions.values()}
        for model in self._run.config.funding_models:
            position = positions.get(model.instrument_id)
            if position is None or not position.quantity:
                continue
            through = self._funding_cursors.get(
                model.instrument_id, self._run.created_at.wall_ns
            )
            if model.mode == "periodic":
                assert model.first_wall_ns is not None
                assert model.interval_ns is not None
                assert model.rate is not None
                wall = (
                    model.first_wall_ns
                    + max(0, (through - model.first_wall_ns) // model.interval_ns + 1)
                    * model.interval_ns
                )
                rate = model.rate
            elif model.mode == "events":
                index = bisect_right(model.events, through, key=lambda e: e.wall_ns)
                if index == len(model.events):
                    continue
                event = model.events[index]
                wall, rate = event.wall_ns, event.rate
            else:
                continue
            origin = self._run.created_at
            assert origin.wall_ns is not None
            at = ClockReading(
                origin.clock_id, origin.mono_ns + wall - origin.wall_ns, wall
            )
            candidates.append((at, model, rate))
        return min(
            candidates, key=lambda x: (x[0].mono_ns, x[1].instrument_id), default=None
        )

    def _settle_funding_at(self, at: ClockReading) -> None:
        while (funding := self._next_funding()) is not None:
            if funding[0].clock_id != at.clock_id or funding[0].mono_ns != at.mono_ns:
                return
            self._apply_funding(*funding)

    def _apply_funding(
        self, at: ClockReading, model: PaperFundingModel, rate: Decimal
    ) -> None:
        assert at.wall_ns is not None
        with localcontext(DECIMAL_CONTEXT):
            key, position = next(
                (k, p)
                for k, p in self._perpetual_positions.items()
                if p.instrument_id == model.instrument_id
            )
            marked = self._mark_perpetual(position, at)
            amount: Decimal | None = None
            reasons: tuple[str, ...] = ()
            if marked.mark_price is None:
                reasons = ("funding_mark_unavailable_payment_unresolved",)
            else:
                amount = (
                    settlement_value(
                        position.instrument, position.quantity, marked.mark_price
                    )
                    * rate
                    * (1 if position.quantity > 0 else -1)
                )
                position = replace(
                    position,
                    isolated_collateral=position.isolated_collateral - amount,
                    funding_paid=position.funding_paid + amount,
                )
            self._perpetual_positions[key] = replace(
                position, reasons=tuple(dict.fromkeys((*position.reasons, *reasons)))
            )
            self._funding_payments.append(
                PaperFundingPayment(
                    position.instrument_id,
                    position.settlement_asset_id,
                    at,
                    rate,
                    position.quantity,
                    marked.mark_price,
                    amount,
                    model.provenance,
                    reasons,
                )
            )
            self._funding_cursors[model.instrument_id] = at.wall_ns
            self._archive_dirty = True

    def _perpetual_risk(self, at: ClockReading) -> None:
        if self._run is None or not self._activated or self._clock_mismatch:
            return
        with localcontext(DECIMAL_CONTEXT):
            for key, position in tuple(self._perpetual_positions.items()):
                if not position.quantity:
                    continue
                marked = self._mark_perpetual(position, at)
                breach = (
                    marked.equity is not None
                    and marked.maintenance_margin is not None
                    and marked.equity <= marked.maintenance_margin
                )
                if not breach and not position.liquidating:
                    continue
                position = replace(position, liquidating=True)
                self._perpetual_positions[key] = position
                self._archive_dirty = True
                if len(self._liquidations) >= MAX_PERPETUAL_RECORDS:
                    self._perpetual_positions[key] = replace(
                        position,
                        reasons=tuple(
                            dict.fromkeys(
                                (*position.reasons, "liquidation_record_limit")
                            )
                        ),
                    )
                    continue
                if self._liquidation_last.get(position.instrument_id) == (
                    self._liquidation_state(position, at)
                ):
                    continue
                request = PaperOrderRequest(
                    position.instrument_id,
                    "sell" if position.quantity > 0 else "buy",
                    abs(position.quantity),
                    reduce_only=True,
                )
                order = PaperOrder(
                    self._run.run_id + ":liquidation:" + str(len(self._liquidations)),
                    self._run.run_id,
                    request,
                    at,
                    self._delivery,
                    at,
                    "pending",
                )
                book, reasons = self._perpetual_book(position.instrument, at)
                if book is not None:
                    quantity, fee, reasons, fill_id = self._execute_perpetual(
                        order, book, position.instrument, protective=True
                    )
                else:
                    quantity, fee, fill_id = ZERO, ZERO, None
                after = self._perpetual_positions[key]
                record = PaperLiquidation(
                    position.instrument_id,
                    position.settlement_asset_id,
                    at,
                    abs(position.quantity),
                    quantity,
                    abs(after.quantity),
                    fee,
                    after.debt,
                    reasons,
                    fill_id,
                )
                self._liquidations.append(record)
                # Dedup against post-fill money and remaining native evidence,
                # not the pre-fill request or its exhaustion reason.
                self._liquidation_last[position.instrument_id] = (
                    self._liquidation_state(after, at)
                )

    def _liquidation_state(
        self, position: PaperPerpetualPosition, at: ClockReading
    ) -> tuple[
        PaperPerpetualPosition,
        tuple[Level, ...],
        tuple[PaperReservation, ...],
        tuple[str, ...],
    ]:
        book, reasons = self._perpetual_book(position.instrument, at)
        side = "sell" if position.quantity > 0 else "buy"
        levels = () if book is None else book.bids if side == "sell" else book.asks
        reservations = tuple(
            reservation
            for (iid, reserved_side, _), reservation in self._reservations.items()
            if iid == position.instrument_id and reserved_side == side
        )
        return position, levels, reservations, reasons

    def _execute_perpetual(
        self,
        order: PaperOrder,
        book: Book,
        instrument: Instrument,
        *,
        protective: bool = False,
    ) -> tuple[Decimal, Decimal, tuple[str, ...], str | None]:
        assert self._run is not None

        request, at = order.request, order.arrival_at

        def finish(
            reasons: tuple[str, ...],
            quantity: Decimal = ZERO,
            fee: Decimal = ZERO,
            fill_id: str | None = None,
        ) -> tuple[Decimal, Decimal, tuple[str, ...], str | None]:
            if not protective:
                self._finish(order, reasons, quantity)
            return quantity, fee, reasons, fill_id

        reasons = self._perpetual_inputs(instrument, at)
        if reasons:
            return finish(reasons)
        gate = evaluate(book, instrument, "paper_fill", at, self._run.config.policy)
        if not gate.eligible:
            return finish(gate.reasons)
        risk = self._risk_model(instrument.instrument_id)
        assert risk is not None and instrument.settlement is not None
        funding = self._funding_model(instrument.instrument_id)
        assert funding is not None and at.wall_ns is not None
        # A full audit history fences only exposure that can need another record.
        # The catch-up guard still rejects due overflow before any ledger mutation.
        funding_record_required = len(
            self._funding_payments
        ) >= MAX_PERPETUAL_RECORDS and (
            funding.mode == "periodic"
            or (
                funding.mode == "events"
                and bool(funding.events)
                and funding.events[-1].wall_ns > at.wall_ns
            )
        )
        settlement = instrument.settlement.asset_id
        fees = next(
            (
                f.schedule
                for f in self._run.config.fees
                if f.instrument_id == instrument.instrument_id
            ),
            FeeSchedule(),
        )
        if fees.rate is None:
            return finish(("unknown_fees",))
        assert fees.asof_wall_ns is not None
        if at.wall_ns is None or fees.asof_wall_ns > at.wall_ns:
            return finish(
                (
                    "fee_date_clock_unavailable"
                    if at.wall_ns is None
                    else "fee_date_in_future",
                )
            )
        if fees.asset_id not in (None, settlement):
            return finish(("unsupported_perpetual_fee_asset",))
        key = (instrument.venue, instrument.instrument_id)
        position = self._perpetual_positions.get(
            key,
            PaperPerpetualPosition(
                instrument.venue, instrument.instrument_id, settlement, instrument
            ),
        )
        if position.instrument != instrument:
            return finish(("position_metadata_changed",))
        balance_key = (instrument.venue, settlement)
        free = self._balances.get(balance_key, ZERO)
        sign = 1 if request.side == "buy" else -1
        lot = instrument.lot_size
        assert lot is not None  # The supported native size rule is fixed_lot.
        levels = book.asks if sign > 0 else book.bids
        consumed = []
        total = notional = fee_total = penalty_total = ZERO
        remaining = request.quantity
        constraints = native_order_constraints(instrument)
        if (
            constraints.min_notional is not None or constraints.max_notional is not None
        ) and constraints.min_notional_currency != instrument.quote.asset_id:
            return finish(("unknown_notional_constraint_currency",))
        maxima = [
            v
            for v in (
                constraints.max_base,
                constraints.max_market_base
                if request.order_type == "market"
                else constraints.max_limit_base,
            )
            if v is not None
        ]
        if maxima:
            remaining = min(remaining, min(maxima))
        why = []
        for level in levels:
            if request.limit_price is not None and (
                level.price > request.limit_price
                if sign > 0
                else level.price < request.limit_price
            ):
                why.append("limit_price_reached")
                break
            try:
                _validate_price(instrument, level.price)
                instrument.validate_quantity(level.quantity)
            except ValueError:
                return finish(("invalid_native_level",))
            old = self._reservations.get(
                (instrument.instrument_id, request.side, level.price)
            )
            take = min(
                remaining, max(ZERO, level.quantity - (old.consumed if old else ZERO))
            )
            _, unit_quote = _amounts(instrument, level.price, Decimal(1))
            if constraints.max_notional is not None:
                take = min(
                    take, max(ZERO, constraints.max_notional - notional) / unit_quote
                )
            take = (take / lot).to_integral_value(rounding=ROUND_FLOOR) * lot
            if take <= 0:
                continue
            closing = (
                min(take, abs(position.quantity))
                if position.quantity * sign < 0
                else ZERO
            )
            opening = take - closing
            unit = settlement_value(instrument, Decimal(1), level.price)
            if closing:
                fraction = closing / abs(position.quantity)
                realized = pnl(position, closing, level.price)
                fee = unit * closing * fees.rate
                penalty = (
                    unit * closing * risk.liquidation_fee_rate if protective else ZERO
                )
                collateral = position.isolated_collateral + realized - fee - penalty
                released_margin = position.initial_margin * fraction
                flat = closing == abs(position.quantity)
                funded_share = (
                    position.isolated_collateral * fraction + realized - fee - penalty
                )
                released = (
                    max(ZERO, collateral)
                    if flat
                    else min(max(ZERO, collateral), max(ZERO, funded_share))
                )
                debt = max(ZERO, -collateral) if flat else ZERO
                free += released
                position = replace(
                    position,
                    quantity=position.quantity + sign * closing,
                    entry_price=None if flat else position.entry_price,
                    initial_margin=ZERO
                    if flat
                    else position.initial_margin - released_margin,
                    isolated_collateral=ZERO if flat else collateral - released,
                    realized_pnl=position.realized_pnl + realized,
                    fees_paid=position.fees_paid + fee,
                    liquidation_fees_paid=position.liquidation_fees_paid + penalty,
                    debt=position.debt + debt,
                    liquidating=position.liquidating and not flat,
                )
                fee_total += fee
                penalty_total += penalty
            if opening:
                marked = self._mark_perpetual(position, at)
                if request.reduce_only or protective:
                    why.append("reduce_only_clamped")
                    opening = ZERO
                elif (
                    position.debt
                    or position.liquidating
                    or position.reasons
                    or (
                        position.quantity
                        and (
                            marked.equity is None
                            or marked.maintenance_margin is None
                            or marked.equity <= marked.maintenance_margin
                        )
                    )
                    or any(
                        p.debt or p.liquidating or p.reasons
                        for p in self._perpetual_positions.values()
                        if p.venue == instrument.venue
                        and p.settlement_asset_id == settlement
                    )
                ):
                    why.append("isolated_risk_frozen")
                    opening = ZERO
                elif funding_record_required:
                    why.append("funding_record_limit")
                    opening = ZERO
                else:
                    unit_cost = unit * (1 / risk.leverage + fees.rate)
                    cap = (free / unit_cost / lot).to_integral_value(
                        rounding=ROUND_FLOOR
                    ) * lot
                    if opening > cap:
                        why.append("insufficient_free_collateral")
                    opening = min(opening, cap)
                if opening:
                    margin, fee = (
                        unit * opening / risk.leverage,
                        unit * opening * fees.rate,
                    )
                    old_size = abs(position.quantity)
                    size = old_size + opening
                    if not old_size:
                        entry = level.price
                    elif instrument.inverse:
                        assert position.entry_price is not None
                        entry = size / (
                            old_size / position.entry_price + opening / level.price
                        )
                    else:
                        assert position.entry_price is not None
                        entry = (
                            old_size * position.entry_price + opening * level.price
                        ) / size
                    free -= margin + fee
                    position = replace(
                        position,
                        quantity=position.quantity + sign * opening,
                        entry_price=entry,
                        initial_margin=position.initial_margin + margin,
                        isolated_collateral=position.isolated_collateral + margin,
                        fees_paid=position.fees_paid + fee,
                    )
                    fee_total += fee
            taken = closing + opening
            if taken:
                base, quote = _amounts(instrument, level.price, taken)
                consumed.append(ConsumedLevel(level.price, taken, base, quote))
                total += taken
                notional += quote
                remaining -= taken
            if remaining <= 0:
                break
        if not total:
            return finish(tuple(dict.fromkeys(why)) or ("no_unreserved_depth",))
        if (constraints.min_base is not None and total < constraints.min_base) or (
            constraints.min_notional is not None and notional < constraints.min_notional
        ):
            return finish(("below_order_minimum",))
        new_prices = sum(
            (instrument.instrument_id, request.side, c.price) not in self._reservations
            for c in consumed
        )
        if len(self._reservations) + new_prices > MAX_RESERVED_PRICES:
            return finish(("reservation_price_limit",))
        if self._fill_level_count + len(consumed) > MAX_FILL_LEVELS:
            return finish(("fill_evidence_limit",))
        # Commit only after all native constraints and bounded evidence pass.
        self._balances[balance_key] = free
        previous = self._perpetual_positions.get(key)
        self._perpetual_positions[key] = position
        if previous is None or not previous.quantity:
            assert at.wall_ns is not None
            self._funding_cursors[instrument.instrument_id] = at.wall_ns
        self._fill_level_count += len(consumed)
        for c in consumed:
            rkey = (instrument.instrument_id, request.side, c.price)
            old = self._reservations.get(rkey)
            if old is None:
                displayed = next(
                    level.quantity for level in levels if level.price == c.price
                )
                old = PaperReservation(book.key, request.side, c.price, displayed, ZERO)
            self._reservations[rkey] = replace(
                old, consumed=old.consumed + c.native_quantity
            )
        fill_id = order.order_id + ":fill"
        self._fills.append(
            PaperFill(
                fill_id,
                order.run_id,
                order.order_id,
                instrument.instrument_id,
                instrument.venue,
                request.side,
                at,
                self._delivery,
                total,
                notional,
                instrument.quote.asset_id,
                fee_total,
                settlement,
                fees,
                tuple(consumed),
                book.asof,
                book.key,
                book.revision,
            )
        )
        self._archive_dirty = True
        if total < request.quantity and not why:
            why.append("known_depth_exhausted")
        return finish(tuple(dict.fromkeys(why)), total, penalty_total, fill_id)
