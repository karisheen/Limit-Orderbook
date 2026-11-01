"""Loopback-only-by-default terminal HTTP/WebSocket transport, API version 1."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from anyio import CancelScope
from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .precision import decimal
from .runtime import RuntimeFailure, TerminalRuntime, dto


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CreateSession(StrictInput):
    mode: Literal["demo", "replay", "live"] = "demo"
    source_id: str | None = Field(default=None, max_length=256)
    instrument_id: str | None = Field(default=None, max_length=512)


class SessionAction(StrictInput):
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-f0-9]+$")
    generation: str = Field(max_length=20, pattern=r"^[0-9]+$")


class SelectInstrument(SessionAction):
    instrument_id: str = Field(min_length=1, max_length=512)


class ReplayAction(SessionAction):
    action: Literal["play", "pause", "step", "speed", "seek", "reset"]
    count: int = Field(default=1, ge=1, le=1000)
    speed: str = Field(default="1", max_length=32)
    delivery_index: str = Field(default="0", max_length=20, pattern=r"^[0-9]+$")

    @field_validator("speed")
    @classmethod
    def valid_speed(cls, value):
        parsed = decimal(value, positive=True)
        if not decimal("0.01") <= parsed <= decimal("100"):
            raise ValueError("speed must be 0.01..100")
        return value


class ClockInput(StrictInput):
    clock_id: str = Field(min_length=1, max_length=256)
    mono_ns: str = Field(max_length=24, pattern=r"^[0-9]+$")
    wall_ns: str | None = Field(default=None, max_length=24, pattern=r"^[0-9]+$")


class FXInput(StrictInput):
    base_asset_id: str = Field(min_length=1, max_length=256)
    quote_asset_id: str = Field(min_length=1, max_length=256)
    bid: str = Field(max_length=128)
    ask: str = Field(max_length=128)
    asof: ClockInput
    source: str = Field(min_length=1, max_length=256)
    conversion_fee_rate: str | None = Field(default=None, max_length=128)
    assumed: bool = True


class InventoryInput(StrictInput):
    venue: str = Field(min_length=1, max_length=256)
    asset_id: str = Field(min_length=1, max_length=256)
    available: str = Field(max_length=128)
    provenance: str = Field(
        default="user supplied prefunded balance", min_length=1, max_length=256
    )
    asof: ClockInput | None = None


class CostRequest(SessionAction):
    side: Literal["buy", "sell"]
    base_quantity: str | None = Field(default=None, max_length=128)
    quote_budget: str | None = Field(default=None, max_length=128)
    fee_bps: str | None = Field(default=None, max_length=128)
    fee_provenance: str | None = Field(default=None, min_length=1, max_length=256)
    fee_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    latency_bps: str = Field(default="0", max_length=128)
    rebalance_quote: str = Field(default="0", max_length=128)
    numeraire: str | None = Field(default=None, min_length=1, max_length=256)
    fx_rates: list[FXInput] = Field(default_factory=list, max_length=16)
    inventory: list[InventoryInput] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_financial_inputs(self):
        if (self.base_quantity is None) == (self.quote_budget is None):
            raise ValueError("exactly one base quantity or quote budget is required")
        for name in (
            "base_quantity",
            "quote_budget",
            "fee_bps",
            "latency_bps",
            "rebalance_quote",
        ):
            value = getattr(self, name)
            if value is not None:
                parsed = decimal(
                    value, positive=name in ("base_quantity", "quote_budget")
                )
                if parsed < 0:
                    raise ValueError(name + " must be nonnegative")
                if name in ("fee_bps", "latency_bps") and parsed > 10000:
                    raise ValueError(name + " exceeds 10000")
        fee_fields = (self.fee_bps, self.fee_provenance, self.fee_date)
        if any(value is not None for value in fee_fields) and not all(
            value is not None for value in fee_fields
        ):
            raise ValueError("fee rate, provenance and date must be supplied together")
        if self.fee_date is not None:
            from datetime import date

            date.fromisoformat(self.fee_date)
        return self


class RenderAck(StrictInput):
    type: Literal["ack"]
    frame_id: str = Field(max_length=24, pattern=r"^[0-9]+$")
    browser_receive_to_paint_ms: float = Field(ge=0, le=60000, allow_inf_nan=False)


class LocalBoundary:
    """Reject untrusted browser origins and bounded request bodies before parsing."""

    def __init__(self, app, origins):
        self.app, self.origins = app, frozenset(origins)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {key.lower(): value for key, value in scope.get("headers", ())}
        origin = headers.get(b"origin", b"").decode("latin-1")
        if (origin and origin not in self.origins) or headers.get(
            b"sec-fetch-site"
        ) == b"cross-site":
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            return await JSONResponse(
                {"detail": "origin not allowed"}, status_code=403
            )(scope, receive, send)
        if scope["type"] == "http":
            # Buffer only this small bounded API body, including chunked input.
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > 65536:
                    return await JSONResponse(
                        {"detail": "request body too large"}, status_code=413
                    )(scope, receive, send)
                if not message.get("more_body", False):
                    break
            sent = False

            async def bounded_receive():
                nonlocal sent
                if not sent:
                    sent = True
                    return {
                        "type": "http.request",
                        "body": bytes(body),
                        "more_body": False,
                    }
                return await receive()

            return await self.app(scope, bounded_receive, send)
        return await self.app(scope, receive, send)


def create_app(
    runtime: TerminalRuntime | None = None,
    *,
    allowed_hosts=("127.0.0.1", "localhost", "[::1]"),
    allowed_origins=(
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ),
    frontend_dist: Path | None = None,
) -> FastAPI:
    runtime = runtime or TerminalRuntime()

    @asynccontextmanager
    async def lifespan(app):
        await runtime.start()
        try:
            yield
        finally:
            await runtime.close()

    app = FastAPI(
        title="Limit Orderbook terminal",
        version="1",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.runtime = runtime
    app.add_middleware(LocalBoundary, origins=allowed_origins)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))

    @app.exception_handler(RuntimeFailure)
    async def runtime_failure(request: Request, exc: RuntimeFailure):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)

    @app.exception_handler(ValueError)
    async def invalid_value(request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)[:1024]}, status_code=422)

    from .instruments import UnknownInstrument

    @app.exception_handler(UnknownInstrument)
    async def unknown_instrument(request: Request, exc: UnknownInstrument):
        return JSONResponse({"detail": "unknown exact instrument"}, status_code=422)

    @app.get("/health/live")
    async def live():
        return {"live": not runtime.closed}

    @app.get("/health/ready")
    async def ready():
        reasons = []
        sources = list(runtime.sources.values())
        if not any(source["status"] == "ready" for source in sources):
            reasons.append("no_ready_source")
        if not runtime.sessions:
            reasons.append("no_synchronized_session")
        for session in tuple(runtime.sessions.values()):
            health = session.health()
            if not health["ready"]:
                reasons.append(session.id + ":" + ",".join(health["reasons"]))
        return JSONResponse(
            {"ready": not reasons, "reasons": reasons, "sources": sources},
            status_code=503 if reasons else 200,
        )

    @app.get("/api/v1/instruments")
    async def instruments(source_id: str | None = Query(default=None, max_length=256)):
        if source_id is not None:
            runtime.source(source_id)
            selected = runtime.source_instruments.get(source_id, ())
        else:
            selected = tuple(runtime.instruments.values())
        return {
            "version": "1",
            "instruments": dto(selected),
            "sources": list(runtime.sources.values()),
        }

    @app.get("/api/v1/sessions")
    async def sessions():
        return {
            "sessions": [
                {
                    "id": session.id,
                    "generation": str(session.generation),
                    "mode": session.mode,
                    "source_id": session.source_id,
                    "status": session.status,
                }
                for session in runtime.sessions.values()
            ]
        }

    @app.post("/api/v1/sessions", status_code=201)
    async def create_session(request: CreateSession):
        session = await runtime.create(
            request.mode, request.source_id, request.instrument_id
        )
        return await session.frame()

    @app.get("/api/v1/sessions/{session_id}")
    async def session_frame(session_id: str):
        return await runtime.get(session_id).frame()

    @app.delete("/api/v1/sessions/{session_id}")
    async def delete_session(session_id: str):
        await runtime.remove(session_id)
        return {"closed": True}

    @app.post("/api/v1/subscriptions")
    async def subscribe(request: SelectInstrument):
        session = runtime.get(request.session_id)
        async with session.activity(), session.actions:
            return await runtime.select(
                session, request.generation, request.instrument_id
            )

    @app.post("/api/v1/replay")
    async def replay(request: ReplayAction):
        session = runtime.get(request.session_id)
        async with session.activity(), session.actions:
            return await session.control(
                request.generation,
                request.action,
                request.count,
                request.speed,
                request.delivery_index,
            )

    @app.get("/api/v1/history")
    async def history(
        session_id: str = Query(max_length=64),
        generation: str = Query(max_length=20, pattern=r"^[0-9]+$"),
        start_delivery: int = Query(default=0, ge=0, le=10**18),
        end_delivery: int = Query(default=10**18, ge=0, le=10**18),
        limit: int = Query(default=100, ge=1, le=1000),
    ):
        session = runtime.get(session_id)
        session.check(generation)
        async with session.activity(), session.lock:
            session.check(generation)
            return await session.work(
                session._history, start_delivery, end_delivery, limit
            )

    @app.post("/api/v1/costs")
    async def costs(request: CostRequest):
        session = runtime.get(request.session_id)
        session.check(request.generation)
        async with session.activity(), session.lock:
            session.check(request.generation)
            return await session.work(session._costs, request)

    @app.websocket("/api/v1/stream")
    async def stream(websocket: WebSocket, session_id: str = Query(max_length=64)):
        try:
            session = runtime.get(session_id)
        except RuntimeFailure:
            await websocket.close(code=1008)
            return
        if session.socket_attached:
            await websocket.close(code=1008)
            return
        session.socket_attached = True
        try:
            await websocket.accept()
        except BaseException:
            with CancelScope(shield=True):
                session.socket_attached = False
                await runtime.remove(session.id)
            raise

        async def publish():
            while True:
                frame = await session.frame()
                # One in-flight snapshot. Slow sockets cannot enqueue analytical
                # events or unbounded frames; a nonresponsive client is evicted.
                await asyncio.wait_for(websocket.send_json(frame), timeout=5)
                await asyncio.sleep(0.2)

        async def acknowledgements():
            while True:
                raw = await websocket.receive_text()
                if len(raw) > 4096:
                    await websocket.close(code=1009)
                    return
                ack = RenderAck.model_validate_json(raw)
                session.ack(ack.frame_id, ack.browser_receive_to_paint_ms)

        tasks = (
            asyncio.create_task(publish()),
            asyncio.create_task(acknowledgements()),
        )
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (WebSocketDisconnect, RuntimeFailure, ValueError, TimeoutError):
            pass
        finally:
            # ASGI servers may use level-triggered cancellation (AnyIO). Shield
            # ownership cleanup, not the connection task's cancellation itself:
            # join publisher/worker work and release leases before propagating it.
            with CancelScope(shield=True):
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                session.socket_attached = False
                await runtime.remove(session.id)
                try:
                    await websocket.close(code=1000)
                except (RuntimeError, WebSocketDisconnect):
                    pass

    dist = frontend_dist or Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if (dist / "assets").is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=dist / "assets", follow_symlink=False),
            name="assets",
        )

    @app.get("/", include_in_schema=False)
    async def terminal():
        index = dist / "index.html"
        if not index.is_file():
            return JSONResponse(
                {
                    "detail": "Frontend not built; run the documented frontend build or Vite development server."
                },
                status_code=503,
            )
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app
