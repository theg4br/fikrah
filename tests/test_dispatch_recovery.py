"""Failed publication releases only this batch's unpublished dispatch claims."""

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.core.config import get_worker_settings
from app.infrastructure.persistence.worker_outbox import WorkerOutboxRepository
from app.models import NoteProcessingJob, VectorCleanupRequest
from app.workers import tasks
from tests import test_note_worker as worker_tests

note_job_db = worker_tests.note_job_db


@pytest.fixture
def publications(monkeypatch):
    repository = Mock()
    jobs, cleanups = [uuid4() for _ in range(3)], [uuid4() for _ in range(2)]
    repository.claim_pending.return_value = (jobs, cleanups)
    monkeypatch.setattr(tasks, "WorkerOutboxRepository", lambda: repository)
    connection = object()
    connect = Mock(return_value=nullcontext(connection))
    monkeypatch.setattr(tasks, "bounded_write_connection", connect)
    notes, vectors = Mock(), Mock()
    monkeypatch.setattr(tasks.process_note_job, "apply_async", notes)
    monkeypatch.setattr(tasks.delete_note_vector, "apply_async", vectors)
    return repository, jobs, cleanups, connection, connect, notes, vectors


@pytest.mark.parametrize(
    "kind,index", [("job", 0), ("job", 1), ("job", 2), ("cleanup", 0), ("cleanup", 1)]
)
def test_partial_publication_releases_failed_and_unattempted_items(
    publications, kind, index
):
    repository, jobs, cleanups, connection, _, notes, vectors = publications
    failure = ConnectionError("controlled ambiguous publication failure")
    target = notes if kind == "job" else vectors
    target.side_effect = [None] * index + [failure]
    with pytest.raises(ConnectionError) as caught:
        tasks.dispatch_pending_work.run()
    assert caught.value is failure
    claimed = repository.claim_pending.call_args.kwargs["now"]
    repository.release_unpublished.assert_called_once_with(
        job_ids=jobs[index:] if kind == "job" else [],
        cleanup_ids=cleanups if kind == "job" else cleanups[index:],
        dispatched_at=claimed,
    )
    assert notes.call_count == (index + 1 if kind == "job" else len(jobs))
    assert vectors.call_count == (0 if kind == "job" else index + 1)
    for call in notes.call_args_list + vectors.call_args_list:
        assert call.kwargs["connection"] is connection and call.kwargs["retry"] is False
    repository.close.assert_called_once()


def test_connection_failure_releases_the_whole_batch(publications):
    repository, jobs, cleanups, _, connect, *_ = publications
    connect.side_effect = ConnectionError("controlled")
    with pytest.raises(ConnectionError):
        tasks.dispatch_pending_work.run()
    repository.release_unpublished.assert_called_once_with(
        job_ids=jobs,
        cleanup_ids=cleanups,
        dispatched_at=repository.claim_pending.call_args.kwargs["now"],
    )


def test_budget_expiry_releases_remaining_work_without_more_publication(
    publications, monkeypatch
):
    repository, jobs, cleanups, _, _, notes, vectors = publications
    ticks = iter([0, 0, tasks.settings.dispatch_publish_budget_seconds + 1])
    monkeypatch.setattr(tasks, "monotonic", lambda: next(ticks))
    with pytest.raises(TimeoutError, match="budget expired"):
        tasks.dispatch_pending_work.run()
    notes.assert_called_once()
    vectors.assert_not_called()
    repository.release_unpublished.assert_called_once_with(
        job_ids=jobs[1:],
        cleanup_ids=cleanups,
        dispatched_at=repository.claim_pending.call_args.kwargs["now"],
    )


def test_release_failure_preserves_the_publication_error(publications, caplog):
    repository, _, _, _, _, notes, _ = publications
    failure = ConnectionError("original publication failure")
    notes.side_effect = failure
    repository.release_unpublished.side_effect = RuntimeError("database unavailable")
    with pytest.raises(ConnectionError) as caught:
        tasks.dispatch_pending_work.run()
    assert caught.value is failure
    assert "Failed to release unpublished dispatch claims" in caplog.text
    assert "database unavailable" not in caplog.text
    repository.close.assert_called_once()


def test_successful_and_empty_batches_do_not_release_claims(publications):
    repository, jobs, cleanups, connection, connect, notes, vectors = publications
    tasks.dispatch_pending_work.run()
    assert notes.call_count == len(jobs) and vectors.call_count == len(cleanups)
    repository.release_unpublished.assert_not_called()
    for call in notes.call_args_list + vectors.call_args_list:
        assert call.kwargs["connection"] is connection and call.kwargs["retry"] is False
    connect.reset_mock()
    repository.claim_pending.return_value = ([], [])
    tasks.dispatch_pending_work.run()
    connect.assert_not_called()
    assert repository.close.call_count == 2


def _claim(repository, now):
    return repository.claim_pending(
        now=now,
        stale_dispatch_after=timedelta(minutes=5),
        stale_processing_after=timedelta(minutes=15),
        batch_size=100,
    )


def _seed_cleanup(engine):
    with Session(engine) as db:
        cleanup = VectorCleanupRequest(note_id=uuid4())
        db.add(cleanup)
        db.commit()
        return cleanup.id


