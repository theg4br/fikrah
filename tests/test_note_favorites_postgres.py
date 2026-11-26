"""Migration safety and last-committed favorite writes under PostgreSQL locks."""

import asyncio
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, inspect, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.models import Note
from app.repositories.notes import NoteRepository
from app.services.notes_service import NoteService


def test_favorite_migration_preserves_existing_notes(postgres_engine):
    command.upgrade(Config("alembic.ini"), "20261126_01")
    user_id, project_id, note_id = uuid4(), uuid4(), uuid4()
    with postgres_engine.begin() as connection:
        connection.execute(text("INSERT INTO users (id,email) VALUES (:id,'favorite-migration@example.com')"), {"id": user_id})
        connection.execute(text("INSERT INTO projects (id,user_id,name) VALUES (:id,:user_id,'Existing')"), {"id": project_id, "user_id": user_id})
        connection.execute(text("INSERT INTO notes (id,user_id,project_id,content) VALUES (:id,:user_id,:project_id,'Existing note')"), {"id": note_id, "user_id": user_id, "project_id": project_id})
        original_time = connection.scalar(text("SELECT updated_at FROM notes WHERE id=:id"), {"id": note_id})
    command.upgrade(Config("alembic.ini"), "head")
    with Session(postgres_engine) as db:
        note = db.get(Note, note_id)
        assert note.is_favorite is False
        assert note.content == "Existing note"
        assert note.updated_at == original_time
        assert note.revision == note.processing_generation == 1
    command.downgrade(Config("alembic.ini"), "20261126_01")
    assert "is_favorite" not in {c["name"] for c in inspect(postgres_engine).get_columns("notes")}
    command.upgrade(Config("alembic.ini"), "head")


@pytest.mark.asyncio
async def test_last_committed_favorite_set_wins_without_changing_content(postgres_case):
    engine, (user_id, _, note_id, _) = postgres_case
    with engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
    async_engine = create_async_engine(
        engine.url.set(drivername="postgresql+asyncpg", query={}),
        connect_args={"server_settings": {"search_path": schema}},
    )
    sessions = async_sessionmaker(async_engine, expire_on_commit=False)
    first_wrote = asyncio.Event()
    release_first = asyncio.Event()
    second_waiting = asyncio.Event()

    class PausedRepository(NoteRepository):
        async def set_favorite(self, db, note, value):
            await super().set_favorite(db, note, value)
            first_wrote.set()
            await release_first.wait()

    service = NoteService(PausedRepository())
    lock_requests = 0

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal lock_requests
        if "FROM notes" in statement and "FOR UPDATE" in statement:
            lock_requests += 1
            if lock_requests == 2:
                second_waiting.set()

    async def write(service, value):
        async with sessions() as db:
            return await service.set_favorite(db, user_id, note_id, value)

    event.listen(async_engine.sync_engine, "before_cursor_execute", observe)
    pending = []
    try:
        with Session(engine) as db:
            original_time = db.get(Note, note_id).updated_at
        pending.append(asyncio.create_task(write(service, True)))
        await asyncio.wait_for(first_wrote.wait(), timeout=5)
        pending.append(asyncio.create_task(write(NoteService(), False)))
        await asyncio.wait_for(second_waiting.wait(), timeout=5)
        release_first.set()
        first, second = await asyncio.wait_for(asyncio.gather(*pending), timeout=5)
        assert first.is_favorite is True
        assert second.is_favorite is False
        with Session(engine) as db:
            note = db.scalar(select(Note).where(Note.id == note_id))
            assert note.is_favorite is False
            assert note.updated_at == original_time
            assert note.revision == note.processing_generation == 1
            assert note.content == "Lock order"
    finally:
        release_first.set()
        for task in pending:
            if not task.done():
                task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        event.remove(async_engine.sync_engine, "before_cursor_execute", observe)
        await async_engine.dispose()
