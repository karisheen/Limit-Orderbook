"""Shared-origin request starts remain paced across concurrent family cooldowns."""

import asyncio
from collections import Counter
from types import SimpleNamespace
import ssl

import httpx
import pytest

from src.workstation import transport
from src.workstation.adapters.base import TransportRequest


class ManualClock:
    """Advance transport deadlines without changing the event loop's real clock."""

    def __init__(self):
        self.now = 0.0
        self.sleepers: dict[asyncio.Future[None], float] = {}
        self.changed = asyncio.Event()

    async def sleep(self, delay):
        if delay <= 0:
            await asyncio.sleep(0)
            return
        future = asyncio.get_running_loop().create_future()
        self.sleepers[future] = self.now + delay
        self.changed.set()
        try:
            await future
        finally:
            del self.sleepers[future]
            self.changed.set()

    async def expect_sleepers(self, deadlines):
        async def ready():
            while sorted(self.sleepers.values()) != pytest.approx(deadlines):
                self.changed.clear()
                await self.changed.wait()

        await asyncio.wait_for(ready(), 1)

    def advance(self, now):
        assert now >= self.now
        self.now = now
        for future, deadline in tuple(self.sleepers.items()):
            if deadline <= now and not future.done():
                future.set_result(None)


@pytest.mark.parametrize("status", [429, 503])
def test_shared_origin_cooldown_extension_preserves_actual_start_spacing(
    monkeypatch, status
):
    clock = ManualClock()
    monkeypatch.setattr(transport, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(
        transport,
        "asyncio",
        SimpleNamespace(sleep=clock.sleep, get_running_loop=asyncio.get_running_loop),
    )
    monkeypatch.setattr(transport.random, "uniform", lambda low, high: 1.0)

    async def scenario():
        started = {family: asyncio.Event() for family in ("spot", "linear")}
        release = {family: asyncio.Event() for family in started}
        calls = []
        counts = Counter()
        raw = b' { "price":1.2300,"id":999999999999999999 }\n'

        async def handler(request):
            family = request.url.params["category"]
            calls.append((family, clock.now))
            counts[family] += 1
            if family in started and counts[family] == 1:
                started[family].set()
                await release[family].wait()
                return httpx.Response(
                    status,
                    headers={"Retry-After": "2" if family == "spot" else "5"},
                )
            return httpx.Response(200, content=raw)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            tasks = []

            def start(family):
                task = asyncio.create_task(
                    transport.fetch_public(
                        TransportRequest(
                            "GET",
                            "https://api.bybit.com/v5/market/instruments-info"
                            f"?category={family}",
                        ),
                        client=client,
                        attempts=2,
                    )
                )
                tasks.append(task)

            try:
                start("spot")
                await asyncio.wait_for(started["spot"].wait(), 1)
                start("linear")
                await clock.expect_sleepers([0.1])
                clock.advance(0.1)
                await asyncio.wait_for(started["linear"].wait(), 1)
                start("inverse")
                await clock.expect_sleepers([0.2])
                release["spot"].set()
                await clock.expect_sleepers([0.2, 2.1])

                # The queued family must honor the first response's cooldown.
                clock.advance(0.2)
                await clock.expect_sleepers([2.1, 2.1])
                assert calls == [("spot", 0.0), ("linear", 0.1)]

                # A later in-flight response extends the shared deadline while
                # both other families are already asleep on the old deadline.
                clock.advance(1.0)
                release["linear"].set()
                await clock.expect_sleepers([2.1, 2.1, 6.0])
                clock.advance(2.1)
                await clock.expect_sleepers([6.0, 6.0, 6.0])
                assert calls == [("spot", 0.0), ("linear", 0.1)]

                # All three wake together, but only one may start each slot.
                clock.advance(6.0)
                await clock.expect_sleepers([6.1, 6.1])
                clock.advance(6.1)
                next_start = clock.now + 0.1
                await clock.expect_sleepers([next_start])
                clock.advance(next_start)
                assert await asyncio.wait_for(asyncio.gather(*tasks), 1) == [raw] * 3
                assert not client.is_closed
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

        assert counts == {"spot": 2, "linear": 2, "inverse": 1}
        starts = [at for _, at in calls]
        assert starts[2] == 6.0
        assert all(later >= earlier + 0.1 for earlier, later in zip(starts, starts[1:]))

    asyncio.run(scenario())


def test_owned_poll_clients_reuse_verified_trust_store_without_sharing_state(
    monkeypatch,
):
    """Recurring REST polls must not reload CA bundles on the event-loop thread."""
    create_context = httpx.create_ssl_context
    create_client = httpx.AsyncClient
    context_options = []
    client_options = []
    clients = []
    requests = []

    def context_factory(**options):
        context_options.append(options)
        return create_context(**options)

    async def respond(request):
        requests.append(request)
        return httpx.Response(
            200, content=b'{"exact": 1.00}', headers={"set-cookie": "private=first"}
        )

    def client_factory(**options):
        client_options.append(options)
        client = create_client(transport=httpx.MockTransport(respond), **options)
        clients.append(client)
        return client

    monkeypatch.setattr(httpx, "create_ssl_context", context_factory)
    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    # Preserve the existing public transport's refusal to use ambient trust config.
    monkeypatch.setenv("SSL_CERT_FILE", "/nonexistent/public-transport-certificate")
    monkeypatch.setenv("SSL_CERT_DIR", "/nonexistent/public-transport-certificates")
    transport._public_ssl_context.cache_clear()

    async def scenario():
        for host in ("first.example", "second.example"):
            assert (
                await transport.fetch_public(
                    TransportRequest("GET", f"https://{host}/snapshot")
                )
                == b'{"exact": 1.00}'
            )
        assert context_options == [{"trust_env": False}]
        assert len(clients) == 2 and all(client.is_closed for client in clients)
        first, second = client_options
        context = first["verify"]
        assert context is second["verify"]
        assert context.check_hostname
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.cert_store_stats()["x509_ca"] > 0
        assert all(not options["trust_env"] for options in client_options)
        assert all(not options["follow_redirects"] for options in client_options)
        assert all("cookie" not in request.headers for request in requests)

    try:
        asyncio.run(scenario())
    finally:
        transport._public_ssl_context.cache_clear()


def test_supplied_public_client_does_not_construct_owned_trust_store(monkeypatch):
    def unexpected_context():
        raise AssertionError("supplied clients retain their own TLS configuration")

    monkeypatch.setattr(transport, "_public_ssl_context", unexpected_context)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"exact")
            ),
            trust_env=False,
        ) as client:
            assert (
                await transport.fetch_public(
                    TransportRequest("GET", "https://supplied.example/snapshot"),
                    client=client,
                )
                == b"exact"
            )
            assert not client.is_closed

    asyncio.run(scenario())


