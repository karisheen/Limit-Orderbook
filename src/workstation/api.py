"""Loopback-only-by-default terminal HTTP/WebSocket transport, API version 1."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from anyio import CancelScope
from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .precision import decimal
from .runtime import RuntimeFailure, TerminalRuntime


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CreateSession(StrictInput):
    mode: Literal["demo", "replay", "live"] = "demo"
    source_id: str | None = Field(default=None, max_length=256)
    instrument_id: str | None = Field(default=None, max_length=512)


class SessionAction(StrictInput):
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-f0-9]+$")
    generation: str = Field(max_length=20, pattern=r"^[0-9]+$")


class MarketDataRequest(SessionAction):
    after_delivery: str = Field(default="0", max_length=24, pattern=r"^[0-9]+$")
    after_event: int = Field(default=-1, ge=-1, le=10**9)
    limit: int = Field(default=1000, ge=1, le=2000)

    @model_validator(mode="after")
    def initial_cursor(self):
        if int(self.after_delivery) == 0 and self.after_event != -1:
            raise ValueError("initial market cursor event must be -1")
        return self


class RecordingStart(SessionAction):
    scope: Literal["instrument", "cohort"] = "instrument"


class RecordingStop(SessionAction):
    id: str = Field(min_length=32, max_length=32, pattern=r"^[a-f0-9]{32}$")


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


class CandleRequest(SessionAction):
    interval: str = Field(min_length=1, max_length=8, pattern=r"^[1-9][0-9]*[mhdwM]$")
    limit: int = Field(default=300, ge=1, le=500)
    source: Literal["session_trades", "venue_ohlcv"] = "session_trades"
    instrument_ids: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("instrument_ids")
    @classmethod
    def valid_candle_cohort(cls, values):
        if any(not 1 <= len(value) <= 256 for value in values):
            raise ValueError("invalid candle instrument identity")
        if len(values) != len(set(values)):
            raise ValueError("duplicate candle instrument identity")
        return values


class VisualizationRequest(SessionAction):
    """Optional price_min pins a pane's exact, nonnegative lower grid origin.

    It must align to an explicit price_step and cannot request recenter. The
    displayed low is max(0, price_min + price_offset_rows * price_step).
    Omitting it retains the shared legacy auto-fit/recenter behavior.
    """

    start_ns: str | None = Field(default=None, max_length=24, pattern=r"^[0-9]+$")
    end_ns: str | None = Field(default=None, max_length=24, pattern=r"^[0-9]+$")
    price_step: str | None = Field(default=None, max_length=128)
    price_min: str | None = Field(default=None, max_length=128)
    price_offset_rows: int = Field(default=0, ge=-100000, le=100000)
    recenter: bool = False
    bucket_ns: Literal["1000000000", "5000000000", "10000000000"] | None = None
    rows: int = Field(default=128, ge=32, le=256)
    max_columns: int = Field(default=180, ge=1, le=600)
    after_column: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("price_step")
    @classmethod
    def valid_step(cls, value):
        if value is not None:
            decimal(value, positive=True)
        return value

    @model_validator(mode="after")
    def valid_origin(self):
        if self.price_min is not None:
            decimal(self.price_min, nonnegative=True)
            if self.price_step is None:
                raise ValueError("price_min requires explicit price_step")
            if self.recenter:
                raise ValueError("price_min cannot be combined with recenter")
        return self


class LadderRequest(SessionAction):
    price_step: str | None = Field(default=None, max_length=128)
    rows: int = Field(default=20, ge=1, le=100)

    @field_validator("price_step")
    @classmethod
    def valid_step(cls, value):
        if value is not None:
            decimal(value, positive=True)
        return value


class ProposalRiskRequest(SessionAction):
    side: Literal["buy", "sell"]
    entry_price: str = Field(max_length=128)
    stop_price: str = Field(max_length=128)
    base_quantity: str | None = Field(default=None, max_length=128)
    risk_budget: str | None = Field(default=None, max_length=128)
    fee_bps: str | None = Field(default=None, max_length=128)
    fee_provenance: str | None = Field(default=None, min_length=1, max_length=256)
    fee_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")

    @model_validator(mode="after")
    def validate_proposal(self):
        if (self.base_quantity is None) == (self.risk_budget is None):
            raise ValueError("exactly one base quantity or risk budget is required")
        for name in ("entry_price", "stop_price", "base_quantity", "risk_budget"):
            value = getattr(self, name)
            if value is not None:
                decimal(value, positive=True)
        fee_fields = (self.fee_bps, self.fee_provenance, self.fee_date)
        if any(value is not None for value in fee_fields) and not all(
            value is not None for value in fee_fields
        ):
            raise ValueError("fee rate, provenance and date must be supplied together")
        if self.fee_bps is not None:
            if not 0 <= decimal(self.fee_bps) < 10000:
                raise ValueError("fee bps must be nonnegative and below 10000")
            from datetime import date

            assert self.fee_date is not None
            date.fromisoformat(self.fee_date)
        return self


class FeeInput(StrictInput):
    fee_bps: str | None = Field(default=None, max_length=128)
    fee_provenance: str | None = Field(default=None, min_length=1, max_length=256)
    fee_date: str | None = Field(default=None, pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    fee_asset_id: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_fee(self):
        values = (self.fee_bps, self.fee_provenance, self.fee_date)
        if any(value is not None for value in values):
            if not all(value is not None for value in values):
                raise ValueError(
                    "fee rate, provenance and date must be supplied together"
                )
            assert self.fee_bps is not None
            if not 0 <= decimal(self.fee_bps) < 10000:
                raise ValueError("fee bps must be nonnegative and below 10000")
            from datetime import date

            assert self.fee_date is not None
            date.fromisoformat(self.fee_date)
        return self


class AlertQuery(SessionAction):
    event_limit: int = Field(default=20, ge=1, le=100)


class AlertRuleRequest(AlertQuery):
    rule_id: str | None = Field(default=None, min_length=1, max_length=128)
    instrument_id: str | None = Field(default=None, min_length=1, max_length=512)
    kind: Literal[
        "spread_shock",
        "depth_shock",
        "large_executed_print",
        "event_ofi",
        "best_quote_imbalance",
        "persistent_displayed_liquidity",
        "net_cost",
    ]
    direction: Literal["above", "below"]
    threshold: str = Field(max_length=128)
    rearm_threshold: str = Field(max_length=128)
    window_ns: str = Field(default="0", max_length=20, pattern=r"^[0-9]+$")
    cooldown_ns: str = Field(default="0", max_length=20, pattern=r"^[0-9]+$")
    depth_side: Literal["bid", "ask"] = "bid"
    depth_bps: str = Field(default="10", max_length=128)
    sell_instrument_id: str | None = Field(default=None, min_length=1, max_length=512)
    base_quantity: str | None = Field(default=None, max_length=128)
    buy_fees: FeeInput = Field(default_factory=FeeInput)
    sell_fees: FeeInput = Field(default_factory=FeeInput)
    latency_bps: str = Field(default="0", max_length=128)
    rebalance_quote: str = Field(default="0", max_length=128)


class AlertRemoveRequest(AlertQuery):
    rule_id: str = Field(min_length=1, max_length=128)


class AlertEvidenceRequest(SessionAction):
    rule_id: str | None = Field(default=None, min_length=1, max_length=128)
    event_id: str | None = Field(default=None, max_length=20, pattern=r"^[0-9]+$")

    @model_validator(mode="after")
    def validate_target(self):
        if (self.rule_id is None) == (self.event_id is None):
            raise ValueError("choose one current rule or historical event")
        return self


class ContextRequest(SessionAction):
    fx_rates: list[FXInput] = Field(default_factory=list, max_length=16)


class PaperQuery(SessionAction):
    orders_limit: int = Field(default=20, ge=1, le=100)
    fills_limit: int = Field(default=20, ge=1, le=100)


class PaperFeeInput(FeeInput):
    instrument_id: str = Field(min_length=1, max_length=512)


class PaperConfigureRequest(PaperQuery):
    balances: list[InventoryInput] = Field(max_length=64)
    fees: list[PaperFeeInput] = Field(max_length=64)
    feed_latency_ns: str = Field(default="0", max_length=20, pattern=r"^[0-9]+$")
    decision_latency_ns: str = Field(default="0", max_length=20, pattern=r"^[0-9]+$")
    order_latency_ns: str = Field(default="0", max_length=20, pattern=r"^[0-9]+$")


class PaperOrderInput(PaperQuery):
    instrument_id: str | None = Field(default=None, min_length=1, max_length=512)
    side: Literal["buy", "sell"]
    quantity: str = Field(max_length=128)
    order_type: Literal["market", "limit"] = "market"
    limit_price: str | None = Field(default=None, max_length=128)


class PaperCancelRequest(PaperQuery):
    order_id: str = Field(min_length=1, max_length=128)


class PaperRunRequest(PaperQuery):
    run_id: str = Field(min_length=1, max_length=128)


class PaperRunsRequest(SessionAction):
    limit: int = Field(default=20, ge=1, le=100)


class JournalQuery(SessionAction):
    limit: int = Field(default=50, ge=1, le=100)
    all_sources: bool = False
    run_id: str | None = Field(default=None, min_length=1, max_length=128)


class JournalPutRequest(SessionAction):
    entry_id: str | None = Field(default=None, min_length=1, max_length=128)
    rationale: str = Field(min_length=1, max_length=8192)
    entry: str | None = Field(default=None, max_length=128)
    stop: str | None = Field(default=None, max_length=128)
    context: str = Field(default="", max_length=16384)
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    order_id: str | None = Field(default=None, min_length=1, max_length=128)


class ResearchQuery(SessionAction):
    limit: int = Field(default=20, ge=1, le=100)
    attempt_id: str | None = Field(default=None, min_length=1, max_length=128)
    trial_offset: int = Field(default=0, ge=0, le=50000)
    trial_limit: int = Field(default=50, ge=1, le=200)


class VisualAck(SessionAction):
    view_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-f0-9]+$")
    browser_receive_to_paint_ms: float = Field(ge=0, le=60000, allow_inf_nan=False)


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
        sources = tuple(
            {**source, "reasons": list(source["reasons"])}
            for source in runtime.sources.values()
        )
        content = await runtime._catalog_work(
            runtime._catalog_response,
            selected,
            sources,
            "live" if runtime.enable_live else runtime.demo_id,
        )
        # Already encoded off-loop; avoid a second recursive framework walk.
        return Response(content=content, media_type="application/json")

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

    @app.post("/api/v1/market-data")
    async def market_data(request: MarketDataRequest):
        return await runtime.market_data(request)

    @app.get("/api/v1/recordings")
    async def recordings():
        return runtime.captures.listing()

    @app.post("/api/v1/recordings/start")
    async def start_recording(request: RecordingStart):
        return await runtime.recording_action(request)

    @app.post("/api/v1/recordings/stop")
    async def stop_recording(request: RecordingStop):
        return await runtime.recording_action(request, stop=True)

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

    @app.post("/api/v1/visualization")
    async def visualization(request: VisualizationRequest):
        session = runtime.get(request.session_id)
        session.check(request.generation)
        async with session.activity(), session.actions:
            async with session.lock:
                session.check(request.generation)
                snapshot = await session.work(session._visual_snapshot)
            result = await runtime._visual_work(
                snapshot.query, request, request.generation
            )
            async with session.lock:
                session.check(request.generation)
                return await session.work(
                    session._publish_visualization, snapshot, result
                )

    @app.post("/api/v1/candles")
    async def candles(request: CandleRequest):
        return await runtime.candles(request)

    @app.post("/api/v1/ladder")
    async def ladder(request: LadderRequest):
        session = runtime.get(request.session_id)
        session.check(request.generation)
        async with session.activity(), session.actions, session.lock:
            session.check(request.generation)
            return await session.work(session._ladder, request)

    @app.post("/api/v1/proposal-risk")
    async def proposal(request: ProposalRiskRequest):
        session = runtime.get(request.session_id)
        session.check(request.generation)
        async with session.activity(), session.actions, session.lock:
            session.check(request.generation)
            return await session.work(session._proposal_risk, request)

    @app.post("/api/v1/visualization/ack")
    async def visual_ack(request: VisualAck):
        session = runtime.get(request.session_id)
        session.check(request.generation)
        async with session.activity(), session.actions, session.lock:
            session.check(request.generation)
            return await session.work(session._visual_ack, request)

    async def consumer_action(request, method: str):
        session = runtime.get(request.session_id)
        session.check(request.generation)
        async with session.activity(), session.actions, session.lock:
            session.check(request.generation)
            return await session.work(getattr(session, method), request)

    @app.post("/api/v1/alerts/query")
    async def alerts_query(request: AlertQuery):
        return await consumer_action(request, "_alerts_query")

    @app.post("/api/v1/alerts/rules/put")
    async def alert_put(request: AlertRuleRequest):
        return await consumer_action(request, "_alert_put")

    @app.post("/api/v1/alerts/rules/remove")
    async def alert_remove(request: AlertRemoveRequest):
        return await consumer_action(request, "_alert_remove")

    @app.post("/api/v1/alerts/evidence")
    async def alert_evidence(request: AlertEvidenceRequest):
        return await consumer_action(request, "_alert_evidence")

    @app.post("/api/v1/context")
    async def context_query(request: ContextRequest):
        return await consumer_action(request, "_context_query")

    @app.post("/api/v1/paper/query")
    async def paper_query(request: PaperQuery):
        return await consumer_action(request, "_paper_query")

    @app.post("/api/v1/paper/configure")
    async def paper_configure(request: PaperConfigureRequest):
        return await consumer_action(request, "_paper_configure")

    @app.post("/api/v1/paper/orders")
    async def paper_order(request: PaperOrderInput):
        return await consumer_action(request, "_paper_order")

    @app.post("/api/v1/paper/cancel")
    async def paper_cancel(request: PaperCancelRequest):
        return await consumer_action(request, "_paper_cancel")

    @app.post("/api/v1/paper/runs")
    async def paper_runs(request: PaperRunsRequest):
        return await consumer_action(request, "_paper_runs")

    @app.post("/api/v1/paper/archive")
    async def paper_archive(request: PaperRunRequest):
        return await consumer_action(request, "_paper_archive")

    @app.post("/api/v1/paper/resume")
    async def paper_resume(request: PaperRunRequest):
        session = runtime.get(request.session_id)
        session.check(request.generation)
        async with session.activity(), session.actions:
            async with session.lock:
                session.check(request.generation)
                candidate = await session.work(session._paper_candidate, request)
                # The held lock excludes all pacer work. Joining it here keeps
                # the staged prefix fixed through the atomic owner cutover.
                was_playing = session.status == "playing"
                try:
                    await session.stop_play()
                    session.check(request.generation)
                    await session.work(session._resume_paper, candidate)
                except BaseException:
                    if (
                        was_playing
                        and not session.closed
                        and session.status == "playing"
                        and session.play_task is None
                    ):
                        session.play_task = asyncio.create_task(session._play())
                    raise
            return await session.frame()

    @app.post("/api/v1/journal/query")
    async def journal_query(request: JournalQuery):
        return await consumer_action(request, "_journal_query")

    @app.post("/api/v1/journal/put")
    async def journal_put(request: JournalPutRequest):
        return await consumer_action(request, "_journal_put")

    @app.post("/api/v1/research/query")
    async def research_query(request: ResearchQuery):
        session = runtime.get(request.session_id)
        session.check(request.generation)
        # Immutable research history uses the catalog worker, not the replay
        # owner. Hold lifecycle actions, but leave source delivery unblocked.
        async with session.activity(), session.actions:
            session.check(request.generation)
            result = await runtime._catalog_work(runtime._research_query, request)
            session.check(request.generation)
            return {
                **result,
                "session_id": session.id,
                "generation": str(session.generation),
            }

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

    # Serve the complete production bundle, including local fonts, venue icons
    # and distribution licenses. API routes and the no-cache entry stay first.
    if dist.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=dist, follow_symlink=False),
            name="frontend",
        )

    return app
