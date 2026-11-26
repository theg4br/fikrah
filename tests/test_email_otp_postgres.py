"""Real row-lock and migration checks in disposable PostgreSQL test schemas."""

import asyncio
from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.contracts.auth import EmailOtpConfirmation
from app.db.base import Base
from app.infrastructure.container import auth_service
from app.models import EmailActionToken, User
from app.services.auth_errors import AuthServiceError
from app.services.email_otp_service import EmailOtpService


def test_otp_migration_preserves_existing_link_rows(postgres_engine):
    command.upgrade(Config("alembic.ini"), "20261121_03")
    from uuid import uuid4

    user_id, action_id = uuid4(), uuid4()
    with postgres_engine.begin() as connection:
        connection.execute(text("INSERT INTO users (id, email) VALUES (:id, :email)"), {"id": user_id, "email": "legacy@example.com"})
        connection.execute(text(
            "INSERT INTO email_action_tokens (id, user_id, token_hash, purpose, expires_at) "
            "VALUES (:id, :user_id, :hash, 'verify_email', :expires)"
        ), {"id": action_id, "user_id": user_id, "hash": "a" * 64, "expires": datetime.now(timezone.utc)})
    command.upgrade(Config("alembic.ini"), "head")
    columns = {column["name"] for column in inspect(postgres_engine).get_columns("email_action_tokens")}
    assert {"otp_digest", "failed_attempts"} <= columns
    with Session(postgres_engine) as db:
        old = db.get(EmailActionToken, action_id)
        assert old.otp_digest is None
        assert old.failed_attempts == 0
        assert old.token_hash == "a" * 64
    command.downgrade(Config("alembic.ini"), "20261121_03")
    assert "otp_digest" not in {column["name"] for column in inspect(postgres_engine).get_columns("email_action_tokens")}
    command.upgrade(Config("alembic.ini"), "head")


@pytest.mark.asyncio
@pytest.mark.parametrize("valid", [True, False])
async def test_concurrent_code_submissions_are_serialized(postgres_engine, monkeypatch, valid):
    monkeypatch.setenv("FIKRAH_JWT_SECRET", "otp-postgres-private-signing-key-with-32-characters")
    Base.metadata.create_all(postgres_engine)
    with postgres_engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
    engine = create_async_engine(
        postgres_engine.url.set(drivername="postgresql+asyncpg", query={}),
        connect_args={"server_settings": {"search_path": schema}},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db, db.begin():
            user = User(email="concurrent@example.com")
            db.add(user)
            await db.flush()
            issued = await EmailOtpService(auth_service._repository).issue(db, user, "verify_email")
        wrong = "000000" if issued.code != "000000" else "111111"
        count = 2 if valid else 8
        barrier = asyncio.Barrier(count)

        async def submit():
            async with sessions() as db:
                await barrier.wait()
                try:
                    await auth_service.verify_email_otp(
                        EmailOtpConfirmation(email="concurrent@example.com", code=issued.code if valid else wrong), db
                    )
                    return True
                except AuthServiceError:
                    return False

        results = await asyncio.wait_for(asyncio.gather(*(submit() for _ in range(count))), timeout=10)
        assert sum(results) == (1 if valid else 0)
        async with sessions() as db:
            action = await db.scalar(select(EmailActionToken))
            assert action.used_at is not None
            assert action.failed_attempts == (0 if valid else 5)
    finally:
        await engine.dispose()
