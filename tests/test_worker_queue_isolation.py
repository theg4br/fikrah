"""Real prefork workers: blocked notes cannot occupy the other role pools."""

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from celery import Celery
from sqlalchemy.orm import Session

from app.models import Note, NoteProcessingJob, VectorCleanupRequest
from app.workers.routing import (
    DISPATCH_QUEUE,
    EMAIL_QUEUE,
    LEGACY_QUEUE,
    NOTES_QUEUE,
    TASK_QUEUES,
    TASK_ROUTES,
    VECTORS_QUEUE,
)

_WORKER = """
import json, os, time
from pathlib import Path
from celery.signals import task_prerun
from app.workers.celery_app import celery_app
from app.infrastructure import worker_container
from app.workers import tasks
root = Path(os.environ["FIKRAH_TEST_WORKER_FILES"])
@task_prerun.connect(weak=False)
def record(sender=None, **kwargs):
    with (root / "deliveries.jsonl").open("a") as out:
        out.write(json.dumps({"task": sender.name, "args": list(sender.request.args),
                              "queue": sender.request.delivery_info.get("routing_key"),
                              "retries": sender.request.retries}) + "\\n")
class Generator:
    def close(self): pass
    def generate_structured(self, **kwargs):
        content = json.loads(kwargs["user_content"])["note_content"]
        if content == "blocked":
            (root / "note-started").touch()
            until = time.monotonic() + 30
            while not (root / "release-note").exists():
                if time.monotonic() > until: raise TimeoutError("test note gate expired")
                time.sleep(.05)
        if content == "legacy-retry" and not (root / "legacy-failed-once").exists():
            (root / "legacy-failed-once").touch()
            raise TimeoutError("controlled transient test failure")
        return kwargs["response_model"](title="Synthetic", refinement_proposal="Synthetic", concepts=[])
class Embeddings:
    def close(self): pass
    def embed_note(self, content): return [.1]
class Vectors:
    def upsert_note(self, *args, **kwargs): pass
    def delete_note(self, note_id): (root / "vector-deleted").touch()
async def email(): (root / "email-sent").touch()
worker_container.LLMProviderFactory.create_text_generator = staticmethod(Generator)
worker_container.create_embedding_model = Embeddings
worker_container.QdrantNoteVectorStore = Vectors
tasks.QdrantNoteVectorStore = Vectors
tasks.deliver_pending_account_emails = email
celery_app.worker_main([
    "worker", "--pool=prefork", "--concurrency=1", "--loglevel=INFO",
    "--without-gossip", "--without-mingle", "--without-heartbeat",
    "--queues=" + os.environ["FIKRAH_TEST_WORKER_QUEUES"],
    "--hostname=" + os.environ["FIKRAH_TEST_WORKER_NAME"] + "@localhost",
])
"""


def _wait_until(check, seconds=25):
    until = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < until, "isolated queue test deadline expired"
        time.sleep(0.05)