def test_unpublished_intents_are_immediately_eligible_without_waiting_for_stale_timer(
    note_job_db,
):
    path, job_id, _ = note_job_db
    engine = create_engine(f"sqlite:///{path}")
    cleanup_id = _seed_cleanup(engine)
    repository = WorkerOutboxRepository()
    now = datetime.now(timezone.utc)
    try:
        assert _claim(repository, now) == ([job_id], [cleanup_id])
        assert _claim(repository, now + timedelta(seconds=1)) == ([], [])
        repository.release_unpublished(
            job_ids=[job_id], cleanup_ids=[cleanup_id], dispatched_at=now
        )
        assert _claim(repository, now + timedelta(seconds=2)) == (
            [job_id],
            [cleanup_id],
        )
    finally:
        repository.close()
        engine.dispose()


@pytest.mark.parametrize(
    "scenario", ["newer_claim", "processing", "completed", "failed", "owned_queued"]
)
def test_release_cannot_clear_newer_or_active_completed_claims(note_job_db, scenario):
    path, job_id, _ = note_job_db
    engine = create_engine(f"sqlite:///{path}")
    cleanup_id = _seed_cleanup(engine)
    repository = WorkerOutboxRepository()
    now = datetime.now(timezone.utc)
    try:
        _claim(repository, now)
        with Session(engine) as db:
            job, cleanup = (
                db.get(NoteProcessingJob, job_id),
                db.get(VectorCleanupRequest, cleanup_id),
            )
            if scenario == "newer_claim":
                job.dispatched_at = cleanup.dispatched_at = now + timedelta(seconds=1)
            elif scenario == "owned_queued":
                job.worker_task_id, job.started_at = "another-owner", now
                cleanup.completed_at = now
            else:
                job.status = scenario
                cleanup.completed_at = now
            db.commit()
        repository.release_unpublished(
            job_ids=[job_id], cleanup_ids=[cleanup_id], dispatched_at=now
        )
        with Session(engine) as db:
            job, cleanup = (
                db.get(NoteProcessingJob, job_id),
                db.get(VectorCleanupRequest, cleanup_id),
            )
            assert job.dispatched_at is not None and cleanup.dispatched_at is not None
    finally:
        repository.close()
        engine.dispose()


def test_release_is_one_transaction_across_job_and_cleanup_metadata(note_job_db):
    path, job_id, _ = note_job_db
    engine = create_engine(f"sqlite:///{path}")
    cleanup_id = _seed_cleanup(engine)
    repository = WorkerOutboxRepository()
    now = datetime.now(timezone.utc)

    def fail_cleanup(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE vector_cleanup_requests"):
            raise RuntimeError("controlled second-update failure")

    try:
        _claim(repository, now)
        event.listen(repository._engine, "before_cursor_execute", fail_cleanup)
        with pytest.raises(RuntimeError):
            repository.release_unpublished(
                job_ids=[job_id], cleanup_ids=[cleanup_id], dispatched_at=now
            )
        with Session(engine) as db:
            assert db.get(NoteProcessingJob, job_id).dispatched_at is not None
            assert db.get(VectorCleanupRequest, cleanup_id).dispatched_at is not None
    finally:
        event.remove(repository._engine, "before_cursor_execute", fail_cleanup)
        repository.close()
        engine.dispose()


def test_publish_budget_configuration():
    assert get_worker_settings({}).dispatch_publish_budget_seconds == 10
    assert (
        get_worker_settings(
            {"FIKRAH_DISPATCH_PUBLISH_BUDGET_SECONDS": "2"}
        ).dispatch_publish_budget_seconds
        == 2
    )
    for invalid in ("0", "-1", "invalid"):
        with pytest.raises(ValueError, match="FIKRAH_DISPATCH_PUBLISH_BUDGET_SECONDS"):
            get_worker_settings({"FIKRAH_DISPATCH_PUBLISH_BUDGET_SECONDS": invalid})


def test_postgres_release_rechecks_fence_after_a_concurrent_claim_commits(
    postgres_case,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import update

    engine, (_, _, _, job_id) = postgres_case
    cleanup_id = _seed_cleanup(engine)
    repository = WorkerOutboxRepository()
    now = datetime.now(timezone.utc)
    newer = now + timedelta(seconds=1)
    trying = Event()

    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE note_processing_jobs"):
            trying.set()

    try:
        _claim(repository, now)
        event.listen(repository._engine, "before_cursor_execute", observe)
        with ThreadPoolExecutor(max_workers=1) as executor:
            with engine.begin() as connection:
                connection.execute(
                    update(NoteProcessingJob)
                    .where(NoteProcessingJob.id == job_id)
                    .values(dispatched_at=newer)
                )
                connection.execute(
                    update(VectorCleanupRequest)
                    .where(VectorCleanupRequest.id == cleanup_id)
                    .values(dispatched_at=newer)
                )
                pending = executor.submit(
                    repository.release_unpublished,
                    job_ids=[job_id],
                    cleanup_ids=[cleanup_id],
                    dispatched_at=now,
                )
                assert trying.wait(3), "release never reached its database update"
            pending.result(timeout=5)
        with Session(engine) as db:
            assert db.get(NoteProcessingJob, job_id).dispatched_at == newer
            assert db.get(VectorCleanupRequest, cleanup_id).dispatched_at == newer
    finally:
        event.remove(repository._engine, "before_cursor_execute", observe)
        repository.close()
