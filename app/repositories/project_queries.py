"""Shared project ownership predicate with an explicit optional row lock."""

from uuid import UUID

from sqlalchemy import Select, select

from app.models import Project


def owned_project_query(
    user_id: UUID, project_id: UUID, *, lock: bool = False
) -> Select[tuple[Project]]:
    query = select(Project).where(Project.id == project_id, Project.user_id == user_id)
    return query.with_for_update() if lock else query
