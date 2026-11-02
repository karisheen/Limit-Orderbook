"""Operator startup preferences must not turn an offline server into a collector."""

from fastapi.testclient import TestClient

from src.workstation.api import create_app
from src.workstation.runtime import TerminalRuntime


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
