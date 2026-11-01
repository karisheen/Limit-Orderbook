"""Bounded, cancellable public-data transports for the legacy Streamlit path.

REST honors Retry-After (seconds or HTTP date), including the final failed
attempt. WebSocket recovery means a validated book, never an arbitrary frame.
"""

from __future__ import annotations

import logging
import math
import random
import threading
import time
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Optional

import requests
import websocket

from src.workstation.precision import exact_json_loads

logger = logging.getLogger(__name__)
DEFAULT_TIMEOUT = 10.0
DEFAULT_HEADERS = {"User-Agent": "crypto-lob/1.0"}


class FeedError(Exception):
    """A data fetch failed permanently (after retries)."""


class RateLimitError(FeedError):
    """Rate limited; retry_after is the venue's remaining cooldown in seconds."""

    def __init__(self, message: str, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def retry_after_seconds(value: Optional[str], now: Optional[float] = None) -> float:
    if not value:
        return 0.0
    try:
        delay = float(value)
    except (ValueError, TypeError):
        try:
            delay = parsedate_to_datetime(value).timestamp() - (
                time.time() if now is None else now
            )
        except (ValueError, TypeError, OverflowError):
            return 0.0
    return max(0.0, delay) if math.isfinite(delay) else 0.0


class HttpClient:
    """Finite retries with jitter, venue cooldowns and interruptible waits.

    An in-flight requests call finishes within its connect/read timeout; stop
    interrupts retry waits immediately. The owning worker closes the session.
    """

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        backoff_cap: float = 8.0,
        session: Optional[requests.Session] = None,
        sleep: Callable[[float], None] = time.sleep,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self._sleep = sleep
        self._stop_event = stop_event
        self._session = session or requests.Session()
        self._session.headers.update(DEFAULT_HEADERS)

    def close(self) -> None:
        self._session.close()

    def _check_cancelled(self) -> None:
        if self._stop_event is not None and self._stop_event.is_set():
            raise FeedError("request cancelled")

    def _backoff(self, attempt: int, minimum: float = 0.0) -> None:
        delay = min(self.backoff_base * (2**attempt), self.backoff_cap)
        delay = max(minimum, delay + random.uniform(0, delay * 0.25))
        if self._stop_event is None:
            self._sleep(delay)
        else:
            self._stop_event.wait(delay)
            self._check_cancelled()

    def get_json(self, url: str, params: Optional[dict] = None) -> Any:
        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries):
            self._check_cancelled()
            cooldown = 0.0
            try:
                response = self._session.get(url, params=params, timeout=self.timeout)
                status = response.status_code
                cooldown = retry_after_seconds(response.headers.get("Retry-After"))
                if status == 429:
                    raise RateLimitError(f"429 rate limited by {url}", cooldown)
                if 400 <= status < 500:
                    raise FeedError(f"HTTP {status} from {url}: {response.text[:200]}")
                if status >= 500:
                    raise _RetryableHttpError(f"HTTP {status} from {url}", cooldown)
                try:
                    return exact_json_loads(response.content)
                except ValueError as exc:
                    raise _RetryableHttpError(f"malformed JSON from {url}") from exc
            except (RateLimitError, _RetryableHttpError) as exc:
                last_error = exc
            except FeedError:
                raise
            except requests.exceptions.RequestException as exc:
                last_error = exc
            if attempt < self.max_retries - 1:
                self._backoff(attempt, cooldown)
        if isinstance(last_error, RateLimitError):
            raise last_error
        if isinstance(last_error, _RetryableHttpError) and last_error.retry_after:
            raise RateLimitError(
                str(last_error), last_error.retry_after
            ) from last_error
        raise FeedError(
            f"request to {url} failed after {self.max_retries} attempts: {last_error}"
        ) from last_error


class _RetryableHttpError(Exception):
    def __init__(self, message: str, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class WsConnection:
    """One finite-deadline socket session, owned by its feed worker.

    The callback returns True only for a validated synchronized snapshot.
    Initial snapshot/ack progress is bounded by snapshot_timeout; subsequent
    heartbeat-only sessions expire at the same book-observation deadline.
    Transport silence and ping/pong have independent deadlines. No helper
    thread survives connection replacement.
    """

    def __init__(
        self,
        url: str,
        on_message: Callable[[str], bool],
        subscribe_message: Optional[str] = None,
        ping_interval: float = 20.0,
        ping_timeout: float = 10.0,
        snapshot_timeout: float = 15.0,
        silence_timeout: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        subscribe_messages: tuple[str, ...] = (),
    ) -> None:
        self.url = url
        self._on_message = on_message
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._snapshot_timeout = snapshot_timeout
        self._silence_timeout = silence_timeout
        self._subscribe_messages = subscribe_messages or (
            (subscribe_message,) if subscribe_message else ()
        )
        self._clock = clock
        self._socket = None
        self._closed = threading.Event()
        self._lock = threading.Lock()

    def run(self) -> bool:
        got_book = False
        if self._closed.is_set():
            return False
        sock = None
        try:
            sock = websocket.create_connection(self.url, timeout=DEFAULT_TIMEOUT)
            with self._lock:
                self._socket = sock
            if self._closed.is_set():
                return False
            sock.settimeout(1.0)
            for message in self._subscribe_messages:
                sock.send(message)
            last_transport = last_book = last_ping = self._clock()
            awaiting_pong = None
            while not self._closed.is_set():
                now = self._clock()
                if now - last_book >= self._snapshot_timeout:
                    raise FeedError("websocket valid snapshot deadline exceeded")
                if now - last_transport >= self._silence_timeout:
                    raise FeedError("websocket transport silence deadline exceeded")
                if (
                    awaiting_pong is not None
                    and now - awaiting_pong >= self._ping_timeout
                ):
                    raise FeedError("websocket heartbeat deadline exceeded")
                if awaiting_pong is None and now - last_ping >= self._ping_interval:
                    sock.ping()
                    awaiting_pong = last_ping = now
                try:
                    opcode, raw = sock.recv_data(control_frame=True)
                except websocket.WebSocketTimeoutException:
                    continue
                last_transport = self._clock()
                if opcode == websocket.ABNF.OPCODE_CLOSE:
                    break
                if opcode == websocket.ABNF.OPCODE_PONG:
                    awaiting_pong = None
                elif opcode == websocket.ABNF.OPCODE_TEXT:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    if self._on_message(raw):
                        got_book = True
                        last_book = last_transport
        except (websocket.WebSocketException, OSError, ValueError, FeedError) as exc:
            logger.warning("websocket session ended for %s: %s", self.url, exc)
        finally:
            with self._lock:
                self._socket = None
            if sock is not None:
                sock.close()
        return got_book

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            sock = self._socket
        if sock is not None:
            # shutdown interrupts recv; run() owns the final close.
            try:
                sock.shutdown()
            except (OSError, websocket.WebSocketException) as exc:
                logger.debug("socket already closing for %s: %s", self.url, exc)
