"""Deterministic cancellation barriers for owned executor/task completion."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import anyio
import pytest

from src.workstation.ownership import join_owned


@pytest.mark.parametrize("failure", (False, True))
def test_repeated_cancellation_joins_executor_and_chains_failure(failure):
    async def scenario():
        entered, release = threading.Event(), threading.Event()
        completed = []

        def worker():
            entered.set()
            assert release.wait(10)
            completed.append(True)
            if failure:
                raise ValueError("owned failure")
            return 42

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = asyncio.get_running_loop().run_in_executor(executor, worker)
            waiter = asyncio.create_task(join_owned(future))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                for _ in range(3):
                    waiter.cancel()
                    await asyncio.sleep(0)
                    assert not waiter.done()
                    assert not future.cancelled()
                    assert not completed
                release.set()
                with pytest.raises(asyncio.CancelledError) as caught:
                    await waiter
                assert completed == [True]
                assert future.done() and not future.cancelled()
                if failure:
                    assert isinstance(caught.value.__cause__, ValueError)
                    assert str(caught.value.__cause__) == "owned failure"
            finally:
                release.set()
                await asyncio.gather(waiter, return_exceptions=True)

    asyncio.run(scenario())


def test_level_cancelled_scope_joins_before_propagating():
    async def scenario():
        release = asyncio.Event()
        owned_done = asyncio.Event()
        entered = asyncio.Event()
        propagated = []
        scopes = []

        async def owner():
            await release.wait()
            owned_done.set()
            return 42

        owned = asyncio.create_task(owner())

        async def caller():
            with anyio.CancelScope() as scope:
                scopes.append(scope)
                entered.set()
                try:
                    await join_owned(owned)
                    pytest.fail("cancelled mutation reported success")
                except asyncio.CancelledError:
                    assert owned_done.is_set()
                    propagated.append(True)
                    raise

        waiter = asyncio.create_task(caller())
        await entered.wait()
        scopes[0].cancel()
        for _ in range(3):
            await asyncio.sleep(0)
            assert not waiter.done()
            assert not owned.cancelled()
        release.set()
        await waiter
        assert propagated == [True]
        assert owned.result() == 42

    asyncio.run(scenario())


def test_owned_result_failure_and_owner_cancellation_are_not_hidden():
    async def scenario():
        loop = asyncio.get_running_loop()
        successful = loop.create_future()
        successful.set_result(7)
        assert await join_owned(successful) == 7
        failed = loop.create_future()
        failed.set_exception(ValueError("failure"))
        with pytest.raises(ValueError, match="failure"):
            await join_owned(failed)
        cancelled = loop.create_future()
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await join_owned(cancelled)

    asyncio.run(scenario())
