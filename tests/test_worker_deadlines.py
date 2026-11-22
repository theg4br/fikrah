"""Opt-in prefork verification: real signals, isolated PostgreSQL and Redis.

No provider or vector network requests are made. Eager task execution cannot
exercise Celery's soft/hard limits, so this starts a dedicated test worker.
"""

import os
import signal
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from celery import Celery
from sqlalchemy.orm import Session

from app.contracts.processing import NoteProcessingSnapshot
from app.infrastructure.persistence.worker_notes import WorkerNoteProcessingRepository
from app.llm.contracts import NoteAnalysis
from app.models import Note, NoteProcessingJob

_WORKER = """
import os, time
from celery.exceptions import SoftTimeLimitExceeded
from app.workers.celery_app import celery_app
from app.infrastructure import worker_container
class Generator:
    def close(self): pass
    def generate_structured(self, **kwargs):
        if os.environ["FIKRAH_TEST_DEADLINE_MODE"] == "soft":
            time.sleep(60)
        else:
            try:
                time.sleep(60)
            except SoftTimeLimitExceeded:
                time.sleep(60)
        raise AssertionError("test provider should be interrupted")
worker_container.LLMProviderFactory.create_text_generator = staticmethod(Generator)
celery_app.worker_main([
    "worker", "--pool=prefork", "--concurrency=1", "--loglevel=INFO",
    "--without-gossip", "--without-mingle", "--without-heartbeat",
    "--queues=" + os.environ["FIKRAH_TEST_DEADLINE_QUEUE"],
    "--hostname=deadline-" + os.environ["FIKRAH_TEST_DEADLINE_QUEUE"] + "@localhost",
])
"""


def _wait_until(check, seconds=20):
    until = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < until, "test worker deadline expired"
        time.sleep(0.05)


@pytest.mark.parametrize("mode", ["soft", "hard"])
def test_prefork_deadline_releases_or_recovers_current_attempt(
    postgres_case, tmp_path, mode
):
    redis_url = os.getenv("FIKRAH_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("Set FIKRAH_TEST_REDIS_URL to an isolated Redis test instance")
    engine, (_, _, note_id, job_id) = postgres_case
    queue = f"deadline-test-{uuid4().hex}"
    environment = os.environ | {
        "DATABASE_URL": engine.url.render_as_string(hide_password=False),
        "REDIS_URL": redis_url,
        "FIKRAH_NOTE_SOFT_TIME_LIMIT_SECONDS": "2",
        "FIKRAH_NOTE_HARD_TIME_LIMIT_SECONDS": "4",
        "FIKRAH_CELERY_MAX_RETRIES": "1",
        "FIKRAH_CELERY_RETRY_BACKOFF_SECONDS": "1",
        "FIKRAH_TEST_DEADLINE_QUEUE": queue,
        "FIKRAH_TEST_DEADLINE_MODE": mode,
    }
    log = tmp_path / "worker.log"
    publisher = Celery("deadline-test-publisher", broker=redis_url)
    with log.open("w") as output:
        worker = subprocess.Popen(
            [sys.executable, "-c", _WORKER],
            env=environment,
            cwd=Path(__file__).resolve().parents[1],
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:

            def ready():
                assert worker.poll() is None, "test worker exited before startup"
                return "ready." in log.read_text()

            _wait_until(ready)
            publisher.send_task(
                "app.worker.tasks.process_note_job",
                args=[str(job_id)],
                queue=queue,
                soft_time_limit=2,
                time_limit=4,
                retry=False,
            )
            if mode == "soft":

                def failed():
                    with Session(engine) as db:
                        return db.get(NoteProcessingJob, job_id).status == "failed"

                _wait_until(failed)
                with Session(engine) as db:
                    job = db.get(NoteProcessingJob, job_id)
                    assert job.attempt_count == 2  # initial attempt + one retry
                    assert job.worker_task_id is None and job.started_at is None
                    assert db.get(Note, note_id).processing_status == "failed"
                assert "Soft time limit (2s) exceeded" in log.read_text()
            else:
                _wait_until(lambda: "Hard time limit (4s) exceeded" in log.read_text())
                with Session(engine) as db:
                    job = db.get(NoteProcessingJob, job_id)
                    note = db.get(Note, note_id)
                    assert (
                        job.status == "processing"
                    )  # killed processes cannot clean up
                    old = NoteProcessingSnapshot(
                        job_id=job.id,
                        note_id=note.id,
                        user_id=note.user_id,
                        project_id=note.project_id,
                        revision=note.revision,
                        processing_generation=note.processing_generation,
                        content=note.content,
                        attempt_token=job.worker_task_id,
                    )
                repository = WorkerNoteProcessingRepository()
                try:
                    # Advance only the test lease cutoff; do not wait 15 minutes.
                    recovered = repository.claim(
                        job_id,
                        "recovered-attempt",
                        datetime.now(timezone.utc) + timedelta(seconds=1),
                    )
                    assert recovered is not None
                    assert recovered == replace(old, attempt_token="recovered-attempt")
                    analysis = NoteAnalysis(
                        title="Recovered", refinement_proposal="Recovered", concepts=[]
                    )
                    assert not repository.fail_if_current(old, terminal=True)
                    assert repository.complete_if_current(
                        recovered, analysis, lambda: None
                    )
                    write_calls = []
                    assert not repository.complete_if_current(
                        old, analysis, lambda: write_calls.append(True)
                    )
                    assert write_calls == []
                finally:
                    repository.close()
                with Session(engine) as db:
                    assert db.get(NoteProcessingJob, job_id).status == "completed"
                    assert db.get(Note, note_id).processing_status == "completed"
        finally:
            if worker.poll() is None:
                worker.send_signal(signal.SIGTERM)
                try:
                    worker.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait(timeout=5)
            publisher.close()
