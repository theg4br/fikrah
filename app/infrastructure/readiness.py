"""Cheap dependency probes: no mutations, email, or paid model requests."""

import httpx
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.session import initialize_database
from app.services.readiness_service import DependencyUnavailable


async def check_postgresql() -> None:
    try:
        factory = initialize_database()
        async with factory() as session:
            await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, RuntimeError, OSError, ValueError) as error:
        raise DependencyUnavailable from error


async def check_redis() -> None:
    try:
        async with Redis.from_url(
            get_settings().redis_url, socket_connect_timeout=3, socket_timeout=3
        ) as client:
            if not await client.ping():
                raise DependencyUnavailable
    except (RedisError, OSError, ValueError) as error:
        raise DependencyUnavailable from error


async def check_qdrant() -> None:
    settings = get_settings()
    if not settings.qdrant_url:
        raise DependencyUnavailable
    headers = {"api-key": settings.qdrant_api_key} if settings.qdrant_api_key else {}
    try:
        async with httpx.AsyncClient(timeout=3, follow_redirects=False) as client:
            response = await client.get(
                settings.qdrant_url.rstrip("/") + "/readyz", headers=headers
            )
            # Redirects must not leak credentials or masquerade as readiness.
            response.raise_for_status()
    except (httpx.HTTPError, ValueError) as error:
        raise DependencyUnavailable from error
