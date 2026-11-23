"""Google verification budgets, public certificate caching, and real JWT checks."""

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from app.api.deps import get_db_session
from app.core.execution import ExecutionBusy, execution_budget
from app.infrastructure import container
from app.infrastructure.execution import BoundedExecutor
from app.infrastructure.identity import google
from app.infrastructure.rate_limits import RedisBudgetStore
from app.main import app
from app.services.auth_ports import (
    GoogleIdentityTokenError,
    GoogleVerificationUnavailable,
)
from app.services.request_budgets import (
    RequestBudgetExceeded,
    RequestBudgets,
    RequestBudgetUnavailable,
)


@pytest.fixture
def certificate_server(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test certificates")])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256()))
    state = {
        "count": 0, "status": 200, "cache_control": "public, max-age=3600",
        "age": "0", "body": json.dumps({"test-key": certificate.public_bytes(
            serialization.Encoding.PEM).decode()}).encode(),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/certs"
            assert "Authorization" not in self.headers
            assert "Cookie" not in self.headers
            state["count"] += 1
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(state["body"])))
            self.send_header("Cache-Control", state["cache_control"])
            self.send_header("Age", state["age"])
            self.end_headers()
            self.wfile.write(state["body"])

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/certs"
    # Local test transport only: production accepts one fixed HTTPS URL.
    monkeypatch.setattr(google, "_CERTIFICATES_URL", url)
    monkeypatch.setattr(google.id_token, "_GOOGLE_OAUTH2_CERTS_URL", url)
    monkeypatch.setattr(google, "_certificate_request", google.GoogleCertificateRequest())

    def token(**overrides):
        claims = {"sub": "owner", "email": "owner@gmail.com", "email_verified": True,
                  "iss": "https://accounts.google.com", "aud": "mobile-client",
                  "iat": int(now.timestamp()) - 10, "exp": int(now.timestamp()) + 3600}
        claims.update(overrides)
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})

    try:
        yield state, token
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_concurrent_verifications_share_only_fresh_certificates(certificate_server):
    state, token = certificate_server
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(
            lambda index: google.verify_google_id_token(token(sub=str(index)), "mobile-client"),
            range(16),
        ))
    assert state["count"] == 1
    assert {result["sub"] for result in results} == {str(i) for i in range(16)}


@pytest.mark.parametrize("overrides", [
    {"aud": "other-client"}, {"iss": "https://attacker.example"}, {"exp": 1},
])
def test_cached_certificates_do_not_skip_token_validation(certificate_server, overrides):
    state, token = certificate_server
    google.verify_google_id_token(token(), "mobile-client")
    with pytest.raises(GoogleIdentityTokenError):
        google.verify_google_id_token(token(**overrides), "mobile-client")
    assert state["count"] == 1


def test_signature_is_verified_for_every_token(certificate_server):
    state, token = certificate_server
    original = token()
    google.verify_google_id_token(original, "mobile-client")
    header, payload, signature = original.split(".")
    tampered = f"{header}.{payload}.{'A' if signature[0] != 'A' else 'B'}{signature[1:]}"
    with pytest.raises(GoogleIdentityTokenError):
        google.verify_google_id_token(tampered, "mobile-client")
    assert state["count"] == 1


@pytest.mark.parametrize("cache_control,age", [("no-store", "0"), ("max-age=60", "3600")])
def test_noncacheable_or_aged_certificates_are_refetched(certificate_server, cache_control, age):
    state, token = certificate_server
    state.update(cache_control=cache_control, age=age)
    for _ in range(2):
        google.verify_google_id_token(token(), "mobile-client")
    assert state["count"] == 2
    state["status"] = 503
    with pytest.raises(GoogleVerificationUnavailable):
        google.verify_google_id_token(token(), "mobile-client")
    assert state["count"] == 3


def test_expired_certificate_cache_cannot_hide_dependency_outage(certificate_server, monkeypatch):
    from cachecontrol import controller

    state, token = certificate_server
    state["cache_control"] = "max-age=60"
    google.verify_google_id_token(token(), "mobile-client")
    wall_clock = controller.time.time
    state["status"] = 503
    with monkeypatch.context() as patch:
        patch.setattr(controller.time, "time", lambda: wall_clock() + 120)
        with pytest.raises(GoogleVerificationUnavailable):
            google.verify_google_id_token(token(), "mobile-client")
    assert state["count"] == 2


@pytest.mark.parametrize("status,body", [(503, b"unavailable"), (200, b"not-json"), (200, b"[]")])
def test_bad_certificate_responses_are_dependency_failures(certificate_server, status, body):
    state, token = certificate_server
    original = state["body"]
    state.update(status=status, body=body)
    with pytest.raises(GoogleVerificationUnavailable):
        google.verify_google_id_token(token(), "mobile-client")
    # A bad public response must not poison subsequent verification attempts.
    state.update(status=200, body=original)
    assert google.verify_google_id_token(token(), "mobile-client")["sub"] == "owner"
    assert state["count"] == 2


def test_certificate_transport_rejects_nonpublic_requests():
    request = google.GoogleCertificateRequest()
    with pytest.raises(google.TransportError):
        request("https://attacker.example/certs")
    with pytest.raises(google.TransportError):
        request(google._CERTIFICATES_URL, headers={"Authorization": "Bearer private"})


