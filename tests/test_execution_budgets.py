"""Capacity belongs to actual work, including after an HTTP waiter leaves."""

import asyncio
from contextvars import ContextVar
from threading import Event, get_ident
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.execution import (
    ExecutionBusy,
    ExecutionDeadlineExceeded,
    execution_budget,
    remaining_time,
)
from app.infrastructure.execution import BoundedExecutor
from app.llm.lifecycle import run_with_provider


async def wait_event(event):
    assert await asyncio.to_thread(event.wait, 3)


@pytest.mark.asyncio
async def test_executor_copies_context_and_does_not_use_the_default_pool(monkeypatch):
    pool = BoundedExecutor(1, "test")
    variable = ContextVar("test_context", default="unset")
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop, "run_in_executor", Mock(side_effect=AssertionError("shared executor"))
    )
    try:
        variable.set("one")
        result, thread = await pool.run(lambda: (variable.get(), get_ident()))
        assert result == "one" and thread != get_ident()
        variable.set("two")
        assert await pool.run(variable.get) == "two"
    finally:
        pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["cancel", "deadline"])
async def test_cancelled_or_expired_call_retains_capacity_until_provider_closes(stop):
    pool = BoundedExecutor(1, "test")
    started, release, closed = Event(), Event(), Event()
    threads = []

    class Provider:
        def __init__(self):
            threads.append(get_ident())

        def close(self):
            threads.append(get_ident())
            closed.set()

    def operation(_):
        threads.append(get_ident())
        started.set()
        assert release.wait(3)
        return "late result"

    async def execute():
        async with execution_budget(0.1 if stop == "deadline" else 10):
            return await pool.run(run_with_provider, Provider, operation)

    task = asyncio.create_task(execute())
    try:
        await wait_event(started)
        if stop == "cancel":
            task.cancel()
        with pytest.raises(
            asyncio.CancelledError if stop == "cancel" else TimeoutError
        ):
            await task
        assert not closed.is_set()
        another = Mock()
        with pytest.raises(ExecutionBusy):
            await pool.run(another)
        another.assert_not_called()
        # Closing and reopening must not create fresh capacity over draining work.
        pool.close()
        with pytest.raises(ExecutionBusy):
            await pool.run(another)
    finally:
        release.set()
        await wait_event(closed)
        # closed is set just before the worker/future finishes; allow completion.
        await asyncio.sleep(0.02)
        pool.close()
    assert len(threads) == 3 and len(set(threads)) == 1
    try:
        assert await pool.run(lambda: 42) == 42
    finally:
        pool.close()


@pytest.mark.asyncio
async def test_failed_submission_and_operation_do_not_leak_capacity(monkeypatch):
    pool = BoundedExecutor(1, "test")
    try:
        with pytest.raises(ValueError):
            await pool.run(lambda: int("bad"))
        assert await pool.run(lambda: 42) == 42
        original = pool._executor.submit
        monkeypatch.setattr(
            pool._executor, "submit", Mock(side_effect=RuntimeError("controlled"))
        )
        with pytest.raises(RuntimeError):
            await pool.run(lambda: None)
        monkeypatch.setattr(pool._executor, "submit", original)
        assert await pool.run(lambda: 7) == 7
    finally:
        pool.close()


@pytest.mark.asyncio
async def test_independent_pools_isolate_capacity_and_default_executor():
    text, retrieval = BoundedExecutor(1, "text"), BoundedExecutor(1, "retrieval")
    started, release = Event(), Event()

    def blocking():
        started.set()
        assert release.wait(3)

    task = asyncio.create_task(text.run(blocking))
    try:
        await wait_event(started)
        assert await retrieval.run(lambda: "search available") == "search available"
        assert (
            await asyncio.to_thread(lambda: "auth/default available")
            == "auth/default available"
        )
        with pytest.raises(ExecutionBusy):
            await text.run(lambda: None)
    finally:
        release.set()
        await task
        text.close()
        retrieval.close()


@pytest.mark.asyncio
async def test_nested_deadlines_do_not_extend_parent_and_restore_context():
    assert remaining_time(90) == 90
    async with execution_budget(0.05):
        before = remaining_time(90)
        async with execution_budget(10):
            assert remaining_time(90) <= before
        assert 0 < remaining_time(90) <= before
        with pytest.raises(TimeoutError):
            async with execution_budget(0.01):
                await asyncio.sleep(1)
        assert remaining_time(90) > 0
    assert remaining_time(90) == 90


@pytest.mark.asyncio
async def test_expired_scope_stops_background_work_before_another_stage():
    pool = BoundedExecutor(1, "test")
    started, release, stopped = Event(), Event(), Event()
    later = Mock()

    def work():
        started.set()
        assert release.wait(3)
        try:
            remaining_time(90)
        except ExecutionDeadlineExceeded:
            stopped.set()
            raise
        later()

    async def execute():
        async with execution_budget(10):
            await pool.run(work)

    task = asyncio.create_task(execute())
    try:
        await wait_event(started)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        await wait_event(stopped)
        pool.close()
    later.assert_not_called()


@pytest.mark.parametrize(
    "name", ["FIKRAH_SEARCH_TIMEOUT_SECONDS", "FIKRAH_CHAT_TIMEOUT_SECONDS"]
)
@pytest.mark.parametrize("value", ["0", "-1", "inf", "nan", "invalid"])
def test_deadlines_must_be_finite_positive_numbers(name, value):
    with pytest.raises(ValidationError):
        get_settings({name: value})


@pytest.mark.parametrize(
    "name", ["FIKRAH_SEARCH_EXECUTOR_WORKERS", "FIKRAH_LLM_EXECUTOR_WORKERS"]
)
@pytest.mark.parametrize("value", ["0", "-1", "1.5", "invalid"])
def test_executor_capacity_must_be_a_positive_integer(name, value):
    with pytest.raises(ValidationError):
        get_settings({name: value})
