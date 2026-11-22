"""Bound blocking SDK work without borrowing the event loop's shared executor."""

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import BoundedSemaphore, Lock
from typing import TypeVar

from app.core.execution import ExecutionBusy, check_execution_budget
from app.core.telemetry import measure

T = TypeVar("T")


class BoundedExecutor:
    """At most capacity submitted/running calls, with no waiting admission queue.

    Slots belong to concurrent futures, not HTTP waiters. Cancellation cannot
    free a slot while the thread still owns a provider. No loop-bound primitives
    or threads are created at import time; context is copied for each submission.
    """

    def __init__(self, capacity: int, name: str) -> None:
        if capacity < 1:
            raise ValueError("Executor capacity must be positive")
        self._capacity = capacity
        self._name = name
        self._slots = BoundedSemaphore(capacity)
        self._lock = Lock()
        self._executor: ThreadPoolExecutor | None = None

    async def run(self, operation: Callable[..., T], *args: object) -> T:
        with measure(f"executor.{self._name}"):
            check_execution_budget()
            with self._lock:
                if not self._slots.acquire(blocking=False):
                    raise ExecutionBusy("Blocking execution capacity exhausted")
                try:
                    if self._executor is None:
                        self._executor = ThreadPoolExecutor(
                            max_workers=self._capacity,
                            thread_name_prefix=f"fikrah-{self._name}",
                        )
                    context = copy_context()

                    def work() -> T:
                        check_execution_budget()
                        result = operation(*args)
                        check_execution_budget()
                        return result

                    future = self._executor.submit(context.run, work)
                except BaseException:
                    self._slots.release()
                    raise
                future.add_done_callback(lambda _: self._slots.release())
            # Cancelling this asyncio future may cancel an unstarted call. A
            # running call retains its slot until its own finally/close finishes.
            return await asyncio.wrap_future(future)

    def close(self) -> None:
        """Stop this pool without blocking ASGI shutdown on remote SDK calls.

        A later lifespan can lazily open a new pool. Its capacity remains shared
        with old draining work because the admission semaphore is never replaced.
        Running Python threads cannot be forcibly terminated by shutdown().
        """
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
