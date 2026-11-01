"""Bounded public HTTP transport; response bytes are never JSON re-encoded.

Budgets/circuits are shared per event loop and origin. 403/451 are access blocks,
404/410 are unsupported; neither is retried. Retry-After is honored up to a
120-second safety bound (larger values open the circuit rather than retry early).
"""

import asyncio
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import random
import time
from urllib.parse import urlsplit
from weakref import WeakKeyDictionary

import httpx

from .adapters.base import TransportRequest, validate_public_endpoint
from .precision import MAX_MARKET_DATA_BYTES


class PublicTransportError(RuntimeError):
    def __init__(
        self, reason: str, *, status: int | None = None, retry_after: float = 0
    ):
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.retry_after = retry_after


@dataclass
class _OriginBudget:
    next_request: float = 0
    failures: int = 0
    blocked_until: float = 0
    retry_until: float = 0
    block_reason: str = "circuit_open"


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
            timeout=15, follow_redirects=False, trust_env=False
        ) as owned:
            return await fetch_public(request, client=owned, attempts=attempts)
    budget = _budget(request.endpoint_id)
    last: PublicTransportError = PublicTransportError("transport_unavailable")
    for attempt in range(attempts):
        now = time.monotonic()
        if budget.blocked_until > now:
            raise PublicTransportError(
                budget.block_reason, retry_after=budget.blocked_until - now
            )
        # Reserve the slot before yielding: concurrent discoveries share the budget.
        slot = max(now, budget.next_request)
        budget.next_request = slot + 0.1
        await asyncio.sleep(max(0, slot - now))
        while budget.retry_until > time.monotonic():
            if budget.blocked_until > time.monotonic():
                raise PublicTransportError(
                    budget.block_reason,
                    retry_after=budget.blocked_until - time.monotonic(),
                )
            await asyncio.sleep(budget.retry_until - time.monotonic())
        if budget.blocked_until > time.monotonic():
            raise PublicTransportError(
                budget.block_reason, retry_after=budget.blocked_until - time.monotonic()
            )
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
                    raise PublicTransportError("blocked", status=status, retry_after=60)
                if status in (404, 410):
                    raise PublicTransportError("unsupported", status=status)
                if status == 429 or 500 <= status <= 599:
                    delay = retry_after_seconds(response.headers.get("Retry-After"))
                    last = PublicTransportError(
                        "rate_limited" if status == 429 else "server_error",
                        status=status,
                        retry_after=delay,
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
                        raise last
                elif not 200 <= status < 300:
                    raise PublicTransportError("http_rejected", status=status)
                else:
                    payload = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(payload) + len(chunk) > MAX_MARKET_DATA_BYTES:
                            raise PublicTransportError(
                                "response_too_large", status=status
                            )
                        payload.extend(chunk)
                    budget.failures = 0
                    return bytes(payload)
        except httpx.TransportError as exc:
            last = PublicTransportError("network_error")
            last.__cause__ = exc
        budget.failures += 1
        if budget.failures >= 6:
            budget.blocked_until = time.monotonic() + 30
            budget.block_reason = "circuit_open"
            raise PublicTransportError("circuit_open", retry_after=30) from last
        if attempt + 1 < attempts:
            await asyncio.sleep(
                max(
                    last.retry_after,
                    min(8, 0.25 * 2**attempt) * random.uniform(0.5, 1.5),
                )
            )
    raise last
