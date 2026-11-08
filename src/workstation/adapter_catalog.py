"""Lazy venue catalog and explicit dependency evidence, never access by inference."""

import asyncio
from dataclasses import dataclass, replace
from importlib import import_module
import time

from .adapters.base import Adapter
from .events import BookUpdate, RawFrame, SessionId, StreamId, SubscriptionKey, Trade
from .ingestion import FeedCallbacks, FeedSupervisor, SupervisorConfig
from .instruments import Instrument, InstrumentCatalog, InstrumentQuery
from .transport import PublicTransportError, fetch_public


ADAPTER_CLASSES = {
    "binance-global": ("binance", "BinanceGlobalAdapter"),
    "binance-us": ("binance", "BinanceUSAdapter"),
    "coinbase": ("coinbase", "CoinbaseAdapter"),
    "kraken": ("kraken", "KrakenAdapter"),
    "bybit": ("bybit", "BybitAdapter"),
    "kucoin": ("kucoin", "KuCoinAdapter"),
    "hyperliquid": ("hyperliquid", "HyperliquidAdapter"),
    "okx": ("okx", "OKXAdapter"),
    "bitget": ("bitget", "BitgetAdapter"),
    "deribit": ("deribit", "DeribitAdapter"),
}


def create_adapter(venue: str) -> Adapter:
    try:
        module, name = ADAPTER_CLASSES[venue]
    except KeyError as exc:
        raise ValueError(f"unsupported venue: {venue}") from exc
    return getattr(import_module(f".adapters.{module}", __package__), name)()


@dataclass(frozen=True)
class VenueStatus:
    venue: str
    state: str = "unknown"
    detail: str = "not_discovered"
    dependencies: tuple[tuple[str, str], ...] = ()
    checked_wall_ns: int | None = None
    # Evidence applies to this exact representative instrument, not every product.
    probed_instrument_id: str | None = None

    @property
    def fully_usable(self) -> bool:
        evidence = dict(self.dependencies)
        metadata = (
            evidence.get("metadata:product", evidence.get("metadata"))
            if self.probed_instrument_id is not None
            else evidence.get("metadata")
        )
        return metadata == "available" and all(
            evidence.get(name) == "available" for name in ("snapshot", "trades", "ws")
        )


def metadata_status(
    venue: str,
    adapter: Adapter | None,
    *,
    state: str = "available",
    detail: str = "metadata_only",
) -> VenueStatus:
    """Keep family access evidence distinct from transport/product readiness."""
    families = getattr(adapter, "discovery_evidence", ())
    dependencies: tuple[tuple[str, str], ...] = (("metadata", state),)
    if families:
        states = {status for _, status, _ in families}
        if states == {"available"}:
            state = "available"
        elif "available" in states:
            state = "degraded"
            detail = "metadata_partial"
        else:
            state = next(iter(states)) if len(states) == 1 else "unavailable"
            detail = "metadata_unavailable"
        metadata_state = "partial" if state == "degraded" else state
        dependencies = (("metadata", metadata_state),) + tuple(
            (f"metadata:{family}", status) for family, status, _ in families
        )
        if states != {"available"}:
            detail += "; " + "; ".join(
                f"{family}: {status}: {reason}" for family, status, reason in families
            )
    return VenueStatus(venue, state, detail[:4096], dependencies, time.time_ns())


