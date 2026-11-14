"""Bounded public transports; response bytes are never JSON re-encoded.

Budgets/circuits are shared per event loop and origin. 403/451 are access blocks,
404/410 are unsupported; neither is retried. Retry-After is honored up to a
120-second safety bound (larger values open the circuit rather than retry early).
Actual request starts remain spaced after shared cooldowns, including extensions.
Native WebSocket TLS retains Python's system/environment trust policy. Its
shared, verified context is initialized off-loop before opening each TLS leg.
"""

import asyncio
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from functools import lru_cache
import os
import random
import ssl
from threading import Lock
import time
from urllib.parse import urlsplit
from weakref import WeakKeyDictionary

import httpx
from websockets.asyncio.client import ClientConnection, connect
from websockets.proxy import get_proxy, parse_proxy

from .adapters.base import TransportRequest, validate_public_endpoint
from .ownership import join_owned
from .precision import MAX_MARKET_DATA_BYTES


class PublicTransportError(RuntimeError):
    def __init__(
        self,
        reason: str,
        *,
        status: int | None = None,
        retry_after: float = 0,
        endpoint_id: str | None = None,
        failure_reason: str | None = None,
    ):
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.retry_after = retry_after
        self.endpoint_id = endpoint_id
        self.failure_reason = failure_reason

    def __str__(self):
        reason = super().__str__()
        return f"{reason}: {self.endpoint_id}" if self.endpoint_id else reason

    @property
    def peer_retryable(self) -> bool:
        """Only transport/service failures permit a documented official peer.

        A circuit retains its triggering failure: denied, rate-limited, unknown
        and malformed responses must never become cross-origin retry permission.
        """
        reason = self.failure_reason if self.reason == "circuit_open" else self.reason
        return (reason == "network_error" and self.status is None) or (
            reason == "server_error"
            and self.status is not None
            and 500 <= self.status <= 599
        )


@dataclass
class _OriginBudget:
    next_request: float = 0
    failures: int = 0
    blocked_until: float = 0
    retry_until: float = 0
    block_reason: str = "circuit_open"
    block_status: int | None = None
    block_failure_reason: str | None = None


_BUDGETS: WeakKeyDictionary = WeakKeyDictionary()


def _budget(url: str) -> _OriginBudget:
    origins = _BUDGETS.setdefault(asyncio.get_running_loop(), {})
    return origins.setdefault(urlsplit(url).netloc, _OriginBudget())


def retry_after_seconds(value: str | None, *, now: float | None = None) -> float:
    if not value:
        return 0
    try:
        seconds = float(value)
        if not 0 <= seconds < float("inf"):
            return 0
        return seconds
    except ValueError:
        try:
            return max(
                0,
                parsedate_to_datetime(value).timestamp()
                - (time.time() if now is None else now),
            )
        except (ValueError, TypeError, OverflowError):
            return 0


@lru_cache(maxsize=1)
def _public_ssl_context() -> ssl.SSLContext:
    """Load the public client's verified trust store once, not on every poll.

    The transport never mutates trust configuration after construction. Clients
    still own and close their connections independently; no cookies, credentials,
    retry budgets or connection pools are shared through this cache.
    """
    return httpx.create_ssl_context(trust_env=False)


_WS_SSL_LOCK = Lock()
_WS_SSL_PENDING: WeakKeyDictionary = WeakKeyDictionary()


@lru_cache(maxsize=1)
def _cached_websocket_ssl_context(
    trust_environment: tuple[str | None, str | None, str | None],
) -> ssl.SSLContext:
    # Unlike public HTTP, native WebSockets have always used Python's default
    # system/environment trust, including SSL_CERT_FILE and SSL_CERT_DIR.
    return ssl.create_default_context()


def _load_websocket_ssl_context(
    trust_environment: tuple[str | None, str | None, str | None],
) -> ssl.SSLContext:
    # lru_cache alone allows duplicate concurrent misses. Serialize construction
    # across event loops too, without ever taking this lock on an asyncio loop.
    with _WS_SSL_LOCK:
        return _cached_websocket_ssl_context(trust_environment)


async def _websocket_ssl_context() -> ssl.SSLContext:
    trust_environment = (
        os.environ.get("SSL_CERT_FILE"),
        os.environ.get("SSL_CERT_DIR"),
        os.environ.get("SSLKEYLOGFILE"),
    )
    loop = asyncio.get_running_loop()
    pending = _WS_SSL_PENDING.get(loop)
    if pending is None or pending[0] != trust_environment:
        work = loop.run_in_executor(
            None, _load_websocket_ssl_context, trust_environment
        )
        _WS_SSL_PENDING[loop] = (trust_environment, work)

        def completed(future: asyncio.Future[ssl.SSLContext]) -> None:
            current = _WS_SSL_PENDING.get(loop)
            if current is not None and current[1] is future:
                del _WS_SSL_PENDING[loop]

        work.add_done_callback(completed)
    else:
        work = pending[1]
    # A cancelled session joins initialization but cannot cancel another
    # session's trust load. This worker never touches feed/reducer state.
    return await join_owned(work)


