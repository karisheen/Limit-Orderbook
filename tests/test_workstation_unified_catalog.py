"""Catalog consumers retain exact market routes and honest discovery evidence."""

import asyncio
from dataclasses import replace
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from src.workstation import adapter_catalog
from src.workstation.adapter_catalog import AdapterCatalog, VenueStatus
from src.workstation.adapters.base import TransportRequest
from src.workstation.events import BookUpdate, SubscriptionKey, Trade
from src.workstation.instruments import (
    MetadataSnapshot,
    OrderConstraints,
    synthetic_instrument,
)
from src.workstation.streaming_bootstrap import (
    STREAMING_CRYPTO_BASES,
    STREAMING_FAMILIES,
    STREAMING_SOURCE,
    streaming_snapshot,
)
from src.workstation.transport import PublicTransportError


@pytest.mark.parametrize(
    "venue,family,market,endpoint",
    [
        (
            "binance-global",
            "spot-v3",
            "spot",
            "wss://data-stream.binance.vision:443/stream",
        ),
        (
            "binance-global",
            "usdm-v1",
            "perpetual",
            "wss://fstream.binance.com/public/stream",
        ),
        (
            "bybit",
            "v5-spot",
            "spot",
            "wss://stream.bybit.com/v5/public/spot",
        ),
        (
            "bybit",
            "v5-linear",
            "perpetual",
            "wss://stream.bybit.com/v5/public/linear",
        ),
    ],
)
def test_catalog_adapter_dispatches_exact_market_without_spot_perp_aliasing(
    venue, family, market, endpoint
):
    catalog = AdapterCatalog()
    adapter = catalog.adapter(venue)
    instrument = synthetic_instrument(
        venue=venue,
        quote="USDT",
        api_family=family,
        instrument_id=f"{venue}:{family}:{market}:BTCUSDT",
        native_symbol="BTCUSDT",
        market_type=market,
    )
    key = catalog.canonical_key(SubscriptionKey.for_instrument(instrument))
    assert adapter.ws_endpoint(instrument, key) == endpoint
    assert adapter.subscription_requests(instrument, key)[0].endpoint_id == endpoint
    wrong_market = replace(
        instrument, market_type="spot" if market == "perpetual" else "perpetual"
    )
    with pytest.raises(ValueError):
        adapter.capabilities(wrong_market, key)


def test_discovery_bounds_slow_venues_without_hiding_good_metadata(monkeypatch):
    async def scenario():
        instrument = synthetic_instrument(venue="okx")
        cancelled = asyncio.Event()

        class Healthy:
            async def discover(self):
                return MetadataSnapshot("fixture", 1, "fixture", (instrument,))

        class Slow:
            async def discover(self):
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        class Blocked:
            async def discover(self):
                raise PublicTransportError("blocked", status=451)

        class Unsupported:
            async def discover(self):
                raise PublicTransportError("unsupported", status=404)

        adapters = {"okx": Healthy(), "bitget": Slow(), "deribit": Blocked()}
        monkeypatch.setattr(
            adapter_catalog,
            "create_adapter",
            lambda venue: adapters.get(venue, Unsupported()),
        )
        catalog = AdapterCatalog()
        async with asyncio.timeout(1):
            statuses = await catalog.discover(metadata_timeout=0.01)
        status = {item.venue: item for item in statuses}
        assert cancelled.is_set()
        assert catalog.instruments.list() == (instrument,)
        assert status["okx"].state == "available"
        assert status["okx"].detail == "metadata_only"
        assert not status["okx"].fully_usable
        assert status["bitget"].state == "unavailable"
        assert status["bitget"].detail == "metadata_timeout"
        assert status["deribit"].state == "blocked"
        assert status["kraken"].state == "unsupported"
        assert "binance-us" not in status
        assert catalog.preferred_binance is None

    asyncio.run(scenario())


def test_cancelled_discovery_cancels_venue_work_without_inventing_metadata(monkeypatch):
    async def scenario():
        started = asyncio.Event()
        active = set()

        class Pending:
            def __init__(self, venue):
                self.venue = venue

            async def discover(self):
                active.add(self.venue)
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    active.remove(self.venue)

        monkeypatch.setattr(adapter_catalog, "create_adapter", Pending)
        catalog = AdapterCatalog()
        discovery = asyncio.create_task(catalog.discover())
        await started.wait()
        discovery.cancel()
        with pytest.raises(asyncio.CancelledError):
            await discovery
        assert not active
        assert catalog.instruments.list() == ()
        assert all(status.state == "unknown" for status in catalog.snapshot())

    asyncio.run(scenario())


