"""Stable internal queues; task names and the legacy default remain unchanged."""

from kombu import Exchange, Queue

LEGACY_QUEUE = "celery"
NOTES_QUEUE = "fikrah.notes"
DISPATCH_QUEUE = "fikrah.dispatch"
EMAIL_QUEUE = "fikrah.emails"
VECTORS_QUEUE = "fikrah.vectors"

TASK_ROUTES = {
    "app.worker.tasks.process_note_job": {"queue": NOTES_QUEUE},
    "app.worker.tasks.dispatch_pending_work": {"queue": DISPATCH_QUEUE},
    "app.workers.tasks.deliver_account_emails": {"queue": EMAIL_QUEUE},
    "app.worker.tasks.delete_note_vector": {"queue": VECTORS_QUEUE},
}

TASK_QUEUES = tuple(
    Queue(name, Exchange(name, type="direct"), routing_key=name)
    for name in (LEGACY_QUEUE, NOTES_QUEUE, DISPATCH_QUEUE, EMAIL_QUEUE, VECTORS_QUEUE)
)