def test_websocket_trust_cache_tracks_environment_and_stays_bounded(monkeypatch):
    environments = []

    def create_context():
        import os

        environments.append(
            (os.environ.get("SSL_CERT_FILE"), os.environ.get("SSL_CERT_DIR"))
        )
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    monkeypatch.setattr(ssl, "create_default_context", create_context)
    monkeypatch.delenv("SSLKEYLOGFILE", raising=False)
    transport._cached_websocket_ssl_context.cache_clear()

    async def scenario():
        monkeypatch.setenv("SSL_CERT_FILE", "/first.pem")
        monkeypatch.setenv("SSL_CERT_DIR", "/first-directory")
        first = await transport._websocket_ssl_context()
        assert await transport._websocket_ssl_context() is first
        monkeypatch.setenv("SSL_CERT_FILE", "/second.pem")
        second = await transport._websocket_ssl_context()
        assert second is not first
        monkeypatch.setenv("SSL_CERT_DIR", "/second-directory")
        third = await transport._websocket_ssl_context()
        assert third is not second
        assert await transport._websocket_ssl_context() is third
        assert environments == [
            ("/first.pem", "/first-directory"),
            ("/second.pem", "/first-directory"),
            ("/second.pem", "/second-directory"),
        ]
        assert all(
            context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
            for context in (first, second, third)
        )
        assert transport._cached_websocket_ssl_context.cache_info().currsize == 1
        assert not transport._WS_SSL_PENDING

    try:
        asyncio.run(scenario())
    finally:
        transport._cached_websocket_ssl_context.cache_clear()


