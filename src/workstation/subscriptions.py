"""Reference-counted single-worker FeedHub with independent client generations.

A process/OS collector lock prevents accidental duplicate backend workers. Browser
presentation may coalesce; raw and normalized sinks are separate loss-intolerant
callbacks. Headless clients hold ordinary leases without presentation queues.
"""

import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass, field
import fcntl
from io import BufferedRandom
import os
from pathlib import Path
import tempfile
from typing import Callable
from uuid import uuid4

from .adapter_catalog import AdapterCatalog
from .book import Book
from .events import (
    ClientId,
    EventEnvelope,
    LeaseId,
    RawFrame,
    StreamId,
    SubscriptionKey,
)
from .ingestion import FeedCallbacks, FeedSupervisor, ReceiveBoundary, SupervisorConfig


@dataclass(frozen=True)
class SubscriptionLease:
    lease_id: LeaseId
    client_id: ClientId
    key: SubscriptionKey
    generation: int
    stream_id: StreamId


@dataclass(frozen=True)
class SubscriptionStatus:
    key: SubscriptionKey
    stream_id: StreamId
    reference_count: int
    lifecycle: str
    connection_epoch: int
    queue_depth: int
    queue_bytes: int
    reason: str
    capture_complete: bool


@dataclass(frozen=True)
class Presentation:
    book: Book
    sequence: int
    coalesced: int
    resync_required: bool


class PresentationQueue:
    """Bounded latest-book delivery, never an analytical event subscription."""

    def __init__(self, capacity: int = 16):
        if type(capacity) is not int or capacity < 1:
            raise ValueError("invalid presentation capacity")
        self.capacity = capacity
        self._items: deque[Presentation] = deque()
        self._ready = asyncio.Event()
        self.sequence = 0
        self.coalesced = 0
        self.closed = False

    def put(self, book: Book) -> None:
        if self.closed:
            return
        self.sequence += 1
        retained = deque(item for item in self._items if item.book.key != book.key)
        removed = len(self._items) - len(retained)
        if len(retained) >= self.capacity:
            retained.popleft()
            removed += 1
        self.coalesced += removed
        retained.append(
            Presentation(book, self.sequence, self.coalesced, bool(removed))
        )
        self._items = retained
        self._ready.set()

    async def get(self) -> Presentation:
        while not self._items:
            if self.closed:
                raise EOFError("presentation client closed")
            await self._ready.wait()
        item = self._items.popleft()
        if not self._items:
            self._ready.clear()
        return item

    def retain(self, keys: frozenset[SubscriptionKey]) -> None:
        self._items = deque(item for item in self._items if item.book.key in keys)
        if not self._items:
            self._ready.clear()

    def close(self) -> None:
        self.closed = True
        self._items.clear()
        self._ready.set()

    def __len__(self) -> int:
        return len(self._items)


@dataclass
class _Entry:
    supervisor: FeedSupervisor
    task: asyncio.Task
    leases: set[LeaseId] = field(default_factory=set)
    closing: asyncio.Task | None = None


async def _join_cleanup(task: asyncio.Task) -> None:
    """Cancellation cannot hand physical ownership to a new task before join."""
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


