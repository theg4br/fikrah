"""Application use case for asynchronous note analysis and indexing."""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from app.core.telemetry import measure, timed
from app.llm.lifecycle import run_with_provider
from app.llm.ports import EmbeddingModel, StructuredTextGenerator
from app.llm.use_cases import analyze_note
from app.services.processing_errors import RetryableProcessingError
from app.services.processing_ports import NoteProcessingRepository, NoteVectorStore


class NoteProcessingService:
    def __init__(
        self,
        repository: NoteProcessingRepository,
        vector_store: NoteVectorStore,
        text_generator_factory: Callable[[], StructuredTextGenerator],
        embedding_model_factory: Callable[[], EmbeddingModel],
        stale_processing_after: timedelta,
        retryable_error: Callable[[Exception], bool] | None = None,
    ) -> None:
        self._repository = repository
        self._vector_store = vector_store
        self._text_generator_factory = text_generator_factory
        self._embedding_model_factory = embedding_model_factory
        self._stale_processing_after = stale_processing_after
        self._retryable_error = retryable_error

    @timed("note.processing")
    def process(
        self, job_id: UUID, worker_task_id: str, *, terminal_on_error: bool = False
    ) -> None:
        now = datetime.now(timezone.utc)
        snapshot = self._repository.claim(
            job_id,
            f"{worker_task_id}:{uuid4()}",
            now - self._stale_processing_after,
        )
        if snapshot is None:
            return

        try:
            with measure("note.analysis"):
                analysis = run_with_provider(
                    self._text_generator_factory,
                    lambda generator: analyze_note(generator, snapshot.content),
                )
            with measure("note.embedding"):
                embedding = run_with_provider(
                    self._embedding_model_factory,
                    lambda model: model.embed_note(snapshot.content),
                )

            def persist_vector() -> None:
                self._vector_store.upsert_note(
                    snapshot.note_id,
                    embedding,
                    user_id=snapshot.user_id,
                    project_id=snapshot.project_id,
                    revision=snapshot.revision,
                    processing_generation=snapshot.processing_generation,
                )

            self._repository.complete_if_current(snapshot, analysis, persist_vector)
        except Exception as error:
            retryable = self._retryable_error is not None and self._retryable_error(error)
            terminal = terminal_on_error or not retryable
            self._repository.fail_if_current(snapshot, terminal=terminal)
            if not terminal:
                raise RetryableProcessingError("Transient note processing failure") from error
            raise
