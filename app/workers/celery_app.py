"""Celery configuration for Redis-backed FIKRAH jobs."""

import sys

from celery import Celery
from celery.signals import (
    after_setup_logger,
    worker_process_init,
    worker_process_shutdown,
    worker_shutdown,
)

from app.core.config import get_settings, get_worker_settings
from app.infrastructure.observability import start_timing_logs, stop_timing_logs
from app.workers.routing import LEGACY_QUEUE, TASK_QUEUES, TASK_ROUTES

settings = get_worker_settings()

celery_app = Celery(
    "fikrah",
    broker=get_settings().redis_url,
    include=["app.workers.tasks"],
)
celery_app.conf.update(
    task_default_queue=LEGACY_QUEUE,
    task_queues=TASK_QUEUES,
    task_routes=TASK_ROUTES,
    task_ignore_result=True,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_acks_late=True,
    task_reject_on_worker_lost=False,
    task_publish_retry=True,
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        "visibility_timeout": settings.broker_visibility_timeout_seconds
    },
    worker_prefetch_multiplier=1,
    beat_schedule={
        "deliver-durable-account-emails": {
            "task": "app.workers.tasks.deliver_account_emails",
            "schedule": float(settings.dispatch_interval_seconds),
        },
        "dispatch-durable-backend-jobs": {
            "task": "app.worker.tasks.dispatch_pending_work",
            "schedule": float(settings.dispatch_interval_seconds),
        },
    },
)


def _start_worker_timing_logs(**_kwargs) -> None:
    # Celery redirects sys.stdout into its logger at WARNING by default. Use the
    # original stream so JSON timings are neither re-logged nor misclassified.
    start_timing_logs(sink=sys.__stdout__)


# Prefork children need their own log queue and writer thread.
after_setup_logger.connect(_start_worker_timing_logs, weak=False)
worker_process_init.connect(_start_worker_timing_logs, weak=False)
worker_process_shutdown.connect(stop_timing_logs, weak=False)
worker_shutdown.connect(stop_timing_logs, weak=False)
