"""Bounded per-client analytical owners over one shared live registry.

Every replay reader, cursor and observer belongs to one dedicated worker thread.
The event loop owns lifecycle locks and pacing, never SQLite or heavy seek work.
"""

import asyncio
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, nullcontext
from dataclasses import fields, is_dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from functools import lru_cache, partial
from hashlib import sha256
import json
import os
from pathlib import Path
import time
from tempfile import TemporaryDirectory
from typing import (
    AsyncIterator,
    Callable,
    Hashable,
    Iterable,
    ParamSpec,
    TypeAlias,
    TypeVar,
    TypedDict,
    cast,
)
from uuid import uuid4

from .alerts import AlertEngine
from .analytics import AnalyticsEngine, AnalyticsSnapshot
from .book import Book
from .context import ContextEngine
from .demo import demo_source
from .demo_memory import MemoryReplaySource
from .events import ClientId, EventEnvelope, Gap, StreamId, SubscriptionKey
from .instruments import Instrument
from .journal import JournalStore
from .paper import PaperEngine, PaperStore
from .precision import DERIVED_DECIMAL_POLICY, canonical_decimal, decimal
from .quality import ClockReading, EligibilityPolicy, evaluate
from .recording import RecordedBatch, RecordingReader, SessionManifest, StreamSpec
from .replay import ReplayClock, ReplayPacer, ReplaySession, ReplaySource
from .subscriptions import SubscriptionLease, SubscriptionRegistry
from .visuals import VisualHistory, grouped_ladder, proposal_risk
from .wire import dumps


CaptureIdentity: TypeAlias = tuple[int, int, int, int, int, int]
LiveDelivery: TypeAlias = tuple[
    tuple[EventEnvelope, ...], tuple[Book, ...], int, int, int
]
P = ParamSpec("P")
R = TypeVar("R")
FRAME_BOOK_LEVELS = 100
FRAME_PROFILE_LEVELS = 200
FRAME_DISPLAY_EVIDENCE = 100
LIVE_QUEUE_BYTES = 128 * 1024 * 1024


class ClientTelemetry(TypedDict):
    queue_depth: int
    queue_highwater: int
    coalesced: int
    overflows: int
    ack_expired: int
    observer_duration_ms: float | None
    queue_wait_ms: float | None
    publish_to_ack_upper_bound_ms: float | None
    browser_receive_to_paint_ms: float | None


class SessionHealth(TypedDict):
    ready: bool
    reasons: list[str]


class SourceStatus(TypedDict):
    id: str
    mode: str
    label: str
    status: str
    synthetic: bool
    recording: bool
    duration_ns: str | None
    streams: int
    reasons: list[str]


@lru_cache(maxsize=128)
def _dto_field_names(kind):
    return tuple(field.name for field in fields(kind))


def dto(value):
    """Exact HTTP JSON; immutable provenance is converted once per call."""
    return _dto_value(value, {})


def _dto_value(value, memo):
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, Decimal):
        return canonical_decimal(value, policy=DERIVED_DECIMAL_POLICY)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return value
    if is_dataclass(value):
        identity = id(value)
        prior = memo.get(identity)
        if prior is not None:
            return prior
        result = {
            name: _dto_value(getattr(value, name), memo)
            for name in _dto_field_names(cast(Hashable, type(value)))
        }
        memo[identity] = result
        return result
    if isinstance(value, dict):
        return {str(key): _dto_value(item, memo) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_dto_value(item, memo) for item in value]
    raise TypeError(f"unsupported terminal DTO: {type(value).__name__}")


def _book_presentation(book: Book | None):
    if book is None:
        return None
    result = {
        field.name: dto(getattr(book, field.name))
        for field in fields(book)
        if field.name not in ("bids", "asks")
    }
    result["bids"] = dto(book.bids[:FRAME_BOOK_LEVELS])
    result["asks"] = dto(book.asks[:FRAME_BOOK_LEVELS])
    result["presentation"] = {
        "depth_limit": FRAME_BOOK_LEVELS,
        "bids_omitted": max(0, len(book.bids) - FRAME_BOOK_LEVELS),
        "asks_omitted": max(0, len(book.asks) - FRAME_BOOK_LEVELS),
    }
    return result


def _analytics_presentation(analytics: AnalyticsSnapshot | None):
    if analytics is None:
        return None
    result = {
        field.name: dto(getattr(analytics, field.name))
        for field in fields(analytics)
        if field.name not in ("profile", "display_evidence")
    }
    result["profile"] = dto(analytics.profile[:FRAME_PROFILE_LEVELS])
    result["display_evidence"] = dto(
        analytics.display_evidence[:FRAME_DISPLAY_EVIDENCE]
    )
    result["presentation"] = {
        "profile_limit": FRAME_PROFILE_LEVELS,
        "profile_omitted": max(0, len(analytics.profile) - FRAME_PROFILE_LEVELS),
        "evidence_limit": FRAME_DISPLAY_EVIDENCE,
        "evidence_omitted": max(
            0, len(analytics.display_evidence) - FRAME_DISPLAY_EVIDENCE
        ),
    }
    return result


def _input_clock(value):
    return (
        None
        if value is None
        else ClockReading(
            value.clock_id,
            int(value.mono_ns),
            None if value.wall_ns is None else int(value.wall_ns),
        )
    )