def test_role_workers_progress_while_note_is_blocked_and_drain_legacy_retries(
    postgres_case, tmp_path
):
    redis_url = os.getenv("FIKRAH_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("Set FIKRAH_TEST_REDIS_URL to an isolated Redis test instance")
    engine, (user_id, project_id, note_id, job_id) = postgres_case
    with Session(engine) as db:
        db.get(Note, note_id).content = "blocked"
        # Publish the first note directly, then prove a later dispatch can run
        # while it occupies the only note execution slot.
        db.get(NoteProcessingJob, job_id).dispatched_at = datetime.now(timezone.utc)
        db.commit()
    env = os.environ | {
        "DATABASE_URL": engine.url.render_as_string(hide_password=False),
        "REDIS_URL": redis_url,
        "FIKRAH_CELERY_RETRY_BACKOFF_SECONDS": "1",
        "FIKRAH_TEST_WORKER_FILES": str(tmp_path),
    }
    roles = {
        "notes": f"{NOTES_QUEUE},{LEGACY_QUEUE}",
        "dispatch": DISPATCH_QUEUE,
        "email": EMAIL_QUEUE,
        "vectors": VECTORS_QUEUE,
    }
    workers, outputs, logs = [], [], []
    publisher = Celery("queue-isolation-test", broker=redis_url)
    publisher.conf.update(task_routes=TASK_ROUTES, task_queues=TASK_QUEUES)
    try:
        for role, queues in roles.items():
            log = tmp_path / f"{role}.log"
            output = log.open("w")
            outputs.append(output)
            logs.append(log)
            workers.append(
                subprocess.Popen(
                    [sys.executable, "-c", _WORKER],
                    env=env
                    | {
                        "FIKRAH_TEST_WORKER_QUEUES": queues,
                        "FIKRAH_TEST_WORKER_NAME": f"queue-test-{role}-{uuid4().hex}",
                    },
                    cwd=Path(__file__).resolve().parents[1],
                    stdout=output,
                    stderr=subprocess.STDOUT,
                )
            )

        def ready():
            assert all(w.poll() is None for w in workers), "test worker exited"
            return all("ready." in log.read_text() for log in logs)

        _wait_until(ready)
        publisher.send_task(
            "app.worker.tasks.process_note_job", args=[str(job_id)], retry=False
        )
        _wait_until(lambda: (tmp_path / "note-started").exists())
        with Session(engine) as db:
            extra = Note(user_id=user_id, project_id=project_id, content="second")
            legacy = Note(
                user_id=user_id, project_id=project_id, content="legacy-retry"
            )
            db.add_all([extra, legacy])
            db.flush()
            extra_job = NoteProcessingJob(note_id=extra.id, revision=1)
            legacy_job = NoteProcessingJob(
                note_id=legacy.id, revision=1, dispatched_at=datetime.now(timezone.utc)
            )
            cleanup = VectorCleanupRequest(note_id=uuid4())
            db.add_all([extra_job, legacy_job, cleanup])
            db.commit()
            extra_id, legacy_id, cleanup_id = extra_job.id, legacy_job.id, cleanup.id
        publisher.send_task("app.worker.tasks.dispatch_pending_work", retry=False)
        publisher.send_task("app.workers.tasks.deliver_account_emails", retry=False)
        publisher.send_task(
            "app.worker.tasks.process_note_job",
            args=[str(legacy_id)],
            queue=LEGACY_QUEUE,
            retry=False,
        )

        def independent_work_done():
            with Session(engine) as db:
                return (
                    db.get(NoteProcessingJob, extra_id).dispatched_at is not None
                    and db.get(VectorCleanupRequest, cleanup_id).completed_at
                    is not None
                    and (tmp_path / "email-sent").exists()
                )

        _wait_until(independent_work_done)
        assert (tmp_path / "vector-deleted").exists()
        with Session(engine) as db:
            assert db.get(NoteProcessingJob, job_id).status == "processing"
            assert db.get(NoteProcessingJob, extra_id).status == "queued"
        (tmp_path / "release-note").touch()

        def notes_done():
            with Session(engine) as db:
                return all(
                    db.get(NoteProcessingJob, identity).status == "completed"
                    for identity in (job_id, extra_id, legacy_id)
                )

        _wait_until(notes_done)
        deliveries = [
            json.loads(line)
            for line in (tmp_path / "deliveries.jsonl").read_text().splitlines()
        ]
        expected = {task: route["queue"] for task, route in TASK_ROUTES.items()}
        for record in deliveries:
            if record["args"] == [str(legacy_id)]:
                assert record["queue"] == LEGACY_QUEUE
            else:
                assert record["queue"] == expected[record["task"]]
        legacy_attempts = [row for row in deliveries if row["args"] == [str(legacy_id)]]
        assert [row["retries"] for row in legacy_attempts] == [0, 1]
    finally:
        (tmp_path / "release-note").touch()
        for worker in workers:
            if worker.poll() is None:
                worker.send_signal(signal.SIGTERM)
        for worker in workers:
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)
        for output in outputs:
            output.close()
        publisher.close()
