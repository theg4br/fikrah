"""Content-free timing events shared by application and infrastructure layers."""

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import iscoroutinefunction
from time import perf_counter
from uuid import UUID, uuid4

LOGGER_NAME = "fikrah.performance"
logger = logging.getLogger(LOGGER_NAME)
_trace: ContextVar[str | None] = ContextVar("performance_trace", default=None)
_job: ContextVar[str | None] = ContextVar("performance_job", default=None)


@contextmanager
def correlation(job_id: UUID | None = None) -> Iterator[None]:
    """Isolate one HTTP request or worker attempt, including child thread work."""
    trace_token = _trace.set(uuid4().hex)
    job_token = _job.set(str(job_id) if job_id is not None else None)
    try:
        yield
    finally:
        _job.reset(job_token)
        _trace.reset(trace_token)


def observe(
    stage: str,
    seconds: float,
    *,
    outcome: str = "ok",
    route: str | None = None,
    method: str | None = None,
    status_code: int | None = None,
) -> None:
    """Emit only explicit metadata; never accept exception text or user content."""
    measurement = {
        "event": "performance",
        "stage": stage,
        "duration_ms": round(max(0, seconds) * 1000, 3),
        "outcome": outcome,
        "trace_id": _trace.get(),
    }
    if (job_id := _job.get()) is not None:
        measurement["job_id"] = job_id
    if route is not None:
        measurement["route"] = route
    if method is not None:
        measurement["method"] = method
    if status_code is not None:
        measurement["status_code"] = status_code
    try:
        logger.info("performance", extra={"measurement": measurement})
    except Exception:  # noqa: BLE001, S110 - telemetry must not change application outcomes
        pass


@contextmanager
def measure(stage: str) -> Iterator[None]:
    started = perf_counter()
    outcome = "ok"
    try:
        yield
    except BaseException as error:
        outcome = "cancelled" if isinstance(error, asyncio.CancelledError) else "error"
        raise
    finally:
        observe(stage, perf_counter() - started, outcome=outcome)


def timed(stage: str):
    """Time a synchronous or asynchronous operation without changing its contract."""

    def decorate(operation):
        if iscoroutinefunction(operation):

            @wraps(operation)
            async def asynchronous(*args, **kwargs):
                with measure(stage):
                    return await operation(*args, **kwargs)

            return asynchronous

        @wraps(operation)
        def synchronous(*args, **kwargs):
            with measure(stage):
                return operation(*args, **kwargs)

        return synchronous

    return decorate
