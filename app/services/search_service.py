"""Project-scoped semantic retrieval use case."""

import asyncio
from collections.abc import Callable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.search import SearchMatch
from app.core.config import get_settings
from app.core.execution import BlockingRunner, execution_budget
from app.core.telemetry import timed
from app.llm.ports import EmbeddingModel
from app.rag.errors import SearchUnavailableError
from app.rag.retriever import retrieve_current_notes
from app.repositories.search import SearchRepository
from app.services.search_errors import SearchProjectNotFound


class SemanticSearchService:
    def __init__(
        self,
        embedding_model_factory: Callable[[], EmbeddingModel],
        vector_search: Callable[
            [list[float], UUID, UUID, int], list[tuple[UUID, int, float]]
        ],
        repository: SearchRepository | None = None,
        *,
        run_blocking: BlockingRunner = asyncio.to_thread,
    ) -> None:
        self._embedding_model_factory = embedding_model_factory
        self._vector_search = vector_search
        self._repository = repository or SearchRepository()
        self._run_blocking = run_blocking

    @timed("search.total")
    async def retrieve(
        self,
        db: AsyncSession,
        user_id: UUID,
        project_id: UUID,
        query: str,
        limit: int,
    ) -> list[SearchMatch]:
        project = await self._repository.find_owned_project(db, user_id, project_id)
        if project is None:
            raise SearchProjectNotFound
        # Ownership is checked before external work; release the read transaction
        # while providers run, then validate their candidates in a fresh read.
        await db.rollback()
        try:
            async with execution_budget(get_settings().fikrah_search_timeout_seconds):
                return await retrieve_current_notes(
                    db,
                    user_id,
                    project_id,
                    query,
                    limit,
                    embedding_model_factory=self._embedding_model_factory,
                    vector_search=self._vector_search,
                    repository=self._repository,
                    run_blocking=self._run_blocking,
                )
        except TimeoutError as error:
            raise SearchUnavailableError("Semantic retrieval budget expired") from error
        finally:
            await db.rollback()