@pytest.mark.parametrize("factory", ["binance-global", "bybit"])
@pytest.mark.parametrize("failure", ["blocked", "timeout"])
def test_catalog_publishes_partial_family_evidence(monkeypatch, factory, failure):
    async def scenario():
        catalog = AdapterCatalog()
        adapter = catalog.adapter(factory)
        spot_family, perp_family = adapter.metadata_families
        instrument = synthetic_instrument(venue=factory, api_family=spot_family)
        cancelled = asyncio.Event()

        async def discover_family(family):
            if family == spot_family:
                return MetadataSnapshot(
                    "reachable-source", 1, "exact-hash", (instrument,)
                )
            if failure == "blocked":
                raise PublicTransportError("blocked", status=451)
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        monkeypatch.setattr(adapter, "discover_family", discover_family)
        catalog.statuses = {factory: catalog.statuses[factory]}
        (status,) = await catalog.discover(metadata_timeout=0.01)
        assert catalog.instruments.list() == (instrument,)
        assert status.state == "degraded"
        assert status.detail.startswith("metadata_partial;")
        assert not status.fully_usable
        assert dict(status.dependencies) == {
            "metadata": "partial",
            f"metadata:{spot_family}": "available",
            f"metadata:{perp_family}": (
                "blocked" if failure == "blocked" else "unavailable"
            ),
        }
        if failure == "blocked":
            assert "HTTP 451" in status.detail
        else:
            assert cancelled.is_set()
            assert "metadata_timeout" in status.detail

    asyncio.run(scenario())


@pytest.mark.parametrize("venue", ["binance-global", "bybit"])
def test_catalog_retains_both_family_failures_without_inventing_products(
    monkeypatch, venue
):
    catalog = AdapterCatalog()
    adapter = catalog.adapter(venue)

    async def failed(family):
        raise PublicTransportError("blocked", status=403)

    monkeypatch.setattr(adapter, "discover_family", failed)
    catalog.statuses = {venue: catalog.statuses[venue]}
    (status,) = asyncio.run(catalog.discover())
    assert catalog.instruments.list() == ()
    assert status.state == "blocked"
    assert not status.fully_usable
    assert "HTTP 403" in status.detail
    assert dict(status.dependencies) == {
        "metadata": "blocked",
        **{f"metadata:{family}": "blocked" for family in adapter.metadata_families},
    }


@pytest.mark.parametrize("venue", ["binance-global", "bybit"])
def test_cancelled_family_discovery_cleans_up_without_publishing(monkeypatch, venue):
    async def scenario():
        catalog = AdapterCatalog()
        adapter = catalog.adapter(venue)
        catalog.statuses = {venue: catalog.statuses[venue]}
        started = asyncio.Event()
        active = set()

        async def pending(family):
            active.add(family)
            if len(active) == 2:
                started.set()
            try:
                await asyncio.Event().wait()
            finally:
                active.remove(family)

        monkeypatch.setattr(adapter, "discover_family", pending)
        task = asyncio.create_task(catalog.discover())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not active
        assert adapter.discovery_evidence == ()
        assert catalog.instruments.list() == ()
        assert catalog.statuses[venue].state == "unknown"

    asyncio.run(scenario())


@pytest.mark.parametrize("family,usable", [("spot-v3", True), ("usdm-v1", False)])
def test_product_probe_uses_selected_family_without_erasing_partial_evidence(
    monkeypatch, family, usable
):
    instrument = synthetic_instrument(venue="binance-global", api_family=family)
    trade = Trade("fixture", "fixture", Decimal("10"), Decimal("1"))

    class FixtureAdapter:
        def snapshot_request(self, instrument, key):
            return TransportRequest("GET", f"https://fixture.invalid/{key.channel}")

        def decode(self, frame, instrument, key):
            payload = BookUpdate("snapshot", ()) if key.channel == "book" else trade
            return SimpleNamespace(
                error=None, events=(SimpleNamespace(payload=payload),)
            )

    class FixtureSupervisor:
        def __init__(self, adapter, instrument, key, *, callbacks, config):
            self.key, self.callbacks = key, callbacks

        async def run(self):
            if self.key.channel == "book":
                self.callbacks.book(
                    SimpleNamespace(
                        quality=SimpleNamespace(
                            integrity="valid", source_mode="ws_delta"
                        )
                    )
                )
            else:
                self.callbacks.events(
                    (SimpleNamespace(payload=trade, source_mode="ws_delta"),)
                )
            await asyncio.Event().wait()

    async def fetch(request):
        return b"fixture"

    monkeypatch.setattr(adapter_catalog, "FeedSupervisor", FixtureSupervisor)
    monkeypatch.setattr(adapter_catalog, "fetch_public", fetch)
    catalog = AdapterCatalog()
    catalog.adapters["binance-global"] = FixtureAdapter()
    catalog.statuses["binance-global"] = VenueStatus(
        "binance-global",
        "degraded",
        "metadata_partial",
        (
            ("metadata", "partial"),
            ("metadata:spot-v3", "available"),
            ("metadata:usdm-v1", "blocked"),
        ),
    )
    status = asyncio.run(catalog.probe(instrument, timeout=1))
    assert status.fully_usable is usable
    assert status.state == ("available" if usable else "degraded")
    assert status.probed_instrument_id == str(instrument.instrument_id)
    assert dict(status.dependencies)["metadata"] == "partial"
    assert dict(status.dependencies)["metadata:usdm-v1"] == "blocked"
    assert dict(status.dependencies)["metadata:product"] == (
        "available" if usable else "blocked"
    )
    assert replace(status, probed_instrument_id=None).fully_usable is False


