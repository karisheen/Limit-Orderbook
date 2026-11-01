"""Thin-client regressions; native financial correctness lives in shared-core tests."""

from __future__ import annotations

import json
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest

from src.ui import api_client
from src.ui.api_client import ApiClient, ApiError, loopback_url


def frame(session="a", generation="1", number="1", instrument="native:BTC-USD"):
    return {
        "type": "frame",
        "version": "1",
        "session_id": session,
        "generation": generation,
        "frame_id": number,
        "selection": {"instrument_id": instrument},
        "source": {"id": "demo", "mode": "demo"},
        "book": None,
        "analytics": None,
    }


def client_for(handler):
    return ApiClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8000",
        "http://example.com",
        "http://127.0.0.2",
        "http://127.0.0.1.evil.example",
        "http://user@localhost:8000",
        "http://localhost:8000/api",
        "http://localhost:8000?remote=yes",
        "http://localhost:8000#fragment",
        "http://localhost:99999",
        "file:///tmp/api",
    ],
)
def test_loopback_boundary_rejects_nonlocal_or_ambiguous_origins(url):
    with pytest.raises(ApiError):
        loopback_url(url)


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:8000", "http://localhost:8000", "http://[::1]:8000"]
)
def test_loopback_origins(url):
    assert loopback_url(url + "/") == url


def test_documents_own_sessions_and_close_only_their_own():
    opened = []
    deleted = []

    def handle(request):
        if request.method == "DELETE":
            deleted.append(request.url.path.rsplit("/", 1)[1])
            return httpx.Response(200, json={"closed": True})
        body = json.loads(request.content)
        session = "a" if not opened else "b"
        opened.append(body)
        return httpx.Response(
            201, json=frame(session=session, instrument=body["instrument_id"])
        )

    first = client_for(handle)
    second = client_for(handle)
    try:
        first.open("demo", "demo", "coinbase:BTC-USD")
        second.open("demo", "demo", "kraken:BTC/USD")
        assert first.session_id != second.session_id
        assert first.frame["selection"] != second.frame["selection"]
        first.close()
        assert deleted == ["a"]
        assert second.session_id == "b"
        assert second.frame["selection"]["instrument_id"] == "kraken:BTC/USD"
        first.close()
        assert deleted == ["a"]
    finally:
        first.close()
        second.close()
    assert deleted == ["a", "b"]


@pytest.mark.parametrize(
    "bad",
    [
        frame(generation="0", number="2"),
        frame(number="1"),
        frame(session="b", number="2"),
        frame(generation=2, number="2"),
        frame(number="2") | {"version": "2"},
        frame(generation="١", number="2"),
    ],
)
def test_reordered_cross_document_or_inexact_frames_are_never_presented(bad):
    def handle(request):
        if request.method == "DELETE":
            return httpx.Response(200, json={"closed": True})
        return httpx.Response(200, json=frame() if request.method == "POST" else bad)

    client = client_for(handle)
    try:
        client.open("demo", "demo", "native:BTC-USD")
        with pytest.raises(ApiError, match="Rejected"):
            client.refresh()
        assert client.frame is None
    finally:
        client.close()


def test_expired_session_clears_presentation_and_requires_reconnect():
    def handle(request):
        if request.method == "POST":
            return httpx.Response(201, json=frame())
        if request.method == "DELETE":
            return httpx.Response(200, json={"closed": True})
        return httpx.Response(404, json={"detail": "unknown session"})

    client = client_for(handle)
    try:
        client.open("demo", "demo", "native:BTC-USD")
        with pytest.raises(ApiError, match="HTTP 404: unknown session"):
            client.refresh()
        assert client.frame is None
    finally:
        client.close()


def test_obsolete_analysis_is_rejected():
    def handle(request):
        if request.method == "DELETE":
            return httpx.Response(200, json={"closed": True})
        if request.url.path.endswith("/sessions"):
            return httpx.Response(201, json=frame())
        return httpx.Response(200, json={"generation": "0", "estimate": None})

    client = client_for(handle)
    try:
        client.open("demo", "demo", "native:BTC-USD")
        with pytest.raises(ApiError, match="obsolete generation"):
            client.costs(side="buy", base_quantity="1")
    finally:
        client.close()


def test_switch_closes_previous_session_before_opening_new_source():
    calls = []

    def handle(request):
        calls.append((request.method, request.url.path))
        if request.method == "DELETE":
            return httpx.Response(200, json={"closed": True})
        return httpx.Response(201, json=frame(session="a" if len(calls) == 1 else "b"))

    client = client_for(handle)
    try:
        client.open("demo", "demo", "native:BTC-USD")
        client.open("replay", "opaque-capture", "native:BTC-USD")
        assert calls == [
            ("POST", "/api/v1/sessions"),
            ("DELETE", "/api/v1/sessions/a"),
            ("POST", "/api/v1/sessions"),
        ]
        assert client.session_id == "b"
    finally:
        client.close()