def _input_fees(value):
    from .costs import FeeSchedule

    if value.fee_bps is None:
        return FeeSchedule(asset_id=value.fee_asset_id)
    date = datetime.strptime(value.fee_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    with localcontext() as context:
        context.prec = 80
        return FeeSchedule(
            rate=decimal(value.fee_bps) / Decimal(10000),
            asset_id=value.fee_asset_id,
            provenance=value.fee_provenance,
            asof_wall_ns=int(date.timestamp()) * 1_000_000_000,
            assumed=True,
        )


def _input_fx(values):
    from .costs import FXRate

    return tuple(
        FXRate(
            item.base_asset_id,
            item.quote_asset_id,
            decimal(item.bid, positive=True),
            decimal(item.ask, positive=True),
            _input_clock(item.asof),
            item.source,
            None
            if item.conversion_fee_rate is None
            else decimal(item.conversion_fee_rate),
            item.assumed,
        )
        for item in values
    )


def _alert_summary(evaluation):
    detail_fields = (
        "references",
        "metric",
        "prints",
        "displayed",
        "pair",
        "trade_evidence",
        "book_evidence",
    )
    result = {
        field.name: dto(getattr(evaluation, field.name))
        for field in fields(evaluation)
        if field.name not in detail_fields
    }
    result["evidence_available"] = any(
        getattr(evaluation, name) for name in detail_fields
    )
    return result


def _paper_run_presentation(run, *, configuration=True):
    if run is None:
        return None
    excluded = ("instruments",) if configuration else ("instruments", "config")
    result = {
        field.name: dto(getattr(run, field.name))
        for field in fields(run)
        if field.name not in excluded
    }
    if configuration:
        result["instrument_metadata"] = [
            {
                "instrument_id": str(item.instrument_id),
                "metadata_version": item.metadata_version,
            }
            for item in run.instruments
        ]
    return result


def _paper_presentation(snapshot, request):
    result = {
        field.name: dto(getattr(snapshot, field.name))
        for field in fields(snapshot)
        if field.name not in ("run", "orders", "fills")
    }
    result["run"] = _paper_run_presentation(snapshot.run)
    result["orders"] = dto(snapshot.orders[-request.orders_limit :])
    fills = []
    for fill in snapshot.fills[-request.fills_limit :]:
        item = {
            field.name: dto(getattr(fill, field.name))
            for field in fields(fill)
            if field.name != "consumed_levels"
        }
        item["consumed_levels"] = dto(fill.consumed_levels[:20])
        item["consumed_levels_omitted"] = max(0, len(fill.consumed_levels) - 20)
        fills.append(item)
    result["fills"] = fills
    result["presentation"] = {
        "orders_limit": request.orders_limit,
        "orders_omitted": max(0, len(snapshot.orders) - request.orders_limit),
        "fills_limit": request.fills_limit,
        "fills_omitted": max(0, len(snapshot.fills) - request.fills_limit),
        "fill_levels_limit": 20,
    }
    return result


def _live_cohort(
    instrument: Instrument, instruments: tuple[Instrument, ...]
) -> tuple[Instrument, ...]:
    """Fill the existing cohort bound fairly, not by catalog venue ordering."""
    venues: dict[str, list[Instrument]] = {}
    for item in instruments:
        if (
            item.base.asset_id == instrument.base.asset_id
            and item.instrument_id != instrument.instrument_id
            and item.status == "active"
        ):
            venues.setdefault(item.venue, []).append(item)
    for peers in venues.values():
        peers.sort(
            key=lambda item: (
                item.market_type != instrument.market_type,
                item.quote.asset_id != instrument.quote.asset_id,
                item.instrument_id,
            )
        )
    ordered_venues = sorted(
        venues, key=lambda venue: (venue == instrument.venue, venue)
    )
    cohort = [instrument]
    for offset in range(15):
        for venue in ordered_venues:
            if offset < len(venues[venue]):
                cohort.append(venues[venue][offset])
                if len(cohort) == 16:
                    return tuple(cohort)
    return tuple(cohort)


class RuntimeFailure(Exception):
    def __init__(self, status: int, detail: str):
        self.status, self.detail = status, detail
        super().__init__(detail)


def capture_identity(path: Path) -> CaptureIdentity:
    """Pin both directory and manifest identity, including in-place rewrites."""
    directory, manifest = path.stat(), (path / "manifest.json").stat()
    return (
        directory.st_dev,
        directory.st_ino,
        manifest.st_dev,
        manifest.st_ino,
        manifest.st_size,
        manifest.st_mtime_ns,
    )


class ClientSession:
    def __init__(self, runtime: "TerminalRuntime", mode: str, source_id: str) -> None:
        self.runtime, self.mode, self.source_id = runtime, mode, source_id
        self.id = uuid4().hex
        self.generation = 1
        self.instrument_id: str | None = None
        self.key: SubscriptionKey | None = None
        self.status = "loading"
        self.error: str | None = None
        self.lock = asyncio.Lock()
        self.actions = asyncio.Lock()
        self.active_requests = 0
        self.active_work = 0
        self.health_evidence: tuple[
            Book | None, Instrument | None, ClockReading | None
        ] = (None, None, None)
        self.reader_identity: CaptureIdentity | None = None
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="terminal-" + self.id[:8]
        )
        self.replay: ReplaySession | None = None
        self.reader: RecordingReader | ReplaySource | None = None
        self.engine: AnalyticsEngine | None = None
        self.visuals: VisualHistory | None = None
        self.context: ContextEngine | None = None
        self.alerts: AlertEngine | None = None
        self.paper: PaperEngine | None = None
        self.paper_store: PaperStore | None = None
        self.journal: JournalStore | None = None
        self.source_identity = ""
        self.streams: tuple[StreamSpec, ...] = ()
        self.live_books: tuple[Book, ...] = ()
        self.live_clock = ReplayClock()
        self.delivery = 0
        self.history: deque[RecordedBatch] = deque()
        self.history_sizes: deque[int] = deque()
        self.history_bytes = 0
        self.history_floor = 1
        self.play_task: asyncio.Task[None] | None = None
        self.live_task: asyncio.Task[None] | None = None
        self.live_queue: asyncio.Queue[LiveDelivery] = asyncio.Queue(maxsize=256)
        self.live_bytes = 0
        self.live_inflight = 0
        self.live_keys: frozenset[SubscriptionKey] = frozenset()
        self.live_stream_ids: frozenset[StreamId] = frozenset()
        self.live_stream_keys: dict[StreamId, SubscriptionKey] = {}
        self.unsubscribe: Callable[[], None] | None = None
        self.socket_attached = False
        self.closed = False
        self.last_access = time.monotonic()
        self.pending_acks: OrderedDict[str, int] = OrderedDict()
        self.pending_views: OrderedDict[str, tuple[int, int]] = OrderedDict()
        self.frame_sequence = 0
        self.last_presentation: tuple[tuple[int, str | None, str], int] | None = None
        self.completed_view: (
            tuple[
                int,
                str | None,
                tuple[dict | None, dict | None, dict | None, SessionHealth],
            ]
            | None
        ) = None
        self.telemetry: ClientTelemetry = {
            "queue_depth": 0,
            "queue_highwater": 0,
            "coalesced": 0,
            "overflows": 0,
            "ack_expired": 0,
            "observer_duration_ms": None,
            "queue_wait_ms": None,
            "publish_to_ack_upper_bound_ms": None,
            "browser_receive_to_paint_ms": None,
        }

    @asynccontextmanager
    async def activity(self) -> AsyncIterator[None]:
        self.active_requests += 1
        try:
            yield
        finally:
            self.active_requests -= 1
            self.last_access = time.monotonic()

    def _owned_call(self, fn: Callable[[], R], closing: bool) -> R:
        if isinstance(self.reader, RecordingReader) and not closing:
            try:
                if capture_identity(self.reader.path) != self.reader_identity:
                    raise ValueError("managed capture identity changed")
            except (OSError, ValueError) as exc:
                raise RuntimeFailure(
                    503, "managed capture unavailable: " + str(exc)
                ) from exc
        result = fn()
        if not closing:
            books = self.replay.books() if self.replay else self.live_books
            book = next((item for item in books if item.key == self.key), None)
            instrument = next(
                (spec.instrument for spec in self.streams if spec.key == self.key), None
            )
            now = self.replay.clock.reading if self.replay else self.live_clock.reading
            self.health_evidence = (book, instrument, now)
        return result

    async def work(self, fn: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
        # Join cancellation before releasing reader/observer ownership.
        self.active_work += 1
        try:
            future = asyncio.get_running_loop().run_in_executor(
                self.executor,
                self._owned_call,
                partial(fn, *args, **kwargs),
                fn == self._close_worker,
            )
            try:
                return await asyncio.shield(future)
            except asyncio.CancelledError:
                await future
                raise
        except RuntimeFailure as exc:
            if exc.status == 503 and self.mode == "replay":
                self.status, self.error = "unavailable", exc.detail
                self.runtime.sources[self.source_id].update(
                    {"status": "unavailable", "reasons": [exc.detail]}
                )
            raise
        finally:
            self.active_work -= 1

    def health(self) -> SessionHealth:
        """Read immutable completed-owner evidence; never publish or touch access."""
        if self.closed or self.status in ("loading", "seeking", "unavailable", "error"):
            return {"ready": False, "reasons": [self.error or self.status]}
        book, instrument, now = self.health_evidence
        if book is None or instrument is None or now is None:
            return {"ready": False, "reasons": ["no_validated_book"]}
        if self.mode == "live":
            now = ClockReading(now.clock_id, time.monotonic_ns(), time.time_ns())
        eligibility = evaluate(book, instrument, "display", now, EligibilityPolicy())
        return {"ready": eligibility.eligible, "reasons": list(eligibility.reasons)}

    def check(self, generation: str | None = None) -> None:
        self.last_access = time.monotonic()
        if self.closed:
            raise RuntimeFailure(404, "session closed")
        if generation is not None and str(self.generation) != generation:
            raise RuntimeFailure(409, "obsolete session generation")
        if self.status in ("seeking", "loading"):
            raise RuntimeFailure(409, self.status)
        if self.status in ("unavailable", "error"):
            raise RuntimeFailure(503, self.error or self.status)

    def _initialize_consumers(self) -> None:
        if self.engine is None or not self.source_identity:
            raise RuntimeError("consumer source and analytics must be initialized")
        self.context = ContextEngine(self.streams)
        self.alerts = AlertEngine(self.streams, self.engine)
        self.paper_store = PaperStore(self.runtime.state_dir / "paper.sqlite3")
        self.journal = JournalStore(self.runtime.state_dir / "journal.sqlite3")
        self.paper = PaperEngine(self.streams, self.paper_store, self.source_identity)

    def _observers(
        self,
    ) -> tuple[VisualHistory, ContextEngine, AlertEngine, PaperEngine]:
        if (
            self.visuals is None
            or self.context is None
            or self.alerts is None
            or self.paper is None
        ):
            raise RuntimeError("session consumers have not been initialized")
        return self.visuals, self.context, self.alerts, self.paper

    def _initialize_replay(
        self,
        source: Path | ReplaySource,
        expected_capture: tuple[SessionManifest, CaptureIdentity] | None = None,
    ) -> None:
        if isinstance(source, Path):
            try:
                self.reader = RecordingReader(source)
                self.reader_identity = capture_identity(source)
                if expected_capture != (self.reader.manifest, self.reader_identity):
                    raise ValueError(
                        "capture differs from its verified catalog identity"
                    )
            except Exception as exc:
                if isinstance(self.reader, RecordingReader):
                    self.reader.close()
                self.reader = None
                raise RuntimeFailure(
                    503, "managed capture unavailable: " + str(exc)
                ) from exc
        else:
            self.reader = source
        streams = (
            self.reader.manifest.streams
            if isinstance(self.reader, RecordingReader)
            else self.reader.descriptor.streams
        )
        self.streams = streams
        self.engine = AnalyticsEngine(streams)
        self.visuals = VisualHistory(self.engine)
        if isinstance(self.reader, RecordingReader):
            manifest_hash = sha256(
                (self.reader.path / "manifest.json").read_bytes()
            ).hexdigest()
            self.source_identity = (
                "recording:" + self.reader.manifest.session_id + ":" + manifest_hash
            )
        else:
            self.source_identity = (
                "demo:" + sha256(dumps(self.reader.descriptor)).hexdigest()
            )
        self._initialize_consumers()
        self.replay = ReplaySession(self.reader, observers=self._observers())
        # Deliver the entire bootstrap and first scenario second, not a reduced
        # fixture. No data later than this cursor can enter analytics/history.
        first = None
        for _ in range(512):
            delay = self.replay.next_delay_ns()
            if delay is None:
                break
            if (
                first is not None
                and self.replay.clock.virtual_mono_ns - first >= 1_000_000_000
            ):
                break
            self.replay.step()
            if first is None:
                first = self.replay.clock.virtual_mono_ns
        self.status = "paused"

    def _retain_live_observation(self, keys: frozenset[SubscriptionKey]) -> None:
        if self.paper is not None:
            # Stop observing departed books at ownership cutover, not after the
            # asynchronous collector replacement has waited for network cleanup.
            # Retained subscriptions keep their existing causal book evidence.
            self.paper.replace_streams(
                tuple(spec for spec in self.streams if spec.key in keys),
                books=(),
                now=self._command_now(),
            )

    def _initialize_live(
        self,
        streams: tuple[StreamSpec, ...],
        instrument_id: str,
        baseline: tuple[Book, ...],
        clock_id: str,
    ) -> None:
        self.streams = streams
        self.choose(instrument_id)
        self.engine = AnalyticsEngine(streams)
        self.visuals = VisualHistory(self.engine, self.key)
        self.visuals.initialize_books(baseline)
        self.live_books = baseline
        self.live_clock.observe_frontier(
            ClockReading(clock_id, time.monotonic_ns(), time.time_ns())
        )
        now = self.live_clock.reading
        assert now is not None
        if self.paper is None:
            # Independent live sessions have different delivered histories even
            # when they share a physical source. Never resume across either ID.
            self.source_identity = (
                "live:" + sha256(dumps((clock_id, self.id))).hexdigest()
            )
            self._initialize_consumers()
            assert self.paper is not None
            self.paper.initialize_books(baseline, now)
        else:
            assert self.alerts is not None
            self.context = ContextEngine(streams)
            self.alerts.replace_streams(streams, self.engine)
            self.paper.replace_streams(streams, books=baseline, now=now)
        self.history.clear()
        self.history_sizes.clear()
        self.history_bytes = 0
        self.history_floor = self.delivery + 1

    def choose(self, instrument_id: str) -> None:
        books = [
            spec
            for spec in self.streams
            if spec.key.channel == "book"
            and str(spec.instrument.instrument_id) == instrument_id
        ]
        if len(books) != 1:
            raise RuntimeFailure(
                422, "instrument has no unique book reconstruction key"
            )
        self.instrument_id = instrument_id
        self.key = books[0].key
        if self.replay is not None and self.visuals is not None:
            # Reuse the owned reader and actual replay machinery; selection must
            # rebuild prior observations, not pretend the current book is history.
            cursor = self.replay.cursor.delivery_index
            self.visuals.select(self.key)
            self.replay.seek_delivery(cursor)
        self.pending_views.clear()

    def _view(self):
        books = self.replay.books() if self.replay is not None else self.live_books
        now = (
            self.replay.clock.reading
            if self.replay is not None
            else self.live_clock.reading
        )
        if self.mode == "live" and now is not None:
            now = ClockReading(now.clock_id, time.monotonic_ns(), time.time_ns())
        selected = next((book for book in books if book.key == self.key), None)
        analytics = (
            self.engine.snapshot(self.instrument_id, now, key=self.key)
            if self.engine is not None and now is not None and self.instrument_id
            else None
        )
        reasons = ["no_validated_book"]
        ready = False
        if selected is not None and now is not None:
            instrument = next(
                spec.instrument for spec in self.streams if spec.key == self.key
            )
            eligibility = evaluate(
                selected, instrument, "display", now=now, policy=EligibilityPolicy()
            )
            ready, reasons = eligibility.eligible, list(eligibility.reasons)
        replay = None
        if self.replay is not None:
            total = self.replay.total_deliveries
            replay = {
                "cursor": dto(self.replay.cursor),
                "speed": str(self.replay.clock.speed),
                "status": self.status,
                "total_deliveries": str(total),
            }
        return (
            _book_presentation(selected),
            _analytics_presentation(analytics),
            replay,
            {"ready": ready, "reasons": reasons},
        )

    async def frame(self):
        async with self.activity():
            return await self._frame()

    async def _frame(self):
        self.last_access = time.monotonic()
        presentation_status = None
        # Loading/seeking frames must not leak a partially recomputed observer.
        if self.status in ("loading", "seeking", "unavailable", "error"):
            book, analytics, replay, health = (
                None,
                None,
                None,
                {"ready": False, "reasons": [self.error or self.status]},
            )
        else:
            try:
                await asyncio.wait_for(self.lock.acquire(), timeout=0.05)
            except TimeoutError:
                # A queued seek/query must not park WS liveness behind its work.
                presentation_status = (
                    self.status
                    if self.status in ("seeking", "unavailable", "error")
                    else "loading"
                )
                book, analytics, replay, health = (
                    None,
                    None,
                    None,
                    {"ready": False, "reasons": [self.error or "owner_busy"]},
                )
                cached = self.completed_view
                if (
                    self.status not in ("loading", "seeking", "unavailable", "error")
                    and cached is not None
                    and cached[:2] == (self.generation, self.instrument_id)
                ):
                    # One completed DTO, never a partially advancing observer.
                    # Retained evidence is diagnostic until ownership is free.
                    book, analytics, replay, _ = cached[2]
                    presentation_status = self.status
                    health = {
                        "ready": False,
                        "reasons": ["owner_busy", "last_completed_presentation"],
                    }
            else:
                try:
                    if self.closed:
                        raise RuntimeFailure(404, "session closed")
                    if self.status in ("loading", "seeking", "unavailable", "error"):
                        book, analytics, replay, health = (
                            None,
                            None,
                            None,
                            {"ready": False, "reasons": [self.error or self.status]},
                        )
                    else:
                        book, analytics, replay, health = await self.work(self._view)
                        self.completed_view = (
                            self.generation,
                            self.instrument_id,
                            (book, analytics, replay, health),
                        )
                finally:
                    self.lock.release()
        self.frame_sequence += 1
        frame_id = str(self.frame_sequence)
        stamp = time.monotonic_ns()
        self.pending_acks[frame_id] = stamp
        while len(self.pending_acks) > 64:
            self.pending_acks.popitem(last=False)
            self.telemetry["ack_expired"] += 1
        if book is not None:
            identity = (self.generation, self.instrument_id, book["connection_epoch"])
            revision = int(book["revision"])
            if (
                self.last_presentation is not None
                and self.last_presentation[0] == identity
            ):
                self.telemetry["coalesced"] += max(
                    0, revision - self.last_presentation[1] - 1
                )
            self.last_presentation = (identity, revision)
        return {
            "version": "1",
            "type": "frame",
            "frame_id": frame_id,
            "session_id": self.id,
            "generation": str(self.generation),
            "status": presentation_status or self.status,
            "selection": {"instrument_id": self.instrument_id},
            "source": self.runtime.source(self.source_id),
            "book": book,
            "analytics": analytics,
            "replay": replay,
            "health": health,
            "telemetry": {
                **self.telemetry,
                "published_mono_ns": str(stamp),
                "ingestion": dto(self.runtime.registry.snapshot())
                if self.mode == "live" and self.runtime.registry is not None
                else [],
            },
            "error": self.error,
        }

    def ack(self, frame_id: str, browser_ms: float) -> None:
        stamp = self.pending_acks.pop(frame_id, None)
        if stamp is None:
            return
        self.telemetry["publish_to_ack_upper_bound_ms"] = (
            time.monotonic_ns() - stamp
        ) / 1_000_000
        self.telemetry["browser_receive_to_paint_ms"] = browser_ms

    async def stop_play(self) -> None:
        task, self.play_task = self.play_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self.replay is not None and not self.closed:
            await self.work(self.replay.pause)

    async def _play(self) -> None:
        replay = self.replay
        if replay is None:
            raise RuntimeError("replay playback requires a replay session")
        loop = asyncio.get_running_loop()
        pacer = ReplayPacer(loop.time(), replay.clock.speed)
        try:
            while not self.closed and self.status == "playing":
                async with self.lock:
                    delay = await self.work(replay.next_delay_ns)
                if delay is None:
                    self.status = "ended"
                    break
                pacer.advance(delay)
                remaining = pacer.remaining(loop.time())
                if remaining:
                    await asyncio.sleep(remaining)
                async with self.lock:
                    started = time.monotonic_ns()
                    await self.work(replay.step)
                    self.telemetry["observer_duration_ms"] = (
                        time.monotonic_ns() - started
                    ) / 1_000_000
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.status, self.error = (
                (
                    "unavailable"
                    if isinstance(exc, RuntimeFailure) and exc.status == 503
                    else "error"
                ),
                str(exc),
            )

    async def control(self, generation, action, count=1, speed="1", delivery_index="0"):
        self.check(generation)
        if self.replay is None:
            raise RuntimeFailure(409, "live sources have no replay cursor")
        was_playing = self.status == "playing"
        if action == "seek":
            total = self.replay.total_deliveries
            if not 0 <= int(delivery_index) <= total:
                raise RuntimeFailure(422, "seek target beyond source deliveries")
        # Pacing is stopped before acquiring the owner lock (never deadlock a
        # playback task waiting for that same lock).
        await self.stop_play()
        async with self.lock:
            self.check(generation)
            if action in ("seek", "reset"):
                self.status = "seeking"
                self.generation += 1
                self.pending_acks.clear()
                self.pending_views.clear()
                try:
                    await self.work(
                        self.replay.seek_delivery,
                        0 if action == "reset" else int(delivery_index),
                    )
                except BaseException as exc:
                    self.status, self.error = (
                        (
                            "unavailable"
                            if isinstance(exc, RuntimeFailure) and exc.status == 503
                            else "error"
                        ),
                        str(exc),
                    )
                    raise
            elif action == "step":
                await self.work(self.replay.step, count)
            elif action == "speed":
                await self.work(self.replay.set_speed, decimal(speed, positive=True))
            self.status = "paused"
            if action == "play" or action == "speed" and was_playing:
                self.status = "playing"
                self.play_task = asyncio.create_task(self._play())
        return await self.frame()

    def affected_keys(self, events: tuple[EventEnvelope, ...]) -> set[SubscriptionKey]:
        affected = {event.key for event in events}
        for event in events:
            if isinstance(event.payload, Gap):
                affected.update(
                    self.live_stream_keys[stream_id]
                    for stream_id in event.payload.affected_streams
                    if stream_id in self.live_stream_keys
                )
        return affected

    def offer(self, events: tuple[EventEnvelope, ...], books: tuple[Book, ...]) -> None:
        if self.closed or self.status in ("unavailable", "error") or not events:
            return
        selected = tuple(
            event for event in events if event.stream_id in self.live_stream_ids
        )
        if not selected:
            return
        self._enqueue_live(selected, books)

    def _enqueue_live(
        self, selected: tuple[EventEnvelope, ...], books: tuple[Book, ...]
    ) -> None:
        # Immutable batch + atomic post-reduction books from the registry only.
        affected = self.affected_keys(selected)
        changed_books = tuple(book for book in books if book.key in affected)
        # Charge retained immutable books as well as events. Fixed native Decimal
        # bounds permit a conservative per-level object budget without building
        # giant book repr/JSON strings in the ingestion callback.
        event_bytes = len(repr(selected))
        size = event_bytes + sum(
            4096 + 512 * (len(book.bids) + len(book.asks)) for book in changed_books
        )
        if (
            self.live_queue.qsize() + self.live_inflight >= self.live_queue.maxsize
            or self.live_bytes + size > LIVE_QUEUE_BYTES
        ):
            self.telemetry["overflows"] += 1
            self.status, self.error = (
                "unavailable",
                "analytical_queue_overflow: reselect to establish fresh history",
            )
            return
        self.live_bytes += size
        self.live_queue.put_nowait(
            (selected, changed_books, time.monotonic_ns(), size, event_bytes)
        )
        depth = self.live_queue.qsize() + self.live_inflight
        self.telemetry["queue_depth"] = depth
        self.telemetry["queue_highwater"] = max(
            self.telemetry["queue_highwater"], depth
        )

    def _apply_live(
        self,
        events: tuple[EventEnvelope, ...],
        books: tuple[Book, ...],
        event_bytes: int,
    ) -> None:
        if self.engine is None:
            raise RuntimeError("live observer has not been initialized")
        self.delivery += 1
        batch = RecordedBatch(self.delivery, events)
        self.live_clock.advance(events[0])
        affected = self.affected_keys(events)
        if books or any(key.channel == "book" for key in affected):
            current = {book.key: book for book in self.live_books}
            supplied = frozenset(book.key for book in books)
            for key in affected:
                if key.channel == "book" and key not in supplied:
                    current.pop(key, None)
            current.update((book.key, book) for book in books)
            self.live_books = tuple(current.values())
        for observer in self._observers():
            observer.on_batch(batch, self.live_books, self.live_clock)
        size = event_bytes
        while self.history and (
            len(self.history) >= 4096 or self.history_bytes + size > 8 * 1024 * 1024
        ):
            self.history_floor = self.history.popleft().delivery_index + 1
            self.history_bytes -= self.history_sizes.popleft()
        if size <= 8 * 1024 * 1024:
            self.history.append(batch)
            self.history_sizes.append(size)
            self.history_bytes += size
        else:
            self.history_floor = self.delivery + 1

    def _take_live_batch(
        self, first: LiveDelivery, limit: int = 32
    ) -> list[LiveDelivery]:
        batch = [first]
        for _ in range(1, limit):
            try:
                batch.append(self.live_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        self.live_inflight = len(batch)
        self.telemetry["queue_depth"] = self.live_queue.qsize() + self.live_inflight
        return batch

    def _release_live_batch(self, batch: list[LiveDelivery]) -> None:
        self.live_bytes -= sum(delivery[3] for delivery in batch)
        self.live_inflight = 0
        self.telemetry["queue_depth"] = self.live_queue.qsize()

    def _apply_live_batch(
        self, batch: list[LiveDelivery], bootstrap: bool = False
    ) -> None:
        for events, books, _queued, _size, event_bytes in batch:
            if self.status in ("unavailable", "error"):
                break
            if bootstrap:
                original_count = len(events)
                events = tuple(
                    event for event in events if event.stream_id in self.live_stream_ids
                )
                if len(events) != original_count:
                    event_bytes = len(repr(events))
                affected = self.affected_keys(events)
                books = tuple(book for book in books if book.key in affected)
            if events:
                self._apply_live(events, books, event_bytes)

    async def consume_live(self, *, bootstrap: bool = False) -> None:
        try:
            while True:
                batch = self._take_live_batch(await self.live_queue.get())
                try:
                    # Bootstrap is private creation or runs under the selecting
                    # caller's lock; waiting for that lock would deadlock cutover.
                    async with nullcontext() if bootstrap else self.lock:
                        if self.status != ("loading" if bootstrap else "live"):
                            continue
                        self.telemetry["queue_wait_ms"] = (
                            time.monotonic_ns() - batch[0][2]
                        ) / 1_000_000
                        start = time.monotonic_ns()
                        await self.work(self._apply_live_batch, batch, bootstrap)
                        self.telemetry["observer_duration_ms"] = (
                            time.monotonic_ns() - start
                        ) / 1_000_000
                finally:
                    # Work joins cancellation before releasing admitted ownership.
                    # A detached batch remains charged to both queue bounds.
                    self._release_live_batch(batch)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.status, self.error = "error", str(exc)

    def _history(self, start, end, limit):
        cursor = self.replay.cursor.delivery_index if self.replay else self.delivery
        requested_end = min(end, cursor)
        end = min(requested_end, max(1, start) + 9999)
        events: list[dict[str, object]] = []
        response_bytes = 0
        range_truncated = end < requested_end or bool(
            self.replay is None and max(1, start) < self.history_floor
        )
        truncated = False
        if end >= max(1, start):
            if self.replay is not None and self.reader is None:
                raise RuntimeError("replay reader is unavailable")
            batches = (
                self.reader.iter_batches(
                    start_delivery=max(1, start), stop_delivery=end
                )
                if self.replay is not None and self.reader is not None
                else (
                    batch
                    for batch in self.history
                    if max(1, start) <= batch.delivery_index <= end
                )
            )
            try:
                for batch in batches:
                    for event in batch.events:
                        if str(event.key.instrument_id) != self.instrument_id:
                            continue
                        if len(events) == limit:
                            truncated = True
                            break
                        encoded = dto(event)
                        response_bytes += len(repr(encoded))
                        if response_bytes > 4 * 1024 * 1024:
                            truncated = True
                            break
                        events.append(encoded)
                    if truncated:
                        break
            finally:
                close = getattr(batches, "close", None)
                if close:
                    close()
        return {
            "generation": str(self.generation),
            "cursor": str(cursor),
            "events": events,
            "truncated": truncated or range_truncated,
        }

    def _visual_snapshot(self):
        if self.visuals is None:
            raise RuntimeFailure(409, "visualization observer unavailable")
        return self.visuals.query_snapshot()

    def _publish_visualization(self, snapshot, result):
        if self.visuals is None or not self.visuals.accept_query(snapshot):
            raise RuntimeFailure(409, "visualization source changed during query")
        published = time.monotonic_ns()
        view_id = uuid4().hex
        while self.pending_views and (
            len(self.pending_views) >= 64
            or published - next(iter(self.pending_views.values()))[1] > 60_000_000_000
        ):
            self.pending_views.popitem(last=False)
        self.pending_views[view_id] = (self.generation, published)
        result.update(view_id=view_id, published_mono_ns=str(published))
        return result

    def _visual_ack(self, request):
        pending = self.pending_views.pop(request.view_id, None)
        now = time.monotonic_ns()
        if pending is None or pending[0] != self.generation:
            raise RuntimeFailure(409, "unknown or obsolete visualization publication")
        elapsed = now - pending[1]
        if elapsed > 60_000_000_000:
            raise RuntimeFailure(409, "visualization publication expired")
        return {
            "publish_to_ack_upper_bound_ms": elapsed / 1_000_000,
            "browser_receive_to_paint_ms": request.browser_receive_to_paint_ms,
        }

    def _visual_evidence(self):
        books = self.replay.books() if self.replay else self.live_books
        book = next((item for item in books if item.key == self.key), None)
        instrument = next(
            spec.instrument for spec in self.streams if spec.key == self.key
        )
        now = self.replay.clock.reading if self.replay else self.live_clock.reading
        if self.mode == "live" and now is not None:
            now = ClockReading(now.clock_id, time.monotonic_ns(), time.time_ns())
        elif (
            self.replay
            and self.replay.clock.source_reading is not None
            and now is not None
        ):
            now = ClockReading(
                now.clock_id, now.mono_ns, self.replay.clock.source_reading.wall_ns
            )
        return book, instrument, now

    def _ladder(self, request):
        book, instrument, now = self._visual_evidence()
        return grouped_ladder(book, instrument, now, request, str(self.generation))

    def _proposal_risk(self, request):
        _, instrument, now = self._visual_evidence()
        return proposal_risk(instrument, now, request, str(self.generation))

    def _costs(self, request):
        from .costs import (
            FeeSchedule,
            FXRate,
            InventoryBalance,
            PairEstimate,
            StressAssumptions,
            ComparisonEntry,
            compare_matrix,
            walk_book,
        )

        books = self.replay.books() if self.replay else self.live_books
        now = self.replay.clock.reading if self.replay else self.live_clock.reading
        if self.mode == "live" and now is not None:
            now = ClockReading(now.clock_id, time.monotonic_ns(), time.time_ns())
        selected = next((book for book in books if book.key == self.key), None)
        if selected is None or now is None:
            return {
                "version": "1",
                "generation": str(self.generation),
                "estimate": None,
                "matrix": [],
                "reasons": ["no_validated_book"],
            }
        instrument = next(
            spec.instrument for spec in self.streams if spec.key == self.key
        )
        fees = FeeSchedule()

        def clock(value):
            return (
                None
                if value is None
                else ClockReading(
                    value.clock_id,
                    int(value.mono_ns),
                    None if value.wall_ns is None else int(value.wall_ns),
                )
            )

        fx_rates = tuple(
            FXRate(
                item.base_asset_id,
                item.quote_asset_id,
                decimal(item.bid, positive=True),
                decimal(item.ask, positive=True),
                clock(item.asof),
                item.source,
                None
                if item.conversion_fee_rate is None
                else decimal(item.conversion_fee_rate),
                item.assumed,
            )
            for item in request.fx_rates
        )
        inventory = tuple(
            InventoryBalance(
                item.venue,
                item.asset_id,
                decimal(item.available),
                item.provenance,
                clock(item.asof),
            )
            for item in request.inventory
        )
        with localcontext() as context:
            context.prec = 80
            if request.fee_bps is not None:
                date = datetime.strptime(request.fee_date, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
                fees = FeeSchedule(
                    rate=decimal(request.fee_bps) / Decimal(10_000),
                    provenance=request.fee_provenance,
                    asof_wall_ns=int(date.timestamp()) * 1_000_000_000,
                    assumed=True,
                )
            estimate = walk_book(
                selected,
                instrument,
                request.side,
                base_quantity=None
                if request.base_quantity is None
                else decimal(request.base_quantity, positive=True),
                quote_budget=None
                if request.quote_budget is None
                else decimal(request.quote_budget, positive=True),
                fees=fees,
                policy=EligibilityPolicy(),
                now=now,
            )
            quantity = (
                decimal(request.base_quantity, positive=True)
                if request.base_quantity
                else estimate.filled_base_quantity
            )
            pairs: tuple[PairEstimate, ...] = ()
            if quantity is not None and quantity > 0:
                entries = []
                instruments = {
                    spec.key: spec.instrument
                    for spec in self.streams
                    if spec.key.channel == "book"
                }
                for book in books:
                    other = instruments.get(book.key)
                    if (
                        other is not None
                        and other.base.asset_id == instrument.base.asset_id
                    ):
                        entries.append(ComparisonEntry(book, other, fees))
                matrix = compare_matrix(
                    tuple(entries),
                    base_quantity=quantity,
                    policy=EligibilityPolicy(),
                    now=now,
                    numeraire=request.numeraire,
                    fx_rates=fx_rates,
                    inventory=inventory,
                    stress=StressAssumptions(
                        latency_bps=decimal(request.latency_bps),
                        rebalance_cost=decimal(request.rebalance_quote),
                    ),
                )
                pairs = matrix.pairs
        return {
            "version": "1",
            "generation": str(self.generation),
            "estimate": dto(estimate),
            "matrix": dto(pairs),
            "reasons": [],
        }

    def _delivery_index(self) -> int:
        return self.replay.cursor.delivery_index if self.replay else self.delivery

    def _consumer_result(self, **payload):
        return {
            "version": "1",
            "session_id": self.id,
            "generation": str(self.generation),
            "source_identity": self.source_identity,
            "delivery_index": str(self._delivery_index()),
            **payload,
        }

    def _now(self) -> ClockReading | None:
        if self.replay is not None:
            return self.replay.clock.reading
        previous = self.live_clock.reading
        if previous is not None:
            self.live_clock.observe_frontier(
                ClockReading(previous.clock_id, time.monotonic_ns(), time.time_ns())
            )
        return self.live_clock.reading

    def _command_now(self) -> ClockReading:
        now = self._now()
        if now is None:
            raise RuntimeFailure(
                409, "deliver a source observation before authoring a command"
            )
        return now

    def _book_spec(self, instrument_id: str | None = None) -> StreamSpec:
        identifier = self.instrument_id if instrument_id is None else instrument_id
        matches = [
            spec
            for spec in self.streams
            if spec.key.channel == "book"
            and str(spec.instrument.instrument_id) == identifier
        ]
        if len(matches) != 1:
            raise RuntimeFailure(
                422, "instrument has no unique currently observed book key"
            )
        return matches[0]

    def _alerts_query(self, request):
        from .alerts import ALERT_KINDS, alert_unit

        assert self.alerts is not None
        snapshot = self.alerts.snapshot()
        events = [
            {
                **{
                    field.name: dto(getattr(event, field.name))
                    for field in fields(event)
                    if field.name != "evaluation"
                },
                "evaluation": _alert_summary(event.evaluation),
            }
            for event in snapshot.events[-request.event_limit :]
        ]
        instrument = self._book_spec().instrument
        return self._consumer_result(
            snapshot={
                "rules": dto(snapshot.rules),
                "evaluations": [_alert_summary(item) for item in snapshot.evaluations],
                "events": events,
                "delivery_index": str(snapshot.delivery_index),
                "now": dto(snapshot.now),
                "dropped_events": str(snapshot.dropped_events),
                "reasons": dto(snapshot.reasons),
            },
            kinds=[
                {"kind": kind, "unit": alert_unit(instrument, kind)}
                for kind in ALERT_KINDS
            ],
            evaluation_scope="last_delivered_batch",
            presentation={
                "event_limit": request.event_limit,
                "events_omitted": max(0, len(snapshot.events) - request.event_limit),
            },
        )

    def _alert_put(self, request):
        from .alerts import AlertRule, alert_unit
        from .costs import StressAssumptions

        assert self.alerts is not None
        spec = self._book_spec(request.instrument_id)
        sell = (
            None
            if request.sell_instrument_id is None
            else self._book_spec(request.sell_instrument_id)
        )
        rule = AlertRule(
            rule_id=request.rule_id or uuid4().hex,
            key=spec.key,
            kind=request.kind,
            direction=request.direction,
            threshold=decimal(request.threshold),
            rearm_threshold=decimal(request.rearm_threshold),
            unit=alert_unit(spec.instrument, request.kind),
            window_ns=int(request.window_ns),
            cooldown_ns=int(request.cooldown_ns),
            depth_side=request.depth_side,
            depth_bps=decimal(request.depth_bps, positive=True),
            sell_key=None if sell is None else sell.key,
            base_quantity=None
            if request.base_quantity is None
            else decimal(request.base_quantity, positive=True),
            buy_fees=_input_fees(request.buy_fees),
            sell_fees=_input_fees(request.sell_fees),
            stress=StressAssumptions(
                latency_bps=decimal(request.latency_bps),
                rebalance_cost=decimal(request.rebalance_quote),
            ),
        )
        self.alerts.put_rule(rule, delivery_index=self._delivery_index())
        return self._alerts_query(request)

    def _alert_remove(self, request):
        assert self.alerts is not None
        self.alerts.remove_rule(request.rule_id, delivery_index=self._delivery_index())
        return self._alerts_query(request)

    def _alert_evidence(self, request):
        assert self.alerts is not None
        snapshot = self.alerts.snapshot()
        if request.event_id is not None:
            evaluation = next(
                (
                    item.evaluation
                    for item in snapshot.events
                    if str(item.event_id) == request.event_id
                ),
                None,
            )
        else:
            evaluation = next(
                (
                    item
                    for item in snapshot.evaluations
                    if item.rule.rule_id == request.rule_id
                ),
                None,
            )
        if evaluation is None:
            raise RuntimeFailure(
                404, "alert evidence is absent from this retained prefix"
            )
        return self._consumer_result(evaluation=dto(evaluation))

    def _context_query(self, request):
        assert self.context is not None
        snapshot = self.context.snapshot(
            str(self._book_spec().instrument.instrument_id),
            books=self.replay.books() if self.replay else self.live_books,
            now=self._now(),
            fx_rates=_input_fx(request.fx_rates),
        )
        return self._consumer_result(snapshot=dto(snapshot))

    def _paper_query(self, request):
        assert self.paper is not None
        now = self._now()
        if self.mode == "live" and now is not None:
            self.paper.advance_time(now)
        return self._consumer_result(
            snapshot=_paper_presentation(self.paper.snapshot(now), request)
        )

    def _paper_configure(self, request):
        from .costs import InventoryBalance
        from .paper import PaperConfig, PaperFee

        assert self.paper is not None
        config = PaperConfig(
            balances=tuple(
                InventoryBalance(
                    item.venue,
                    item.asset_id,
                    decimal(item.available),
                    item.provenance,
                    _input_clock(item.asof),
                )
                for item in request.balances
            ),
            fees=tuple(
                PaperFee(item.instrument_id, _input_fees(item)) for item in request.fees
            ),
            feed_latency_ns=int(request.feed_latency_ns),
            decision_latency_ns=int(request.decision_latency_ns),
            order_latency_ns=int(request.order_latency_ns),
        )
        self.paper.configure(
            config, now=self._command_now(), delivery_index=self._delivery_index()
        )
        return self._paper_query(request)

    def _paper_order(self, request):
        from .paper import PaperOrderRequest

        assert self.paper is not None
        spec = self._book_spec(request.instrument_id)
        self.paper.submit(
            PaperOrderRequest(
                spec.instrument.instrument_id,
                request.side,
                decimal(request.quantity, positive=True),
                request.order_type,
                None
                if request.limit_price is None
                else decimal(request.limit_price, positive=True),
            ),
            now=self._command_now(),
            delivery_index=self._delivery_index(),
        )
        return self._paper_query(request)

    def _paper_cancel(self, request):
        assert self.paper is not None
        self.paper.cancel(
            request.order_id,
            now=self._command_now(),
            delivery_index=self._delivery_index(),
        )
        return self._paper_query(request)

    def _paper_runs(self, request):
        assert self.paper_store is not None
        return self._consumer_result(
            runs=[
                _paper_run_presentation(run, configuration=False)
                for run in self.paper_store.list_runs(limit=request.limit)
            ]
        )

    def _paper_archive(self, request):
        assert self.paper_store is not None
        snapshot = self.paper_store.archived_snapshot(request.run_id)
        if snapshot is None:
            raise RuntimeFailure(404, "no archived accounting transition for this run")
        return self._consumer_result(
            archived=True,
            archive_scope="historical_accounting_not_current_replay_state",
            snapshot=_paper_presentation(snapshot, request),
        )

    def _paper_candidate(self, request) -> PaperEngine:
        if self.replay is None:
            raise RuntimeFailure(
                422, "live histories cannot be resumed across session/source ownership"
            )
        assert self.paper_store is not None
        candidate = PaperEngine(
            self.streams,
            self.paper_store,
            self.source_identity,
            run_id=request.run_id,
            archive_enabled=False,
        )
        # Rebuild only the candidate against an independent projection. Failure
        # must not reset the active ledger, analytics, alerts, history or cursor.
        staged = ReplaySession(
            self.replay.reader,
            mode=self.replay.mode,
            observers=(candidate,),
            checkpoint_policy=self.replay.checkpoint_policy,
        )
        try:
            staged.seek_delivery(self.replay.cursor.delivery_index)
        finally:
            staged.close()
        return candidate

    def _resume_paper(self, candidate: PaperEngine) -> None:
        assert self.replay is not None
        # A failed durable write leaves the previous projection untouched.
        candidate.publish_archive()
        self.paper = candidate
        self.replay.observers = self._observers()
        self.generation += 1
        self.status, self.error = "paused", None
        self.pending_acks.clear()
        self.pending_views.clear()
        self.completed_view = None
        self.last_presentation = None

    def _journal_query(self, request):
        assert self.journal is not None
        return self._consumer_result(
            entries=dto(
                self.journal.list(
                    source_id=None if request.all_sources else self.source_identity,
                    run_id=request.run_id,
                    limit=request.limit,
                )
            ),
            scope="all_manually_authored_sources"
            if request.all_sources
            else "current_source",
            limit=request.limit,
        )

    def _journal_put(self, request):
        from .journal import JournalEntry

        assert self.journal is not None
        entry = None if request.entry is None else decimal(request.entry, positive=True)
        stop = None if request.stop is None else decimal(request.stop, positive=True)
        if request.entry_id is not None:
            previous = self.journal.get(request.entry_id)
            if previous is None:
                raise RuntimeFailure(404, "journal entry not found")
            if (request.run_id, request.order_id) != (
                previous.run_id,
                previous.order_id,
            ):
                raise RuntimeFailure(422, "journal provenance cannot be reassigned")
            authored = replace(
                previous,
                rationale=request.rationale,
                entry=entry,
                stop=stop,
                context=request.context,
            )
        else:
            spec, now = self._book_spec(), self._command_now()
            if request.run_id is not None or request.order_id is not None:
                assert self.paper is not None
                snapshot = self.paper.snapshot(now)
                if snapshot.run is None or snapshot.run.run_id != request.run_id:
                    raise RuntimeFailure(
                        422, "journal run must be active at this source prefix"
                    )
                if request.order_id is not None:
                    order = next(
                        (
                            item
                            for item in snapshot.orders
                            if item.order_id == request.order_id
                        ),
                        None,
                    )
                    if (
                        order is None
                        or order.request.instrument_id != spec.instrument.instrument_id
                    ):
                        raise RuntimeFailure(
                            422,
                            "journal order must match this instrument and visible prefix",
                        )
            authored = JournalEntry(
                uuid4().hex,
                self.source_identity,
                self.id,
                self._delivery_index(),
                now,
                spec.instrument.instrument_id,
                spec.instrument.venue,
                request.rationale,
                entry,
                stop,
                request.context,
                request.run_id,
                request.order_id,
            )
        return self._consumer_result(entry=dto(self.journal.put(authored)))

    def _close_worker(self) -> None:
        if self.replay:
            self.replay.close()
        if isinstance(self.reader, RecordingReader):
            self.reader.close()
        self.pending_views.clear()
        if self.visuals is not None:
            self.visuals.reset()
            self.visuals = None
        if self.paper_store is not None:
            self.paper_store.close()
            self.paper_store = None
        if self.journal is not None:
            self.journal.close()
            self.journal = None
        self.context = None
        self.alerts = None
        self.paper = None

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.unsubscribe:
            self.unsubscribe()
            self.unsubscribe = None
        await self.stop_play()
        if self.live_task:
            self.live_task.cancel()
            await asyncio.gather(self.live_task, return_exceptions=True)
        if self.mode == "live" and self.runtime.registry is not None:
            await self.runtime.registry.release_client(ClientId(self.id))
        async with self.lock:
            await self.work(self._close_worker)
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.pending_acks.clear()


class TerminalRuntime:
    def __init__(
        self,
        *,
        captures: Iterable[str | Path] = (),
        registry: SubscriptionRegistry | None = None,
        enable_live: bool = False,
        max_sessions: int = 8,
        orphan_seconds: float = 60,
        state_dir: str | Path | None = None,
    ) -> None:
        if (
            max(
                int(os.environ.get("WEB_CONCURRENCY", "1")),
                int(os.environ.get("UVICORN_WORKERS", "1")),
            )
            != 1
        ):
            raise ValueError("terminal requires exactly one backend worker")
        if not 1 <= orphan_seconds <= 3600:
            raise ValueError("orphan timeout must be 1..3600 seconds")
        if not 1 <= max_sessions <= 32:
            raise ValueError("session limit must be 1..32")
        self._temporary_state: TemporaryDirectory[str] | None = None
        if state_dir is None:
            self._temporary_state = TemporaryDirectory(prefix="orderbook-state-")
            self.state_dir = Path(self._temporary_state.name)
        else:
            self.state_dir = Path(state_dir).expanduser().resolve()
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.registry = registry
        self.owns_registry = registry is None
        self.enable_live = enable_live or registry is not None
        self.max_sessions, self.orphan_seconds = max_sessions, orphan_seconds
        self.sessions: dict[str, ClientSession] = {}
        self.initializers: set[asyncio.Task[object]] = set()
        self.reservations = 0
        self.session_lock = asyncio.Lock()
        self.catalog_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="terminal-catalog"
        )
        self.visual_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="terminal-visual"
        )
        self.demo: ReplaySource | None = None
        self.demo_task: asyncio.Task[None] | None = None
        self.demo_ready = asyncio.Event()
        self.reaper: asyncio.Task[None] | None = None
        self.capture_paths = tuple(
            Path(path).expanduser().resolve() for path in captures
        )
        self.sources: dict[str, SourceStatus] = {}
        self.capture_map: dict[str, Path] = {}
        self.capture_descriptors: dict[
            str, tuple[SessionManifest, CaptureIdentity]
        ] = {}
        self.instruments: dict[str, Instrument] = {}
        self.source_instruments: dict[str, tuple[Instrument, ...]] = {}
        self._catalog_cache: tuple[object, bytes] | None = None
        self.closed = False

    def _research_query(self, request):
        from .research import ResearchStore

        store = ResearchStore(self.state_dir / "research.sqlite3")
        try:
            result = {
                "version": "1",
                "attempts": dto(store.list_attempts(request.limit)),
                "attempt": None,
                "report": None,
                "config": None,
                "source": None,
            }
            if request.attempt_id is not None:
                attempt = store.get_attempt(request.attempt_id)
                if attempt is None:
                    raise RuntimeFailure(404, "research attempt not found")
                result["attempt"] = dto(attempt)
                result["config"] = dto(store.get_config(request.attempt_id))
                result["source"] = dto(store.get_source(request.attempt_id))
                report = store.get_report(request.attempt_id)
                if report is not None:
                    presented = {
                        field.name: dto(getattr(report, field.name))
                        for field in fields(report)
                        if field.name != "trials"
                    }
                    presented["trials"] = dto(
                        report.trials[
                            request.trial_offset : request.trial_offset
                            + request.trial_limit
                        ]
                    )
                    presented["presentation"] = {
                        "trial_offset": request.trial_offset,
                        "trial_limit": request.trial_limit,
                        "trial_total": len(report.trials),
                    }
                    result["report"] = presented
            return result
        finally:
            store.close()

    def _catalog_response(self, instruments, sources, preferred_source_id):
        # One encoded catalog, owned by the catalog worker. Source snapshots
        # include readiness/reasons, not just immutable instrument metadata.
        identity = (instruments, sources, preferred_source_id)
        if self._catalog_cache is not None and self._catalog_cache[0] == identity:
            return self._catalog_cache[1]
        encoded = json.dumps(
            {
                "version": "1",
                "instruments": dto(instruments),
                "sources": sources,
                "preferred_source_id": preferred_source_id,
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self._catalog_cache = (identity, encoded)
        return encoded

    async def _catalog_work(
        self, fn: Callable[P, R], *args: P.args, **kwargs: P.kwargs
    ) -> R:
        future = asyncio.get_running_loop().run_in_executor(
            self.catalog_executor, partial(fn, *args, **kwargs)
        )
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            await future
            raise

    async def _visual_work(
        self, fn: Callable[P, R], *args: P.args, **kwargs: P.kwargs
    ) -> R:
        # Per-session lifecycle actions admit at most one bounded snapshot each.
        future = asyncio.get_running_loop().run_in_executor(
            self.visual_executor, partial(fn, *args, **kwargs)
        )
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            await future
            raise

    def source(self, source_id: str) -> SourceStatus:
        source = self.sources.get(source_id)
        if source is None:
            raise RuntimeFailure(404, "unknown managed source ID")
        return source.copy()

    async def start(self) -> None:
        metadata = await self._catalog_work(demo_source)
        self.demo_id = metadata.session_id
        self.sources[self.demo_id] = {
            "id": self.demo_id,
            "mode": "demo",
            "label": f"Synthetic 180s / {len(metadata.streams)} streams (memory, not recording)",
            "status": "loading",
            "synthetic": True,
            "recording": False,
            "duration_ns": "180000000000",
            "streams": len(metadata.streams),
            "reasons": [],
        }
        for spec in metadata.streams:
            self.instruments[str(spec.instrument.instrument_id)] = spec.instrument
        self.source_instruments[self.demo_id] = tuple(
            {
                str(spec.instrument.instrument_id): spec.instrument
                for spec in metadata.streams
            }.values()
        )
        self.demo_task = asyncio.create_task(self._load_sources())
        self.reaper = asyncio.create_task(self._reap())

    def _capture_descriptor(
        self, path: Path
    ) -> tuple[SessionManifest, CaptureIdentity]:
        with RecordingReader(path) as reader:
            return reader.manifest, capture_identity(path)

    async def _load_sources(self) -> None:
        try:
            self.demo = await self._catalog_work(MemoryReplaySource.demo)
            self.sources[self.demo_id]["status"] = "ready"
        except Exception as exc:
            self.sources[self.demo_id].update(
                {"status": "unavailable", "reasons": [str(exc)]}
            )
        finally:
            self.demo_ready.set()
        for path in self.capture_paths:
            source_id = uuid4().hex
            source: SourceStatus = {
                "id": source_id,
                "mode": "replay",
                "label": path.name,
                "status": "loading",
                "synthetic": False,
                "recording": True,
                "duration_ns": None,
                "streams": 0,
                "reasons": [],
            }
            self.sources[source_id] = source
            try:
                manifest, identity = await self._catalog_work(
                    self._capture_descriptor, path
                )
                self.capture_descriptors[source_id] = (manifest, identity)
                self.capture_map[source_id] = path
                source.update(
                    {
                        "status": "ready",
                        "streams": len(manifest.streams),
                        "synthetic": (
                            dict(manifest.config).get("fixture_version") in ("2", "3")
                            and "fixture_sha256" in dict(manifest.config)
                        ),
                    }
                )
                for spec in manifest.streams:
                    self.instruments[str(spec.instrument.instrument_id)] = (
                        spec.instrument
                    )
                self.source_instruments[source_id] = tuple(
                    {
                        str(spec.instrument.instrument_id): spec.instrument
                        for spec in manifest.streams
                    }.values()
                )
            except Exception as exc:
                source.update({"status": "unavailable", "reasons": [str(exc)]})
        if self.enable_live:
            self.sources["live"] = {
                "id": "live",
                "mode": "live",
                "label": "Public live shared collector",
                "status": "loading",
                "synthetic": False,
                "recording": False,
                "duration_ns": None,
                "streams": 0,
                "reasons": [],
            }
            try:
                if self.registry is None:
                    self.registry = SubscriptionRegistry()
                    await self.registry.catalog.discover()
                instruments = self.registry.catalog.instruments.list()
                self.source_instruments["live"] = instruments
                for instrument in instruments:
                    self.instruments[str(instrument.instrument_id)] = instrument
                if not instruments:
                    raise ValueError("no discovered live instruments")
                self.sources["live"]["status"] = "ready"
            except Exception as exc:
                self.sources["live"].update(
                    {"status": "unavailable", "reasons": [str(exc)]}
                )

    async def create(
        self,
        mode: str = "demo",
        source_id: str | None = None,
        instrument_id: str | None = None,
    ) -> ClientSession:
        source_id = source_id or (self.demo_id if mode == "demo" else "live")
        source = self.source(source_id)
        if source["mode"] != mode:
            raise RuntimeFailure(422, "source mode mismatch")
        if source["status"] == "loading" and mode == "demo":
            await self.demo_ready.wait()
            source = self.source(source_id)
        if source["status"] != "ready":
            raise RuntimeFailure(503, "; ".join(source["reasons"]) or source["status"])
        async with self.session_lock:
            if (
                self.closed
                or len(self.sessions) + self.reservations >= self.max_sessions
            ):
                raise RuntimeFailure(503, "terminal session capacity reached")
            session = ClientSession(self, mode, source_id)
            initializer = asyncio.current_task()
            if initializer is None:
                raise RuntimeError("session initialization requires an asyncio task")
            self.initializers.add(initializer)
            self.reservations += 1
        try:
            async with session.actions:
                if mode == "live":
                    if not instrument_id:
                        raise RuntimeFailure(
                            422, "live requires an exact instrument ID"
                        )
                    await self._select_live(session, instrument_id)
                else:
                    source_reader = (
                        self.demo if mode == "demo" else self.capture_map[source_id]
                    )
                    if source_reader is None:
                        raise RuntimeFailure(503, "demo source is unavailable")
                    await session.work(
                        session._initialize_replay,
                        source_reader,
                        None if mode == "demo" else self.capture_descriptors[source_id],
                    )
                    if instrument_id is None:
                        candidates = [
                            spec
                            for spec in session.streams
                            if spec.key.channel == "book"
                        ]
                        preferred = next(
                            (
                                spec
                                for spec in candidates
                                if spec.instrument.venue == "coinbase"
                                and spec.instrument.base.ticker == "BTC"
                            ),
                            candidates[0],
                        )
                        instrument_id = str(preferred.instrument.instrument_id)
                    await session.work(session.choose, instrument_id)
                async with self.session_lock:
                    if self.closed:
                        raise RuntimeFailure(503, "runtime is closing")
                    session.last_access = time.monotonic()
                    self.sessions[session.id] = session
            return session
        except BaseException:
            cleanup = asyncio.create_task(session.close())
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
            raise
        finally:
            # Loop-owned counters retire without a cancellation point.
            self.initializers.discard(initializer)
            self.reservations -= 1

    def get(self, session_id: str) -> ClientSession:
        session = self.sessions.get(session_id)
        if session is None or session.closed:
            raise RuntimeFailure(404, "unknown session")
        session.last_access = time.monotonic()
        return session

    async def _select_live(self, session: ClientSession, instrument_id: str) -> None:
        if self.registry is None:
            raise RuntimeFailure(503, "live registry is unavailable")
        registry = self.registry
        instrument = self.registry.catalog.instruments.get(instrument_id)
        # Subscribe the selected base cohort, not all products. Same base is not
        # claimed comparable: the cost engine still excludes quote/market mismatch.
        cohort = _live_cohort(instrument, self.registry.catalog.instruments.list())
        references = [
            item
            for item in self.registry.catalog.instruments.list()
            if item.venue == "hyperliquid"
            and item.market_type == "perpetual"
            and item.dex_namespace == "default"
            and item.status == "active"
            and (
                item.base.ticker in ("BTC", "ETH", "SOL", "HYPE")
                or item.base.asset_id == instrument.base.asset_id
            )
        ][:8]
        keys = frozenset(
            self.registry.catalog.canonical_key(
                SubscriptionKey.for_instrument(item, channel=channel)
            )
            for item in cohort
            for channel in ("book", "trades")
        ) | frozenset(
            self.registry.catalog.canonical_key(
                SubscriptionKey.for_instrument(item, channel=channel)
            )
            for item in references
            for channel in ("reference", "context")
        )
        session.live_keys = keys
        session.live_stream_keys = {}
        session.error = None
        session.status = "loading"

        def bootstrap(
            events: tuple[EventEnvelope, ...], books: tuple[Book, ...]
        ) -> None:
            if session.closed or session.status in ("unavailable", "error"):
                return
            selected = tuple(event for event in events if event.key in keys)
            if not selected:
                return
            session.live_stream_keys.update(
                (event.stream_id, event.key) for event in selected
            )
            session._enqueue_live(selected, books)

        # Subscribe and sample the already-completed boundary without an await.
        # This is not a pre-publication callback read or a deferred book sample.
        unsubscribe_bootstrap = registry.subscribe_processed(bootstrap)
        session.unsubscribe = unsubscribe_bootstrap
        baseline = tuple(book for book in self.registry.books() if book.key in keys)
        baseline_owners = {
            status.key: status.stream_id
            for status in self.registry.snapshot()
            if status.key in keys
        }
        session.live_stream_keys.update(
            (stream_id, key) for key, stream_id in baseline_owners.items()
        )
        bootstrap_task: asyncio.Task[None] | None = None

        async def initialize(leases: tuple[SubscriptionLease, ...]) -> None:
            nonlocal bootstrap_task
            streams = tuple(registry.describe(lease) for lease in leases)
            session.live_stream_ids = frozenset(spec.stream_id for spec in streams)
            session.live_stream_keys = {spec.stream_id: spec.key for spec in streams}
            current_owners = {spec.key: spec.stream_id for spec in streams}
            owned_baseline = tuple(
                book
                for book in baseline
                if baseline_owners.get(book.key) == current_owners.get(book.key)
            )
            if session.status in ("unavailable", "error"):
                raise RuntimeFailure(503, session.error or "live bootstrap unavailable")
            await session.work(
                session._initialize_live,
                streams,
                instrument_id,
                owned_baseline,
                registry.clock_id,
            )
            if session.status in ("unavailable", "error"):
                raise RuntimeFailure(503, session.error or "live bootstrap unavailable")
            unsubscribe_bootstrap()
            session.unsubscribe = registry.subscribe_processed(session.offer)
            bootstrap_task = asyncio.create_task(session.consume_live(bootstrap=True))

        try:
            await session.work(session._retain_live_observation, keys)
            try:
                await registry.replace(
                    ClientId(session.id),
                    keys,
                    session.generation,
                    on_commit=initialize,
                )
            finally:
                if bootstrap_task is not None:
                    bootstrap_task.cancel()
                    await asyncio.gather(bootstrap_task, return_exceptions=True)
            if session.status in ("unavailable", "error"):
                raise RuntimeFailure(503, session.error or "live bootstrap unavailable")
            remaining = session.live_queue.qsize()
            while remaining:
                batch = session._take_live_batch(
                    session.live_queue.get_nowait(), min(32, remaining)
                )
                remaining -= len(batch)
                try:
                    session.telemetry["queue_wait_ms"] = (
                        time.monotonic_ns() - batch[0][2]
                    ) / 1_000_000
                    await session.work(session._apply_live_batch, batch, True)
                finally:
                    session._release_live_batch(batch)
            if session.status == "unavailable":
                raise RuntimeFailure(503, session.error or "live bootstrap unavailable")
            session.telemetry["queue_depth"] = session.live_queue.qsize()
            session.status = "live"
            session.live_task = asyncio.create_task(session.consume_live())
        except BaseException:
            if session.unsubscribe is not None:
                session.unsubscribe()
                session.unsubscribe = None
            await self.registry.release_client(ClientId(session.id))
            raise

    async def select(self, session, generation, instrument_id):
        if str(session.generation) != generation:
            raise RuntimeFailure(409, "obsolete session generation")
        if session.closed or session.status in ("loading", "seeking"):
            raise RuntimeFailure(409, "session is not selectable")
        if session.mode == "live":
            if self.registry is None:
                raise RuntimeFailure(503, "live registry is unavailable")
            self.registry.catalog.instruments.get(instrument_id)
        elif (
            sum(
                spec.key.channel == "book"
                and str(spec.instrument.instrument_id) == instrument_id
                for spec in session.streams
            )
            != 1
        ):
            raise RuntimeFailure(
                422, "instrument has no unique book reconstruction key"
            )
        await session.stop_play()
        if session.live_task:
            session.live_task.cancel()
            await asyncio.gather(session.live_task, return_exceptions=True)
        if session.unsubscribe:
            session.unsubscribe()
            session.unsubscribe = None
        async with session.lock:
            session.generation += 1
            session.pending_acks.clear()
            session.pending_views.clear()
            if session.mode == "live":
                session.live_queue = asyncio.Queue(maxsize=256)
                session.live_bytes = 0
                session.live_inflight = 0
                session.status = "loading"
                try:
                    await self._select_live(session, instrument_id)
                except BaseException as exc:
                    session.status, session.error = "error", str(exc)
                    raise
            else:
                await session.work(session.choose, instrument_id)
                session.status = "paused"
        return await session.frame()

    async def remove(
        self, session_id: str, *, idle_before: float | None = None
    ) -> None:
        session = self.sessions.get(session_id)
        if session is None:
            return
        if idle_before is not None and (
            session.actions.locked()
            or session.lock.locked()
            or session.active_requests
            or session.active_work
        ):
            return
        async with session.actions:
            if idle_before is not None and (
                session.socket_attached
                or session.last_access >= idle_before
                or session.lock.locked()
                or session.active_requests
                or session.active_work
            ):
                return
            if self.sessions.get(session_id) is not session:
                return
            self.sessions.pop(session_id)
            cleanup = asyncio.create_task(session.close())
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise

    async def _reap(self) -> None:
        while True:
            await asyncio.sleep(min(10, self.orphan_seconds))
            cutoff = time.monotonic() - self.orphan_seconds
            for session in tuple(self.sessions.values()):
                if not session.socket_attached and session.last_access < cutoff:
                    await self.remove(session.id, idle_before=cutoff)

    async def close(self) -> None:
        self.closed = True
        initializers = tuple(self.initializers)
        for task in initializers:
            task.cancel()
        await asyncio.gather(*initializers, return_exceptions=True)
        if self.reaper:
            self.reaper.cancel()
            await asyncio.gather(self.reaper, return_exceptions=True)
        if self.demo_task:
            self.demo_task.cancel()
            await asyncio.gather(self.demo_task, return_exceptions=True)
        await asyncio.gather(
            *(self.remove(session_id) for session_id in tuple(self.sessions))
        )
        if self.registry is not None and self.owns_registry:
            await self.registry.close()
        self.catalog_executor.shutdown(wait=True, cancel_futures=True)
        self.visual_executor.shutdown(wait=True, cancel_futures=True)
        if self._temporary_state is not None:
            self._temporary_state.cleanup()
