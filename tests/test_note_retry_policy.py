"""Classified retries preserve durable job state and release provider clients."""

from unittest.mock import Mock

import pytest
from celery.exceptions import Retry, SoftTimeLimitExceeded
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.infrastructure import worker_container
from app.models import Note, NoteProcessingJob
from app.rag.errors import RetryableProcessingError
from app.workers.tasks import process_note_job, settings
from tests import test_note_worker as worker_tests

note_job_db = worker_tests.note_job_db


@pytest.mark.parametrize("stage", ["analysis", "embedding", "vectors"])
@pytest.mark.parametrize(
    "error_type", [ValueError, TimeoutError, SoftTimeLimitExceeded]
)
def test_task_retries_only_transient_failures_and_releases_its_attempt(
    note_job_db, monkeypatch, stage, error_type
):
    path, job_id, note_id = note_job_db
    failure = error_type("controlled failure")
    text = worker_tests.FakeStructuredTextGenerator()
    embedding = worker_tests.FakeEmbeddingModel()
    vector = worker_tests.FakeVectorStore()
    text.close, embedding.close = Mock(), Mock()
    target, name = {
        "analysis": (text, "generate_structured"),
        "embedding": (embedding, "embed_note"),
        "vectors": (vector, "upsert_note"),
    }[stage]
    monkeypatch.setattr(target, name, Mock(side_effect=failure))
    monkeypatch.setattr(
        worker_container.LLMProviderFactory, "create_text_generator", lambda: text
    )
    monkeypatch.setattr(worker_container, "create_embedding_model", lambda: embedding)
    monkeypatch.setattr(worker_container, "QdrantNoteVectorStore", lambda: vector)
    retry = Mock(side_effect=Retry("controlled retry"))
    monkeypatch.setattr(process_note_job, "retry", retry)
    process_note_job.push_request(id="retry-policy-test", retries=0)
    try:
        with pytest.raises(ValueError if error_type is ValueError else Retry):
            process_note_job.run(str(job_id))
    finally:
        process_note_job.pop_request()
    expected = "failed" if error_type is ValueError else "queued"
    if expected == "queued":
        retry.assert_called_once()
        assert isinstance(retry.call_args.kwargs["exc"], RetryableProcessingError)
        assert retry.call_args.kwargs["max_retries"] == settings.max_retries
    else:
        retry.assert_not_called()
    engine = create_engine(f"sqlite:///{path}")
    try:
        with Session(engine) as db:
            job = db.get(NoteProcessingJob, job_id)
            assert job.status == expected
            assert job.started_at is None and job.worker_task_id is None
            assert db.get(Note, note_id).processing_status == expected
            assert db.get(Note, note_id).content == "Original thought"
    finally:
        engine.dispose()
    text.close.assert_called_once()
    if stage != "analysis":
        embedding.close.assert_called_once()
    else:
        embedding.close.assert_not_called()


def test_retry_exhaustion_marks_current_attempt_failed(note_job_db, monkeypatch):
    path, job_id, note_id = note_job_db
    text = worker_tests.FakeStructuredTextGenerator()
    monkeypatch.setattr(
        text, "generate_structured", Mock(side_effect=TimeoutError("controlled"))
    )
    monkeypatch.setattr(
        worker_container.LLMProviderFactory, "create_text_generator", lambda: text
    )
    result = process_note_job.apply(
        args=[str(job_id)], retries=settings.max_retries, throw=False
    )
    assert result.failed()
    engine = create_engine(f"sqlite:///{path}")
    try:
        with Session(engine) as db:
            assert db.get(NoteProcessingJob, job_id).status == "failed"
            assert db.get(Note, note_id).processing_status == "failed"
    finally:
        engine.dispose()


def test_task_limits_are_scoped_to_note_processing():
    from app.workers.tasks import (
        delete_note_vector,
        deliver_account_emails,
        dispatch_pending_work,
    )

    assert (
        process_note_job.soft_time_limit == settings.note_soft_time_limit_seconds == 240
    )
    assert process_note_job.time_limit == settings.note_hard_time_limit_seconds == 270
    assert process_note_job.autoretry_for == (RetryableProcessingError,)
    for task in (dispatch_pending_work, deliver_account_emails, delete_note_vector):
        assert task.soft_time_limit is None and task.time_limit is None
