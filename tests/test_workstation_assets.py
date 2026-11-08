"""Production assets load without exposing files outside the frontend bundle."""

from fastapi.testclient import TestClient

from src.workstation.api import create_app
from src.workstation.runtime import TerminalRuntime


def test_production_assets_and_api_routes_preserve_public_boundary(tmp_path):
    dist = tmp_path / "dist"
    (dist / "coins").mkdir(parents=True)
    (dist / "fonts").mkdir()
    (dist / "licenses").mkdir()
    (dist / "index.html").write_text("<title>Orderbook</title>")
    (dist / "coins" / "btc.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    (dist / "fonts" / "Geist.ttf").write_bytes(b"font-data")
    (dist / "licenses" / "NOTICE.txt").write_text("Attribution")
    secret = tmp_path / "private.txt"
    secret.write_text("not public")
    (dist / "leaked.txt").symlink_to(secret)
    runtime = TerminalRuntime(state_dir=tmp_path / "state")
    with TestClient(
        create_app(runtime, frontend_dist=dist), base_url="http://127.0.0.1"
    ) as client:
        index = client.get("/")
        assert index.headers["cache-control"] == "no-cache"
        assert (
            client.get("/coins/btc.svg")
            .headers["content-type"]
            .startswith("image/svg+xml")
        )
        assert client.get("/fonts/Geist.ttf").content == b"font-data"
        assert client.get("/licenses/NOTICE.txt").text == "Attribution"
        assert client.get("/health/live").json() == {"live": True}
        assert client.get("/api/v1/sessions").json() == {"sessions": []}
        assert client.get("/leaked.txt").status_code == 404
        assert client.get("/%2e%2e/private.txt").status_code == 404
        assert client.get("/missing.svg").status_code == 404
