"""Process health endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.schemas.operations import ReadinessView
from app.services.readiness_service import ReadinessService

router = APIRouter(tags=["operations"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Report that the API process is running."""
    return {"status": "ok"}


def get_readiness_service() -> ReadinessService:
    from app.infrastructure.container import readiness_service

    return readiness_service


@router.get("/ready", response_model=ReadinessView, responses={503: {"model": ReadinessView}})
async def ready(
    response: Response,
    service: Annotated[ReadinessService, Depends(get_readiness_service)],
) -> ReadinessView:
    """Check PostgreSQL, Redis, and Qdrant within a shared three-second budget."""
    checks = await service.check()
    available = all(checks.values())
    response.status_code = 200 if available else 503
    response.headers["Cache-Control"] = "no-store"
    return ReadinessView(
        status="ready" if available else "unavailable",
        checks={key: "ready" if value else "unavailable" for key, value in checks.items()},
    )
