"""Manually authored, exact journal evidence; never changes simulated accounting."""

from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
import sqlite3

from .instruments import InstrumentId
from .precision import decimal, nonnegative_int, text
from .quality import ClockReading
from .wire import dumps, loads

JOURNAL_SCHEMA_VERSION = 1
MAX_JOURNAL_ENTRIES = 100_000


def _user_text(value: str, maximum: int, *, required: bool = False) -> None:
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or (required and not value.strip())
    ):
        raise ValueError("invalid bounded journal text")
    if any(ord(c) < 32 and c not in "\n\t\r" for c in value):
        raise ValueError("journal text contains control characters")


@dataclass(frozen=True)
class JournalEntry:
    entry_id: str
    source_id: str
    session_id: str
    delivery_index: int
    at: ClockReading
    instrument_id: InstrumentId
    venue: str
    rationale: str
    entry: Decimal | None = None
    stop: Decimal | None = None
    context: str = ""
    run_id: str | None = None
    order_id: str | None = None

    def __post_init__(self):
        for value in (self.entry_id, self.source_id, self.session_id, self.venue):
            text(value)
        object.__setattr__(self, "instrument_id", InstrumentId(self.instrument_id))
        nonnegative_int(self.delivery_index)
        if not isinstance(self.at, ClockReading):
            raise ValueError("journal requires an explicit source clock")
        for name in ("entry", "stop"):
            amount: Decimal | None = getattr(self, name)
            if amount is not None:
                object.__setattr__(self, name, decimal(amount, positive=True))
        for linked_id in (self.run_id, self.order_id):
            if linked_id is not None:
                text(linked_id)
        if self.order_id is not None and self.run_id is None:
            raise ValueError("order linkage requires simulation run")
        _user_text(self.rationale, 8192, required=True)
        _user_text(self.context, 16384)


class JournalStore:
    def __init__(self, path: Path):
        self._db = sqlite3.connect(path, timeout=10)
        try:
            with self._db:
                self._db.execute("BEGIN IMMEDIATE")
                version = self._db.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, JOURNAL_SCHEMA_VERSION):
                    raise ValueError("unsupported journal schema")
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS entries (entry_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, run_id TEXT, payload BLOB NOT NULL)"
                )
                self._db.execute(
                    "CREATE INDEX IF NOT EXISTS journal_source_run ON entries(source_id, run_id)"
                )
                self._db.execute("PRAGMA user_version=1")
        except BaseException:
            self._db.close()
            raise

    def put(self, entry: JournalEntry) -> JournalEntry:
        if not isinstance(entry, JournalEntry):
            raise ValueError("expected JournalEntry")
        payload = dumps(entry, canonical=False)
        if len(payload) > 200_000:
            raise ValueError("journal payload limit")
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            old = self.get(entry.entry_id)
            if old is not None and old.source_id != entry.source_id:
                raise ValueError("journal source identity is immutable")
            if (
                old is not None
                and replace(
                    entry,
                    rationale=old.rationale,
                    entry=old.entry,
                    stop=old.stop,
                    context=old.context,
                )
                != old
            ):
                raise ValueError("journal origin provenance is immutable")
            if (
                old is None
                and self._db.execute("SELECT count(*) FROM entries").fetchone()[0]
                >= MAX_JOURNAL_ENTRIES
            ):
                raise ValueError("journal entry limit")
            self._db.execute(
                "INSERT INTO entries VALUES(?,?,?,?) ON CONFLICT(entry_id) DO UPDATE SET payload=excluded.payload",
                (entry.entry_id, entry.source_id, entry.run_id, payload),
            )
        return entry

    def get(self, entry_id: str) -> JournalEntry | None:
        """Read one bounded record so edits can retain its original provenance."""
        text(entry_id)
        row = self._db.execute(
            "SELECT payload FROM entries WHERE entry_id=?", (entry_id,)
        ).fetchone()
        return None if row is None else loads(row[0], JournalEntry, max_bytes=200_000)

    def list(
        self,
        *,
        source_id: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
    ) -> tuple[JournalEntry, ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("journal query limit must be 1..1000")
        clauses, args = [], []
        for name, value in (("source_id", source_id), ("run_id", run_id)):
            if value is not None:
                text(value)
                clauses.append(name + "=?")
                args.append(value)
        query = (
            "SELECT payload FROM entries"
            + (" WHERE " + " AND ".join(clauses) if clauses else "")
            + " ORDER BY rowid DESC LIMIT ?"
        )
        return tuple(
            loads(row[0], JournalEntry, max_bytes=200_000)
            for row in self._db.execute(query, (*args, limit))
        )

    def close(self) -> None:
        self._db.close()