@pytest.mark.parametrize(
    "venue,family,market,symbol",
    [
        ("okx", "v5-spot", "spot", "BTC-USDT"),
        ("okx", "v5-swap", "perpetual", "BTC-USDT-SWAP"),
        ("bitget", "v2-spot", "spot", "BTCUSDT"),
        ("bitget", "v2-usdt-futures", "perpetual", "BTCUSDT"),
        ("deribit", "jsonrpc-v2", "perpetual", "BTC-PERPETUAL"),
    ],
)
def test_new_venue_requests_preserve_native_market_and_channel_identity(
    venue, family, market, symbol
):
    instrument = synthetic_instrument(
        venue=venue,
        api_family=family,
        market_type=market,
        native_symbol=symbol,
        quote="USD" if venue == "deribit" else "USDT",
        instrument_id=f"{venue}:{family}:{market}:{symbol}",
    )
    if venue == "deribit":
        instrument = replace(
            instrument,
            inverse=True,
            quantity_unit="contracts",
            contract_value_currency=instrument.quote.asset_id,
            contract_multiplier=Decimal("10"),
            lot_size=Decimal("1"),
            settlement=instrument.base,
            settlement_conversion="none",
            contract_specification_source="fixture",
        )
    elif venue == "okx" and market == "perpetual":
        instrument = replace(
            instrument,
            quantity_unit="contracts",
            contract_value_currency=instrument.base.asset_id,
            contract_multiplier=Decimal("0.01"),
            settlement=instrument.quote,
            settlement_conversion="none",
            contract_specification_source="fixture",
        )
    catalog = AdapterCatalog()
    adapter = catalog.adapter(venue)
    for channel in ("book", "trades"):
        key = catalog.canonical_key(
            SubscriptionKey.for_instrument(instrument, channel=channel)
        )
        (request,) = adapter.subscription_requests(instrument, key)
        body = json.loads(request.body)
        if venue == "deribit":
            topic = (
                f"book.{symbol}.none.20.100ms"
                if channel == "book"
                else f"trades.{symbol}.100ms"
            )
            assert body["method"] == "public/subscribe"
            assert body["params"] == {"channels": [topic]}
        else:
            assert body["op"] == "subscribe"
            (argument,) = body["args"]
            assert argument["instId"] == symbol
            assert argument["channel"] == (
                "books5"
                if channel == "book"
                else "trades"
                if venue == "okx"
                else "trade"
            )
            if venue == "bitget":
                assert argument["instType"] == (
                    "SPOT" if market == "spot" else "USDT-FUTURES"
                )


def test_streaming_bootstrap_is_explicit_complete_and_not_exchange_metadata():
    catalog = AdapterCatalog()
    assert catalog.instruments.list() == ()
    candidates = catalog.seed_streaming()
    assert len(candidates) == 116
    assert len(STREAMING_CRYPTO_BASES) == 29
    assert candidates == streaming_snapshot().instruments
    assert catalog.seed_streaming() == ()
    for venue, family, market in STREAMING_FAMILIES:
        products = [
            item
            for item in candidates
            if (item.venue, item.api_family) == (venue, family)
        ]
        assert {item.base.ticker for item in products} == set(STREAMING_CRYPTO_BASES)
        for item in products:
            assert (
                item.instrument_id == f"{venue}:{family}:{market}:{item.native_symbol}"
            )
            assert item.native_symbol == f"{item.base.ticker}USDT"
            assert item.market_type == market
            assert item.tick_size is None and item.lot_size is None
            assert item.constraints == OrderConstraints(
                price_rule="unconstrained", size_rule="unconstrained"
            )
            assert item.metadata_status == "streaming_only"
            assert item.metadata_source == STREAMING_SOURCE
            assert item.metadata_asof_wall_ns == 0
            assert item.status == "unknown" and item.streamable
            assert item.data_access == "unknown"
            assert item.execution_access == "unknown"
            assert item.quantity_unit == "base"
            assert item.contract_multiplier == Decimal("1")
            if market == "perpetual":
                assert item.settlement == item.quote
                assert item.contract_value_currency == item.base.asset_id
                assert item.settlement_conversion == "none"
            key = catalog.canonical_key(SubscriptionKey.for_instrument(item), item)
            assert catalog.adapter(venue).subscription_requests(item, key)
    assert all(status.state == "unknown" for status in catalog.snapshot())
    assert catalog.preferred_binance is None


