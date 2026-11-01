"""Offline consumer boundaries over real full-scenario analytics/replay paths."""

import asyncio
from dataclasses import replace
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from src.workstation.api import create_app
from src.workstation.demo import create_demo
from src.workstation.demo_memory import MemoryReplaySource
from src.workstation.replay import ReplaySession
from src.workstation.runtime import TerminalRuntime


@pytest.fixture(scope="module")
def terminal():
    runtime = TerminalRuntime(max_sessions=2)
    with TestClient(create_app(runtime), base_url="http://127.0.0.1") as client:
        yield client


@pytest.fixture(autouse=True)
def clean_sessions(terminal):
    yield
    for session in terminal.get("/api/v1/sessions").json()["sessions"]:
        terminal.delete("/api/v1/sessions/" + session["id"])


def new_session(client):
    response = client.post("/api/v1/sessions", json={"mode": "demo"})
    assert response.status_code == 201, response.text
    return response.json()


def action(client, frame, action, **options):
    return client.post(
        "/api/v1/replay",
        json={
            "session_id": frame["session_id"],
            "generation": frame["generation"],
            "action": action,
            **options,
        },
    )


def test_full_demo_is_honest_exact_and_ready(terminal):
    assert terminal.get("/health/live").json() == {"live": True}
    assert terminal.get("/health/ready").status_code == 503
    frame = new_session(terminal)
    assert frame["source"]["synthetic"] is True
    assert frame["source"]["recording"] is False
    assert frame["source"]["streams"] == 24
    assert frame["source"]["duration_ns"] == "180000000000"
    assert int(frame["replay"]["total_deliveries"]) > 180 * 24
    assert frame["book"] is not None
    assert isinstance(frame["book"]["bids"][0]["price"], str)
    assert isinstance(frame["book"]["asof"]["ingest_ordinal"], str)
    assert frame["analytics"]["instrument_id"] == frame["selection"]["instrument_id"]
    assert terminal.get("/health/ready").status_code == 200


def test_clients_seek_and_selection_are_independent_without_future_history(terminal):
    first, second = new_session(terminal), new_session(terminal)
    initial_second = second["replay"]["cursor"]
    stepped = action(terminal, first, "step", count=40).json()
    assert int(stepped["replay"]["cursor"]["delivery_index"]) > int(
        first["replay"]["cursor"]["delivery_index"]
    )
    assert (
        terminal.get("/api/v1/sessions/" + second["session_id"]).json()["replay"][
            "cursor"
        ]
        == initial_second
    )
    reset = action(terminal, stepped, "reset").json()
    assert reset["book"] is None
    assert reset["analytics"] is None
    assert reset["replay"]["cursor"]["delivery_index"] == "0"
    history = terminal.get(
        "/api/v1/history",
        params={
            "session_id": first["session_id"],
            "generation": reset["generation"],
            "end_delivery": 999999,
        },
    ).json()
    assert history["events"] == []
    assert action(terminal, first, "step").status_code == 409
    catalog = terminal.get("/api/v1/instruments").json()["instruments"]
    eth = next(
        item
        for item in catalog
        if item["venue"] == "coinbase" and item["base"]["ticker"] == "ETH"
    )
    selected = terminal.post(
        "/api/v1/subscriptions",
        json={
            "session_id": second["session_id"],
            "generation": second["generation"],
            "instrument_id": eth["instrument_id"],
        },
    ).json()
    assert selected["book"]["instrument_id"] == eth["instrument_id"]
    assert (
        terminal.get("/api/v1/sessions/" + first["session_id"]).json()["selection"]
        == first["selection"]
    )


