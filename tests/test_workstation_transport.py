"""Shared-origin request starts remain paced across concurrent family cooldowns."""

import asyncio
from collections import Counter
from types import SimpleNamespace

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
