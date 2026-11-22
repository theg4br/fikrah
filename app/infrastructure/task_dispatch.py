"""Bounded Celery notification for already committed PostgreSQL work."""

import asyncio

from app.core.telemetry import timed
from app.services.note_ports import ProcessingDispatchUnavailable
from app.workers.celery_app import celery_app

_NOTIFICATION_TIMEOUT = 1.0
_BROKER_TIMEOUT = 0.5


def _publish_dispatch_request() -> None:
    options = dict(celery_app.conf.broker_transport_options or {})
    options.update(
        socket_connect_timeout=_BROKER_TIMEOUT,
        socket_timeout=_BROKER_TIMEOUT,
        retry_on_timeout=False,
        max_retries=0,
    )
    with celery_app.connection_for_write(
        connect_timeout=_BROKER_TIMEOUT, transport_options=options
    ) as connection:
        celery_app.send_task(
            "app.worker.tasks.dispatch_pending_work",
            connection=connection,
            retry=False,
        )


@timed("note.dispatch_notification")
async def notify_pending_work() -> None:
    """Wake the existing dispatcher without blocking the event loop or retrying."""
    try:
        async with asyncio.timeout(_NOTIFICATION_TIMEOUT):
            await asyncio.to_thread(_publish_dispatch_request)
    except Exception as error:
        raise ProcessingDispatchUnavailable from error