def test_costs_use_real_depth_and_explicit_fee_provenance(terminal):
    frame = new_session(terminal)
    request = {
        "session_id": frame["session_id"],
        "generation": frame["generation"],
        "side": "buy",
        "quote_budget": "500",
    }
    gross = terminal.post("/api/v1/costs", json=request)
    assert gross.status_code == 200, gross.text
    estimate = gross.json()["estimate"]
    assert Decimal(estimate["filled_base_quantity"]) > 0
    assert estimate["fee_amount"] is None
    assert estimate["effective_price"] is None
    net = terminal.post(
        "/api/v1/costs",
        json={
            **request,
            "fee_bps": "10",
            "fee_provenance": "explicit user assumption",
            "fee_date": "2026-11-01",
        },
    )
    assert net.status_code == 200, net.text
    assert Decimal(net.json()["estimate"]["fee_amount"]) > 0
    assert Decimal(net.json()["estimate"]["effective_price"]) > Decimal(
        net.json()["estimate"]["vwap"]
    )
    assert net.json()["matrix"]
    assert (
        terminal.post(
            "/api/v1/costs", json={**request, "base_quantity": "1"}
        ).status_code
        == 422
    )
    assert (
        terminal.post("/api/v1/costs", json={**request, "fee_bps": "0"}).status_code
        == 422
    )


def test_origin_capacity_bounds_and_released_capacity(terminal):
    assert (
        terminal.post(
            "/api/v1/sessions", json={}, headers={"origin": "https://evil.invalid"}
        ).status_code
        == 403
    )
    assert (
        terminal.get("/health/live", headers={"host": "evil.invalid"}).status_code
        == 400
    )
    assert terminal.post("/api/v1/sessions", content=b"x" * 65537).status_code == 413
    one, two = new_session(terminal), new_session(terminal)
    assert terminal.post("/api/v1/sessions", json={}).status_code == 503
    assert action(terminal, one, "step", count=1001).status_code == 422
    assert action(terminal, one, "speed", speed="NaN").status_code == 422
    assert (
        terminal.post(
            "/api/v1/sessions", json={"mode": "replay", "source_id": "/etc/passwd"}
        ).status_code
        == 404
    )
    terminal.delete("/api/v1/sessions/" + two["session_id"])
    assert new_session(terminal)["session_id"] != two["session_id"]


def test_websocket_ack_and_disconnect_release_session(terminal):
    frame = new_session(terminal)
    with terminal.websocket_connect(
        "ws://127.0.0.1/api/v1/stream?session_id=" + frame["session_id"]
    ) as socket:
        first = socket.receive_json()
        socket.send_json(
            {
                "type": "ack",
                "frame_id": first["frame_id"],
                "browser_receive_to_paint_ms": 1.25,
            }
        )
        second = socket.receive_json()
        assert int(second["frame_id"]) > int(first["frame_id"])
        assert second["telemetry"]["publish_to_ack_upper_bound_ms"] is not None
        assert second["telemetry"]["browser_receive_to_paint_ms"] == 1.25
    assert terminal.get("/api/v1/sessions/" + frame["session_id"]).status_code == 404


def test_memory_source_uses_same_raw_and_normalized_reducer_and_backward_seek():
    source = MemoryReplaySource.demo()
    raw, normalized = ReplaySession(source, mode="raw"), ReplaySession(source)
    try:
        raw.step(200)
        normalized.step(200)
        assert raw.book_hash == normalized.book_hash
        initial = normalized.state_hash
        normalized.step(50)
        normalized.seek_delivery(200)
        assert normalized.state_hash == initial
        assert normalized.cursor.delivery_index == 200
        assert not hasattr(source, "manifest")
        assert source.descriptor.recording is False
    finally:
        raw.close()
        normalized.close()


