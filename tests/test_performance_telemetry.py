"""Timing preserves results, isolation, cancellation, and sensitive-data boundaries."""

import asyncio
import json
import logging
from io import StringIO
from threading import Event
from time import perf_counter
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from sqlalchemy import create_engine, text

from app.api.timing import RequestTimingMiddleware
from app.core import telemetry
from app.db.timing import instrument_database
from app.infrastructure import observability


@pytest.fixture
def measurements():
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record.measurement)

    logger = logging.getLogger(telemetry.LOGGER_NAME)
    level, propagate, disabled = logger.level, logger.propagate, logger.disabled
    capture = Capture()
    logger.addHandler(capture)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.disabled = False
    try:
        yield records
    finally:
        logger.removeHandler(capture)
        logger.setLevel(level)
        logger.propagate = propagate
        logger.disabled = disabled


def test_sync_timing_preserves_result_exception_and_logs_no_arguments(measurements):
    @telemetry.timed("example.sync")
    def operation(password):
        if password == "raise":
            raise ValueError("private provider response")
        return password

    assert operation("private password") == "private password"
    with pytest.raises(ValueError, match="private provider response"):
        operation("raise")
    assert [row["outcome"] for row in measurements] == ["ok", "error"]
    assert all(row["duration_ms"] >= 0 for row in measurements)
    assert "private" not in json.dumps(measurements)


@pytest.mark.asyncio
async def test_async_cancellation_and_concurrent_thread_contexts(measurements):
    @telemetry.timed("example.thread")
    def thread_work():
        return 7

    async def request():
        with telemetry.correlation():
            assert await asyncio.to_thread(thread_work) == 7

    await asyncio.gather(request(), request())
    assert len({row["trace_id"] for row in measurements}) == 2
    telemetry.observe("outside", 0)
    assert measurements[-1]["trace_id"] is None

    @telemetry.timed("example.cancelled")
    async def cancel():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await cancel()
    assert measurements[-1]["outcome"] == "cancelled"


@pytest.mark.asyncio
async def test_request_timing_uses_templates_and_preserves_streaming(measurements):
    app = FastAPI()
    app.add_middleware(RequestTimingMiddleware)
    released = Event()

    @app.get("/items/{item_id}")
    async def streamed(item_id: str):
        async def chunks():
            yield b"first"
            released.set()
            yield b"second"

        return StreamingResponse(chunks())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/items/private-id?token=secret", headers={"Authorization": "Bearer secret"}
        )
        assert response.status_code == 200
        assert response.content == b"firstsecond"
        assert released.is_set()
        await client.get("/missing/private-path?token=secret")
    assert [row["stage"] for row in measurements] == [
        "http.first_body",
        "http.request",
    ] * 2
    first, total = measurements[:2]
    assert first["route"] == total["route"] == "/items/{item_id}"
    assert first["trace_id"] == total["trace_id"]
    assert first["duration_ms"] <= total["duration_ms"]
    assert measurements[-1]["route"] == "unmatched"
    assert measurements[-1]["status_code"] == 404
    assert measurements[-1]["outcome"] == "error"
    assert "private" not in json.dumps(measurements)
    assert "secret" not in json.dumps(measurements)


@pytest.mark.asyncio
async def test_middleware_delivers_first_chunk_before_application_finishes(
    measurements,
):
    messages = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"first", "more_body": True})
        assert messages[-1]["body"] == b"first"
        assert measurements[-1]["stage"] == "http.first_body"
        raise asyncio.CancelledError

    async def send(message):
        messages.append(message)

    with pytest.raises(asyncio.CancelledError):
        await RequestTimingMiddleware(app)(
            {"type": "http", "method": "GET"}, AsyncMock(), send
        )
    assert measurements[-1]["outcome"] == "cancelled"
    assert measurements[-1]["trace_id"] is not None


def test_database_timing_is_idempotent_and_does_not_log_sql_or_parameters(measurements):
    engine = create_engine("sqlite://")
    instrument_database(engine)
    instrument_database(engine)
    try:
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("select :value"), {"value": "private SQL parameter"}
                )
                == "private SQL parameter"
            )
            with pytest.raises(Exception, match="no such table"):
                connection.execute(text("select * from private_table"))
    finally:
        engine.dispose()
    assert [row["outcome"] for row in measurements] == ["ok", "error"]
    assert {row["stage"] for row in measurements} == {"db.query"}
    assert "private" not in json.dumps(measurements)


def test_logging_failure_does_not_change_the_operation_result(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("failed sink")

    monkeypatch.setattr(telemetry.logger, "info", fail)
    with telemetry.measure("sink.failure"):
        value = 42
    assert value == 42


def test_bounded_logging_drops_when_a_sink_stalls():
    entered, release = Event(), Event()

    class SlowSink:
        def write(self, message):
            entered.set()
            release.wait(3)

        def flush(self):
            pass

    handler = observability.BufferedTimingHandler(SlowSink(), capacity=1)
    record = logging.makeLogRecord({"measurement": {"event": "performance"}})
    try:
        handler.handle(record)
        assert entered.wait(1)
        handler.handle(record)
        handler.handle(record)
        assert handler.dropped_records == 1
    finally:
        started = perf_counter()
        handler.close()
        assert perf_counter() - started < 1
        release.set()
        handler._thread.join(timeout=1)


@pytest.mark.asyncio
async def test_disconnect_without_an_exception_is_recorded_as_cancelled(measurements):
    async def downstream(scope, receive, send):
        assert (await receive())["type"] == "http.disconnect"

    await RequestTimingMiddleware(downstream)(
        {"type": "http", "method": "GET"},
        AsyncMock(return_value={"type": "http.disconnect"}),
        AsyncMock(),
    )
    assert measurements[-1]["outcome"] == "cancelled"


def test_json_logging_and_process_restart_own_distinct_queues(monkeypatch):
    logger = logging.getLogger(telemetry.LOGGER_NAME)
    previous = (list(logger.handlers), logger.level, logger.propagate, logger.disabled)
    inherited = observability.BufferedTimingHandler(StringIO())
    logger.handlers = [inherited]
    inherited.owner_pid = -1  # Simulate the handler inherited from the prefork parent.
    sink = StringIO()
    monkeypatch.setattr(observability.sys, "stdout", sink)
    try:
        observability.start_timing_logs()
        logger.disabled = True
        observability.start_timing_logs()
        assert logger.disabled is False
        assert len(logger.handlers) == 1
        assert logger.handlers[0] is not inherited
        telemetry.observe("example.json", 0.125)
        observability.stop_timing_logs()
        value = json.loads(sink.getvalue())
        assert value["duration_ms"] == 125
        assert value["dropped_records"] == 0
    finally:
        observability.stop_timing_logs()
        inherited.owner_pid = observability.os.getpid()
        inherited.close()
        logger.handlers, logger.level, logger.propagate, logger.disabled = previous
