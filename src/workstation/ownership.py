"""Join owned work before propagating cancellation to its lifecycle caller."""

import asyncio
from typing import TypeVar

from anyio import CancelScope

T = TypeVar("T")


async def join_owned(future: asyncio.Future[T]) -> T:
    """Never transfer waiter cancellation to an owned task or executor future.

    The first cancellation is remembered, including AnyIO level cancellation.
    Shield its completion from the cancel scope and from repeated Task.cancel()
    calls. A worker failure remains the cancellation's explicit cause rather
    than becoming an unobserved exception or a successful cancelled mutation.
    """
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError as cancelled:
        with CancelScope(shield=True):
            while not future.done():
                try:
                    await asyncio.shield(future)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            try:
                future.result()
            except BaseException as failure:
                raise cancelled from failure
        raise cancelled