@pytest.mark.parametrize("venue", ["binance-global", "bybit"])
def test_metadata_denial_retains_stream_candidates_and_separate_evidence(
    monkeypatch, venue
):
    catalog = AdapterCatalog()
    original = catalog.seed_streaming()
    adapter = catalog.adapter(venue)
    catalog.statuses = {venue: catalog.statuses[venue]}

    async def blocked(family):
        raise PublicTransportError("blocked", status=403 if venue == "bybit" else 451)

    monkeypatch.setattr(adapter, "discover_family", blocked)
    (status,) = asyncio.run(catalog.discover())
    assert catalog.instruments.list() == tuple(
        sorted(original, key=lambda item: item.instrument_id)
    )
    assert status.state == "blocked"
    assert (
        "HTTP 403" in status.detail if venue == "bybit" else "HTTP 451" in status.detail
    )
    assert "ws" not in dict(status.dependencies)
    assert not status.fully_usable
    for item in original:
        assert (
            catalog.instruments.get(item.instrument_id, item.metadata_version) is item
        )
        assert item.streamable
    assert catalog.seed_streaming() == ()


@pytest.mark.parametrize("venue", ["binance-global", "bybit"])
@pytest.mark.parametrize("seed_first", [False, True])
def test_authoritative_family_never_resurrects_missing_or_suspended_candidates(
    monkeypatch, venue, seed_first
):
    catalog = AdapterCatalog()
    candidates = streaming_snapshot().instruments
    if seed_first:
        catalog.seed_streaming()
    adapter = catalog.adapter(venue)
    spot, perpetual = adapter.metadata_families
    original = next(
        item
        for item in candidates
        if (item.venue, item.api_family, item.native_symbol) == (venue, spot, "BTCUSDT")
    )
    verified = replace(
        original,
        metadata_version="verified-suspended",
        metadata_status="verified",
        metadata_source="authoritative-fixture",
        metadata_asof_wall_ns=10,
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.001"),
        constraints=OrderConstraints(),
        status="suspended",
    )

    async def partial(family):
        if family == spot:
            return MetadataSnapshot(
                "authoritative-fixture", 10, "complete", (verified,)
            )
        raise PublicTransportError("blocked", status=451)

    monkeypatch.setattr(adapter, "discover_family", partial)
    catalog.statuses = {venue: catalog.statuses[venue]}
    (status,) = asyncio.run(catalog.discover())
    catalog.seed_streaming()
    assert catalog.instruments.get(original.instrument_id) is verified
    assert not verified.streamable
    spot_products = [
        item
        for item in catalog.instruments.list()
        if (item.venue, item.api_family) == (venue, spot)
    ]
    assert not any(item.streamable for item in spot_products)
    assert len(spot_products) == (29 if seed_first else 1)
    assert (
        sum(
            item.streamable
            for item in catalog.instruments.list()
            if (item.venue, item.api_family) == (venue, perpetual)
        )
        == 29
    )
    if seed_first:
        assert (
            catalog.instruments.get(original.instrument_id, original.metadata_version)
            == original
        )
    assert dict(status.dependencies)[f"metadata:{perpetual}"] == "blocked"
    assert "ws" not in dict(status.dependencies)


@pytest.mark.parametrize("seed_first", [False, True])
def test_empty_authoritative_families_are_not_repopulated(monkeypatch, seed_first):
    catalog = AdapterCatalog()
    if seed_first:
        catalog.seed_streaming()
    adapter = catalog.adapter("bybit")

    async def empty(family):
        return MetadataSnapshot(f"authoritative:{family}", 10, family, ())

    monkeypatch.setattr(adapter, "discover_family", empty)
    catalog.statuses = {"bybit": catalog.statuses["bybit"]}
    asyncio.run(catalog.discover())
    catalog.seed_streaming()
    assert not any(
        item.streamable for item in catalog.instruments.list() if item.venue == "bybit"
    )