@pytest.mark.parametrize("age,control,expected", [
    ("20", "public, max-age=60, must-revalidate", "public, max-age=40, must-revalidate"),
    ("bad", "max-age=60", "no-store"),
    ("-1", "max-age=60", "no-store"),
    ("20", "public", "no-store"),
])
def test_certificate_age_never_extends_freshness(age, control, expected):
    response = SimpleNamespace(headers={"Age": age, "Cache-Control": control})
    assert google.CertificateFreshness().update_headers(response) == {"cache-control": expected}


@pytest.mark.asyncio
async def test_certificate_http_timeout_uses_remaining_execution_budget(monkeypatch):
    seen = []

    class Request:
        def __init__(self, session):
            pass

        def __call__(self, url, **kwargs):
            seen.append(kwargs)
            return SimpleNamespace(status=200, data=b'{"key":"test-public-cert"}')

    monkeypatch.setattr(google, "Request", Request)
    async with execution_budget(0.5):
        await asyncio.to_thread(google.GoogleCertificateRequest(), google._CERTIFICATES_URL)
    assert len(seen) == 1 and 0 < seen[0]["timeout"] <= 0.5
    assert seen[0]["allow_redirects"] is False


@pytest.mark.asyncio
async def test_google_executor_admits_eight_without_using_shared_executor():
    executor = BoundedExecutor(8, "google-capacity-test")
    release = Event()
    started = [Event() for _ in range(8)]

    def verify(index):
        started[index].set()
        assert release.wait(5)
        return index

    tasks = [asyncio.create_task(executor.run(verify, index)) for index in range(8)]
    try:
        await asyncio.wait_for(asyncio.to_thread(lambda: all(e.wait(2) for e in started)), 3)
        assert all(event.is_set() for event in started)
        with pytest.raises(ExecutionBusy):
            await executor.run(verify, 0)
    finally:
        release.set()
        assert await asyncio.gather(*tasks) == list(range(8))
        executor.close()


@pytest.mark.asyncio
async def test_google_twenty_attempt_window_is_atomic_in_redis():
    url = os.getenv("FIKRAH_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set FIKRAH_TEST_REDIS_URL to isolated Redis")
    budgets = RequestBudgets(RedisBudgetStore(lambda: url), lambda: 5, lambda: 10)
    peer = f"google-test-{uuid4()}"
    results = await asyncio.gather(*(budgets.google(peer) for _ in range(30)), return_exceptions=True)
    assert sum(value is None for value in results) == 20
    assert sum(isinstance(value, RequestBudgetExceeded) for value in results) == 10


@pytest.fixture
def google_api(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "mobile-client")

    async def no_database():
        yield object()  # Failure paths must not start a DB transaction.

    app.dependency_overrides[get_db_session] = no_database
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db_session)


@pytest.mark.parametrize("error,status", [
    (GoogleIdentityTokenError(), 401), (GoogleVerificationUnavailable(), 503),
])
def test_verification_http_failures(google_api, monkeypatch, error, status):
    def verify(*_):
        raise error

    monkeypatch.setattr(container.auth_service, "verify_google_id_token", verify)
    response = google_api.post("/v1/auth/google", json={"id_token": "opaque-mobile-token-value"})
    assert response.status_code == status


@pytest.mark.parametrize("error,status", [(RequestBudgetExceeded(30), 429), (RequestBudgetUnavailable(), 503)])
def test_google_rate_budget_fails_closed_before_verification(google_api, monkeypatch, error, status):
    budget = AsyncMock(side_effect=error)
    verifier = AsyncMock()
    monkeypatch.setattr(container.request_budgets, "google", budget)
    monkeypatch.setattr(container.auth_service, "_run_google_verification", verifier)
    response = google_api.post("/v1/auth/google", json={"id_token": "opaque-mobile-token-value"})
    assert response.status_code == status
    verifier.assert_not_called()
    if status == 429:
        assert response.headers["retry-after"] == "30"


@pytest.mark.asyncio
async def test_google_budget_uses_asgi_peer_not_forwarding_headers(monkeypatch):
    budget = AsyncMock(side_effect=RequestBudgetExceeded(5))
    monkeypatch.setattr(container.request_budgets, "google", budget)
    transport = httpx.ASGITransport(app=app, client=("192.0.2.42", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/v1/auth/google", json={"id_token": "token"},
            headers={"X-Forwarded-For": "192.0.2.99", "Forwarded": "for=192.0.2.99"})
    assert response.status_code == 429
    budget.assert_awaited_once_with("192.0.2.42")


@pytest.mark.asyncio
async def test_timeout_retains_executor_slot_until_thread_finishes(google_api, monkeypatch):
    release, started, finished = Event(), Event(), Event()
    executor = BoundedExecutor(1, "google-test")

    def verify(*_):
        started.set()
        try:
            release.wait(5)
            raise GoogleIdentityTokenError
        finally:
            finished.set()

    monkeypatch.setenv("FIKRAH_GOOGLE_TIMEOUT_SECONDS", "0.03")
    monkeypatch.setattr(container.auth_service, "verify_google_id_token", verify)
    monkeypatch.setattr(container.auth_service, "_run_google_verification", executor.run)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/v1/auth/google", json={"id_token": "test-mobile-id-token-value"})
            assert started.is_set() and response.status_code == 503
            response = await client.post("/v1/auth/google", json={"id_token": "test-mobile-id-token-value"})
            assert response.status_code == 429 and response.headers["retry-after"] == "1"
            assert (await client.get("/health")).status_code == 200
    finally:
        release.set()
        await asyncio.to_thread(finished.wait, 2)
        executor.close()
