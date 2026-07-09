"""Network clients with centralized reliability policy.

Every outbound call in the app goes through HttpClient (REST) or WsConnection
(websocket). Both are designed to fail with typed exceptions instead of
letting raw network errors propagate into the UI layer.
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
from typing import Any, Callable, Optional

import requests
import websocket

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0
DEFAULT_HEADERS = {"User-Agent": "crypto-lob/1.0"}


class FeedError(Exception):
    """A data fetch failed permanently (after retries)."""


class RateLimitError(FeedError):
    """The venue responded 429 and retries were exhausted."""


class HttpClient:
    """requests wrapper with timeout, capped exponential backoff and jitter.

    Retryable: proxy errors, connection errors, timeouts, HTTP 429, HTTP 5xx,
    and malformed JSON bodies. Non-retryable: other 4xx (fails immediately).
    """

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        backoff_cap: float = 8.0,
        session: Optional[requests.Session] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self._sleep = sleep
        self._session = session or requests.Session()
        self._session.headers.update(DEFAULT_HEADERS)

    def _backoff(self, attempt: int) -> None:
        delay = min(self.backoff_base * (2 ** attempt), self.backoff_cap)
        delay += random.uniform(0, delay * 0.25)
        self._sleep(delay)

    def get_json(self, url: str, params: Optional[dict] = None) -> Any:
        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                response = self._session.get(url, params=params, timeout=self.timeout)
                status = response.status_code
                if status == 429:
                    raise RateLimitError(f"429 rate limited by {url}")
                if 400 <= status < 500:
                    # Client errors other than 429 will not succeed on retry.
                    raise FeedError(f"HTTP {status} from {url}: {response.text[:200]}")
                if status >= 500:
                    raise _RetryableHttpError(f"HTTP {status} from {url}")
                try:
                    return response.json()
                except ValueError as exc:
                    raise _RetryableHttpError(f"malformed JSON from {url}") from exc
            except RateLimitError as exc:
                last_error = exc
            except _RetryableHttpError as exc:
                last_error = exc
            except (
                requests.exceptions.ProxyError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
            ) as exc:
                last_error = exc
            except FeedError:
                raise
            except requests.exceptions.RequestException as exc:
                last_error = exc

            if attempt < self.max_retries - 1:
                self._backoff(attempt)

        if isinstance(last_error, RateLimitError):
            raise last_error
        raise FeedError(f"request to {url} failed after {self.max_retries} attempts: {last_error}") from last_error


class _RetryableHttpError(Exception):
    """Internal marker for responses worth retrying (5xx, bad JSON)."""


class WsConnection:
    """One websocket session: connect, subscribe, pump messages until closed.

    Reconnect supervision lives in the feed worker; this class only reports
    whether the session produced any messages before it ended.
    """

    def __init__(
        self,
        url: str,
        on_message: Callable[[str], None],
        subscribe_message: Optional[str] = None,
        ping_interval: float = 20.0,
        ping_timeout: float = 10.0,
    ) -> None:
        self.url = url
        self._on_message = on_message
        self._subscribe_message = subscribe_message
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._app = None
        self._got_message = False
        self._lock = threading.Lock()

    def run(self) -> bool:
        """Block until the connection closes. Returns True if any message arrived."""

        def on_open(ws) -> None:
            if self._subscribe_message:
                try:
                    ws.send(self._subscribe_message)
                except Exception:
                    logger.exception("failed to send subscribe message to %s", self.url)

        def on_message(ws, raw) -> None:
            self._got_message = True
            try:
                self._on_message(raw)
            except Exception:
                # Parser bugs must not kill the socket pump.
                logger.exception("error handling message from %s", self.url)

        def on_error(ws, error) -> None:
            logger.warning("websocket error from %s: %s", self.url, error)

        app = websocket.WebSocketApp(
            self.url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
        )
        with self._lock:
            self._app = app
        try:
            app.run_forever(ping_interval=self._ping_interval, ping_timeout=self._ping_timeout)
        except Exception as exc:
            logger.warning("websocket run_forever raised for %s: %s", self.url, exc)
        finally:
            with self._lock:
                self._app = None
        return self._got_message

    def close(self) -> None:
        with self._lock:
            app = self._app
        if app is not None:
            try:
                app.close()
            except Exception:
                pass