def test_managed_recording_readers_have_independent_thread_owners(tmp_path):
    async def capture():
        with await create_demo(tmp_path, duration_seconds=2) as result:
            return result.reader.path

    path = asyncio.run(capture())
    runtime = TerminalRuntime(captures=(path,))
    with TestClient(create_app(runtime), base_url="http://127.0.0.1") as client:
        # Catalog verification is off-loop and independent of demo first data.
        demo = new_session(client)
        client.delete("/api/v1/sessions/" + demo["session_id"])

        async def catalog_ready():
            await runtime.demo_task

        client.portal.call(catalog_ready)
        sources = client.get("/api/v1/instruments").json()["sources"]
        capture_source = next(
            source for source in sources if source["mode"] == "replay"
        )
        assert capture_source["status"] == "ready"
        assert capture_source["synthetic"] is True
        assert capture_source["recording"] is True
        one = client.post(
            "/api/v1/sessions",
            json={"mode": "replay", "source_id": capture_source["id"]},
        ).json()
        two = client.post(
            "/api/v1/sessions",
            json={"mode": "replay", "source_id": capture_source["id"]},
        ).json()
        scoped = client.get(
            "/api/v1/instruments", params={"source_id": capture_source["id"]}
        ).json()
        selected_metadata = next(
            item
            for item in scoped["instruments"]
            if item["instrument_id"] == one["selection"]["instrument_id"]
        )
        assert selected_metadata["metadata_version"] == one["book"]["metadata_version"]
        assert (
            client.get(
                "/api/v1/instruments", params={"source_id": "/arbitrary/path"}
            ).status_code
            == 404
        )
        assert action(client, one, "step", count=5).status_code == 200
        assert action(client, two, "reset").status_code == 200
        assert (
            client.get("/api/v1/sessions/" + one["session_id"]).json()["book"]
            is not None
        )


def test_public_recorded_catalog_does_not_classify_negated_prose_as_synthetic(
    tmp_path, monkeypatch
):
    async def capture():
        with await create_demo(tmp_path, duration_seconds=1) as result:
            return result.reader.path

    path = asyncio.run(capture())
    runtime = TerminalRuntime(captures=(path,))
    descriptor = runtime._capture_descriptor

    def public_metadata(path):
        manifest, identity = descriptor(path)
        # Exercise classification of public-producer metadata independently from
        # fixture content. Do not open a session under this deliberately changed pin.
        return replace(
            manifest,
            config=(("capture_scope", "selected_public_book_and_trades"),),
            provenance="Actual public exchange bytes; no synthetic overlays.",
        ), identity

    monkeypatch.setattr(runtime, "_capture_descriptor", public_metadata)
    with TestClient(create_app(runtime), base_url="http://127.0.0.1") as client:

        async def catalog_ready():
            await runtime.demo_task

        client.portal.call(catalog_ready)
        source = next(
            source
            for source in client.get("/api/v1/instruments").json()["sources"]
            if source["mode"] == "replay"
        )
        assert source["synthetic"] is False
        assert source["recording"] is True


def test_controls_join_playback_and_reject_future_or_unknown_selection(terminal):
    frame = new_session(terminal)
    playing = action(terminal, frame, "play").json()
    assert playing["status"] == "playing"
    sped = action(terminal, playing, "speed", speed="2").json()
    assert sped["status"] == "playing"
    assert sped["replay"]["speed"] == "2"
    paused = action(terminal, sped, "pause").json()
    assert paused["status"] == "paused"
    assert (
        terminal.get("/api/v1/sessions/" + frame["session_id"]).json()["replay"][
            "cursor"
        ]
        == paused["replay"]["cursor"]
    )
    assert (
        action(terminal, paused, "seek", delivery_index="999999999").status_code == 422
    )
    assert (
        terminal.post(
            "/api/v1/subscriptions",
            json={
                "session_id": frame["session_id"],
                "generation": frame["generation"],
                "instrument_id": "not-an-instrument",
            },
        ).status_code
        == 422
    )
    unchanged = terminal.get("/api/v1/sessions/" + frame["session_id"]).json()
    assert unchanged["generation"] == paused["generation"]
    assert unchanged["selection"] == paused["selection"]
    history = terminal.get(
        "/api/v1/history",
        params={
            "session_id": frame["session_id"],
            "generation": frame["generation"],
            "end_delivery": 999999,
            "limit": 1,
        },
    ).json()
    assert len(history["events"]) == 1
    assert history["truncated"] is True
    assert int(history["events"][0]["ingest_ordinal"]) <= int(
        paused["replay"]["cursor"]["ordinal"]
    )


