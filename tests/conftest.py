"""External-service substitutes and opt-in isolated PostgreSQL test schemas."""

import os
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db.base import Base
from app.db.session import get_db_session as database_dependency
from app.main import app as fastapi_app
from app.models import Note, NoteProcessingJob, Project, User


@pytest.fixture(autouse=True)
def isolated_processing_notifications(monkeypatch):
    from app.infrastructure.container import note_service

    monkeypatch.setattr(note_service, "_notify_processing", AsyncMock())


@pytest.fixture(autouse=True)
def isolated_request_budgets(monkeypatch):
    from app.infrastructure.container import request_budgets

    monkeypatch.setattr(request_budgets._store, "consume", AsyncMock())


@pytest.fixture
def postgres_engine(monkeypatch):
    """Provide an empty disposable schema; never use a non-test database."""
    raw_url = os.getenv("FIKRAH_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip(
            "Set FIKRAH_TEST_DATABASE_URL to a dedicated PostgreSQL test database"
        )
    url = make_url(raw_url)
    if not url.drivername.startswith("postgresql") or not (url.database or "").endswith(
        "_test"
    ):
        pytest.fail("PostgreSQL regression database must have a name ending in _test")
    schema = f"security_test_{uuid4().hex}"
    control = create_engine(url)
    with control.begin() as connection:
        connection.execute(CreateSchema(schema))
    scoped_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(scoped_url)
    monkeypatch.setenv("DATABASE_URL", scoped_url.render_as_string(hide_password=False))
    try:
        yield engine
    finally:
        engine.dispose()
        with control.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        control.dispose()


@pytest.fixture
def postgres_case(postgres_engine):
    """Seed the existing worker/auth concurrency scenario in its own schema."""
    Base.metadata.create_all(postgres_engine)
    with Session(postgres_engine) as session:
        user = User(email="postgres-lock-test@example.com")
        session.add(user)
        session.flush()
        project = Project(user_id=user.id, name="Isolated test")
        session.add(project)
        session.flush()
        note = Note(user_id=user.id, project_id=project.id, content="Lock order")
        session.add(note)
        session.flush()
        job = NoteProcessingJob(note_id=note.id, revision=1)
        session.add(job)
        session.commit()
        ids = (user.id, project.id, note.id, job.id)
    return postgres_engine, ids


@pytest_asyncio.fixture
async def auth_client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv(
        "FIKRAH_JWT_SECRET", "test-signing-secret-that-is-at-least-32-characters"
    )
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session() -> AsyncIterator:
        async with sessions() as session:
            yield session

    fastapi_app.dependency_overrides[database_dependency] = override_session
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        yield client
    fastapi_app.dependency_overrides.clear()
    await engine.dispose()
