"""Celery tasks for note processing and Qdrant cleanup."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from time import monotonic
from uuid import UUID

from app.core.telemetry import correlation, measure
from app.infrastructure.broker import bounded_write_connection
from app.infrastructure.email.worker import deliver_pending_account_emails
from app.infrastructure.persistence.worker_outbox import WorkerOutboxRepository
from app.infrastructure.processing_failures import is_retryable_processing_failure
from app.infrastructure.vector.qdrant import QdrantNoteVectorStore
from app.infrastructure.worker_container import create_note_processing_service
from app.services.processing_errors import RetryableProcessingError
from app.workers.celery_app import celery_app, settings

_STALE_DISPATCH_AFTER = timedelta(seconds=settings.stale_dispatch_after_seconds)
_STALE_PROCESSING_AFTER = timedelta(seconds=settings.stale_processing_after_seconds)
logger = logging.getLogger(__name__)


@celery_app.task(name="app.workers.tasks.deliver_account_emails", acks_late=True)
def deliver_account_emails() -> None:
    with correlation(), measure("worker.email_batch"):
        asyncio.run(deliver_pending_account_emails())


@celery_app.task(
    bind=True,
    name="app.worker.tasks.process_note_job",
    acks_late=True,
    autoretry_for=(RetryableProcessingError,),
    retry_backoff=settings.retry_backoff_seconds,
    retry_jitter=True,
    retry_kwargs={"max_retries": settings.max_retries},
    soft_time_limit=settings.note_soft_time_limit_seconds,
    time_limit=settings.note_hard_time_limit_seconds,
)
def process_note_job(self, job_id: str) -> None:
    parsed_id = UUID(job_id)
    with correlation(parsed_id), measure("worker.note_task"):
        repository = None
        try:
            service, repository = create_note_processing_service(_STALE_PROCESSING_AFTER)
            service.process(
                parsed_id,
                self.request.id,
                terminal_on_error=self.request.retries >= settings.max_retries,
            )
        except RetryableProcessingError:
            raise
        except Exception as error:
            # Claim/setup failures have no snapshot to release. PostgreSQL's
            # durable dispatcher still owns recovery if a process is killed.
            if is_retryable_processing_failure(error):
                raise RetryableProcessingError("Transient worker dependency failure") from error
            raise
        finally:
            if repository is not None:
                repository.close()


@celery_app.task(name="app.worker.tasks.delete_note_vector", acks_late=True)
def delete_note_vector(cleanup_id: str) -> None:
    with correlation(), measure("worker.vector_cleanup"):
        repository = WorkerOutboxRepository()
        try:
            note_id = repository.pending_vector_cleanup(UUID(cleanup_id))
            if note_id is None:
                return
            QdrantNoteVectorStore().delete_note(note_id)
            repository.complete_vector_cleanup(UUID(cleanup_id))
        finally:
            repository.close()


@celery_app.task(name="app.worker.tasks.dispatch_pending_work")
def dispatch_pending_work() -> None:
    """Publish durable PostgreSQL outbox records, recovering stale dispatches."""
    with correlation(), measure("worker.dispatch"):
        repository = WorkerOutboxRepository()
        try:
            claimed_at = datetime.now(timezone.utc)
            job_ids, cleanup_ids = repository.claim_pending(
                now=claimed_at,
                stale_dispatch_after=_STALE_DISPATCH_AFTER,
                stale_processing_after=_STALE_PROCESSING_AFTER,
                batch_size=settings.dispatch_batch_size,
            )
            if not job_ids and not cleanup_ids:
                return
            deadline = monotonic() + settings.dispatch_publish_budget_seconds
            job_index = cleanup_index = 0
            try:
                with bounded_write_connection(celery_app) as connection:
                    for job_id in job_ids:
                        if monotonic() >= deadline:
                            raise TimeoutError("Dispatch publication budget expired")
                        process_note_job.apply_async(
                            args=[str(job_id)], connection=connection, retry=False
                        )
                        job_index += 1
                    for cleanup_id in cleanup_ids:
                        if monotonic() >= deadline:
                            raise TimeoutError("Dispatch publication budget expired")
                        delete_note_vector.apply_async(
                            args=[str(cleanup_id)], connection=connection, retry=False
                        )
                        cleanup_index += 1
            except Exception:
                try:
                    repository.release_unpublished(
                        job_ids=job_ids[job_index:],
                        cleanup_ids=cleanup_ids[cleanup_index:],
                        dispatched_at=claimed_at,
                    )
                except Exception:  # noqa: BLE001 - keep original failure; the stale lease owns recovery
                    # The stale-dispatch lease is the fallback when the database
                    # also fails. Keep the publication error as the task failure.
                    logger.warning("Failed to release unpublished dispatch claims")
                raise
        finally:
            repository.close()
