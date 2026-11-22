"""Synchronous SQLAlchemy sessions for Celery workers."""

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.timing import instrument_database


def create_worker_session_factory() -> tuple[object, sessionmaker[Session]]:
    database_url = get_settings().database_url
    if not database_url:
        raise RuntimeError("DATABASE_URL is required by the Celery worker")
    url = make_url(database_url)
    if url.drivername in {"postgresql", "postgresql+asyncpg"}:
        url = url.set(drivername="postgresql+psycopg")
    engine = create_engine(url, pool_pre_ping=True)
    instrument_database(engine)
    return engine, sessionmaker(engine, expire_on_commit=False)