def test_unknown_fee_and_prefunding_are_distinct_exclusions(terminal):
    frame = new_session(terminal)
    request = {
        "session_id": frame["session_id"],
        "generation": frame["generation"],
        "side": "buy",
        "base_quantity": "0.01",
        "fee_bps": "0",
        "fee_date": "2026-11-01",
        "fee_provenance": "explicit zero-fee assumption",
        "inventory": [{"venue": "coinbase", "asset_id": "USD", "available": "0"}],
    }
    response = terminal.post("/api/v1/costs", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["estimate"]["fee_amount"] == "0"
    pairs = response.json()["matrix"]
    constrained = [
        pair
        for pair in pairs
        if pair["buy_instrument_id"] == frame["selection"]["instrument_id"]
    ]
    assert all(not pair["eligible"] for pair in constrained)
    instruments = {
        item["instrument_id"]: item
        for item in terminal.get(
            "/api/v1/instruments", params={"source_id": frame["source"]["id"]}
        ).json()["instruments"]
    }
    spot_pairs = [
        pair
        for pair in constrained
        if instruments[pair["sell_instrument_id"]]["market_type"] == "spot"
    ]
    assert len(spot_pairs) == 3
    assert all(Decimal(pair["feasible_base_quantity"]) == 0 for pair in spot_pairs)
    assert all(pair["net"] is None for pair in constrained)


def test_overloaded_analytical_client_never_presents_silently_truncated_state():
    from src.workstation.runtime import ClientSession, RuntimeFailure
    from src.workstation.subscriptions import SubscriptionRegistry

    async def scenario():
        registry = SubscriptionRegistry()
        runtime = TerminalRuntime(registry=registry)
        source = MemoryReplaySource.demo()
        replay = ReplaySession(source)
        session = ClientSession(runtime, "live", "live")
        runtime.sessions[session.id] = session
        runtime.sources["live"] = {
            "id": "live",
            "mode": "live",
            "label": "test public callback boundary",
            "status": "ready",
            "synthetic": False,
            "recording": False,
            "duration_ns": None,
            "streams": 24,
            "reasons": [],
        }
        session.status = "live"
        session.live_stream_ids = frozenset(
            spec.stream_id for spec in source.descriptor.streams
        )
        session.live_keys = frozenset(spec.key for spec in source.descriptor.streams)
        try:
            for _ in range(257):
                batch = replay.step()[0]
                session.offer(batch.events, replay.books())
            assert session.live_queue.qsize() <= 256
            assert session.live_bytes <= 8 * 1024 * 1024
            assert session.telemetry["overflows"] == 1
            frame = await session.frame()
            assert frame["status"] == "unavailable"
            assert frame["book"] is None and frame["analytics"] is None
            assert frame["health"]["ready"] is False
            with pytest.raises(RuntimeFailure):
                session.check("1")
        finally:
            replay.close()
            await runtime.close()
            await registry.close()

    asyncio.run(scenario())


def test_readiness_is_nonpublishing_and_unaffected_by_busy_history_owner(terminal):
    frame = new_session(terminal)
    session = terminal.app.state.runtime.sessions[frame["session_id"]]
    before = (
        session.frame_sequence,
        dict(session.pending_acks),
        dict(session.telemetry),
        session.last_access,
    )
    terminal.portal.call(session.lock.acquire)
    try:
        for _ in range(70):
            assert terminal.get("/health/ready").status_code == 200
        assert (
            session.frame_sequence,
            dict(session.pending_acks),
            dict(session.telemetry),
            session.last_access,
        ) == before
    finally:

        async def release_owner():
            session.lock.release()

        terminal.portal.call(release_owner)


@pytest.mark.parametrize(
    "fees",
    [
        {"fee_bps": "0"},
        {"fee_provenance": "explicit assumption"},
        {"fee_date": "2026-11-01"},
        {"fee_bps": "0", "fee_provenance": "explicit assumption"},
        {"fee_bps": "0", "fee_date": "2026-11-01"},
        {"fee_provenance": "explicit assumption", "fee_date": "2026-11-01"},
    ],
)
def test_fee_assumption_group_rejects_every_partial_group(terminal, fees):
    frame = new_session(terminal)
    response = terminal.post(
        "/api/v1/costs",
        json={
            "session_id": frame["session_id"],
            "generation": frame["generation"],
            "side": "buy",
            "base_quantity": "0.01",
            **fees,
        },
    )
    assert response.status_code == 422
