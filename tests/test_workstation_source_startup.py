"""Operator startup preferences must not turn an offline server into a collector."""

import asyncio
from dataclasses import replace
from decimal import Decimal
import json
import time

from fastapi.testclient import TestClient

from src.workstation.adapter_catalog import AdapterCatalog
from src.workstation.api import MarketDataRequest, create_app
from src.workstation.events import SubscriptionKey
from src.workstation.ingestion import FeedSupervisor
from src.workstation.instruments import MetadataSnapshot, OrderConstraints
from src.workstation.runtime import TerminalRuntime
from src.workstation.subscriptions import SubscriptionRegistry


def test_offline_catalog_declares_demo_and_rejects_requested_live(tmp_path):
    runtime = TerminalRuntime(state_dir=tmp_path)
    with TestClient(create_app(runtime), base_url="http://127.0.0.1") as client:
        catalog = client.get("/api/v1/instruments").json()
        assert catalog["preferred_source_id"] == runtime.demo_id
        assert not any(source["mode"] == "live" for source in catalog["sources"])
        response = client.post(
            "/api/v1/sessions", json={"mode": "live", "source_id": "live"}
        )
        assert response.status_code >= 400
        assert client.get("/api/v1/sessions").json()["sessions"] == []
        assert runtime.registry is None


def test_catalog_refresh_preserves_source_metadata_and_changed_readiness(tmp_path):
    from dataclasses import replace
    from decimal import Decimal

    runtime = TerminalRuntime(state_dir=tmp_path)
    with TestClient(create_app(runtime), base_url="http://127.0.0.1") as client:

        async def catalog_ready():
            await runtime.demo_task

        client.portal.call(catalog_ready)
        source_id = runtime.demo_id
        original = next(
            item
            for item in runtime.source_instruments[source_id]
            if item.tick_size is not None
        )
        instrument_id = str(original.instrument_id)
        first = client.get(
            "/api/v1/instruments", params={"source_id": source_id}
        ).json()
        assert (
            next(
                item
                for item in first["instruments"]
                if item["instrument_id"] == instrument_id
            )["metadata_version"]
            == original.metadata_version
        )
        changed = replace(
            original,
            metadata_version=original.metadata_version + ":updated",
            tick_size=Decimal("0.005"),
        )
        runtime.source_instruments[source_id] = tuple(
            changed if item.instrument_id == original.instrument_id else item
            for item in runtime.source_instruments[source_id]
        )
        refreshed = client.get(
            "/api/v1/instruments", params={"source_id": source_id}
        ).json()
        selected = next(
            item
            for item in refreshed["instruments"]
            if item["instrument_id"] == instrument_id
        )
        assert selected["metadata_version"] == changed.metadata_version
        assert selected["tick_size"] == "0.005"
        runtime.sources[source_id]["status"] = "unavailable"
        runtime.sources[source_id]["reasons"].append("catalog_source_changed")
        unavailable = client.get(
            "/api/v1/instruments", params={"source_id": source_id}
        ).json()
        state = next(item for item in unavailable["sources"] if item["id"] == source_id)
        assert state["status"] == "unavailable"
        assert state["reasons"] == ["catalog_source_changed"]
        union = client.get("/api/v1/instruments").json()
        assert (
            next(
                item
                for item in union["instruments"]
                if item["instrument_id"] == instrument_id
            )["metadata_version"]
            == original.metadata_version
        )


class BybitStartupSupervisor(FeedSupervisor):
    """Native decoder/reducer evidence without external sockets."""

    async def run(self):
        self._running = True
        self._start_epoch(False)
        try:
            if self.key.venue == "bybit":
                symbol = self.instrument.native_symbol
                stamp = time.time_ns() // 1_000_000
                message = (
                    {
                        "topic": f"orderbook.{self.key.depth}.{symbol}",
                        "type": "snapshot",
                        "ts": stamp,
                        "data": {
                            "s": symbol,
                            "u": 2,
                            "seq": 10,
                            "b": [["60000.12345", "0.12345678"]],
                            "a": [["60001.12345", "0.23456789"]],
                        },
                    }
                    if self.key.channel == "book"
                    else {
                        "topic": f"publicTrade.{symbol}",
                        "type": "snapshot",
                        "data": [
                            {
                                "s": symbol,
                                "i": "startup-trade",
                                "S": "Buy",
                                "p": "60001.12345",
                                "v": "0.12345678",
                                "T": stamp,
                            }
                        ],
                    }
                )
                self._receive(
                    json.dumps(message).encode(),
                    "ws",
                    self.adapter.ws_endpoint(self.instrument, self.key),
                    self.epoch,
                )
                frame = self.queue.get_nowait()
                self.queued_bytes -= len(frame.payload)
                await self._process(frame)
            await asyncio.Event().wait()
        finally:
            await self._shutdown()


