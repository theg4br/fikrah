"""Registered tasks, API wakes, Beat and retries use one routing definition."""

import pytest
from celery.beat import ScheduleEntry, Scheduler
from celery.signals import before_task_publish
from kombu import Connection, Producer
from pydantic import ValidationError

from app.core.config import get_settings
from app.workers.celery_app import celery_app
from app.workers.routing import (
    DISPATCH_QUEUE,
    EMAIL_QUEUE,
    LEGACY_QUEUE,
    NOTES_QUEUE,
    TASK_QUEUES,
    TASK_ROUTES,
    VECTORS_QUEUE,
)
from app.workers.tasks import process_note_job


@pytest.fixture
def published():
    rows = []

    def capture(sender=None, exchange=None, routing_key=None, **_):
        rows.append((sender, exchange, routing_key))

    before_task_publish.connect(capture, weak=False)
    try:
        yield rows
    finally:
        before_task_publish.disconnect(capture)


@pytest.mark.parametrize(
    "name, queue",
    [
        ("app.worker.tasks.process_note_job", NOTES_QUEUE),
        ("app.worker.tasks.dispatch_pending_work", DISPATCH_QUEUE),
        ("app.workers.tasks.deliver_account_emails", EMAIL_QUEUE),
        ("app.worker.tasks.delete_note_vector", VECTORS_QUEUE),
    ],
)
@pytest.mark.parametrize("entry", ["send_task", "registered"])
def test_registered_and_name_only_publishers_route_to_the_same_queue(
    name, queue, entry, published
):
    celery_app.loader.import_default_modules()
    with Connection("memory://") as connection:
        if entry == "send_task":
            celery_app.send_task(
                name,
                args=["id"]
                if name.endswith(("process_note_job", "delete_note_vector"))
                else [],
                connection=connection,
                retry=False,
            )
        else:
            celery_app.tasks[name].apply_async(
                args=["id"]
                if name.endswith(("process_note_job", "delete_note_vector"))
                else [],
                connection=connection,
                retry=False,
            )
    assert published == [(name, "", queue)]
    assert celery_app.amqp.router.route({}, name)["queue"].name == queue


def test_default_and_explicit_legacy_routes_are_preserved(published):
    assert celery_app.conf.task_default_queue == LEGACY_QUEUE
    assert {q.name for q in TASK_QUEUES} == {
        LEGACY_QUEUE,
        NOTES_QUEUE,
        DISPATCH_QUEUE,
        EMAIL_QUEUE,
        VECTORS_QUEUE,
    }
    with Connection("memory://") as connection:
        celery_app.send_task("unknown.task", connection=connection, retry=False)
        process_note_job.apply_async(
            args=["id"], queue=LEGACY_QUEUE, connection=connection, retry=False
        )
    assert published == [
        ("unknown.task", "", LEGACY_QUEUE),
        (process_note_job.name, "", LEGACY_QUEUE),
    ]


@pytest.mark.parametrize(
    "schedule_name", ["deliver-durable-account-emails", "dispatch-durable-backend-jobs"]
)
def test_beat_uses_the_same_routes(schedule_name, published):
    config = celery_app.conf.beat_schedule[schedule_name]
    scheduler = Scheduler(app=celery_app, lazy=True)
    entry = ScheduleEntry(name=schedule_name, app=celery_app, **config)
    with Connection("memory://") as connection:
        scheduler.apply_async(entry, producer=Producer(connection), advance=False)
    queue = TASK_ROUTES[entry.task]["queue"]
    assert published == [(entry.task, "", queue)]


@pytest.mark.parametrize("queue", [LEGACY_QUEUE, NOTES_QUEUE])
@pytest.mark.parametrize("anonymous_exchange", [False, True])
def test_retry_preserves_its_delivery_route(queue, published, anonymous_exchange):
    exchange = "" if anonymous_exchange else queue
    process_note_job.push_request(
        id="retry-routing",
        args=["id"],
        kwargs={},
        retries=0,
        delivery_info={"exchange": exchange, "routing_key": queue},
    )
    try:
        signature = process_note_job.signature_from_request()
        with Connection("memory://") as connection:
            signature.apply_async(connection=connection, retry=False)
    finally:
        process_note_job.pop_request()
    assert published == [(process_note_job.name, exchange, queue)]


@pytest.mark.parametrize(
    "name, default",
    [
        ("FIKRAH_NOTE_WORKER_CONCURRENCY", 4),
        ("FIKRAH_DISPATCH_WORKER_CONCURRENCY", 1),
        ("FIKRAH_EMAIL_WORKER_CONCURRENCY", 1),
        ("FIKRAH_VECTOR_WORKER_CONCURRENCY", 1),
    ],
)
def test_worker_capacities_are_validated(name, default):
    field = next(
        k
        for k, v in type(get_settings({})).model_fields.items()
        if v.validation_alias == name
    )
    assert getattr(get_settings({}), field) == default
    assert getattr(get_settings({name: "3"}), field) == 3
    for invalid in ("0", "-1", "1.5", "invalid"):
        with pytest.raises(ValidationError):
            get_settings({name: invalid})
