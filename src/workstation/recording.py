"""Bounded, loss-intolerant local capture with exact lexical wire payloads.

Only offers run on the producer thread. Compression, projection, indexing and
filesystem writes run on one owned worker. See docs/RECORDING.md for limits.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from collections.abc import Hashable
from dataclasses import dataclass, fields, is_dataclass
import fcntl
from functools import lru_cache
import gzip
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import threading
import time
from typing import BinaryIO, Generic, Iterator, TypeVar, cast

from . import wire
from .book import BookCheckpoint
from .events import (
    Control,
    EventEnvelope,
    Gap,
    RawFrame,
    StreamId,
    SubscriptionKey,
    is_local_control,
)
from .instruments import Instrument

STORAGE_SCHEMA_VERSION = 2
_MANIFEST_LIMIT = 16 * 1024 * 1024
_CONFIG_KEYS = frozenset({"poll_interval", "dedup_capacity", "max_levels", "dex"})
_TERMINAL_FAILURE_PREFIXES = (
    "writer_failed",
    "queue_overflow",
    "finalize_timeout",
    "finalization_failed",
)


class RecordingError(RuntimeError):
    """A required capture item could not be retained, or capture is untrusted."""


def _metadata_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError(f"{name} must be nonempty text of at most 4096 characters")


def _pairs(value: tuple[tuple[str, str], ...]) -> None:
    if not isinstance(value, tuple) or len(value) > 256:
        raise ValueError("configuration must be bounded immutable pairs")
    seen = set()
    for item in value:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or any(not isinstance(v, str) or len(v) > 4096 for v in item)
            or not item[0]
            or item[0] in seen
        ):
            raise ValueError("invalid configuration pair")
        seen.add(item[0])


@dataclass(frozen=True)
class StreamSpec:
    stream_id: StreamId
    instrument: Instrument
    key: SubscriptionKey
    adapter_revision: str
    config_hash: str
    adapter_config: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "stream_id", StreamId(self.stream_id))
        if (
            self.key.instrument_id != self.instrument.instrument_id
            or self.key.venue != self.instrument.venue
            or self.key.api_family != self.instrument.api_family
        ):
            raise ValueError("stream instrument/key mismatch")
        if any(
            not isinstance(v, str) or not v or len(v) > 4096
            for v in (self.adapter_revision, self.config_hash)
        ):
            raise ValueError("missing adapter/config revision")
        _pairs(self.adapter_config)
        if any(k not in _CONFIG_KEYS for k, _ in self.adapter_config):
            raise ValueError("adapter configuration is not public/allowlisted")


@dataclass(frozen=True)
class RecordedBatch:
    delivery_index: int
    events: tuple[EventEnvelope, ...]


@dataclass(frozen=True)
class RecordedCheckpoint:
    delivery_index: int
    checkpoint: BookCheckpoint
    observer_state: bytes = b""


@dataclass(frozen=True)
class FileRecord:
    path: str
    kind: str
    sha256: str
    size_bytes: int
    row_count: int
    first_ordinal: int | None = None
    last_ordinal: int | None = None
    first_delivery: int | None = None
    last_delivery: int | None = None
    stream_id: str | None = None


_QueueValue = RawFrame | RecordedBatch | RecordedCheckpoint | StreamSpec


@dataclass
class _RawFileStats:
    path: str
    row_count: int
    first_ordinal: int
    last_ordinal: int


@dataclass
class _FileStats:
    row_count: int = 0
    first_ordinal: int | None = None
    last_ordinal: int | None = None
    first_delivery: int | None = None
    last_delivery: int | None = None

    def add(self, ordinal: int | None, delivery: int | None) -> None:
        self.row_count += 1
        if ordinal is not None:
            self.first_ordinal = (
                ordinal
                if self.first_ordinal is None
                else min(self.first_ordinal, ordinal)
            )
            self.last_ordinal = (
                ordinal
                if self.last_ordinal is None
                else max(self.last_ordinal, ordinal)
            )
        if delivery is not None:
            self.first_delivery = (
                delivery
                if self.first_delivery is None
                else min(self.first_delivery, delivery)
            )
            self.last_delivery = (
                delivery
                if self.last_delivery is None
                else max(self.last_delivery, delivery)
            )


@dataclass(frozen=True)
class RecorderLimits:
    # Raw and normalized callbacks occupy separate slots. The retained terminal
    # burst exceeds 2,400 items in two seconds; native checkpoint service and
    # live producer contention require finite headroom, not a larger byte cap.
    queue_count: int = 4096
    queue_bytes: int = 128 * 1024 * 1024
    raw_rotation_bytes: int = 16 * 1024 * 1024
    parquet_batch_count: int = 128
    parquet_batch_bytes: int = 16 * 1024 * 1024
    max_row_bytes: int = 256 * 1024 * 1024
    max_files: int = 100_000
    max_streams: int = 1024
    min_free_bytes: int = 64 * 1024 * 1024
    managed_budget_bytes: int = 10 * 1024 * 1024 * 1024
    retention_max_sessions: int = 100
    finalize_timeout: float = 30.0
    checkpoint_interval_batches: int = 1000
    max_query_rows: int = 10_000
    parquet_buffer_count: int = 4096
    reader_cache_bytes: int = 32 * 1024 * 1024
    reader_cache_count: int = 8192
    checkpoint_group_count: int = 64
    checkpoint_group_bytes: int = 16 * 1024 * 1024

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value < 0
            ):
                raise ValueError("invalid recording limit")
            if field.name != "finalize_timeout" and type(value) is not int:
                raise ValueError("count/byte limits must be integers")
            if field.name == "finalize_timeout" and not 0 < value <= 3600:
                raise ValueError("invalid finalize deadline")
            if field.name != "min_free_bytes" and value == 0:
                raise ValueError("recording limits must be positive")
        if self.max_row_bytes > 256 * 1024 * 1024:
            raise ValueError("row bound exceeds wire decoder bound")
        if (
            self.max_files > 100_000
            or self.max_streams > 1024
            or self.max_query_rows > 10_000
            or self.parquet_batch_count > 4096
            or self.parquet_buffer_count > 65_536
            or self.parquet_batch_bytes > 256 * 1024 * 1024
            or self.reader_cache_bytes > 256 * 1024 * 1024
            or self.reader_cache_count > 65_536
            or self.checkpoint_group_count > 4096
        ):
            raise ValueError("reader safety limit exceeded")


def _parquet_group_bound(limits: RecorderLimits, row_count: int) -> int:
    # A single atomic row may exceed the normal batching target. Multi-row
    # groups are bounded by the aggregate payload target, not one row's limit.
    # Query columns duplicate a subset of payload metadata; reserve their bytes
    # plus a bounded encoding/page allowance before asking Arrow to allocate.
    payload_bound = (
        limits.max_row_bytes if row_count == 1 else limits.parquet_batch_bytes
    )
    return 2 * payload_bound + 1024 * 1024


_CacheValue = TypeVar("_CacheValue")


class _ReaderCache(Generic[_CacheValue]):
    """A small count/byte-bounded LRU of immutable decoded capture evidence."""

    def __init__(self, limits: RecorderLimits):
        self.entries: OrderedDict[tuple[str, int], tuple[_CacheValue, int]] = (
            OrderedDict()
        )
        self.capacity = limits.reader_cache_count
        self.byte_capacity = limits.reader_cache_bytes
        self.used_bytes = 0

    def get(self, key: tuple[str, int]) -> _CacheValue | None:
        entry = self.entries.get(key)
        if entry is None:
            return None
        self.entries.move_to_end(key)
        return entry[0]

    def put(self, key: tuple[str, int], value: _CacheValue, size: int) -> None:
        size += _size(key) + 256  # Key, entry tuple and ordered-map bookkeeping.
        if size > self.byte_capacity:
            return
        previous = self.entries.pop(key, None)
        if previous is not None:
            self.used_bytes -= previous[1]
        while self.entries and (
            len(self.entries) >= self.capacity
            or self.used_bytes + size > self.byte_capacity
        ):
            _, (_, removed_size) = self.entries.popitem(last=False)
            self.used_bytes -= removed_size
        self.entries[key] = (value, size)
        self.used_bytes += size

    def clear(self) -> None:
        self.entries.clear()
        self.used_bytes = 0


@dataclass(frozen=True)
class RecorderHealth:
    state: str
    queue_count: int
    queue_bytes: int
    high_water_count: int
    high_water_bytes: int
    incomplete_reasons: tuple[str, ...]
    written_bytes: int

    @property
    def terminal_failure(self) -> bool:
        """Whether the required sink is unusable, rather than merely incomplete."""
        return any(
            reason.startswith(_TERMINAL_FAILURE_PREFIXES)
            for reason in self.incomplete_reasons
        )


@dataclass(frozen=True)
class SessionManifest:
    schema_version: int
    session_id: str
    clock_id: str
    streams: tuple[StreamSpec, ...]
    config: tuple[tuple[str, str], ...]
    code_revision: str
    provenance: str
    license: str
    created_wall_ns: int
    finalized_wall_ns: int
    complete: bool
    incomplete_reasons: tuple[str, ...]
    files: tuple[FileRecord, ...]
    raw_count: int
    event_count: int
    batch_count: int
    checkpoint_count: int
    limits: RecorderLimits
    merge_order: str = "normalized_callback_delivery_index"
    capture_scope: str = "declared_streams_only"
    wire_schema_version: int = wire.WIRE_SCHEMA_VERSION
    gaps: tuple[EventEnvelope, ...] = ()

    def to_dict(self) -> dict:
        return _encode(self)


_MODELS = {
    cls.__name__: cls
    for cls in (StreamSpec, FileRecord, RecorderLimits, SessionManifest)
}


def _encode(value):
    if type(value).__name__ in _MODELS and is_dataclass(value):
        return {
            "$type": type(value).__name__,
            **{f.name: _encode(getattr(value, f.name)) for f in fields(value)},
        }
    if isinstance(value, tuple):
        return [_encode(v) for v in value]
    if isinstance(value, float):
        return str(value)
    return wire.to_wire(value, canonical=False)


def _decode_manifest(payload: bytes) -> SessionManifest:
    from typing import get_type_hints, get_args, get_origin

    hints_by_name = {name: get_type_hints(kind) for name, kind in _MODELS.items()}

    def decode(value, kind):
        if get_origin(kind) is tuple:
            args = get_args(kind)
            if not isinstance(value, list):
                raise ValueError("expected immutable array")
            if len(args) == 2 and args[1] is Ellipsis:
                return tuple(decode(v, args[0]) for v in value)
            if len(args) != len(value):
                raise ValueError("tuple width mismatch")
            return tuple(decode(v, k) for v, k in zip(value, args, strict=True))
        if isinstance(kind, type) and kind.__name__ in _MODELS:
            if (
                not isinstance(value, dict)
                or value.get("$type") != kind.__name__
                or set(value) != {f.name for f in fields(kind)} | {"$type"}
            ):
                raise ValueError("unknown/missing manifest fields")
            hints = hints_by_name[kind.__name__]
            return kind(
                **{name: decode(value[name], typ) for name, typ in hints.items()}
            )
        if kind is float:
            result = float(value)
            if not 0 < result <= 3600:
                raise ValueError("invalid finalize deadline")
            return result
        return wire.from_wire(value, kind)

    if len(payload) > _MANIFEST_LIMIT:
        raise RecordingError("oversized manifest")
    try:
        result = decode(json.loads(payload), SessionManifest)
        if (
            result.schema_version != STORAGE_SCHEMA_VERSION
            or result.wire_schema_version != wire.WIRE_SCHEMA_VERSION
            or result.merge_order != "normalized_callback_delivery_index"
            or result.capture_scope != "declared_streams_only"
        ):
            raise ValueError("unsupported capture schema/order/scope")
        for name in ("code_revision", "provenance", "license", "clock_id"):
            _metadata_text(getattr(result, name), name)
        return result
    except (ValueError, TypeError, KeyError) as exc:
        raise RecordingError("invalid manifest") from exc


def _json(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _safe(base: Path, relative: str) -> Path:
    path = Path(relative)
    if (
        path.is_absolute()
        or not path.parts
        or any(p in ("..", ".") for p in path.parts)
    ):
        raise RecordingError("unsafe capture path")
    # All callers supply an already-resolved managed root. With absolute paths
    # and traversal rejected, checking each child component is sufficient; a
    # second realpath walk per record only repeats the same filesystem checks.
    result = base
    for component in path.parts:
        result = result / component
        if result.is_symlink():
            raise RecordingError("symlink capture path")
    return result


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic(path: Path, payload: bytes) -> None:
    partial = path.with_name(path.name + ".partial")
    with partial.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)
    _sync_dir(path.parent)


def _sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@lru_cache(maxsize=128)
def _size_fields(kind: type[object]) -> tuple[str, ...]:
    # Cache schema descriptors, never instances or their resident byte totals.
    return tuple(field.name for field in fields(kind)) if is_dataclass(kind) else ()


def _size(value: object) -> int:
    """Conservative resident-byte accounting; no JSON, compression or I/O."""
    size = sys.getsizeof(value)
    if isinstance(value, (tuple, frozenset)):
        for item in value:
            size += _size(item)
    else:
        # The cache keys are class objects, not potentially unhashable instances.
        for name in _size_fields(cast(Hashable, type(value))):
            size += _size(getattr(value, name))
    return size


def _managed_bytes(root: Path) -> int:
    """Count actual managed file bytes without following symlink boundaries."""
    total = 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not Path(directory, d).is_symlink()]
        for name in names:
            path = Path(directory, name)
            if not path.is_symlink():
                total += path.stat().st_size
    return total


def _catalog(root: Path) -> sqlite3.Connection:
    path = _safe(root, "catalog.sqlite3")
    db = sqlite3.connect(path, timeout=10)
    db.execute(
        "CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, "
        "path TEXT NOT NULL, state TEXT NOT NULL, created_ns TEXT NOT NULL, "
        "bytes INTEGER NOT NULL DEFAULT 0)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    db.commit()
    return db


def _reconcile_retention(root: Path, db: sqlite3.Connection) -> set[str]:
    """Finish durable deletion intents and discard missing finalized rows."""
    locked: set[str] = set()
    rows = db.execute(
        "SELECT session_id,path,state FROM sessions "
        "WHERE state IN ('complete','incomplete','deleting')"
    ).fetchall()
    for session, relative, state in rows:
        if relative != session:
            raise RecordingError("unsafe retention catalog")
        path = _safe(root, relative)
        if not path.exists():
            db.execute("DELETE FROM sessions WHERE session_id=?", (session,))
            db.commit()
            continue
        if state != "deleting":
            continue
        if not path.is_dir():
            raise RecordingError("retention target is not a managed directory")
        handle: BinaryIO | None = None
        try:
            active = _safe(path, ".active")
            if active.exists():
                handle = active.open("r+b")
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    locked.add(relative)
                    continue
            shutil.rmtree(path)
            _sync_dir(root)
            db.execute("DELETE FROM sessions WHERE session_id=?", (session,))
            db.commit()
        finally:
            if handle is not None:
                handle.close()
    return locked


def recover_recordings(root: str | Path) -> tuple[Path, ...]:
    """Quarantine unlocked interrupted captures; never salvage them as complete."""
    root = Path(root).expanduser().resolve()
    if not root.exists():
        return ()
    recovered = []
    with _catalog(root) as db:
        pending_deletions = _reconcile_retention(root, db)
        for child in root.iterdir():
            if (
                child.is_symlink()
                or not child.is_dir()
                or child.name.startswith("quarantine-")
                or child.name in pending_deletions
            ):
                continue
            active = child / ".active"
            if not active.exists() or active.is_symlink():
                continue
            with active.open("r+b") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                manifest = child / "manifest.json"
                if manifest.exists():
                    fcntl.flock(handle, fcntl.LOCK_UN)
                    try:
                        with RecordingReader(child, allow_incomplete=True) as reader:
                            finalized = reader.manifest
                            if finalized.session_id != child.name:
                                raise RecordingError("managed session path mismatch")
                            session_bytes = _managed_bytes(child)
                    except (RecordingError, OSError, sqlite3.Error):
                        try:
                            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            continue
                    else:
                        db.execute(
                            "INSERT INTO sessions VALUES (?,?,?,?,?) "
                            "ON CONFLICT(session_id) DO UPDATE SET "
                            "path=excluded.path,state=excluded.state,"
                            "created_ns=excluded.created_ns,bytes=excluded.bytes",
                            (
                                finalized.session_id,
                                child.name,
                                "complete" if finalized.complete else "incomplete",
                                str(finalized.created_wall_ns),
                                session_bytes,
                            ),
                        )
                        continue
                destination = root / f"quarantine-{child.name}-{time.time_ns()}"
                os.replace(child, destination)
                db.execute(
                    "UPDATE sessions SET state='quarantined', path=? WHERE session_id=?",
                    (destination.name, child.name),
                )
                recovered.append(destination)
    return tuple(recovered)


class Recorder:
    def __init__(
        self,
        root: str | Path,
        *,
        session_id: str,
        clock_id: str,
        streams: tuple[StreamSpec, ...] = (),
        config: tuple[tuple[str, str], ...] = (),
        code_revision: str = "unknown",
        provenance: str = "public market data",
        license: str = "unspecified",
        limits: RecorderLimits = RecorderLimits(),
    ):
        if not isinstance(session_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", session_id
        ):
            raise ValueError("session id is not a safe path component")
        for name, value in (
            ("clock_id", clock_id),
            ("code_revision", code_revision),
            ("provenance", provenance),
            ("license", license),
        ):
            _metadata_text(value, name)
        _pairs(config)
        if (
            not isinstance(streams, tuple)
            or len(streams) > limits.max_streams
            or any(not isinstance(spec, StreamSpec) for spec in streams)
            or _size(streams) > _MANIFEST_LIMIT
        ):
            raise ValueError("invalid/oversized initial stream metadata")
        self.created_wall_ns = time.time_ns()
        initial_payload = _json(
            {
                "schema_version": str(STORAGE_SCHEMA_VERSION),
                "session_id": session_id,
                "clock_id": clock_id,
                "streams": [_encode(s) for s in streams],
                "config": _encode(config),
                "code_revision": code_revision,
                "provenance": provenance,
                "license": license,
                "created_wall_ns": str(self.created_wall_ns),
            }
        )
        if len(initial_payload) > _MANIFEST_LIMIT:
            raise ValueError("oversized initial session metadata")
        # Includes the initial SQLite pages, root catalog row/journal, and metadata.
        initialization_bytes = len(initial_payload) + 128 * 1024
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        recover_recordings(self.root)
        self.session_id, self.clock_id, self.limits = session_id, clock_id, limits
        self.config, self.code_revision = config, code_revision
        self.provenance, self.license = provenance, license
        self._condition = threading.Condition()
        self._queue: deque[tuple[str, _QueueValue, int]] = deque()
        self._queue_bytes = self._high_count = self._high_bytes = 0
        self._inflight_count = self._inflight_bytes = 0
        self._reasons: list[str] = []
        self._closing = self._done = self._abandon = False
        self._delivery = 0
        self._gaps: list[EventEnvelope] = []
        self._last_disk_check = float("-inf")
        self._disk_total = 0
        self._disk_free = 0
        self._finalize_lock = asyncio.Lock()
        self._streams: dict[str, StreamSpec] = {}
        self._files: list[FileRecord] = []
        self._written_bytes = self._raw_count = self._event_count = (
            self._batch_count
        ) = 0
        self._checkpoint_count = 0
        self._manifest: SessionManifest | None = None
        self._pending: dict[StreamId, list[tuple[RecordedBatch, bytes]]] = {}
        self._pending_count = 0
        self._aux_paths: dict[str, str] = {}
        self._pending_bytes = 0
        self._raw_handle: BinaryIO | None = None
        self._raw_info: _RawFileStats | None = None
        self._connection: sqlite3.Connection | None = None
        self._raw_sequence = self._parquet_sequence = self._checkpoint_sequence = 0
        self._enforce_retention(initialization_bytes)
        self._check_disk()
        self._reserve_disk(initialization_bytes)
        self.path = _safe(self.root, session_id)
        self.path.mkdir()
        self._active = (self.path / ".active").open("x+b")
        fcntl.flock(self._active, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            for subdir in ("raw", "normalized", "checkpoints"):
                (self.path / subdir).mkdir()
            self._initialize_index()
            for spec in streams:
                self._register(spec)
            _atomic(self.path / "session.json", initial_payload)
            with _catalog(self.root) as db:
                db.execute(
                    "INSERT INTO sessions VALUES (?,?,?,?,0)",
                    (session_id, session_id, "active", str(self.created_wall_ns)),
                )
            # Imports and dependency failures surface before any feed lease starts.
            import pyarrow as pa
            import duckdb  # noqa: F401

            # Arrow's first Python-to-table conversion lazily loads conversion
            # dependencies. Prepare that path before admission, not during the
            # first loss-intolerant flush while callbacks are arriving.
            pa.Table.from_pylist([], schema=_parquet_schema())
            from .projection import RecordingProjection

            self._projection = RecordingProjection(
                streams,
                dedup_capacity=int(dict(config).get("dedup_capacity", "50000")),
                poll_interval=float(dict(config).get("poll_interval", "2")),
            )
            self._check_disk(force=True)
        except BaseException:
            self._active.close()
            raise
        self._thread = threading.Thread(
            target=self._run, name=f"recorder-{session_id}", daemon=True
        )
        self._thread.start()

    @property
    def _db(self) -> sqlite3.Connection:
        connection = self._connection
        if connection is None:
            raise RecordingError("writer catalog is not open")
        return connection

    def _initialize_index(self):
        with sqlite3.connect(self.path / "index.sqlite3") as db:
            db.executescript("""
                PRAGMA journal_mode=DELETE;
                CREATE TABLE metadata(key TEXT PRIMARY KEY, value BLOB NOT NULL);
                CREATE TABLE raw(stream TEXT, ordinal TEXT, path TEXT, offset INTEGER,
                    length INTEGER, payload_hash TEXT, PRIMARY KEY(stream,ordinal), UNIQUE(ordinal));
                CREATE TABLE batches(delivery INTEGER PRIMARY KEY, stream TEXT, ordinal TEXT,
                    first_event INTEGER, last_event INTEGER, path TEXT,
                    row_group INTEGER, row_offset INTEGER,
                    event_count INTEGER, receive_wall_ns TEXT, receive_mono_ns TEXT);
                CREATE INDEX batch_ordinal ON batches(ordinal,delivery);
                CREATE TABLE checkpoints(delivery INTEGER, stream TEXT, path TEXT,
                    row_offset INTEGER, aux_path TEXT,
                    PRIMARY KEY(delivery,stream));
                CREATE TABLE files(path TEXT PRIMARY KEY, metadata BLOB NOT NULL);
            """)
            db.execute(
                "INSERT INTO metadata VALUES ('session_id',?)", (self.session_id,)
            )
            db.execute("INSERT INTO metadata VALUES ('clock_id',?)", (self.clock_id,))

    @property
    def last_delivery_index(self) -> int:
        with self._condition:
            return self._delivery

    @property
    def health(self) -> RecorderHealth:
        with self._condition:
            state = (
                "incomplete"
                if self._reasons
                else ("finalized" if self._done else "recording")
            )
            return RecorderHealth(
                state,
                len(self._queue) + self._inflight_count,
                self._queue_bytes + self._inflight_bytes,
                self._high_count,
                self._high_bytes,
                tuple(self._reasons),
                self._written_bytes,
            )

    def mark_incomplete(self, reason: str) -> None:
        reason = str(reason)[:256] or "capture_incomplete"
        with self._condition:
            if reason not in self._reasons and len(self._reasons) < 64:
                self._reasons.append(reason)
            elif reason.startswith(_TERMINAL_FAILURE_PREFIXES) and not any(
                r.startswith(_TERMINAL_FAILURE_PREFIXES) for r in self._reasons
            ):
                # A full diagnostic list must not conceal a dead required sink.
                self._reasons[-1] = reason

    def _register(self, spec: StreamSpec):
        if not isinstance(spec, StreamSpec):
            raise TypeError("expected StreamSpec")
        previous = self._streams.get(spec.stream_id)
        if previous is not None and previous != spec:
            raise RecordingError("stream metadata cannot be replaced")
        if previous is None and len(self._streams) >= self.limits.max_streams:
            raise RecordingError("stream capacity exceeded")
        self._streams[spec.stream_id] = spec

    def register_stream(self, spec: StreamSpec) -> None:
        with self._condition:
            if self._streams.get(spec.stream_id) == spec:
                return
            self._register(spec)
            self._offer("stream", spec, _size(spec))

    def _identity(self, value):
        if value.session_id != self.session_id or value.clock_id != self.clock_id:
            raise RecordingError("session/clock provenance mismatch")
        if value.stream_id not in self._streams:
            raise RecordingError("stream must be registered before data")

    def append_raw(self, frame: RawFrame) -> None:
        self._identity(frame)
        self._offer("raw", frame, _size(frame))

    def append_events(self, events: tuple[EventEnvelope, ...]) -> None:
        if not isinstance(events, tuple) or not events:
            raise RecordingError("expected nonempty immutable source batch")
        first = events[0]
        self._identity(first)
        spec = self._streams[first.stream_id]
        for index, event in enumerate(events):
            self._identity(event)
            if (
                event.stream_id != first.stream_id
                or event.ingest_ordinal != first.ingest_ordinal
                or event.event_index != index
                or event.key != spec.key
                or event.instrument_metadata_version != spec.instrument.metadata_version
            ):
                raise RecordingError("inconsistent source-message batch")
        size = _size(events)
        with self._condition:
            batch = RecordedBatch(self._delivery + 1, events)
            self._offer("events", batch, size)
            self._delivery += 1
        for event in events:
            if isinstance(event.payload, Gap) and event.payload.capture_lost:
                self.mark_incomplete("source_capture_lost:" + event.payload.reason_code)

    def append_checkpoint(
        self,
        checkpoint: BookCheckpoint,
        *,
        delivery_index: int | None = None,
        observer_state: bytes = b"",
    ) -> None:
        if (
            checkpoint.stream_id not in self._streams
            or checkpoint.session_id != self.session_id
        ):
            raise RecordingError("checkpoint provenance mismatch")
        with self._condition:
            position = self._delivery if delivery_index is None else delivery_index
            if not 0 <= position <= self._delivery:
                raise RecordingError("checkpoint delivery position outside capture")
            record = RecordedCheckpoint(position, checkpoint, observer_state)
            self._offer("checkpoint", record, _size(record))

    def _offer(self, kind: str, value: _QueueValue, size: int) -> None:
        with self._condition:
            if self._closing or self._done:
                raise RecordingError("recorder is closed")
            # Source gaps are truthful capture status, not a dead writer.
            if any(r.startswith(_TERMINAL_FAILURE_PREFIXES) for r in self._reasons):
                raise RecordingError("required recorder sink failed")
            count = len(self._queue) + self._inflight_count + 1
            total = self._queue_bytes + self._inflight_bytes + size
            if count > self.limits.queue_count or total > self.limits.queue_bytes:
                self.mark_incomplete("queue_overflow")
                raise RecordingError("recorder queue count/byte capacity exceeded")
            self._queue.append((kind, value, size))
            self._queue_bytes += size
            self._high_count, self._high_bytes = (
                max(self._high_count, count),
                max(self._high_bytes, total),
            )
            self._condition.notify()

    def _check_disk(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_disk_check < 1:
            return
        self._disk_free = shutil.disk_usage(self.root).free
        if self._disk_free < self.limits.min_free_bytes:
            raise RecordingError("low_disk")
        total = _managed_bytes(self.root)
        self._disk_total = total
        self._last_disk_check = now
        if total >= self.limits.managed_budget_bytes:
            raise RecordingError("managed_disk_budget_exceeded")

    def _reserve_disk(self, amount):
        self._check_disk()
        if self._disk_total + amount >= self.limits.managed_budget_bytes:
            raise RecordingError("managed_disk_budget_exceeded")
        if self._disk_free - amount < self.limits.min_free_bytes:
            raise RecordingError("low_disk")
        self._disk_total += amount
        self._disk_free -= amount

    def _enforce_retention(self, required_bytes: int = 0) -> None:
        with _catalog(self.root) as db:
            _reconcile_retention(self.root, db)
            rows = db.execute(
                "SELECT session_id,path FROM sessions WHERE state IN "
                "('complete','incomplete') ORDER BY length(created_ns),created_ns"
            ).fetchall()
            total = _managed_bytes(self.root)
            remaining = len(rows)
            for session, relative in rows:
                if (
                    remaining < self.limits.retention_max_sessions
                    and total + required_bytes < self.limits.managed_budget_bytes
                ):
                    break
                path = _safe(self.root, relative)
                active = _safe(path, ".active")
                if not active.exists():
                    continue
                with active.open("r+b") as handle:
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue
                    # Catalog path must identify exactly this managed session.
                    if path.name != session or not (path / "manifest.json").is_file():
                        raise RecordingError("unsafe retention catalog")
                    db.execute(
                        "UPDATE sessions SET state='deleting' WHERE session_id=?",
                        (session,),
                    )
                    db.commit()
                    shutil.rmtree(path)
                    _sync_dir(self.root)
                    db.execute("DELETE FROM sessions WHERE session_id=?", (session,))
                    db.commit()
                total = _managed_bytes(self.root)
                remaining -= 1

    def _add_file(self, path, kind, count, ordinals=(), deliveries=(), stream=None):
        if len(self._files) >= self.limits.max_files - 1:
            raise RecordingError("capture file capacity exceeded")
        record = FileRecord(
            str(path.relative_to(self.path)),
            kind,
            _hash(path),
            path.stat().st_size,
            count,
            min(ordinals) if ordinals else None,
            max(ordinals) if ordinals else None,
            min(deliveries) if deliveries else None,
            max(deliveries) if deliveries else None,
            stream,
        )
        self._files.append(record)
        self._written_bytes += record.size_bytes
        self._db.execute(
            "INSERT INTO files VALUES (?,?)", (record.path, _json(_encode(record)))
        )
        return record

    def _write_raw(self, frame: RawFrame) -> None:
        payload = wire.dumps(frame, canonical=False) + b"\n"
        if len(payload) > self.limits.max_row_bytes:
            raise RecordingError("oversized raw row")
        compressed = gzip.compress(payload, compresslevel=3, mtime=0)
        self._reserve_disk(len(compressed) + 512)
        if (
            self._raw_handle is not None
            and self._raw_handle.tell() + len(compressed)
            > self.limits.raw_rotation_bytes
        ):
            self._close_raw()
        if self._raw_handle is None:
            self._raw_sequence += 1
            relative = f"raw/{self._raw_sequence:08d}.jsonl.gz"
            self._raw_handle = (self.path / (relative + ".partial")).open("xb")
            self._raw_info = _RawFileStats(
                relative, 0, frame.ingest_ordinal, frame.ingest_ordinal
            )
        info, handle = self._raw_info, self._raw_handle
        if info is None or handle is None:
            raise RecordingError("raw writer state is not initialized")
        relative = info.path
        offset = handle.tell()
        handle.write(compressed)
        self._db.execute(
            "INSERT INTO raw VALUES (?,?,?,?,?,?)",
            (
                frame.stream_id,
                str(frame.ingest_ordinal),
                relative,
                offset,
                len(compressed),
                frame.payload_sha256,
            ),
        )
        info.row_count += 1
        info.first_ordinal = min(info.first_ordinal, frame.ingest_ordinal)
        info.last_ordinal = max(info.last_ordinal, frame.ingest_ordinal)
        self._raw_count += 1

    def _close_raw(self):
        if self._raw_handle is None:
            return
        self._raw_handle.flush()
        os.fsync(self._raw_handle.fileno())
        self._raw_handle.close()
        info = self._raw_info
        if info is None:
            raise RecordingError("raw writer statistics are missing")
        path = self.path / info.path
        os.replace(str(path) + ".partial", path)
        _sync_dir(path.parent)
        self._add_file(
            path, "raw", info.row_count, (info.first_ordinal, info.last_ordinal)
        )
        self._raw_handle = None
        self._raw_info = None

    def _write_events(self, batch: RecordedBatch) -> None:
        first = batch.events[0]
        raw_exists = (
            self._db.execute(
                "SELECT 1 FROM raw WHERE stream=? AND ordinal=?",
                (first.stream_id, str(first.ingest_ordinal)),
            ).fetchone()
            is not None
        )
        if not raw_exists and not all(
            is_local_control(event) for event in batch.events
        ):
            self.mark_incomplete("missing_required_raw_evidence")
        elif raw_exists and any(is_local_control(event) for event in batch.events):
            self.mark_incomplete("conflicting_local_raw_origin")
        payload = wire.dumps(batch.events, canonical=False)
        if len(payload) > self.limits.max_row_bytes:
            raise RecordingError("oversized normalized row")
        if (
            self._pending
            and self._pending_bytes + len(payload) > self.limits.parquet_batch_bytes
        ):
            self._flush_events()
        self._pending.setdefault(first.stream_id, []).append((batch, payload))
        self._pending_count += 1
        self._pending_bytes += len(payload)
        if any(
            isinstance(event.payload, Control) and event.payload.kind == "source_change"
            for event in batch.events
        ):
            # A source reset may leave every reducer pristine, with no native
            # event identity to index a post-reset checkpoint. Retain the complete
            # preceding state; replay then applies the captured reset itself.
            self._save_projection(batch.delivery_index - 1)
        self._projection.apply_batch(batch)
        for event in batch.events:
            if isinstance(event.payload, Gap):
                if len(self._gaps) >= 1024:
                    raise RecordingError("manifest gap capacity exceeded")
                self._gaps.append(event)
        boundary = any(
            isinstance(e.payload, Gap)
            or isinstance(e.payload, Control)
            and e.payload.kind in ("source_change", "reset", "disconnect")
            for e in batch.events
        )
        if (
            boundary
            or batch.delivery_index % self.limits.checkpoint_interval_batches == 0
        ):
            self._save_projection(batch.delivery_index)
        if len(self._pending[first.stream_id]) >= self.limits.parquet_batch_count:
            self._flush_events(first.stream_id)
        if (
            self._pending_count >= self.limits.parquet_buffer_count
            or self._pending_bytes >= self.limits.parquet_batch_bytes
        ):
            self._flush_events()

    def _flush_events(self, stream_id: StreamId | None = None) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        streams = tuple(self._pending) if stream_id is None else (stream_id,)
        for stream in streams:
            items = self._pending.pop(stream)
            self._parquet_sequence += 1
            partition = (
                self.path / "normalized" / sha256(stream.encode()).hexdigest()[:24]
            )
            partition.mkdir(exist_ok=True)
            path = partition / f"{self._parquet_sequence:08d}.parquet"
            rows = []
            for batch, payload in items:
                event = batch.events[0]
                rows.append(
                    {
                        "delivery_index": batch.delivery_index,
                        "stream_id": str(stream),
                        "instrument_id": str(event.key.instrument_id),
                        "venue": event.key.venue,
                        "ingest_ordinal": str(event.ingest_ordinal),
                        "receive_wall_ns": str(event.receive_wall_ns),
                        "receive_mono_ns": str(event.receive_mono_ns),
                        "source_mode": event.source_mode,
                        "segment_id": str(event.segment_id),
                        "payload_kind": ",".join(
                            sorted({type(e.payload).__name__ for e in batch.events})
                        ),
                        "event_count": len(batch.events),
                        "payload": payload,
                    }
                )
            table = pa.Table.from_pylist(rows, schema=_parquet_schema())
            if (
                table.nbytes
                > _parquet_group_bound(self.limits, len(items)) - 1024 * 1024
            ):
                raise RecordingError("oversized parquet row group")
            partial = Path(str(path) + ".partial")
            self._reserve_disk(sum(len(payload) for _, payload in items) + 65536)
            pq.write_table(
                table,
                partial,
                compression="zstd",
                row_group_size=self.limits.parquet_batch_count,
                use_dictionary=["stream_id", "venue", "source_mode", "payload_kind"],
            )
            with partial.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(partial, path)
            _sync_dir(path.parent)
            relative = str(path.relative_to(self.path))
            for row_number, (batch, _) in enumerate(items):
                event = batch.events[0]
                self._db.execute(
                    "INSERT INTO batches VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        batch.delivery_index,
                        str(stream),
                        str(event.ingest_ordinal),
                        event.event_index,
                        batch.events[-1].event_index,
                        relative,
                        row_number // self.limits.parquet_batch_count,
                        row_number % self.limits.parquet_batch_count,
                        len(batch.events),
                        str(event.receive_wall_ns),
                        str(event.receive_mono_ns),
                    ),
                )
                self._event_count += len(batch.events)
                self._batch_count += 1
            self._add_file(
                path,
                "normalized",
                len(items),
                tuple(b.events[0].ingest_ordinal for b, _ in items),
                tuple(b.delivery_index for b, _ in items),
                str(stream),
            )
            self._pending_count -= len(items)
            self._pending_bytes -= sum(len(payload) for _, payload in items)
        self._db.commit()

    def _save_projection(self, delivery):
        checkpoints = self._projection.checkpoints()
        if checkpoints:
            state = self._projection.checkpoint_state()
            self._write_checkpoint_group(
                tuple(
                    RecordedCheckpoint(delivery, checkpoint, state)
                    for checkpoint in checkpoints
                )
            )

    def _write_checkpoint_aux(self, state: bytes) -> str:
        digest = sha256(state).hexdigest()
        existing = self._aux_paths.get(digest)
        if existing is not None:
            return existing
        payload = wire.dumps(state, canonical=False)
        if len(payload) > self.limits.max_row_bytes:
            raise RecordingError("oversized checkpoint auxiliary state")
        path = self.path / "checkpoints" / f"aux-{digest}.json.gz"
        compressed = gzip.compress(payload, compresslevel=3, mtime=0)
        self._reserve_disk(len(compressed) + 512)
        _atomic(path, compressed)
        self._add_file(path, "checkpoint_aux", 1)
        relative = str(path.relative_to(self.path))
        self._aux_paths[digest] = relative
        return relative

    def _write_checkpoint(self, record: RecordedCheckpoint) -> None:
        self._write_checkpoint_group((record,))

    def _write_checkpoint_group(self, records: tuple[RecordedCheckpoint, ...]) -> None:
        pending: list[tuple[RecordedCheckpoint, bytes, str]] = []
        pending_bytes = 0
        byte_target = min(self.limits.checkpoint_group_bytes, self.limits.max_row_bytes)
        previous_state: bytes | None = None
        previous_auxiliary: str | None = None
        for record in records:
            cp = record.checkpoint
            spec = None if cp.stream_id is None else self._streams.get(cp.stream_id)
            if (
                spec is None
                or cp.session_id != self.session_id
                or cp.metadata_version != spec.instrument.metadata_version
            ):
                raise RecordingError("checkpoint metadata mismatch")
            # Restore owns strategy/config compatibility, including REST clones.
            if self._db.execute(
                "SELECT 1 FROM checkpoints WHERE delivery=? AND stream=?",
                (record.delivery_index, cp.stream_id),
            ).fetchone():
                continue
            payload = wire.dumps((record.delivery_index, cp), canonical=False) + b"\n"
            if len(payload) > self.limits.max_row_bytes:
                raise RecordingError("oversized checkpoint")
            if pending and (
                pending_bytes + len(payload) > byte_target
                or len(pending) >= self.limits.checkpoint_group_count
            ):
                self._flush_checkpoints(pending)
                pending = []
                pending_bytes = 0
            if (
                record.observer_state is previous_state
                and previous_auxiliary is not None
            ):
                auxiliary_path = previous_auxiliary
            else:
                auxiliary_path = self._write_checkpoint_aux(record.observer_state)
                previous_state, previous_auxiliary = (
                    record.observer_state,
                    auxiliary_path,
                )
            pending.append((record, payload, auxiliary_path))
            pending_bytes += len(payload)
        if pending:
            self._flush_checkpoints(pending)

    def _flush_checkpoints(
        self, rows: list[tuple[RecordedCheckpoint, bytes, str]]
    ) -> None:
        self._checkpoint_sequence += 1
        path = self.path / "checkpoints" / f"{self._checkpoint_sequence:08d}.jsonl.gz"
        compressed = gzip.compress(
            b"".join(payload for _, payload, _ in rows), compresslevel=3, mtime=0
        )
        self._reserve_disk(len(compressed) + 512)
        _atomic(path, compressed)
        self._add_file(
            path,
            "checkpoint",
            len(rows),
            tuple(
                record.checkpoint.ordinal
                for record, _, _ in rows
                if record.checkpoint.ordinal is not None
            ),
            tuple(record.delivery_index for record, _, _ in rows),
            str(rows[0][0].checkpoint.stream_id) if len(rows) == 1 else None,
        )
        relative = str(path.relative_to(self.path))
        for offset, (record, _, auxiliary_path) in enumerate(rows):
            self._db.execute(
                "INSERT INTO checkpoints VALUES (?,?,?,?,?)",
                (
                    record.delivery_index,
                    record.checkpoint.stream_id,
                    relative,
                    offset,
                    auxiliary_path,
                ),
            )
        self._checkpoint_count += len(rows)

    def _run(self):
        self._connection = None
        try:
            self._connection = sqlite3.connect(self.path / "index.sqlite3")
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._queue or self._closing)
                    if self._abandon or not self._queue and self._closing:
                        break
                    _kind, value, size = self._queue.popleft()
                    self._queue_bytes -= size
                    self._inflight_count, self._inflight_bytes = 1, size
                self._check_disk()
                if isinstance(value, RawFrame):
                    self._write_raw(value)
                elif isinstance(value, RecordedBatch):
                    self._write_events(value)
                elif isinstance(value, RecordedCheckpoint):
                    self._write_checkpoint(value)
                else:
                    self._projection.register_stream(value)
                with self._condition:
                    self._inflight_count = self._inflight_bytes = 0
            if not self._abandon:
                self._flush_events()
                self._save_projection(self._delivery)
                self._close_raw()
                self._db.commit()
        except BaseException as exc:
            self.mark_incomplete(
                "writer_failed:" + type(exc).__name__ + ":" + str(exc)[:160]
            )
        finally:
            if self._raw_handle is not None:
                self._raw_handle.close()
                self._raw_handle = None
            if self._connection is not None:
                self._connection.close()
                self._connection = None
            if not self._abandon:
                self._finish()
            if self._abandon:
                (self.path / "manifest.json").unlink(missing_ok=True)
            self._active.close()
            with self._condition:
                self._queue.clear()
                self._pending.clear()
                self._queue_bytes = self._pending_bytes = 0
                self._pending_count = 0
                self._inflight_count = self._inflight_bytes = 0
                self._done = True
                self._condition.notify_all()

    def _make_manifest(self):
        return SessionManifest(
            STORAGE_SCHEMA_VERSION,
            self.session_id,
            self.clock_id,
            tuple(self._streams.values()),
            self.config,
            self.code_revision,
            self.provenance,
            self.license,
            self.created_wall_ns,
            time.time_ns(),
            not self._reasons,
            tuple(self._reasons),
            tuple(self._files),
            self._raw_count,
            self._event_count,
            self._batch_count,
            self._checkpoint_count,
            self.limits,
            gaps=tuple(self._gaps),
        )

    async def finalize(self) -> SessionManifest:
        async with self._finalize_lock:
            return await self._finalize()

    async def _finalize(self) -> SessionManifest:
        if self._manifest is not None and (self._done or self._abandon):
            return self._manifest
        with self._condition:
            self._closing = True
            self._condition.notify_all()
        deadline = time.monotonic() + self.limits.finalize_timeout
        # Do not schedule a blocked join in the default executor (which would hang
        # asyncio.run shutdown). The owned daemon remains locked until it exits.
        while self._thread.is_alive() and time.monotonic() < deadline:
            await asyncio.sleep(min(0.01, max(0, deadline - time.monotonic())))
        if self._thread.is_alive():
            self.mark_incomplete("finalize_timeout")
            with self._condition:
                self._abandon = True
            self._manifest = self._make_manifest()
            return self._manifest
        self._thread.join(timeout=0)
        if self._manifest is None:
            self.mark_incomplete("finalization_failed")
            self._manifest = self._make_manifest()
        return self._manifest

    def _finish(self):
        try:
            index = self.path / "index.sqlite3"
            self._files.append(
                FileRecord(
                    "index.sqlite3",
                    "catalog",
                    _hash(index),
                    index.stat().st_size,
                    self._raw_count + self._batch_count + self._checkpoint_count,
                )
            )
            self._manifest = self._make_manifest()
            payload = _json(self._manifest.to_dict())
            if len(payload) > _MANIFEST_LIMIT:
                raise RecordingError("oversized manifest")
            # Initialization and batching reservations are conservative estimates.
            # All owned files are sealed now; settle them before reserving the
            # final manifest rather than falsely retaining unused headroom.
            self._check_disk(force=True)
            self._reserve_disk(len(payload))
            _atomic(self.path / "manifest.json", payload)
            with _catalog(self.root) as db:
                db.execute(
                    "UPDATE sessions SET state=?, bytes=? WHERE session_id=?",
                    (
                        "complete" if self._manifest.complete else "incomplete",
                        _managed_bytes(self.path),
                        self.session_id,
                    ),
                )
        except Exception as exc:
            self.mark_incomplete("finalization_failed:" + type(exc).__name__)
            self._manifest = self._make_manifest()
            (self.path / "manifest.json").unlink(missing_ok=True)


def _parquet_schema():
    import pyarrow as pa

    return pa.schema(
        [
            (
                name,
                pa.int64()
                if name in ("delivery_index", "event_count")
                else pa.binary()
                if name == "payload"
                else pa.string(),
            )
            for name in (
                "delivery_index",
                "stream_id",
                "instrument_id",
                "venue",
                "ingest_ordinal",
                "receive_wall_ns",
                "receive_mono_ns",
                "source_mode",
                "segment_id",
                "payload_kind",
                "event_count",
                "payload",
            )
        ]
    )


class RecordingReader:
    def __init__(
        self, path: str | Path, *, verify: bool = True, allow_incomplete: bool = False
    ):
        supplied = Path(path).expanduser()
        if supplied.is_symlink():
            raise RecordingError("symlink capture root")
        self.path = supplied.resolve(strict=True)
        self._active = _safe(self.path, ".active").open("rb")
        self._connection: sqlite3.Connection | None = None
        self._caches_ready = False
        try:
            try:
                fcntl.flock(self._active, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RecordingError("capture is still active") from exc
            with _safe(self.path, "manifest.json").open("rb") as handle:
                self.manifest = _decode_manifest(handle.read(_MANIFEST_LIMIT + 1))
            self._raw_cache = _ReaderCache[RawFrame](self.manifest.limits)
            self._group_cache = _ReaderCache[list[dict[str, object]]](
                self.manifest.limits
            )
            self._aux_cache = _ReaderCache[bytes](self.manifest.limits)
            self._batch_cache = _ReaderCache[RecordedBatch](self.manifest.limits)
            self._checkpoint_cache = _ReaderCache[tuple[bytes, ...]](
                self.manifest.limits
            )
            self._caches_ready = True
            self._specs = {s.stream_id: s for s in self.manifest.streams}
            if len(self._specs) != len(self.manifest.streams):
                raise RecordingError("duplicate stream metadata")
            if not self.manifest.complete and not allow_incomplete:
                raise RecordingError(
                    "capture incomplete: " + ",".join(self.manifest.incomplete_reasons)
                )
            index = _safe(self.path, "index.sqlite3")
            self._connection = sqlite3.connect(
                index.as_uri() + "?mode=ro&immutable=1", uri=True
            )
            self._file_map = {f.path: f for f in self.manifest.files}
            if verify:
                self.verify()
        except BaseException:
            self.close()
            raise

    @property
    def _db(self) -> sqlite3.Connection:
        connection = self._connection
        if connection is None:
            raise RecordingError("reader catalog is not open")
        return connection

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self) -> None:
        if self._caches_ready:
            self._raw_cache.clear()
            self._group_cache.clear()
            self._batch_cache.clear()
            self._checkpoint_cache.clear()
            self._aux_cache.clear()
            self._caches_ready = False
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        self._active.close()

    def _file(self, relative, kind):
        item = self._file_map.get(relative)
        if item is None or item.kind != kind:
            raise RecordingError("index references undeclared capture file")
        return _safe(self.path, relative)

    def _read_gzip(self, path, *, offset=0, length=None):
        bound = self.manifest.limits.max_row_bytes
        with path.open("rb") as handle:
            handle.seek(offset)
            if length is not None:
                if length <= 0 or length > bound + 1024 * 1024:
                    raise RecordingError("oversized compressed row")
                blob = handle.read(length)
                if len(blob) != length:
                    raise RecordingError("truncated compressed row")
                source = io.BytesIO(blob)
            else:
                source = handle
            try:
                with gzip.GzipFile(fileobj=source) as compressed:
                    value = compressed.read(bound + 1)
                    if len(value) > bound:
                        raise RecordingError("oversized decompressed row")
                    return value
            except (OSError, EOFError) as exc:
                raise RecordingError("corrupt/truncated gzip") from exc

    def raw_frame(self, stream_id: str, ordinal: int) -> RawFrame | None:
        row = self._db.execute(
            "SELECT path,offset,length,payload_hash FROM raw WHERE stream=? AND ordinal=?",
            (str(stream_id), str(ordinal)),
        ).fetchone()
        if row is None:
            return None
        path = self._file(row[0], "raw")
        cached = self._raw_cache.get((str(stream_id), ordinal))
        if cached is not None:
            return cached
        try:
            frame = wire.loads(
                self._read_gzip(path, offset=row[1], length=row[2]),
                RawFrame,
                max_bytes=self.manifest.limits.max_row_bytes,
            )
        except (ValueError, TypeError) as exc:
            raise RecordingError("invalid raw record") from exc
        if (
            frame.stream_id != stream_id
            or frame.ingest_ordinal != ordinal
            or frame.payload_sha256 != row[3]
            or frame.session_id != self.manifest.session_id
            or frame.clock_id != self.manifest.clock_id
            or stream_id not in self._specs
        ):
            raise RecordingError("raw index/provenance mismatch")
        self._raw_cache.put((str(stream_id), ordinal), frame, _size(frame))
        return frame

    def iter_raw(self) -> Iterator[RawFrame]:
        for stream, ordinal in self._db.execute(
            "SELECT stream,ordinal FROM raw ORDER BY length(ordinal),ordinal"
        ):
            frame = self.raw_frame(stream, int(ordinal))
            if frame is None:
                raise RecordingError("missing indexed raw frame")
            yield frame

    def _group_rows(self, relative: str, number: int) -> list[dict[str, object]]:
        import pyarrow.parquet as pq

        path = self._file(relative, "normalized")
        cached = self._group_cache.get((relative, number))
        if cached is not None:
            return cached
        parquet = pq.ParquetFile(path)
        if parquet.schema_arrow != _parquet_schema():
            raise RecordingError("incompatible parquet schema")
        if not 0 <= number < parquet.metadata.num_row_groups:
            raise RecordingError("row group outside normalized file")
        group = parquet.metadata.row_group(number)
        if (
            not 1 <= group.num_rows <= self.manifest.limits.parquet_batch_count
            or group.total_byte_size
            > _parquet_group_bound(self.manifest.limits, group.num_rows)
        ):
            raise RecordingError("oversized parquet row group")
        rows: list[dict[str, object]] = parquet.read_row_group(number).to_pylist()
        if len(rows) != group.num_rows:
            raise RecordingError("parquet row group count mismatch")
        size = sys.getsizeof(rows) + sum(
            sys.getsizeof(row)
            + sum(sys.getsizeof(key) + _size(value) for key, value in row.items())
            for row in rows
        )
        self._group_cache.put((relative, number), rows, size)
        return rows

    def _batch(self, row) -> RecordedBatch:
        (
            delivery,
            stream,
            ordinal,
            first,
            last,
            relative,
            number,
            offset,
            count,
            wall,
            mono,
        ) = row
        cached = self._batch_cache.get((relative, delivery))
        if cached is not None:
            self._file(relative, "normalized")
            return cached
        try:
            rows = self._group_rows(relative, number)
            if not 0 <= offset < len(rows):
                raise RecordingError("row offset outside normalized group")
            stored = rows[offset]
            payload = stored["payload"]
            if (
                not isinstance(payload, bytes)
                or len(payload) > self.manifest.limits.max_row_bytes
            ):
                raise RecordingError("oversized normalized payload")
            events = wire.loads(
                payload,
                tuple[EventEnvelope, ...],
                max_bytes=self.manifest.limits.max_row_bytes,
            )
        except (ValueError, TypeError, IndexError, OSError) as exc:
            raise RecordingError("corrupt normalized batch") from exc
        if (
            not events
            or len(events) != count
            or stored["event_count"] != count
            or stored["delivery_index"] != delivery
            or stored["stream_id"] != stream
            or stored["ingest_ordinal"] != ordinal
            or stored["receive_wall_ns"] != wall
            or stored["receive_mono_ns"] != mono
            or first != 0
            or last != count - 1
            or stored["instrument_id"] != str(events[0].key.instrument_id)
            or stored["venue"] != events[0].key.venue
            or stored["segment_id"] != str(events[0].segment_id)
            or stored["source_mode"] != events[0].source_mode
            or stored["payload_kind"]
            != ",".join(sorted({type(e.payload).__name__ for e in events}))
        ):
            raise RecordingError("batch index mismatch")
        spec = self._specs.get(stream)
        for index, event in enumerate(events):
            if (
                spec is None
                or event.session_id != self.manifest.session_id
                or event.clock_id != self.manifest.clock_id
                or event.stream_id != stream
                or event.ingest_ordinal != int(ordinal)
                or event.event_index != index
                or event.receive_wall_ns != int(wall)
                or event.receive_mono_ns != int(mono)
                or event.key != spec.key
                or event.instrument_metadata_version != spec.instrument.metadata_version
            ):
                raise RecordingError("normalized metadata/provenance mismatch")
        batch = RecordedBatch(delivery, events)
        self._batch_cache.put((relative, delivery), batch, _size(batch))
        return batch

    def iter_batches(
        self, *, start_delivery: int = 1, stop_delivery: int | None = None
    ) -> Iterator[RecordedBatch]:
        if (
            start_delivery < 1
            or stop_delivery is not None
            and stop_delivery < start_delivery
        ):
            raise ValueError("invalid delivery range")
        rows = self._db.execute(
            "SELECT * FROM batches WHERE delivery>=? AND (? IS NULL OR delivery<=?) ORDER BY delivery",
            (start_delivery, stop_delivery, stop_delivery),
        )
        for row in rows:
            yield self._batch(row)

    def resolve_delivery(self, ordinal: int, event_index: int = 0) -> int:
        if ordinal < 0 or event_index < 0:
            raise ValueError("invalid seek position")
        # The requested event resolves to the END of its atomic source batch.
        row = self._db.execute(
            "SELECT delivery FROM batches WHERE ordinal=? AND first_event<=? AND last_event>=? "
            "ORDER BY delivery LIMIT 1",
            (str(ordinal), event_index, event_index),
        ).fetchone()
        if row is None:
            raise RecordingError("seek position is not a captured transaction")
        return row[0]

    def _checkpoint_aux(self, relative: str) -> bytes:
        path = self._file(relative, "checkpoint_aux")
        cached = self._aux_cache.get((relative, 0))
        if cached is not None:
            return cached
        state = wire.loads(
            self._read_gzip(path),
            bytes,
            max_bytes=self.manifest.limits.max_row_bytes,
        )
        expected = f"checkpoints/aux-{sha256(state).hexdigest()}.json.gz"
        if relative != expected:
            raise RecordingError("checkpoint auxiliary content identity mismatch")
        self._aux_cache.put((relative, 0), state, sys.getsizeof(state))
        return state

    def _checkpoint_rows(self, relative: str) -> tuple[bytes, ...]:
        path = self._file(relative, "checkpoint")
        cached = self._checkpoint_cache.get((relative, 0))
        if cached is not None:
            return cached
        payload = self._read_gzip(path)
        if not payload.endswith(b"\n"):
            raise RecordingError("truncated checkpoint row group")
        rows = tuple(payload.splitlines())
        if not 1 <= len(rows) <= self.manifest.limits.checkpoint_group_count:
            raise RecordingError("checkpoint row group count outside bounds")
        if len(rows) != self._file_map[relative].row_count:
            raise RecordingError("checkpoint row group count mismatch")
        self._checkpoint_cache.put((relative, 0), rows, _size(rows))
        return rows

    def iter_checkpoints(
        self, *, before_delivery: int | None = None
    ) -> Iterator[RecordedCheckpoint]:
        rows = self._db.execute(
            "SELECT delivery,stream,path,row_offset,aux_path FROM checkpoints WHERE (? IS NULL OR delivery<=?) "
            "ORDER BY delivery DESC,stream",
            (before_delivery, before_delivery),
        )
        for delivery, stream, relative, offset, auxiliary_path in rows:
            try:
                payloads = self._checkpoint_rows(relative)
                if not 0 <= offset < len(payloads):
                    raise RecordingError("checkpoint row offset outside group")
                position, checkpoint = wire.loads(
                    payloads[offset],
                    tuple[int, BookCheckpoint],
                    max_bytes=self.manifest.limits.max_row_bytes,
                )
                state = self._checkpoint_aux(auxiliary_path)
            except (ValueError, TypeError) as exc:
                raise RecordingError("invalid checkpoint") from exc
            spec = self._specs.get(stream)
            if (
                position != delivery
                or checkpoint.stream_id != stream
                or spec is None
                or checkpoint.session_id != self.manifest.session_id
                or checkpoint.metadata_version != spec.instrument.metadata_version
            ):
                raise RecordingError("checkpoint index/metadata mismatch")
            yield RecordedCheckpoint(position, checkpoint, state)

    def query_history(
        self,
        *,
        stream_id: str | None = None,
        start_ordinal: int | None = None,
        end_ordinal: int | None = None,
        limit: int = 1000,
    ) -> tuple[EventEnvelope, ...]:
        import duckdb

        if (
            not isinstance(limit, int)
            or not 1 <= limit <= self.manifest.limits.max_query_rows
        ):
            raise ValueError("query row limit outside bounds")
        if any(v is not None and v < 0 for v in (start_ordinal, end_ordinal)):
            raise ValueError("negative history ordinal")
        paths = [
            str(self._file(f.path, "normalized"))
            for f in self.manifest.files
            if f.kind == "normalized"
            and (stream_id is None or f.stream_id == stream_id)
            and (
                start_ordinal is None
                or f.last_ordinal is not None
                and f.last_ordinal >= start_ordinal
            )
            and (
                end_ordinal is None
                or f.first_ordinal is not None
                and f.first_ordinal <= end_ordinal
            )
        ]
        if not paths:
            return ()
        db = duckdb.connect(
            config={
                "memory_limit": "128MB",
                "threads": "1",
                "enable_external_access": "true",
                "temp_directory": "",
            }
        )
        try:
            relation = db.read_parquet(paths)
            relation.create_view("capture")
            # Decimal strings are compared by length then lexically: no lossy cast
            # or int64 narrowing for arbitrary-sized source ordinals.
            clauses: list[str] = []
            parameters: list[int | str] = []
            for value, operator in ((start_ordinal, ">="), (end_ordinal, "<=")):
                if value is not None:
                    text = str(value)
                    clauses.append(
                        f"(length(ingest_ordinal) {operator[0]} ? OR "
                        f"(length(ingest_ordinal) = ? AND ingest_ordinal {operator} ?))"
                    )
                    parameters.extend((len(text), len(text), text))
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            cursor = db.execute(
                "SELECT delivery_index FROM capture"
                + where
                + " ORDER BY delivery_index LIMIT ?",
                [*parameters, limit],
            )
            deliveries = cursor.fetchall()
            result: list[EventEnvelope] = []
            for (delivery,) in deliveries:
                row = self._db.execute(
                    "SELECT * FROM batches WHERE delivery=?", (delivery,)
                ).fetchone()
                if row is None:
                    raise RecordingError("query/catalog mismatch")
                batch = self._batch(row)
                result.extend(batch.events[: limit - len(result)])
                if len(result) == limit:
                    break
            return tuple(result)
        finally:
            db.close()

    def verify(self) -> None:
        manifest = self.manifest
        self._raw_cache.clear()
        self._group_cache.clear()
        self._aux_cache.clear()
        self._batch_cache.clear()
        self._checkpoint_cache.clear()
        if (
            len(self._file_map) != len(manifest.files)
            or len(manifest.files) > manifest.limits.max_files
        ):
            raise RecordingError("duplicate/excessive manifest files")
        if [f.path for f in manifest.files if f.kind == "catalog"] != [
            "index.sqlite3"
        ] or any(
            f.kind
            not in ("catalog", "raw", "normalized", "checkpoint", "checkpoint_aux")
            for f in manifest.files
        ):
            raise RecordingError("invalid catalog/file kinds")
        try:
            from .projection import adapter_for_stream

            _pairs(manifest.config)
            if len(manifest.streams) > manifest.limits.max_streams:
                raise RecordingError("manifest stream capacity exceeded")
            for spec in manifest.streams:
                adapter_for_stream(spec)
            if self._db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RecordingError("catalog integrity check failed")
            for key, value in (
                ("session_id", manifest.session_id),
                ("clock_id", manifest.clock_id),
            ):
                if self._db.execute(
                    "SELECT value FROM metadata WHERE key=?", (key,)
                ).fetchone() != (value,):
                    raise RecordingError("catalog provenance mismatch")
            for item in manifest.files:
                path = _safe(self.path, item.path)
                if (
                    not path.is_file()
                    or path.stat().st_size != item.size_bytes
                    or _hash(path) != item.sha256
                ):
                    raise RecordingError(
                        "capture file hash/size mismatch: " + item.path
                    )
                if item.kind != "catalog":
                    row = self._db.execute(
                        "SELECT metadata FROM files WHERE path=?", (item.path,)
                    ).fetchone()
                    if row != (_json(_encode(item)),):
                        raise RecordingError("catalog file metadata mismatch")
                if item.kind == "normalized":
                    import pyarrow.parquet as pq

                    metadata = pq.ParquetFile(path).metadata
                    if (
                        metadata.num_rows != item.row_count
                        or metadata.num_row_groups
                        != (item.row_count + manifest.limits.parquet_batch_count - 1)
                        // manifest.limits.parquet_batch_count
                    ):
                        raise RecordingError("parquet row count mismatch")
            if (
                self._db.execute("SELECT count(*) FROM files").fetchone()[0]
                != len(manifest.files) - 1
            ):
                raise RecordingError("catalog file count mismatch")
            raw_count = batch_count = event_count = checkpoint_count = 0
            stats: dict[str, _FileStats] = {}
            raw_ends: dict[str, int] = {}

            def add(
                relative: str, ordinal: int | None, delivery: int | None = None
            ) -> None:
                stats.setdefault(relative, _FileStats()).add(ordinal, delivery)

            for stream, ordinal, relative, offset, length in self._db.execute(
                "SELECT stream,ordinal,path,offset,length FROM raw ORDER BY path,offset"
            ):
                self.raw_frame(stream, int(ordinal))
                if (
                    offset != raw_ends.get(relative, 0)
                    or offset + length > self._file_map[relative].size_bytes
                ):
                    raise RecordingError("raw offset gap/overlap/outside file")
                raw_ends[relative] = offset + length
                add(relative, int(ordinal))
                raw_count += 1
            if any(
                end != self._file_map[path].size_bytes for path, end in raw_ends.items()
            ):
                raise RecordingError("unindexed raw bytes")
            gaps = []
            for row in self._db.execute("SELECT * FROM batches ORDER BY delivery"):
                batch = self._batch(row)
                batch_count += 1
                if batch.delivery_index != batch_count:
                    raise RecordingError("missing delivery transaction")
                event_count += len(batch.events)
                add(row[5], int(row[2]), batch.delivery_index)
                first = batch.events[0]
                frame = self.raw_frame(first.stream_id, first.ingest_ordinal)
                for event in batch.events:
                    if isinstance(event.payload, Gap):
                        gaps.append(event)
                    if frame is None:
                        if not is_local_control(event):
                            raise RecordingError("missing required raw evidence")
                        continue
                    if is_local_control(event):
                        raise RecordingError("local origin conflicts with indexed raw")
                    if event.raw_ref != frame.ref():
                        raise RecordingError("missing/mismatched raw event reference")
                    if (
                        frame.connection_id,
                        frame.connection_epoch,
                        frame.receive_wall_ns,
                        frame.receive_mono_ns,
                    ) != (
                        event.connection_id,
                        event.connection_epoch,
                        event.receive_wall_ns,
                        event.receive_mono_ns,
                    ):
                        raise RecordingError(
                            "raw/normalized receive provenance mismatch"
                        )
            for record in self.iter_checkpoints():
                relative, auxiliary_path = self._db.execute(
                    "SELECT path,aux_path FROM checkpoints WHERE delivery=? AND stream=?",
                    (record.delivery_index, record.checkpoint.stream_id),
                ).fetchone()
                if record.delivery_index > batch_count:
                    raise RecordingError("checkpoint beyond normalized history")
                add(relative, record.checkpoint.ordinal, record.delivery_index)
                if auxiliary_path not in stats:
                    add(auxiliary_path, None)
                checkpoint_count += 1
            for item in manifest.files:
                if item.kind != "catalog" and stats.get(item.path) != _FileStats(
                    item.row_count,
                    item.first_ordinal,
                    item.last_ordinal,
                    item.first_delivery,
                    item.last_delivery,
                ):
                    raise RecordingError("capture file count/range mismatch")
            if (raw_count, event_count, batch_count, checkpoint_count) != (
                manifest.raw_count,
                manifest.event_count,
                manifest.batch_count,
                manifest.checkpoint_count,
            ):
                raise RecordingError("manifest count mismatch")
            if tuple(gaps) != manifest.gaps:
                raise RecordingError("manifest gap evidence mismatch")
            if manifest.complete and (
                manifest.incomplete_reasons
                or any(
                    isinstance(g.payload, Gap) and g.payload.capture_lost for g in gaps
                )
            ):
                raise RecordingError("contradictory completeness metadata")
        except (OSError, sqlite3.Error, ValueError, KeyError) as exc:
            raise RecordingError("capture verification failed") from exc