def test_live_starts_before_metadata_and_enrichment_does_not_relabel_shared_streams(
    tmp_path, monkeypatch
):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        catalog = AdapterCatalog()
        registry = SubscriptionRegistry(
            catalog,
            supervisor_factory=BybitStartupSupervisor,
            lock_path=tmp_path / "collector",
        )

        async def discover():
            entered.set()
            await release.wait()
            verified = replace(
                candidate,
                metadata_version="verified-startup-fixture",
                metadata_status="verified",
                metadata_asof_wall_ns=1,
                metadata_source="synthetic-authoritative-response",
                status="active",
                tick_size=Decimal("0.00001"),
                lot_size=Decimal("0.00000001"),
                constraints=OrderConstraints(),
            )
            catalog.instruments.apply_metadata(
                MetadataSnapshot(
                    verified.metadata_version, 1, verified.metadata_source, (verified,)
                )
            )

        monkeypatch.setattr(catalog, "discover", discover)
        monkeypatch.setattr(
            "src.workstation.runtime.SubscriptionRegistry", lambda: registry
        )
        runtime = TerminalRuntime(state_dir=tmp_path, enable_live=True)
        try:
            await runtime.start()
            await asyncio.wait_for(entered.wait(), 5)
            candidate = next(
                item
                for item in runtime.source_instruments["live"]
                if item.venue == "bybit"
                and item.market_type == "spot"
                and item.base.ticker == "BTC"
            )
            instrument_id = str(candidate.instrument_id)
            assert runtime.source("live")["status"] == "ready"
            assert candidate.tick_size is None and candidate.lot_size is None
            session = await runtime.create("live", "live", instrument_id)

            async def populated_frame(owner):
                async with asyncio.timeout(5):
                    while True:
                        frame = await owner.frame()
                        if frame["book"] and frame["book"]["bids"]:
                            return frame
                        await asyncio.sleep(0.001)

            initial = await populated_frame(session)
            assert not release.is_set()
            assert initial["book"]["bids"][0]["price"] == "60000.12345"
            assert initial["selection"]["metadata_status"] == "streaming_only"
            assert (
                initial["selection"]["metadata_version"] == candidate.metadata_version
            )
            release.set()
            await runtime.metadata_task
            assert catalog.instruments.get(instrument_id).metadata_status == "verified"
            assert (await session.frame())["selection"] == initial["selection"]

            # A recorder/other consumer can keep old trade metadata pinned while
            # the last old book owner leaves and a verified book starts.
            lease = await registry.acquire(
                "old-trade-owner",
                SubscriptionKey.for_instrument(candidate, channel="trades"),
            )
            await runtime.remove(session.id)
            upgraded = await runtime.create("live", "live", instrument_id)
            frame = await populated_frame(upgraded)
            assert frame["selection"]["metadata_status"] == "verified"
            assert (
                registry.describe(lease).instrument.metadata_status == "streaming_only"
            )
            market = await runtime.market_data(
                MarketDataRequest(
                    session_id=upgraded.id, generation=str(upgraded.generation)
                )
            )
            selected = next(
                item
                for item in market["instruments"]
                if item["instrument_id"] == instrument_id
            )
            book = next(
                item
                for item in market["books"]
                if item["key"]["instrument_id"] == instrument_id
            )
            assert selected["metadata_status"] == "verified"
            assert book["metadata_version"] == selected["metadata_version"]
            assert book["eligibility"]["eligible"] is True
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_shutdown_joins_pending_metadata_enrichment(tmp_path, monkeypatch):
    async def scenario():
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def discover(self):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        monkeypatch.setattr(AdapterCatalog, "discover", discover)
        runtime = TerminalRuntime(state_dir=tmp_path, enable_live=True)
        try:
            await runtime.start()
            await asyncio.wait_for(entered.wait(), 5)
            assert runtime.source("live")["status"] == "ready"
        finally:
            await runtime.close()
        assert cancelled.is_set()
        assert runtime.metadata_task.done()

    asyncio.run(scenario())
