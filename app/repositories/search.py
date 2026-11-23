"""PostgreSQL queries that validate semantic-search candidates."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Note, Project
from app.repositories.project_queries import owned_project_query


class SearchRepository:
    async def find_owned_project(
        self, db: AsyncSession, user_id: UUID, project_id: UUID
    ) -> Project | None:
        return await db.scalar(owned_project_query(user_id, project_id))

    async def find_completed_notes(
        self,
        db: AsyncSession,
        user_id: UUID,
        project_id: UUID,
        note_ids: set[UUID],
    ) -> list[Note]:
        notes = await db.scalars(
            select(Note).where(
                Note.id.in_(note_ids),
                Note.user_id == user_id,
                Note.project_id == project_id,
                Note.processing_status == "completed",
            )
        )
        return list(notes)
