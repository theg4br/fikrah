"""The immediate Celery publisher is bounded and uses the durable dispatcher."""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.infrastructure import task_dispatch
from app.services.note_ports import ProcessingDispatchUnavailable


@pytest.mark.asyncio
async def test_publishes_existing_dispatcher_without_mutating_global_settings(
    monkeypatch,
):
    options = {"visibility_timeout": 3600}
    celery = MagicMock(conf=SimpleNamespace(broker_transport_options=options))
    monkeypatch.setattr(task_dispatch, "celery_app", celery)
    await task_dispatch.notify_pending_work()
    celery.send_task.assert_called_once_with(
        "app.worker.tasks.dispatch_pending_work",
        connection=celery.connection_for_write.return_value.__enter__.return_value,
        retry=False,
    )
    kwargs = celery.connection_for_write.call_args.kwargs
    assert kwargs["connect_timeout"] == 0.5
    assert kwargs["transport_options"]["socket_timeout"] == 0.5
    assert kwargs["transport_options"]["retry_on_timeout"] is False
    assert options == {"visibility_timeout": 3600}


@pytest.mark.asyncio
async def test_publisher_failure_is_translated_to_the_service_boundary(monkeypatch):
    def failed():
        raise OSError("broker unavailable")

    monkeypatch.setattr(task_dispatch, "_publish_dispatch_request", failed)
    with pytest.raises(ProcessingDispatchUnavailable):
        await task_dispatch.notify_pending_work()


@pytest.mark.asyncio
async def test_stalled_publisher_cannot_hold_the_http_request_indefinitely(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(task_dispatch, "_NOTIFICATION_TIMEOUT", 0.01)
    monkeypatch.setattr(
        task_dispatch, "_publish_dispatch_request", lambda: release.wait(1)
    )
    try:
        with pytest.raises(ProcessingDispatchUnavailable):
            await task_dispatch.notify_pending_work()
    finally:
        release.set()