def test_http_limits_timeouts_redirects_and_cleanup(monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            302,
            headers={"Location": "https://remote.example"},
            json={"detail": "redirect forbidden"},
        )

    client = client_for(handle)
    try:
        with pytest.raises(ApiError, match="HTTP 302"):
            client.catalog()
        assert len(requests) == 1
    finally:
        client.close()
    assert client._http.is_closed
    with pytest.raises(ApiError, match="closed"):
        client.catalog()

    monkeypatch.setattr(api_client, "MAX_RESPONSE_BYTES", 16)
    client = client_for(
        lambda request: httpx.Response(
            200, content=b'{"oversized":"' + b"x" * 100 + b'"}'
        )
    )
    try:
        with pytest.raises(ApiError, match="client limit"):
            client.catalog()
    finally:
        client.close()


def test_read_timeout_is_actionable():
    def handle(request):
        raise httpx.ReadTimeout("slow backend", request=request)

    client = client_for(handle)
    try:
        with pytest.raises(ApiError, match="src.workstation.cli serve"):
            client.catalog()
    finally:
        client.close()


def test_cleanup_failure_still_closes_transport_and_drops_session():
    def handle(request):
        if request.method == "DELETE":
            raise httpx.ConnectError("backend stopped", request=request)
        return httpx.Response(201, json=frame())

    client = client_for(handle)
    client.open("demo", "demo", "native:BTC-USD")
    with pytest.raises(ApiError):
        client.close()
    assert client._http.is_closed
    assert client.frame is None
    assert client.session_id is None
    client.close()


def test_request_size_and_stream_elapsed_budget_are_bounded(monkeypatch):
    client = client_for(lambda request: httpx.Response(200, json={"sources": []}))
    try:
        with pytest.raises(ApiError, match="64 KiB"):
            client._request("POST", "/costs", body={"large": "x" * 65536})
        clock = iter([0, 31])
        monkeypatch.setattr(
            api_client, "time", SimpleNamespace(monotonic=lambda: next(clock))
        )
        with pytest.raises(ApiError, match="deadline"):
            client.catalog()
    finally:
        client.close()


def test_heatmap_preserves_server_cells_exact_hover_and_fixed_transfer(monkeypatch):
    from src.ui import components

    figures = []
    monkeypatch.setattr(
        components,
        "st",
        SimpleNamespace(
            subheader=lambda *args: None,
            caption=lambda *args: None,
            warning=lambda *args: None,
            info=lambda *args: None,
            plotly_chart=lambda figure, **kwargs: figures.append(figure),
            expander=lambda *args: nullcontext(),
            json=lambda value, **kwargs: None,
        ),
    )
    grid = {
        "clock_id": "source-clock",
        "price_step": "0.000000000000000001",
        "bucket_ns": "1000000000",
        "quantity_unit": "native-base",
        "complete_window": False,
        "truncated": False,
        "reasons": ["warming"],
        "prices": [
            "9007199254740993.000000000000000001",
            "9007199254740993.000000000000000002",
        ],
        "columns": [
            {
                "time_ns": "900719925474099312345",
                "cells": [[None, "7"], ["0", "8"]],
                "eligible": True,
                "reasons": [],
                "segment_id": "segment",
                "liquidity_hash": "unchanged",
            }
        ],
    }
    components.render_heatmap(grid, "bid", "10")
    trace = figures[0].data[0]
    assert trace.z[0][0] is None
    assert trace.z[1][0] == 0
    assert trace.zmin == 0
    assert trace.zmax == 10
    assert trace.x[0] == "900719925474099312345"
    assert trace.customdata[0][0][0] == grid["prices"][0]
    assert trace.customdata[1][0][1] == "0"
    assert trace.y[0] != trace.y[1]


def test_ladder_uses_server_cumulative_depth_without_recalculation(monkeypatch):
    from src.ui import components

    figures = []
    tables = []
    monkeypatch.setattr(
        components,
        "st",
        SimpleNamespace(
            subheader=lambda *args: None,
            caption=lambda *args: None,
            warning=lambda *args: None,
            dataframe=lambda rows, **kwargs: tables.append(rows),
            plotly_chart=lambda figure, **kwargs: figures.append(figure),
        ),
    )
    ladder = {
        "step": "0.000000000000000001",
        "unit": "native-base",
        "eligible": False,
        "reasons": ["partial_depth_coverage"],
        "rows": [
            {
                "side": "bid",
                "price": "9007199254740993.000000000000000002",
                "base_quantity": "2",
                "cumulative_base": "20.000000000000000001",
            },
            {
                "side": "bid",
                "price": "9007199254740993.000000000000000001",
                "base_quantity": "3",
                "cumulative_base": "30.000000000000000001",
            },
        ],
    }
    before = deepcopy(ladder)
    components.render_ladder(ladder)
    assert tables[0][0]["price"] == ladder["rows"][0]["price"]
    trace = figures[0].data[0]
    assert list(trace.y) == [20.0, 30.0]
    assert trace.customdata[1][1] == "30.000000000000000001"
    assert trace.x[0] != trace.x[1]
    assert ladder == before
