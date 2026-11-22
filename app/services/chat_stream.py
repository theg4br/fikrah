"""Bridge synchronous provider deltas to an async consumer with backpressure."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import suppress
from threading import Event

from app.contracts.chat import ChatCompleted, ChatDelta, MessageSubmissionResult
from app.core.execution import check_execution_budget


async def stream_exchange(
    operation: Callable[[Callable[[str], None]], Awaitable[MessageSubmissionResult]],
) -> AsyncIterator[ChatDelta | ChatCompleted]:
    queue: asyncio.Queue[ChatDelta] = asyncio.Queue(maxsize=32)
    loop = asyncio.get_running_loop()
    stopped = Event()

    def emit(content: str) -> None:
        check_execution_budget()
        if stopped.is_set():
            raise RuntimeError("Chat stream was cancelled")
        pending = asyncio.run_coroutine_threadsafe(queue.put(ChatDelta(content)), loop)
        try:
            while True:
                try:
                    pending.result(timeout=0.1)
                    return
                except FutureTimeout:
                    check_execution_budget()
                    if stopped.is_set():
                        raise RuntimeError("Chat stream was cancelled") from None
        finally:
            if not pending.done():
                pending.cancel()

    task = asyncio.create_task(operation(emit))
    next_delta: asyncio.Task[ChatDelta] | None = None
    try:
        while True:
            next_delta = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait(
                (task, next_delta), return_when=asyncio.FIRST_COMPLETED
            )
            if next_delta in done:
                yield next_delta.result()
                next_delta = None
                continue
            next_delta.cancel()
            with suppress(asyncio.CancelledError):
                await next_delta
            next_delta = None
            result = task.result()  # Propagate sanitized service errors.
            while not queue.empty():
                yield queue.get_nowait()
            yield ChatCompleted(result)
            return
    finally:
        stopped.set()
        if next_delta is not None:
            next_delta.cancel()
            with suppress(asyncio.CancelledError):
                await next_delta
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