class PublicWebSocketConnect(connect):
    """Native connect with off-loop default trust loading, including proxies."""

    async def open_tcp_connection(self) -> ClientConnection:
        original_kwargs = self.create_connection_kwargs
        original_proxy = self.proxy
        proxy = original_proxy
        if (
            original_kwargs.get("unix", False)
            or original_kwargs.get("sock") is not None
        ):
            proxy = None
        if proxy is True:
            proxy = get_proxy(self.ws_uri)
        kwargs = original_kwargs.copy()
        if self.ws_uri.secure and "ssl" not in kwargs:
            kwargs["ssl"] = await _websocket_ssl_context()
        if (
            proxy is not None
            and parse_proxy(proxy).scheme == "https"
            and "proxy_ssl" not in kwargs
        ):
            kwargs["proxy_ssl"] = await _websocket_ssl_context()
        # Resolve proxy routing per attempt, just like websockets. Restore the
        # original options so redirects can change TLS legs and proxy selection.
        # In particular, ws:// and plain HTTP proxies never receive ssl options.
        self.create_connection_kwargs = kwargs
        self.proxy = proxy
        try:
            return await super().open_tcp_connection()
        finally:
            self.create_connection_kwargs = original_kwargs
            self.proxy = original_proxy


async def fetch_public(
    request: TransportRequest,
    *,
    client: httpx.AsyncClient | None = None,
    attempts: int = 3,
) -> bytes:
    """Execute a credential-free public request with bounded response and retry work."""
    if request.method not in ("GET", "POST"):
        raise ValueError("HTTP transport requires GET or POST")
    validate_public_endpoint(request.endpoint_id)
    if not 1 <= attempts <= 10:
        raise ValueError("attempts must be between 1 and 10")
    if client is None:
        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=False,
            trust_env=False,
            verify=_public_ssl_context(),
        ) as owned:
            return await fetch_public(request, client=owned, attempts=attempts)
    budget = _budget(request.endpoint_id)
    last = PublicTransportError(
        "transport_unavailable", endpoint_id=request.endpoint_id
    )
    for attempt in range(attempts):
        while True:
            now = time.monotonic()
            if budget.blocked_until > now:
                raise PublicTransportError(
                    budget.block_reason,
                    status=budget.block_status,
                    failure_reason=budget.block_failure_reason,
                    endpoint_id=request.endpoint_id,
                    retry_after=budget.blocked_until - now,
                )
            delay = max(budget.retry_until, budget.next_request) - now
            if delay > 0:
                await asyncio.sleep(delay)
                # A response may extend the cooldown, or another waiter may
                # take the next slot. Recheck both before reserving a start.
                continue
            # No yield between eligibility and reservation: cooldown waiters
            # share actual start times instead of expiring pre-reserved slots.
            budget.next_request = now + 0.1
            break
        if (
            client.auth is not None
            or client.cookies
            or any(
                name.lower()
                not in (
                    "accept",
                    "accept-encoding",
                    "connection",
                    "content-type",
                    "user-agent",
                )
                for name in client.headers
            )
        ):
            raise ValueError(
                "public HTTP client must not supply credentials or private headers"
            )
        try:
            async with client.stream(
                request.method,
                request.endpoint_id,
                content=request.body,
                headers=dict(request.headers),
                timeout=15,
                auth=None,
                follow_redirects=False,
            ) as response:
                status = response.status_code
                if status in (401, 403, 451):
                    budget.blocked_until = time.monotonic() + 60
                    budget.block_reason = "blocked"
                    budget.block_status = status
                    budget.block_failure_reason = "blocked"
                    raise PublicTransportError(
                        "blocked",
                        status=status,
                        retry_after=60,
                        endpoint_id=request.endpoint_id,
                    )
                if status in (404, 410):
                    raise PublicTransportError(
                        "unsupported", status=status, endpoint_id=request.endpoint_id
                    )
                if status == 429 or 500 <= status <= 599:
                    delay = retry_after_seconds(response.headers.get("Retry-After"))
                    last = PublicTransportError(
                        "rate_limited" if status == 429 else "server_error",
                        status=status,
                        retry_after=delay,
                        endpoint_id=request.endpoint_id,
                    )
                    # Other callers cannot jump ahead of Retry-After either.
                    budget.next_request = max(
                        budget.next_request, time.monotonic() + delay
                    )
                    budget.retry_until = max(
                        budget.retry_until, time.monotonic() + delay
                    )
                    if delay > 120:
                        budget.blocked_until = time.monotonic() + delay
                        budget.block_reason = "circuit_open"
                        budget.block_status = status
                        budget.block_failure_reason = last.reason
                        raise last
                elif not 200 <= status < 300:
                    raise PublicTransportError(
                        "http_rejected", status=status, endpoint_id=request.endpoint_id
                    )
                else:
                    payload = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(payload) + len(chunk) > MAX_MARKET_DATA_BYTES:
                            raise PublicTransportError(
                                "response_too_large",
                                status=status,
                                endpoint_id=request.endpoint_id,
                            )
                        payload.extend(chunk)
                    budget.failures = 0
                    return bytes(payload)
        except httpx.TransportError as exc:
            last = PublicTransportError(
                "network_error", endpoint_id=request.endpoint_id
            )
            last.__cause__ = exc
        budget.failures += 1
        if budget.failures >= 6:
            budget.blocked_until = time.monotonic() + 30
            budget.block_reason = "circuit_open"
            budget.block_status = last.status
            budget.block_failure_reason = last.reason
            raise PublicTransportError(
                "circuit_open",
                status=last.status,
                retry_after=30,
                endpoint_id=request.endpoint_id,
                failure_reason=last.reason,
            ) from last
        if attempt + 1 < attempts:
            await asyncio.sleep(
                max(
                    last.retry_after,
                    min(8, 0.25 * 2**attempt) * random.uniform(0.5, 1.5),
                )
            )
    raise last