class AdapterCatalog:
    def __init__(self, *, include_binance_us: bool = False):
        self.instruments = InstrumentCatalog()
        self.include_binance_us = include_binance_us
        self.adapters: dict[str, Adapter] = {}
        self.statuses: dict[str, VenueStatus] = {
            venue: VenueStatus(venue)
            for venue in ADAPTER_CLASSES
            if include_binance_us or venue != "binance-us"
        }

    def adapter(self, venue: str) -> Adapter:
        if venue == "binance-us" and not self.include_binance_us:
            raise ValueError("Binance.US is an explicit opt-in distinct venue")
        if venue not in self.adapters:
            self.adapters[venue] = create_adapter(venue)
        return self.adapters[venue]

    @staticmethod
    def canonical_key(key: SubscriptionKey) -> SubscriptionKey:
        """Resolve implicit bounded book depth without changing explicit choices."""
        if key.channel != "book" or key.depth is not None:
            return key
        depths = {
            "binance-global": 5000,
            "binance-us": 20,
            "kraken": 25,
            "bybit": 50,
            "kucoin": 20,
            "hyperliquid": 20,
            "okx": 5,
            "bitget": 5,
            "deribit": 20,
        }
        depth = depths.get(key.venue)
        if key.venue == "binance-global" and key.api_family == "usdm-v1":
            depth = 20
        if key.venue == "hyperliquid" and dict(key.aggregation).get("fast") == "true":
            depth = 5
        return replace(key, depth=depth) if depth is not None else key

    async def discover(
        self, *, probe_dependencies: bool = False, metadata_timeout: float = 30
    ) -> tuple[VenueStatus, ...]:
        """Publish real metadata within independent venue/family time budgets.

        Family-aware adapters retain reachable complete families when another
        family fails or times out. Product probes remain separate evidence.
        """

        async def one(venue: str):
            adapter = None
            metadata_received = False
            try:
                adapter = self.adapter(venue)
                discover_metadata = getattr(adapter, "discover_metadata", None)
                if discover_metadata is None:
                    async with asyncio.timeout(metadata_timeout):
                        snapshot = await adapter.discover()
                else:
                    snapshot = await discover_metadata(timeout=metadata_timeout)
                metadata_received = True
                self.instruments.apply_metadata(snapshot)
                self.statuses[venue] = metadata_status(venue, adapter)
                if probe_dependencies and venue in ("binance-global", "binance-us"):
                    products = self.instruments.list(
                        InstrumentQuery(venue=venue, status="active")
                    )
                    if products:
                        preferred = next(
                            (
                                p
                                for p in products
                                if p.base.ticker == "BTC" and p.quote.ticker == "USDT"
                            ),
                            products[0],
                        )
                        await self.probe(preferred)
            except Exception as exc:
                reason = (
                    exc.reason
                    if isinstance(exc, PublicTransportError)
                    else type(exc).__name__
                )
                state = (
                    reason if reason in ("blocked", "unsupported") else "unavailable"
                )
                self.statuses[venue] = metadata_status(
                    venue,
                    None if metadata_received else adapter,
                    state=state,
                    detail="metadata_timeout"
                    if isinstance(exc, TimeoutError)
                    else str(exc)[:4096],
                )

        await asyncio.gather(*(one(venue) for venue in self.statuses))
        return self.snapshot()

    async def probe(
        self, instrument: Instrument, *, timeout: float = 25
    ) -> VenueStatus:
        """Probe exact product REST book/trades and synchronized WS book + trade.

        Access evidence is not execution eligibility or a venue-wide guarantee.
        Every temporary owner is cancelled and awaited. Normal registry streams
        should be acquired instead when a collector is already running.
        """
        adapter = self.adapter(instrument.venue)
        evidence = dict(
            self.statuses.get(
                instrument.venue, VenueStatus(instrument.venue)
            ).dependencies
        )
        evidence["metadata:product"] = evidence.get(
            f"metadata:{instrument.api_family}", evidence.get("metadata", "unknown")
        )
        book_key = self.canonical_key(SubscriptionKey.for_instrument(instrument))
        trade_key = SubscriptionKey.for_instrument(instrument, channel="trades")
        for name, key in (("snapshot", book_key), ("trades", trade_key)):
            request = adapter.snapshot_request(instrument, key)
            if request is None:
                evidence[name] = "unsupported"
                continue
            try:
                payload = await fetch_public(request)
                frame = RawFrame(
                    SessionId("capability-probe"),
                    StreamId(name),
                    name,
                    0,
                    "probe-clock",
                    1,
                    time.time_ns(),
                    time.monotonic_ns(),
                    "rest",
                    request.endpoint_id,
                    payload,
                )
                result = adapter.decode(frame, instrument, key)
                payload_type = BookUpdate if name == "snapshot" else Trade
                evidence[name] = (
                    "available"
                    if result.error is None
                    and any(isinstance(e.payload, payload_type) for e in result.events)
                    else "unavailable"
                )
            except Exception as exc:
                evidence[name] = (
                    exc.reason
                    if isinstance(exc, PublicTransportError)
                    and exc.reason in ("blocked", "unsupported")
                    else "unavailable"
                )
        ready_book, ready_trade = asyncio.Event(), asyncio.Event()

        def book_ready(book):
            if (
                book.quality.integrity == "valid"
                and book.quality.source_mode != "rest_snapshot"
            ):
                ready_book.set()

        def trades_ready(events):
            if any(
                isinstance(event.payload, Trade)
                and event.source_mode != "rest_snapshot"
                for event in events
            ):
                ready_trade.set()

        config = SupervisorConfig(ws_failures_before_rest=1000)
        supervisors = (
            FeedSupervisor(
                adapter,
                instrument,
                book_key,
                callbacks=FeedCallbacks(book=book_ready),
                config=config,
            ),
            FeedSupervisor(
                adapter,
                instrument,
                trade_key,
                callbacks=FeedCallbacks(events=trades_ready),
                config=config,
            ),
        )
        tasks = [asyncio.create_task(supervisor.run()) for supervisor in supervisors]
        try:
            async with asyncio.timeout(timeout):
                await asyncio.gather(ready_book.wait(), ready_trade.wait())
            evidence["ws"] = "available"
        except TimeoutError:
            evidence["ws"] = "unavailable"
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        status = VenueStatus(
            instrument.venue,
            "degraded",
            "product_dependency_probe",
            tuple(sorted(evidence.items())),
            time.time_ns(),
            str(instrument.instrument_id),
        )
        if status.fully_usable:
            status = replace(status, state="available")
        self.statuses[instrument.venue] = status
        return status

    @property
    def preferred_binance(self) -> str | None:
        # Never reinterpret Binance.US as global or switch an existing lease.
        for venue in ("binance-global", "binance-us"):
            status = self.statuses.get(venue)
            if status is not None and status.fully_usable:
                return venue
        return None

    def snapshot(self) -> tuple[VenueStatus, ...]:
        return tuple(self.statuses.values())
