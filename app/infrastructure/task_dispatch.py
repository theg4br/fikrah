"""Bounded Celery notification for already committed PostgreSQL work."""

import asyncio

from app.core.telemetry import timed
from app.infrastructure.broker import bounded_write_connection
from app.services.note_ports import ProcessingDispatchUnavailable
from app.workers.celery_app import celery_app

_NOTIFICATION_TIMEOUT = 1.0


def _publish_dispatch_request() -> None:
    with bounded_write_connection(celery_app) as connection:
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
