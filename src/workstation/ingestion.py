"""Single-owner asynchronous ingestion, independent of rendering and recording.

Callbacks are synchronous, nonblocking enqueue boundaries. Required event sinks
must raise SinkOverflow rather than discard events; recorder/raw sink failures
mark capture incomplete. CPU decoding/reduction is serialized off the asyncio
loop, and cancellation joins that work before releasing ownership.
"""

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, replace
import random
import time
from typing import Callable
from uuid import uuid4

from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from .adapters.base import (
    Adapter,
    DecodeResult,
    DecodedEvent,
    TransportRequest,
    validate_public_endpoint,
    wrap_decoded,
)
from .book import Book, BookReducer
from .events import (
    BookUpdate,
    Control,
    EventEnvelope,
    Gap,
    RawFrame,
    SegmentId,
    SessionId,
    StreamId,
    SubscriptionKey,
    Trade,
)
from .instruments import Instrument
from .precision import MAX_MARKET_DATA_BYTES
from .quality import SourceMode
from .transport import PublicTransportError, fetch_public, retry_after_seconds


class SinkOverflow(RuntimeError):
    """A required consumer could not retain its input; never an allowed drop."""


class FeedFault(RuntimeError):
    pass


def _failure_reason(exc: Exception) -> str:
    """Only typed protocol faults may supply bounded machine-readable reasons."""
    if isinstance(exc, (FeedFault, PublicTransportError)):
        reason = exc.args[0] if isinstance(exc, FeedFault) and exc.args else None
        if isinstance(exc, PublicTransportError):
            reason = exc.reason
        if (
            isinstance(reason, str)
            and 0 < len(reason) <= 480
            and all(c.isascii() and (c.isalnum() or c in "_:-.") for c in reason)
        ):
            return reason
        return "invalid_failure_reason"
    if isinstance(exc, SinkOverflow):
        return "sink_overflow"
    if isinstance(exc, OSError):
        return "network_error"
    return "unexpected_failure"


def _failure_detail(exc: Exception) -> str:
    try:
        detail = str(exc)
    except Exception:
        detail = "exception detail unavailable"
    return "".join(c if c.isprintable() else " " for c in detail[:4096])


@dataclass(frozen=True)
class SupervisorConfig:
    queue_count: int = 512
    queue_bytes: int = 64 * 1024 * 1024
    ack_timeout: float = 10
    snapshot_timeout: float = 20
    valid_data_timeout: float = 30
    silence_timeout: float = 30
    heartbeat_interval: float = 20
    poll_interval: float = 2
    ws_recovery_interval: float = 30
    backoff_base: float = 0.5
    backoff_max: float = 30
    circuit_failures: int = 6
    circuit_cooldown: float = 60
    ws_failures_before_rest: int = 2
    dedup_capacity: int = 50_000
    session_lifetime: float = 23 * 60 * 60

    def __post_init__(self):
        for name, value in vars(self).items():
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not 0 < value < float("inf")
            ):
                raise ValueError(f"invalid supervisor bound: {name}")
        for name in (
            "queue_count",
            "queue_bytes",
            "circuit_failures",
            "ws_failures_before_rest",
            "dedup_capacity",
        ):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name} must be an integer")


class ReceiveBoundary:
    """Session-global ordinals, assigned synchronously before enqueue or decode."""

    def __init__(self, session_id: SessionId | None = None):
        self.session_id = session_id or SessionId(str(uuid4()))
        self.clock_id = str(uuid4())
        self.ordinal = 0

    def frame(
        self,
        stream_id: StreamId,
        connection_id: str,
        epoch: int,
        transport: str,
        endpoint: str,
        payload: bytes,
    ) -> RawFrame:
        self.ordinal += 1
        return RawFrame(
            self.session_id,
            stream_id,
            connection_id,
            epoch,
            self.clock_id,
            self.ordinal,
            time.time_ns(),
            time.monotonic_ns(),
            transport,
            endpoint,
            payload,
        )


@dataclass
class FeedCallbacks:
    raw: Callable[[RawFrame], None] | None = None
    events: Callable[[tuple[EventEnvelope, ...]], None] | None = None
    book: Callable[[Book], None] | None = None
    diagnostic: Callable[[str], None] | None = None


