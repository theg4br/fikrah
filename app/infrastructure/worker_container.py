"""Composition root for Celery worker use cases and infrastructure adapters."""

from datetime import timedelta

from app.infrastructure.persistence.worker_notes import WorkerNoteProcessingRepository
from app.infrastructure.processing_failures import is_retryable_processing_failure
from app.infrastructure.vector.qdrant import QdrantNoteVectorStore
from app.llm.factory import LLMProviderFactory
from app.services.note_processing_service import NoteProcessingService


def create_note_processing_service(
    stale_processing_after: timedelta,
) -> tuple[NoteProcessingService, WorkerNoteProcessingRepository]:
    repository = WorkerNoteProcessingRepository()
    service = NoteProcessingService(
        repository=repository,
        vector_store=QdrantNoteVectorStore(),
        text_generator_factory=LLMProviderFactory.create_text_generator,
        embedding_model_factory=LLMProviderFactory.create_embedding_model,
        stale_processing_after=stale_processing_after,
        retryable_error=is_retryable_processing_failure,
    )
    return service, repository
