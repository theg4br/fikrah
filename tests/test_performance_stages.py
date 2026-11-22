"""Instrumented workflows preserve their persistence and provider boundaries."""

import json
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from tests import test_note_worker
from tests import test_performance_telemetry as timing_tests

note_job_db = test_note_worker.note_job_db
measurements = timing_tests.measurements


@pytest.mark.asyncio
async def test_retrieval_stages_preserve_their_failure_boundary(measurements):
    from app.rag.errors import SearchUnavailableError
    from app.rag.retriever import retrieve_current_notes

    class Embeddings:
        def embed_query(self, query):
            raise ValueError("private query")

        def close(self):
            pass

    with pytest.raises(SearchUnavailableError):
        await retrieve_current_notes(
            None,
            uuid4(),
            uuid4(),
            "private query",
            10,
            embedding_model_factory=Embeddings,
            vector_search=lambda *_: [],
            repository=SimpleNamespace(find_completed_notes=AsyncMock()),
        )
    assert measurements[-1]["stage"] == "search.embedding"
    assert measurements[-1]["outcome"] == "error"
    assert "private" not in json.dumps(measurements)


def test_worker_timings_share_attempt_context_and_measure_dispatch_age(
    measurements, note_job_db, monkeypatch
):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy.orm import Session

    from app.infrastructure import worker_container
    from app.models import NoteProcessingJob
    from app.workers.tasks import process_note_job

    path, job_id, _ = note_job_db
    engine = create_engine(f"sqlite:///{path}")
    with Session(engine) as db:
        db.get(NoteProcessingJob, job_id).dispatched_at = datetime.now(
            timezone.utc
        ) - timedelta(seconds=2)
        db.commit()
    engine.dispose()
    monkeypatch.setattr(
        worker_container.LLMProviderFactory,
        "create_text_generator",
        test_note_worker.FakeStructuredTextGenerator,
    )
    monkeypatch.setattr(
        worker_container, "create_embedding_model", test_note_worker.FakeEmbeddingModel
    )
    monkeypatch.setattr(
        worker_container, "QdrantNoteVectorStore", test_note_worker.FakeVectorStore
    )
    assert process_note_job.apply(
        args=[str(job_id)], task_id="timing-test", throw=True
    ).successful()
    stages = {row["stage"] for row in measurements}
    assert {
        "worker.note_task",
        "note.processing",
        "note.analysis",
        "note.embedding",
        "note.claim",
        "note.persistence",
        "note.queue_wait",
        "connections.refresh",
        "db.query",
    } <= stages
    assert {row["job_id"] for row in measurements} == {str(job_id)}
    assert len({row["trace_id"] for row in measurements}) == 1
    wait = next(row for row in measurements if row["stage"] == "note.queue_wait")
    assert wait["duration_ms"] >= 1900
    assert "Original thought" not in json.dumps(measurements)


def test_first_validated_chat_delta_is_timed_once_without_logging_text(measurements):
    from app.llm.contracts import ChatNoteContext
    from app.llm.use_cases import answer_project_question

    class Generator:
        def stream_structured(self, **kwargs):
            yield '{"supported":true,"source_note_ids":["n"],"answer":"private'
            yield ' answer"}'

    deltas = []
    answer = answer_project_question(
        Generator(),
        "private question",
        [ChatNoteContext(note_id="n", title="private title", content="private note")],
        [],
        on_delta=deltas.append,
    )
    assert answer.answer == "".join(deltas) == "private answer"
    assert [row["stage"] for row in measurements].count(
        "chat.generation_first_delta"
    ) == 1
    assert measurements[-1]["stage"] == "llm.chat_generation"
    assert "private" not in json.dumps(measurements)


def test_celery_logging_bypasses_redirected_stdout(monkeypatch):
    from app.workers import celery_app as worker_module

    sink = StringIO()
    calls = []
    monkeypatch.setattr(worker_module.sys, "__stdout__", sink)
    monkeypatch.setattr(
        worker_module, "start_timing_logs", lambda **kwargs: calls.append(kwargs)
    )
    worker_module._start_worker_timing_logs()
    assert calls == [{"sink": sink}]


def test_control_tasks_have_isolated_traces_and_keep_existing_calls(
    measurements, monkeypatch
):
    from app.workers import tasks

    repository = Mock()
    repository.claim_pending.return_value = ([], [])
    note_id, cleanup_id = uuid4(), uuid4()
    repository.pending_vector_cleanup.return_value = note_id
    vectors, delivery = Mock(), AsyncMock()
    monkeypatch.setattr(tasks, "WorkerOutboxRepository", lambda: repository)
    monkeypatch.setattr(tasks, "QdrantNoteVectorStore", lambda: vectors)
    monkeypatch.setattr(tasks, "deliver_pending_account_emails", delivery)
    tasks.deliver_account_emails()
    tasks.dispatch_pending_work()
    tasks.delete_note_vector(str(cleanup_id))
    assert {row["stage"] for row in measurements} == {
        "worker.email_batch",
        "worker.dispatch",
        "worker.vector_cleanup",
    }
    assert len({row["trace_id"] for row in measurements}) == 3
    assert all("job_id" not in row for row in measurements)
    delivery.assert_awaited_once()
    vectors.delete_note.assert_called_once_with(note_id)
    repository.complete_vector_cleanup.assert_called_once_with(cleanup_id)
    assert repository.close.call_count == 2
