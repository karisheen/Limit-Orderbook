"""Public transports reject credentials before any network operation."""

import asyncio
import json

import httpx
import pytest

from src.workstation.adapters.base import TransportRequest
from src.workstation.book import BookReducer
from src.workstation.events import SubscriptionKey
from src.workstation.ingestion import FeedFault, FeedSupervisor
from src.workstation.instruments import synthetic_instrument
from src.workstation.quality import Capabilities
from src.workstation.transport import fetch_public


@pytest.mark.parametrize(
    "header",
    [
        "Authorization",
        "Cookie",
        "X-API-Key",
        "X-MBX-APIKEY",
        "CB-ACCESS-KEY",
        "CB-ACCESS-SIGN",
        "API-Key",
        "Proxy-Authorization",
        "X-BAPI-API-KEY",
        "KC-API-KEY",
        "aPi-kEy",
        "X-Private-Token",
    ],
)
def test_private_headers_are_rejected(header):
    with pytest.raises(ValueError):
        TransportRequest(
            "GET", "https://public.example/data", headers=((header, "secret"),)
        )


@pytest.mark.parametrize(
    "endpoint",
    [
        "ws://public.example/ws",
        "ws://user:pass@public.example/ws",
        "wss://user:pass@public.example/ws",
        "wss://@public.example/ws",
        "wss:///ws",
        "wss://bad_host/ws",
        "wss://host:99999/ws",
        "wss://host:0/ws",
        "wss://host:invalid/ws",
        "wss://host/ws#fragment",
        "wss://host/ws\n",
        "wss://host/%0a",
        "wss://host/\\private",
        "wss://host/%zz",
        "wss://host/ws?apiKey=secret",
        "wss://host/ws?%74oken=secret",
        "wss://host/ws?signature=secret",
        "wss://host/ws?symbol=BTC&jwt=secret",
        "wss://host/ws?symbol=%00",
    ],
)
def test_invalid_ws_endpoint_never_starts_epoch_or_connects(endpoint):
    async def scenario():
        class Adapter:
            def new_reducer(self, instrument, key):
                return BookReducer(instrument, key)

            def ws_endpoint(self, instrument, key):
                return endpoint

        instrument = synthetic_instrument()
        calls = []

        def connect(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("invalid endpoint reached connector")

        feed = FeedSupervisor(
            Adapter(),
            instrument,
            SubscriptionKey.for_instrument(instrument),
            ws_connect=connect,
        )
        with pytest.raises(FeedFault, match="invalid_ws_endpoint"):
            await feed._ws_session()
        assert calls == []
        assert feed.epoch == 0 and feed.boundary.ordinal == 0

    asyncio.run(scenario())


def test_secure_fake_connector_is_supported_without_plaintext_escape():
    async def scenario():
        class Adapter:
            def new_reducer(self, instrument, key):
                return BookReducer(instrument, key)

            def capabilities(self, instrument, key):
                return Capabilities(snapshot=True)

            def ws_endpoint(self, instrument, key):
                return "wss://fixture.example:443/ws?streams=btcusdt%40depth"

        calls = []

        class Socket:
            async def __aenter__(self):
                raise RuntimeError("fake secure connection")

            async def __aexit__(self, *args):
                pass

        def connect(endpoint, **kwargs):
            calls.append(endpoint)
            return Socket()

        instrument = synthetic_instrument()
        feed = FeedSupervisor(
            Adapter(),
            instrument,
            SubscriptionKey.for_instrument(instrument),
            ws_connect=connect,
        )
        with pytest.raises(RuntimeError, match="fake secure connection"):
            await feed._ws_session()
        assert calls == ["wss://fixture.example:443/ws?streams=btcusdt%40depth"]
        assert feed.epoch == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "field", ["api_key", "apiKey", "token", "jwt", "signature", "password"]
)
def test_private_query_and_nested_body_fields_are_rejected(field):
    with pytest.raises(ValueError):
        TransportRequest("GET", f"https://public.example/data?{field}=secret")
    with pytest.raises(ValueError):
        TransportRequest(
            "WS",
            "wss://public.example/ws",
            json.dumps({"method": "subscribe", "params": [{field: "secret"}]}).encode(),
        )
    with pytest.raises(ValueError):
        TransportRequest(
            "POST",
            "https://public.example/info",
            json.dumps({"type": "l2Book", field: "secret"}).encode(),
        )


@pytest.mark.parametrize(
    "body",
    [b'{"op":"auth"}', b'{"method":"login"}', b'{"type":"order"}', b"api_key=secret"],
)
def test_private_body_operations_and_non_json_bodies_are_rejected(body):
    with pytest.raises(ValueError):
        TransportRequest("POST", "https://public.example/info", body)


@pytest.mark.parametrize(
    "options",
    [
        {"auth": ("user", "password")},
        {"headers": {"X-MBX-APIKEY": "secret"}},
        {"headers": {"Proxy-Authorization": "secret"}},
        {"cookies": {"session": "secret"}},
    ],
)
def test_injected_client_credentials_never_reach_network(options):
    async def scenario():
        calls = []

        def handle(request):
            calls.append(request)
            return httpx.Response(200, content=b"public")

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handle), **options
        ) as client:
            with pytest.raises(ValueError):
                await fetch_public(
                    TransportRequest("GET", "https://public.example/data"),
                    client=client,
                )
        assert calls == []

    asyncio.run(scenario())


def test_public_json_headers_and_exact_body_are_preserved():
    async def scenario():
        body = b'{"type":"l2Book","coin":"BTC","nSigFigs":5}'
        calls = []

        def handle(request):
            calls.append(request)
            assert request.content == body
            assert request.headers["Content-Type"] == "application/json"
            assert request.headers["User-Agent"] == "public-fixture"
            assert "Authorization" not in request.headers
            assert "Cookie" not in request.headers
            return httpx.Response(200, content=b'{"exact":1.000}')

        request = TransportRequest(
            "POST",
            "https://public.example/info",
            body,
            (("Content-Type", "application/json"), ("User-Agent", "public-fixture")),
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            assert await fetch_public(request, client=client) == b'{"exact":1.000}'
        assert len(calls) == 1

    asyncio.run(scenario())


def test_redirects_and_response_cookies_cannot_authorize_another_request():
    async def scenario(status):
        calls = []

        def handle(request):
            calls.append(request)
            return httpx.Response(
                status,
                headers={
                    "Location": "https://private.example/data",
                    "Set-Cookie": "session=secret",
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handle),
            follow_redirects=True,
        ) as client:
            with pytest.raises((ValueError, RuntimeError)):
                await fetch_public(
                    TransportRequest("GET", "https://public.example/data"),
                    client=client,
                )
        assert len(calls) == 1
        assert "Cookie" not in calls[0].headers

    asyncio.run(scenario(302))
    asyncio.run(scenario(503))
