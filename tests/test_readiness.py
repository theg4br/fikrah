"""Readiness fails closed while liveness remains independent."""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.v1.health import get_readiness_service
from app.infrastructure import readiness
from app.main import app
from app.services.readiness_service import DependencyUnavailable, ReadinessService


@pytest.mark.asyncio
async def test_probes_start_concurrently_and_fail_independently():
    started = set()
    gate = asyncio.Event()

    async def check(name):
        started.add(name)
        if len(started) == 3:
            gate.set()
        await asyncio.wait_for(gate.wait(), 1)
        if name == "redis":
            raise DependencyUnavailable("private connection details")

    service = ReadinessService({name: lambda name=name: check(name) for name in (
        "postgresql", "redis", "qdrant",
    )})
    assert await service.check() == {"postgresql": True, "redis": False, "qdrant": True}


@pytest.mark.asyncio
async def test_one_total_deadline_cancels_and_joins_pending_probes():
    closed = set()

    async def hanging(name):
        try:
            await asyncio.Event().wait()
        finally:
            closed.add(name)

    service = ReadinessService(
        {name: lambda name=name: hanging(name) for name in ("a", "b", "c")},
        timeout=0.02,
    )
    assert await asyncio.wait_for(service.check(), 0.5) == dict.fromkeys(("a", "b", "c"), False)
    assert closed == {"a", "b", "c"}


@pytest.mark.asyncio
async def test_caller_cancellation_does_not_leave_probes_running():
    started, closed = asyncio.Event(), asyncio.Event()

    async def hanging():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    task = asyncio.create_task(ReadinessService({"db": hanging}).check())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


@pytest.mark.parametrize("available,status", [(True, 200), (False, 503)])
def test_http_contract_and_independent_health(available, status):
    class FakeService:
        async def check(self):
            return {"postgresql": True, "redis": True, "qdrant": available}

    app.dependency_overrides[get_readiness_service] = FakeService
    try:
        client = TestClient(app)
        response = client.get("/ready")
        assert response.status_code == status
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "status": "ready" if available else "unavailable",
            "checks": {"postgresql": "ready", "redis": "ready", "qdrant": "ready" if available else "unavailable"},
        }
        assert client.get("/health").json() == {"status": "ok"}
    finally:
        app.dependency_overrides.pop(get_readiness_service)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 302, 401, 503])
async def test_qdrant_probe_uses_readyz_and_does_not_follow_redirects(monkeypatch, status):
    monkeypatch.setenv("QDRANT_URL", "https://vectors.example.test")
    monkeypatch.setenv("QDRANT_API_KEY", "test-only")
    original = httpx.AsyncClient

    def handler(request):
        assert request.url == "https://vectors.example.test/readyz"
        assert request.headers["api-key"] == "test-only"
        return httpx.Response(status, headers={"location": "https://other.example.test"})

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["timeout"] == 3
        return original(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(readiness.httpx, "AsyncClient", client)
    if status == 200:
        await readiness.check_qdrant()
    else:
        with pytest.raises(DependencyUnavailable):
            await readiness.check_qdrant()


@pytest.mark.asyncio
async def test_postgres_probe_only_selects_and_closes_session(monkeypatch):
    class Session:
        closed = False

        async def __aenter__(self):
            return self

        async def execute(self, statement):
            assert str(statement) == "SELECT 1"

        async def __aexit__(self, *args):
            self.closed = True

    session = Session()
    monkeypatch.setattr(readiness, "initialize_database", lambda: lambda: session)
    await readiness.check_postgresql()
    assert session.closed
