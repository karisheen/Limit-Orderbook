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