class SubscriptionRegistry:
    def __init__(
        self,
        catalog: AdapterCatalog | None = None,
        *,
        config: SupervisorConfig | None = None,
        supervisor_factory=FeedSupervisor,
        lock_path: str | Path | None = None,
        worker_count: int = 1,
        max_clients: int = 1024,
    ):
        configured_workers = max(
            worker_count,
            int(os.environ.get("WEB_CONCURRENCY", "1")),
            int(os.environ.get("UVICORN_WORKERS", "1")),
        )
        if configured_workers != 1:
            raise RuntimeError("feed collector requires exactly one backend worker")
        if type(max_clients) is not int or max_clients < 1:
            raise ValueError("invalid client bound")
        self.catalog = catalog or AdapterCatalog()
        self.config = config or SupervisorConfig()
        self.boundary = ReceiveBoundary()
        self._trade_dedup: OrderedDict[tuple[str, str, str, str], None] = OrderedDict()
        self._factory = supervisor_factory
        self._lock_path = (
            Path(lock_path)
            if lock_path is not None
            else Path(tempfile.gettempdir())
            / f"limit-orderbook-collector-{os.getuid()}.lock"
        )
        self._lock_file: BufferedRandom | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = asyncio.Lock()
        self._entries: dict[SubscriptionKey, _Entry] = {}
        self._leases: dict[LeaseId, SubscriptionLease] = {}
        self._generations: dict[ClientId, int] = {}
        self._client_locks: dict[ClientId, asyncio.Lock] = {}
        self._presentations: dict[ClientId, PresentationQueue] = {}
        self._books: dict[SubscriptionKey, Book] = {}
        self._raw_sinks: list[Callable[[RawFrame], None]] = []
        self._event_sinks: list[Callable[[tuple[EventEnvelope, ...]], None]] = []
        self._closing: set[asyncio.Task] = set()
        self._closed = False
        self._close_task: asyncio.Task | None = None
        self.max_clients = max_clients

    def _check_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise RuntimeError("registry is bound to one asyncio loop")
        self._loop = loop

    def _claim_collector(self) -> None:
        if self._lock_file is not None:
            return
        handle = self._lock_path.open("a+b")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError(
                "another feed collector owns the singleton lock"
            ) from exc
        self._lock_file = handle

    def _client_lock(self, client_id: ClientId) -> asyncio.Lock:
        self._check_loop()
        if self._closed:
            raise RuntimeError("registry is closed")
        if client_id not in self._client_locks:
            if len(self._client_locks) >= self.max_clients:
                raise ValueError("client capacity exceeded")
            self._client_locks[client_id] = asyncio.Lock()
        return self._client_locks[client_id]

    def subscribe_raw(self, callback: Callable[[RawFrame], None]) -> Callable[[], None]:
        self._raw_sinks.append(callback)
        return lambda: self._remove_sink(self._raw_sinks, callback)

    def subscribe_events(
        self, callback: Callable[[tuple[EventEnvelope, ...]], None]
    ) -> Callable[[], None]:
        self._event_sinks.append(callback)
        return lambda: self._remove_sink(self._event_sinks, callback)

    @staticmethod
    def _remove_sink(sinks, callback) -> None:
        if callback in sinks:
            sinks.remove(callback)

    def _raw(self, frame: RawFrame) -> None:
        failure = None
        for sink in tuple(self._raw_sinks):
            try:
                sink(frame)
            except Exception as exc:
                failure = exc
        if failure is not None:
            raise failure

    def _events(self, events: tuple[EventEnvelope, ...]) -> None:
        failure = None
        for sink in tuple(self._event_sinks):
            try:
                sink(events)
            except Exception as exc:
                failure = exc
        if failure is not None:
            raise failure

    def _book(self, book: Book) -> None:
        entry = self._entries.get(book.key)
        if (
            entry is None
            or entry.closing
            or book.connection_epoch != entry.supervisor.epoch
        ):
            return
        self._books[book.key] = book
        clients = {
            self._leases[lease_id].client_id
            for lease_id in entry.leases
            if lease_id in self._leases
        }
        for client in clients:
            queue = self._presentations.get(client)
            if queue is not None:
                queue.put(book)

    def presentation_queue(
        self, client_id: ClientId, *, capacity: int = 16
    ) -> PresentationQueue:
        client_id = ClientId(client_id)
        self._client_lock(client_id)
        if client_id not in self._presentations:
            self._presentations[client_id] = PresentationQueue(capacity)
        return self._presentations[client_id]

    async def _acquire(
        self, client_id: ClientId, key: SubscriptionKey, generation: int
    ) -> SubscriptionLease:
        while True:
            async with self._lock:
                if self._closed:
                    raise RuntimeError("registry is closed")
                for lease in self._leases.values():
                    if lease.client_id == client_id and lease.key == key:
                        return lease
                entry = self._entries.get(key)
                if entry is not None and entry.closing is not None:
                    closing = entry.closing
                else:
                    if entry is None:
                        instrument = self.catalog.instruments.get(key.instrument_id)
                        adapter = self.catalog.adapter(key.venue)
                        # Fail unsupported keys before installing any lease/worker.
                        adapter.capabilities(instrument, key)
                        self._claim_collector()
                        supervisor = self._factory(
                            adapter,
                            instrument,
                            key,
                            boundary=self.boundary,
                            callbacks=FeedCallbacks(
                                raw=self._raw, events=self._events, book=self._book
                            ),
                            config=self.config,
                            trade_dedup=self._trade_dedup,
                        )
                        entry = _Entry(
                            supervisor,
                            asyncio.create_task(
                                supervisor.run(), name=f"feed:{supervisor.stream_id}"
                            ),
                        )
                        self._entries[key] = entry
                    lease = SubscriptionLease(
                        LeaseId(str(uuid4())),
                        client_id,
                        key,
                        generation,
                        entry.supervisor.stream_id,
                    )
                    entry.leases.add(lease.lease_id)
                    self._leases[lease.lease_id] = lease
                    return lease
            await _join_cleanup(closing)

    async def acquire(
        self, client_id: ClientId, key: SubscriptionKey
    ) -> SubscriptionLease:
        key = self.catalog.canonical_key(key)
        client_id = ClientId(client_id)
        async with self._client_lock(client_id):
            return await self._acquire(
                client_id, key, self._generations.get(client_id, 0)
            )

    async def _finish_entry(self, key: SubscriptionKey, entry: _Entry) -> None:
        entry.task.cancel()
        await asyncio.gather(entry.task, return_exceptions=True)
        async with self._lock:
            if self._entries.get(key) is entry:
                del self._entries[key]
                self._books.pop(key, None)

    async def _release(self, lease_id: LeaseId) -> None:
        closing = None
        async with self._lock:
            lease = self._leases.pop(lease_id, None)
            if lease is None:
                return
            entry = self._entries[lease.key]
            entry.leases.discard(lease_id)
            if not entry.leases:
                closing = asyncio.create_task(self._finish_entry(lease.key, entry))
                entry.closing = closing
                self._closing.add(closing)
                closing.add_done_callback(self._closing.discard)
        if closing is not None:
            await _join_cleanup(closing)

    async def release(self, lease_id: LeaseId) -> None:
        self._check_loop()
        lease = self._leases.get(lease_id)
        if lease is None:
            return
        lock = self._client_locks[lease.client_id]
        async with lock:
            await self._release(lease_id)

    async def replace(
        self, client_id: ClientId, keys: frozenset[SubscriptionKey], generation: int
    ) -> tuple[SubscriptionLease, ...]:
        client_id = ClientId(client_id)
        if type(generation) is not int or generation < 0:
            raise ValueError("invalid generation")
        keys = frozenset(self.catalog.canonical_key(key) for key in keys)
        async with self._client_lock(client_id):
            previous = tuple(
                lease for lease in self._leases.values() if lease.client_id == client_id
            )
            current = self._generations.get(client_id, -1)
            if generation < current or (
                generation == current
                and keys != frozenset(lease.key for lease in previous)
            ):
                raise ValueError("obsolete client generation")
            if generation == current:
                return previous
            acquired = []
            try:
                for key in keys:
                    acquired.append(await self._acquire(client_id, key, generation))
            except BaseException:
                old_ids = {lease.lease_id for lease in previous}
                cleanup = asyncio.create_task(
                    self._release_many(
                        tuple(
                            lease.lease_id
                            for lease in acquired
                            if lease.lease_id not in old_ids
                        )
                    )
                )
                await _join_cleanup(cleanup)
                raise
            # Commit the generation only once the complete new set exists.
            self._generations[client_id] = generation
            result = []
            for lease in acquired:
                updated = SubscriptionLease(
                    lease.lease_id, client_id, lease.key, generation, lease.stream_id
                )
                self._leases[lease.lease_id] = updated
                result.append(updated)
            queue = self._presentations.get(client_id)
            if queue is not None:
                queue.retain(keys)
            cleanup = asyncio.create_task(
                self._release_many(
                    tuple(lease.lease_id for lease in previous if lease.key not in keys)
                )
            )
            await _join_cleanup(cleanup)
            return tuple(result)

    async def _release_many(self, lease_ids: tuple[LeaseId, ...]) -> None:
        await asyncio.gather(*(self._release(lease_id) for lease_id in lease_ids))

    async def release_client(self, client_id: ClientId) -> None:
        client_id = ClientId(client_id)
        self._check_loop()
        lock = self._client_locks.get(client_id)
        if lock is None:
            return
        async with lock:
            self._generations[client_id] = self._generations.get(client_id, 0) + 1
            lease_ids = tuple(
                lease.lease_id
                for lease in self._leases.values()
                if lease.client_id == client_id
            )
            cleanup = asyncio.create_task(self._release_many(lease_ids))
            try:
                await _join_cleanup(cleanup)
            finally:
                queue = self._presentations.pop(client_id, None)
                if queue is not None:
                    queue.close()

    def books(self) -> tuple[Book, ...]:
        return tuple(self._books.values())

    def snapshot(self) -> tuple[SubscriptionStatus, ...]:
        return tuple(
            SubscriptionStatus(
                key,
                entry.supervisor.stream_id,
                len(entry.leases),
                entry.supervisor.lifecycle,
                entry.supervisor.epoch,
                entry.supervisor.queue.qsize(),
                entry.supervisor.queued_bytes,
                entry.supervisor.reason,
                entry.supervisor.capture_complete,
            )
            for key, entry in self._entries.items()
        )

    async def _close(self) -> None:
        async with self._lock:
            self._closed = True
            lease_ids = tuple(self._leases)
        await self._release_many(lease_ids)
        if self._closing:
            await asyncio.gather(*tuple(self._closing), return_exceptions=True)
        for queue in self._presentations.values():
            queue.close()
        self._presentations.clear()
        if self._lock_file is not None:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()
            self._lock_file = None

    async def close(self) -> None:
        self._check_loop()
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await _join_cleanup(self._close_task)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()


# FeedHub is the public headless facade, not a second collector implementation.
FeedHub = SubscriptionRegistry