def make_snapshot_reducer(
    adapter: Adapter,
    instrument: Instrument,
    key: SubscriptionKey,
    *,
    epoch: int = 0,
    segment_id: str = "initial",
    poll_interval: float | None = None,
) -> BookReducer:
    """Validate REST snapshots without claiming the adapter's WS continuity evidence."""
    prototype = adapter.new_reducer(instrument, key)
    reducer = BookReducer(
        instrument,
        key,
        capabilities=replace(
            prototype.capabilities,
            deltas=False,
            exact_event_flow=False,
            sequence_scope=None,
            checksum_scope=None,
        ),
        epoch=epoch,
        segment_id=segment_id,
        price_validator=prototype.price_validator,
        truncate_depth=prototype.truncate_depth,
        effective_depth=prototype.effective_depth,
        max_levels=prototype.max_levels,
        strategy_id=f"{prototype.strategy_id}:rest-snapshot-v1",
        adapter_revision=prototype.adapter_revision,
        config_hash=prototype.config_hash,
    )
    reducer.quality = replace(
        reducer.quality,
        coverage=replace(
            prototype.quality.coverage,
            expected_cadence_ns=(
                None if poll_interval is None else int(poll_interval * 1_000_000_000)
            ),
        ),
    )
    return reducer


class FeedSupervisor:
    def __init__(
        self,
        adapter: Adapter,
        instrument: Instrument,
        key: SubscriptionKey,
        *,
        boundary: ReceiveBoundary | None = None,
        stream_id: StreamId | None = None,
        callbacks: FeedCallbacks | None = None,
        config: SupervisorConfig | None = None,
        ws_connect=connect,
        http_fetch=fetch_public,
        trade_dedup: OrderedDict[tuple[str, str, str, str], None] | None = None,
    ):
        if (key.venue, key.api_family, key.instrument_id) != (
            instrument.venue,
            instrument.api_family,
            instrument.instrument_id,
        ):
            raise ValueError("subscription identity mismatch")
        self.adapter, self.instrument, self.key = adapter, instrument, key
        self.boundary = boundary or ReceiveBoundary()
        self.stream_id = stream_id or StreamId(str(uuid4()))
        self.callbacks = callbacks or FeedCallbacks()
        self.config = config or SupervisorConfig()
        self.ws_connect, self.http_fetch = ws_connect, http_fetch
        self.epoch = 0
        self.segment_id = SegmentId(str(uuid4()))
        self.connection_id = str(uuid4())
        self.source_mode: SourceMode = "ws_snapshot"
        self.lifecycle = "starting"
        self.reason = "starting"
        self.reducer = adapter.new_reducer(instrument, key)
        self.book: Book | None = None
        self.capture_complete = True
        self.queue: asyncio.Queue[RawFrame] = asyncio.Queue(self.config.queue_count)
        self.queued_bytes = 0
        self._fault = asyncio.Event()
        self._fault_reason = ""
        self._first_missing_ordinal: int | None = None
        self._last_missing_ordinal: int | None = None
        self._pending_ordinal: int | None = None
        self.diagnostic_detail = ""
        self._http_retry_until = 0.0
        self._ws_retry_until = 0.0
        self._dedup: OrderedDict[tuple[str, str, str, str], None] = (
            OrderedDict() if trade_dedup is None else trade_dedup
        )
        self.duplicate_trades = 0
        self.last_transport = 0.0
        self.last_valid = 0.0
        self._acked = False
        self._valid = False
        self.reconnects = 0
        self._running = False

    def _diagnostic(self, reason: str) -> None:
        self.reason = reason
        if self.callbacks.diagnostic:
            try:
                self.callbacks.diagnostic(reason)
            except Exception as exc:
                # Diagnostics are observational, never a second owner-failure path.
                self.diagnostic_detail = _failure_detail(exc)

    def _emit_events(self, events: tuple[EventEnvelope, ...]) -> None:
        if self.callbacks.events and events:
            try:
                self.callbacks.events(events)
            except Exception as exc:
                self.capture_complete = False
                self.diagnostic_detail = _failure_detail(exc)
                for event in events:
                    self._record_loss(event.ingest_ordinal)
                raise FeedFault("event_sink_failed") from exc

    def _publish(self, book: Book | None) -> None:
        if book is None:
            return
        if not self.capture_complete:
            book = replace(book, quality=replace(book.quality, capture_complete=False))
        self.book = book
        if self.callbacks.book:
            try:
                self.callbacks.book(book)
            except Exception as exc:
                self.diagnostic_detail = _failure_detail(exc)
                raise FeedFault("book_sink_failed") from exc

    def _local(self, payload: Control | Gap) -> EventEnvelope:
        frame = self.boundary.frame(
            self.stream_id, self.connection_id, self.epoch, "local", "supervisor", b""
        )
        return wrap_decoded(
            frame,
            self.instrument,
            self.key,
            DecodeResult(events=(DecodedEvent(payload),)),
            self.segment_id,
            self.source_mode,
        )[0]

    def _record_loss(self, ordinal: int) -> None:
        if self._first_missing_ordinal is None:
            self._first_missing_ordinal = self._last_missing_ordinal = ordinal
        else:
            assert self._last_missing_ordinal is not None
            self._first_missing_ordinal = min(self._first_missing_ordinal, ordinal)
            self._last_missing_ordinal = max(self._last_missing_ordinal, ordinal)

    def _gap(self, reason: str) -> None:
        # Producers have been joined by the session before recovery. Every queued
        # frame discarded here is known lost normalization, not unknown history.
        while not self.queue.empty():
            self._record_loss(self.queue.get_nowait().ingest_ordinal)
        self.queued_bytes = 0
        if self._pending_ordinal is not None:
            self._record_loss(self._pending_ordinal)
            self._pending_ordinal = None
        event = self._local(
            Gap(
                reason,
                first_missing_ordinal=self._first_missing_ordinal,
                last_missing_ordinal=self._last_missing_ordinal,
                capture_lost=not self.capture_complete,
                affected_streams=(self.stream_id,),
            )
        )
        self._first_missing_ordinal = self._last_missing_ordinal = None
        reduction = self.reducer.invalidate(event)
        self.lifecycle = "resynchronizing"
        # No failed callback may interrupt owner cleanup or the next recovery.
        try:
            self._emit_events((event,))
        except FeedFault:
            self.capture_complete = False
        try:
            self._publish(reduction.book)
        except FeedFault:
            pass
        self._diagnostic(reason)

    def _start_epoch(self, rest: bool) -> None:
        self.epoch += 1
        self.segment_id = SegmentId(str(uuid4()))
        self.connection_id = str(uuid4())
        capabilities = self.adapter.capabilities(self.instrument, self.key)
        self.source_mode = (
            "rest_snapshot"
            if rest
            else ("ws_delta" if capabilities.deltas else "ws_snapshot")
        )
        if rest:
            self.reducer = make_snapshot_reducer(
                self.adapter,
                self.instrument,
                self.key,
                epoch=self.epoch,
                segment_id=self.segment_id,
                poll_interval=self.config.poll_interval,
            )
        else:
            self.reducer = self.adapter.new_reducer(self.instrument, self.key)
            self.reducer.reset(self.epoch, self.segment_id)
        self.reducer.quality = replace(
            self.reducer.quality, capture_complete=self.capture_complete
        )
        self.queue = asyncio.Queue(self.config.queue_count)
        self.queued_bytes = 0
        self._fault.clear()
        self._fault_reason = ""
        self.last_transport = self.last_valid = time.monotonic()
        self._acked = self._valid = False
        self.lifecycle = "waiting_snapshot"
        self._emit_events(
            (
                self._local(
                    Control("source_change", "rest_fallback" if rest else "ws_connect")
                ),
            )
        )

    def _receive(
        self, payload: bytes, transport: str, endpoint: str, epoch: int
    ) -> None:
        if epoch != self.epoch:
            return
        frame = self.boundary.frame(
            self.stream_id, self.connection_id, epoch, transport, endpoint, payload
        )
        self.last_transport = time.monotonic()
        if self.callbacks.raw:
            try:
                self.callbacks.raw(frame)
            except Exception as exc:
                self.capture_complete = False
                self.diagnostic_detail = _failure_detail(exc)
                self._record_loss(frame.ingest_ordinal)
                self._set_fault("capture_overflow")
                return
        if self._fault.is_set():
            self._record_loss(frame.ingest_ordinal)
            return
        if (
            self.queue.full()
            or self.queued_bytes + len(payload) > self.config.queue_bytes
        ):
            self._record_loss(frame.ingest_ordinal)
            self._set_fault("required_queue_overflow")
            return
        self.queued_bytes += len(payload)
        self.queue.put_nowait(frame)

    def _set_fault(self, reason: str) -> None:
        if not self._fault.is_set():
            self._fault_reason = reason
            self._fault.set()
            self.lifecycle = "resynchronizing"
            if self.book is not None:
                try:
                    self._publish(
                        replace(
                            self.book,
                            quality=replace(
                                self.book.quality,
                                integrity="invalid",
                                lifecycle="resynchronizing",
                                continuous_since_ordinal=None,
                                reason_codes=(reason,),
                                capture_complete=self.capture_complete,
                            ),
                        )
                    )
                except FeedFault:
                    pass

    async def _reader(self, ws, endpoint: str, epoch: int) -> None:
        try:
            async for payload in ws:
                self._receive(
                    payload.encode("utf-8") if isinstance(payload, str) else payload,
                    "ws",
                    endpoint,
                    epoch,
                )
                if self._fault.is_set():
                    return
            self._set_fault("ws_closed")
        except Exception as exc:
            self.diagnostic_detail = _failure_detail(exc)
            self._set_fault("ws_receive:" + _failure_reason(exc))

    async def _snapshot(self, request: TransportRequest, epoch: int) -> None:
        try:
            payload = await self.http_fetch(request)
            self._receive(payload, "rest", request.endpoint_id, epoch)
        except Exception as exc:
            delay = exc.retry_after if isinstance(exc, PublicTransportError) else 0
            if not isinstance(delay, (int, float)) or not 0 <= delay < float("inf"):
                delay = 0
            self._http_retry_until = time.monotonic() + delay
            self.diagnostic_detail = _failure_detail(exc)
            self._set_fault("snapshot:" + _failure_reason(exc))

    async def _heartbeat(self, ws) -> None:
        body = {"hyperliquid": '{"method":"ping"}', "bybit": '{"op":"ping"}'}.get(
            self.key.venue
        )
        protocol_ping = self.key.venue == "binance-global"
        if body is None and not protocol_ping:
            return
        while True:
            await asyncio.sleep(self.config.heartbeat_interval)
            try:
                if body is not None:
                    await ws.send(body)
                else:
                    pong = await ws.ping()
                    await asyncio.wait_for(pong, timeout=self.config.silence_timeout)
                    # Only an observed pong supplies evidence, not the timer or ping send.
                    # Queue it so the consumer remains the reducer's sole owner.
                    self._receive(b"", "local", "ws_pong", self.epoch)
            except Exception:
                self._set_fault("heartbeat_send_failed")
                return

    def _decode_reduce(self, frame: RawFrame):
        decoded = (
            DecodeResult(controls=(Control("heartbeat", "ws_pong"),))
            if frame.transport == "local" and frame.endpoint_id == "ws_pong"
            else self.adapter.decode(frame, self.instrument, self.key)
        )
        if decoded.error:
            raise FeedFault(f"decode:{decoded.error.code}")
        events = wrap_decoded(
            frame, self.instrument, self.key, decoded, self.segment_id, self.source_mode
        )
        # Keep sequence-bearing controls and duplicate prints in the reducer's
        # transaction. Dedup applies only at the normalized downstream boundary.
        return events, self.reducer.apply(events)

    async def _process(self, frame: RawFrame) -> None:
        if frame.connection_epoch != self.epoch:
            self._diagnostic("obsolete_epoch")
            return
        self._pending_ordinal = frame.ingest_ordinal
        work = asyncio.create_task(asyncio.to_thread(self._decode_reduce, frame))
        try:
            events, reduction = await asyncio.shield(work)
        except asyncio.CancelledError:
            await asyncio.gather(work, return_exceptions=True)
            raise
        if self._fault.is_set():
            raise FeedFault(self._fault_reason)
        normalized = []
        accepted = iter(reduction.events)
        accepted_event = next(accepted, None)
        for event in events:
            # Reducer output annotates accepted events (for example checked CRCs).
            # Overlay it in source order without dropping staged, ignored or rejected
            # source events needed to replay the complete transaction and its gap.
            if (
                accepted_event is not None
                and accepted_event.order_key == event.order_key
            ):
                event = accepted_event
                accepted_event = next(accepted, None)
            payload = event.payload
            if isinstance(payload, Control):
                if payload.kind == "rejection":
                    raise FeedFault(f"subscription_rejected:{payload.reason_code}")
                if payload.kind == "ack":
                    self._acked = True
            elif isinstance(payload, Trade):
                dedup_key = (
                    self.key.venue,
                    self.key.api_family,
                    str(self.key.instrument_id),
                    payload.dedup_key,
                )
                if dedup_key in self._dedup:
                    self.duplicate_trades += 1
                    # Preserve receive provenance and explain normalized exclusion.
                    normalized.append(
                        replace(
                            event,
                            payload=Control(
                                "ack", "duplicate_trade", payload.dedup_key[:4096]
                            ),
                        )
                    )
                    continue
                self._dedup[dedup_key] = None
                if len(self._dedup) > self.config.dedup_capacity:
                    self._dedup.popitem(last=False)
            normalized.append(event)
        self._emit_events(tuple(normalized))
        self._pending_ordinal = None
        self._publish(reduction.book)
        if reduction.recovery:
            raise FeedFault(reduction.recovery.reason)
        valid_book = (
            reduction.book is not None
            and reduction.book.quality.integrity == "valid"
            and any(isinstance(event.payload, BookUpdate) for event in events)
        )
        valid_other = self.key.channel != "book" and any(
            not isinstance(event.payload, (Control, Gap)) for event in events
        )
        healthy_heartbeat = (
            self._valid
            and self.reducer.synchronized
            and reduction.book is not None
            and reduction.book.quality.integrity == "valid"
            and reduction.book.quality.source_mode == "ws_delta"
            and reduction.book.quality.capabilities.deltas
            and reduction.book.quality.capabilities.exact_event_flow
            and reduction.book.quality.continuous_since_ordinal is not None
            and any(
                isinstance(event.payload, Control) and event.payload.kind == "heartbeat"
                for event in reduction.events
            )
        )
        if valid_book or valid_other or healthy_heartbeat:
            self._valid = self._acked = True
            self.last_valid = time.monotonic()
            self.lifecycle = (
                "degraded" if self.source_mode == "rest_snapshot" else "live"
            )

    async def _consume(self, started: float, *, require_ack: bool) -> None:
        while True:
            if self._fault.is_set():
                raise FeedFault(self._fault_reason)
            now = time.monotonic()
            if (
                require_ack
                and not self._acked
                and now - started >= self.config.ack_timeout
            ):
                raise FeedFault("ack_timeout")
            if not self._valid and now - started >= self.config.snapshot_timeout:
                raise FeedFault("valid_snapshot_timeout")
            if now - self.last_transport >= self.config.silence_timeout:
                raise FeedFault("transport_silence")
            if self._valid and now - self.last_valid >= self.config.valid_data_timeout:
                raise FeedFault("valid_data_stalled")
            if now - started >= self.config.session_lifetime:
                raise FeedFault("planned_session_rotation")
            try:
                frame = await asyncio.wait_for(
                    self.queue.get(),
                    timeout=min(
                        0.1,
                        self.config.ack_timeout,
                        self.config.snapshot_timeout,
                        self.config.silence_timeout,
                    ),
                )
            except TimeoutError:
                continue
            self.queued_bytes -= len(frame.payload)
            await self._process(frame)

    async def _ws_session(self) -> None:
        endpoint = self.adapter.ws_endpoint(self.instrument, self.key)
        if endpoint is None:
            raise FeedFault("ws_unsupported")
        try:
            validate_public_endpoint(endpoint, websocket=True)
        except ValueError as exc:
            raise FeedFault("invalid_ws_endpoint") from exc
        self._start_epoch(False)
        tasks = []
        try:
            async with self.ws_connect(
                endpoint,
                max_size=MAX_MARKET_DATA_BYTES,
                max_queue=1,
                open_timeout=self.config.ack_timeout,
                ping_interval=self.config.heartbeat_interval,
                ping_timeout=self.config.silence_timeout,
                close_timeout=5,
            ) as ws:
                # Preserve the adapter's exact endpoint and wire body; decode validates
                # combined-stream identity rather than accepting transport-normalized JSON.
                tasks.append(
                    asyncio.create_task(self._reader(ws, endpoint, self.epoch))
                )
                requests = self.adapter.subscription_requests(self.instrument, self.key)
                for request in requests:
                    if request.method != "WS" or request.endpoint_id != endpoint:
                        raise FeedFault("invalid_subscription_transport")
                    if request.body is not None:
                        await ws.send(request.body.decode("utf-8"))
                tasks.append(asyncio.create_task(self._heartbeat(ws)))
                if self.key.venue == "binance-global" and self.key.channel == "book":
                    snapshot = self.adapter.snapshot_request(self.instrument, self.key)
                    if snapshot is None:
                        raise FeedFault("bootstrap_snapshot_unavailable")
                    tasks.append(
                        asyncio.create_task(self._snapshot(snapshot, self.epoch))
                    )
                await self._consume(time.monotonic(), require_ack=bool(requests))
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _rest_session(self) -> None:
        request = self.adapter.snapshot_request(self.instrument, self.key)
        if request is None:
            raise FeedFault("rest_channel_unsupported")
        self._start_epoch(True)
        deadline = max(
            time.monotonic() + self.config.ws_recovery_interval, self._ws_retry_until
        )
        recover_ws = self.adapter.ws_endpoint(self.instrument, self.key) is not None
        while True:
            remaining = (
                deadline - time.monotonic()
                if recover_ws
                else self.config.snapshot_timeout
            )
            if remaining <= 0:
                self._gap("ws_recovery_probe")
                return
            try:
                await asyncio.wait_for(
                    self._snapshot(request, self.epoch),
                    timeout=min(self.config.snapshot_timeout, remaining),
                )
            except TimeoutError:
                if recover_ws and time.monotonic() >= deadline:
                    self._gap("ws_recovery_probe")
                    return
                raise FeedFault("rest_snapshot_timeout") from None
            if self._fault.is_set():
                raise FeedFault(self._fault_reason)
            frame = self.queue.get_nowait()
            self.queued_bytes -= len(frame.payload)
            await self._process(frame)
            if not self._valid:
                raise FeedFault("rest_no_valid_data")
            if recover_ws and time.monotonic() >= deadline:
                self._gap("ws_recovery_probe")
                return
            await asyncio.sleep(
                min(self.config.poll_interval, max(0, deadline - time.monotonic()))
                if recover_ws
                else self.config.poll_interval
            )

    async def run(self) -> None:
        if self._running:
            raise RuntimeError("supervisor already running")
        self._running = True
        failures = 0
        try:
            while True:
                try:
                    ws_supported = (
                        self.adapter.ws_endpoint(self.instrument, self.key) is not None
                    )
                    if not ws_supported:
                        await self._rest_session()
                    else:
                        await self._ws_session()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    failures = 1 if self._valid else failures + 1
                    reason = _failure_reason(exc)
                    if not isinstance(exc, FeedFault):
                        self.diagnostic_detail = _failure_detail(exc)
                    if isinstance(exc, InvalidStatus):
                        status = exc.response.status_code
                        if status in (401, 403, 451):
                            reason = "blocked"
                        elif status in (404, 410):
                            reason = "unsupported"
                        elif status == 429:
                            reason = "rate_limited"
                        delay = retry_after_seconds(
                            exc.response.headers.get("Retry-After")
                        )
                        if reason in ("blocked", "unsupported"):
                            delay = max(delay, self.config.circuit_cooldown)
                        self._ws_retry_until = time.monotonic() + delay
                    self._gap(reason)
                    self.reconnects += 1
                    if reason.endswith(":blocked") or reason == "blocked":
                        self.lifecycle = "blocked"
                    elif reason.endswith(":unsupported") or reason in (
                        "unsupported",
                        "rest_channel_unsupported",
                    ):
                        self.lifecycle = "unsupported"
                    if (
                        failures >= self.config.ws_failures_before_rest
                        and self.adapter.snapshot_request(self.instrument, self.key)
                        is not None
                    ):
                        try:
                            await self._rest_session()
                            failures = 0
                            continue
                        except asyncio.CancelledError:
                            raise
                        except Exception as fallback_error:
                            fallback_reason = "rest:" + _failure_reason(fallback_error)
                            if not isinstance(fallback_error, FeedFault):
                                self.diagnostic_detail = _failure_detail(fallback_error)
                            self._gap(fallback_reason)
                            if fallback_reason.endswith(":blocked"):
                                self.lifecycle = "blocked"
                    if failures >= self.config.circuit_failures:
                        self.lifecycle = "disconnected"
                        self._diagnostic("circuit_open")
                        await asyncio.sleep(
                            max(
                                self.config.circuit_cooldown,
                                self._http_retry_until - time.monotonic(),
                                self._ws_retry_until - time.monotonic(),
                            )
                        )
                        failures = 0
                    else:
                        delay = min(
                            self.config.backoff_max,
                            self.config.backoff_base * 2 ** min(failures - 1, 16),
                        )
                        await asyncio.sleep(
                            max(
                                delay * random.uniform(0.5, 1.5),
                                self._http_retry_until - time.monotonic(),
                                self._ws_retry_until - time.monotonic(),
                            )
                        )
        finally:
            self._gap("owner_stopped")
            self.lifecycle = "disconnected"
            self._running = False
