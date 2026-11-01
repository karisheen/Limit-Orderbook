"""Exact-JSON, document-owned transport for the supported legacy UI.

No collector, market model, financial arithmetic, or process-wide session cache
belongs here. All economic input/output remains the server's exact JSON text.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

DEFAULT_BACKEND = "http://127.0.0.1:8000"
MAX_RESPONSE_BYTES = 12 * 1024 * 1024
MAX_REQUEST_BYTES = 64 * 1024
REQUEST_DEADLINE_SECONDS = 30


class ApiError(RuntimeError):
    """An actionable transport, API, or stale-presentation failure."""


def loopback_url(value: str) -> str:
    """Reject remote hosts, credentials, redirects and URL path injection."""
    try:
        url = urlsplit(value.strip())
        port = url.port
    except ValueError as exc:
        raise ApiError("Use a loopback URL such as http://127.0.0.1:8000.") from exc
    if (
        url.scheme != "http"
        or url.hostname not in {"127.0.0.1", "localhost", "::1"}
        or url.username is not None
        or url.password is not None
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ApiError(
            "Backend must be an HTTP loopback origin, without credentials or a path."
        )
    return value.strip().rstrip("/")


def exact_text(value: Any) -> str:
    """Show absent evidence as absent, never as a financial zero."""
    return "Unavailable" if value is None else str(value)


def instrument_label(instrument: dict[str, Any]) -> str:
    return " · ".join(
        str(instrument[key])
        for key in (
            "venue",
            "api_family",
            "native_symbol",
            "market_type",
            "instrument_id",
        )
    )


class ApiClient:
    """One HTTP connection pool and API session per Streamlit document.

    Explicit close/reconnect releases the backend session. Vanished documents
    are covered by the backend's HTTP idle reaper, not unreliable UI finalizers.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BACKEND,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = loopback_url(base_url)
        self.session_id: str | None = None
        self.generation: str | None = None
        self.frame: dict[str, Any] | None = None
        self._frame_id = -1
        self._closed = False
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(10.0, connect=2.0, pool=2.0),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            follow_redirects=False,
            trust_env=False,
            headers={"Accept-Encoding": "identity"},
            transport=transport,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        read_timeout: float | None = None,
    ) -> dict[str, Any]:
        if self._closed:
            raise ApiError("This client is closed. Use Connect / reconnect.")
        encoded = None if body is None else json.dumps(body, allow_nan=False).encode()
        if encoded is not None and len(encoded) > MAX_REQUEST_BYTES:
            raise ApiError("Request exceeds the API's 64 KiB limit.")
        started = time.monotonic()
        try:
            with self._http.stream(
                method,
                "/api/v1" + path,
                content=encoded,
                params=params,
                headers={"Content-Type": "application/json"}
                if body is not None
                else None,
                timeout=self._http.timeout
                if read_timeout is None
                else httpx.Timeout(read_timeout, connect=2.0, pool=2.0),
            ) as response:
                data = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() - started > REQUEST_DEADLINE_SECONDS:
                        raise ApiError(
                            "Backend response exceeded the bounded request deadline."
                        )
                    if len(data) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise ApiError(
                            "Backend response exceeded the 12 MiB client limit."
                        )
                    data.extend(chunk)
                try:
                    result = json.loads(data)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise ApiError(
                        f"HTTP {response.status_code}: backend returned non-JSON content."
                    ) from exc
                if not response.is_success:
                    detail = (
                        result.get("detail", result)
                        if isinstance(result, dict)
                        else result
                    )
                    raise ApiError(f"HTTP {response.status_code}: {detail}")
                if not isinstance(result, dict):
                    raise ApiError("Backend returned an invalid JSON object.")
                return result
        except httpx.HTTPError as exc:
            raise ApiError(
                f"Backend unavailable at {self.base_url}: {exc}. "
                "Start `python -m src.workstation.cli serve`, then reconnect."
            ) from exc

    def catalog(self, source_id: str | None = None) -> dict[str, Any]:
        return self._request(
            "GET",
            "/instruments",
            params={"source_id": source_id} if source_id else None,
        )

    def _accept(self, frame: dict[str, Any]) -> dict[str, Any]:
        try:
            session_id = frame["session_id"]
            generation = frame["generation"]
            frame_id = frame["frame_id"]
            if not all(
                isinstance(value, str) and value.isascii() and value.isdecimal()
                for value in (generation, frame_id)
            ):
                raise ValueError("non-exact counters")
            if frame["version"] != "1":
                raise ValueError("unsupported frame version")
            if (
                not isinstance(session_id, str)
                or not session_id
                or frame["type"] != "frame"
            ):
                raise ValueError("invalid session frame")
            if self.session_id is not None and session_id != self.session_id:
                raise ValueError("different session")
            if self.generation is not None and int(generation) < int(self.generation):
                raise ValueError("obsolete generation")
            if int(frame_id) <= self._frame_id:
                raise ValueError("obsolete frame")
        except (KeyError, TypeError, ValueError) as exc:
            self.frame = None
            raise ApiError(
                f"Rejected invalid or reordered backend Frame: {exc}"
            ) from exc
        self.session_id = session_id
        self.generation = generation
        self._frame_id = int(frame_id)
        self.frame = frame
        return frame

    def open(self, mode: str, source_id: str, instrument_id: str) -> dict[str, Any]:
        self.close_session()
        return self._accept(
            self._request(
                "POST",
                "/sessions",
                body={
                    "mode": mode,
                    "source_id": source_id,
                    "instrument_id": instrument_id,
                },
                # Managed captures are verified before the owned reader is usable.
                # Initialization may exceed the ordinary 10-second polling timeout.
                read_timeout=REQUEST_DEADLINE_SECONDS,
            )
        )

    def _identity(self) -> dict[str, str]:
        if self.session_id is None or self.generation is None:
            raise ApiError("No API session. Use Connect / reconnect.")
        return {"session_id": self.session_id, "generation": self.generation}

    def refresh(self) -> dict[str, Any]:
        identity = self._identity()
        self.frame = None
        return self._accept(
            self._request("GET", "/sessions/" + quote(identity["session_id"], safe=""))
        )

    def _action(self, path: str, values: dict[str, Any]) -> dict[str, Any]:
        identity = self._identity()
        self.frame = None
        return self._accept(self._request("POST", path, body={**values, **identity}))

    def select(self, instrument_id: str) -> dict[str, Any]:
        return self._action("/subscriptions", {"instrument_id": instrument_id})

    def replay(self, action: str, **values: Any) -> dict[str, Any]:
        return self._action("/replay", {"action": action, **values})

    def _query(self, path: str, values: dict[str, Any]) -> dict[str, Any]:
        identity = self._identity()
        result = self._request("POST", path, body={**values, **identity})
        if result.get("generation") != identity["generation"]:
            raise ApiError("Discarded analytical result from an obsolete generation.")
        return result

    def ladder(self, **values: Any) -> dict[str, Any]:
        return self._query("/ladder", values)

    def visualization(self, **values: Any) -> dict[str, Any]:
        # Full bounded server projections; never reconstruct/rebin history here.
        return self._query("/visualization", values)

    def costs(self, **values: Any) -> dict[str, Any]:
        return self._query("/costs", values)

    def close_session(self) -> None:
        session_id = self.session_id
        self.session_id = None
        self.generation = None
        self.frame = None
        self._frame_id = -1
        if session_id is not None:
            self._request("DELETE", "/sessions/" + quote(session_id, safe=""))

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.close_session()
        finally:
            self._closed = True
            self._http.close()
