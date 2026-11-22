"""Execution contracts and cooperative deadlines, independent of providers."""

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Event
from time import monotonic
from typing import Protocol, TypeVar

T = TypeVar("T")


class ExecutionBusy(RuntimeError):
    """All admitted blocking operations are still running."""


class ExecutionDeadlineExceeded(TimeoutError):
    """An operation expired or its caller stopped waiting."""


class BlockingRunner(Protocol):
    async def __call__(self, operation: Callable[..., T], *args: object) -> T: ...


@dataclass
class ExecutionBudget:
    expires_at: float
    parent: "ExecutionBudget | None" = None
    cancelled: Event = field(default_factory=Event)

    def remaining(self) -> float:
        remaining = self.expires_at - monotonic()
        if self.cancelled.is_set() or remaining <= 0:
            raise ExecutionDeadlineExceeded("Execution budget expired")
        if self.parent is not None:
            remaining = min(remaining, self.parent.remaining())
        return remaining


_current_budget: ContextVar[ExecutionBudget | None] = ContextVar(
    "execution_budget", default=None
)


def check_execution_budget() -> None:
    budget = _current_budget.get()
    if budget is not None:
        budget.remaining()


def remaining_time(maximum: float) -> float:
    """Clamp network waits and reject further work after expiration/cancellation."""
    budget = _current_budget.get()
    return maximum if budget is None else min(maximum, budget.remaining())


def iter_with_budget(chunks: Iterator[T]) -> Iterator[T]:
    """Check before a blocking read as well as after a slow read returns."""
    while True:
        check_execution_budget()
        try:
            chunk = next(chunks)
        except StopIteration:
            return
        check_execution_budget()
        yield chunk


@asynccontextmanager
async def execution_budget(seconds: float) -> AsyncIterator[None]:
    parent = _current_budget.get()
    expires_at = monotonic() + seconds
    if parent is not None:
        expires_at = min(expires_at, parent.expires_at)
    budget = ExecutionBudget(expires_at, parent)
    token = _current_budget.set(budget)
    try:
        async with asyncio.timeout(budget.remaining()):
            yield
            check_execution_budget()
    finally:
        budget.cancelled.set()
        _current_budget.reset(token)