@pytest.mark.parametrize("secure", [False, True])
@pytest.mark.parametrize(
    "proxy", [None, "http://proxy.example:8080", "https://proxy.example:8443"]
)
def test_native_websocket_tls_options_preserve_proxy_and_plaintext_routing(
    monkeypatch, secure, proxy
):
    import websockets.asyncio.client as native_client

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    loads = []
    observed = []

    async def trust():
        loads.append(True)
        return context

    async def connect_direct(factory, **kwargs):
        observed.append(("direct", kwargs))
        raise OSError("socket boundary")

    async def connect_proxy(parsed_proxy, uri, **kwargs):
        observed.append((parsed_proxy.scheme, kwargs))
        assert uri.host == "fixture.example"
        raise OSError("socket boundary")

    monkeypatch.setattr(transport, "_websocket_ssl_context", trust)
    monkeypatch.setattr(transport, "get_proxy", lambda uri: proxy)
    monkeypatch.setattr(native_client, "connect_http_proxy", connect_proxy)

    async def scenario():
        monkeypatch.setattr(
            asyncio.get_running_loop(), "create_connection", connect_direct
        )
        connector = transport.PublicWebSocketConnect(
            f"{'wss' if secure else 'ws'}://fixture.example/ws"
        )
        with pytest.raises(OSError, match="socket boundary"):
            await connector.open_tcp_connection()
        kind, options = observed[0]
        assert kind == ("direct" if proxy is None else proxy.split(":")[0])
        if proxy is None:
            if secure:
                assert options["ssl"] is context
                assert options["server_hostname"] == "fixture.example"
            else:
                assert "ssl" not in options
                assert "server_hostname" not in options
        elif proxy.startswith("https:"):
            assert options["ssl"] is context
        else:
            assert "ssl" not in options
        assert len(loads) == int(secure) + int(
            proxy is not None and proxy.startswith("https:")
        )
        # Default automatic proxy selection remains available to redirects and
        # reconnects; TLS options must not leak into a later plaintext leg.
        assert connector.proxy is True
        assert connector.create_connection_kwargs == {}

    asyncio.run(scenario())


def test_websocket_failed_trust_initialization_is_joined_and_retryable(monkeypatch):
    from threading import Event

    release = Event()
    attempts = []
    transport._cached_websocket_ssl_context.cache_clear()

    async def scenario():
        loop = asyncio.get_running_loop()
        entered = asyncio.Event()

        def create_context():
            attempts.append(True)
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5), "event loop could not release trust initialization"
            if len(attempts) == 1:
                raise OSError("trust store unavailable")
            return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

        monkeypatch.setattr(ssl, "create_default_context", create_context)
        owner = asyncio.create_task(transport._websocket_ssl_context())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            owner.cancel()
            await asyncio.sleep(0)
            owner.cancel()
            await asyncio.sleep(0)
            assert not owner.done()
            release.set()
            with pytest.raises(asyncio.CancelledError) as cancelled:
                await owner
            assert isinstance(cancelled.value.__cause__, OSError)
            assert not transport._WS_SSL_PENDING
            context = await transport._websocket_ssl_context()
            assert context.check_hostname
            assert context.verify_mode == ssl.CERT_REQUIRED
            assert len(attempts) == 2
        finally:
            release.set()
            await asyncio.gather(owner, return_exceptions=True)

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        transport._cached_websocket_ssl_context.cache_clear()
