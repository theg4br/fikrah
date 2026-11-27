"""Mobile OTP flows with real persistence and captured, never external, emails."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.db.session import get_db_session
from app.main import app
from app.models import EmailActionToken, EmailDelivery, EmailSendAttempt, Project, User

EMAIL = "otp-user@example.com"
PASSWORD = "a secure original password"
NEW_PASSWORD = "another secure replacement password"


@asynccontextmanager
async def database():
    async for db in app.dependency_overrides[get_db_session]():
        yield db


@pytest.fixture
def emails(monkeypatch):
    delivered = []

    async def capture(email, code, purpose):
        delivered.append((email, code, purpose))

    async def welcome(_email):
        pass

    monkeypatch.setattr("app.infrastructure.container.auth_service.send_action_email", capture)
    monkeypatch.setattr("app.infrastructure.container.auth_service.send_welcome_email", welcome)
    return delivered


async def register(client, email=EMAIL, *, otp=True, password=PASSWORD):
    return await client.post(
        "/v1/auth/register/otp" if otp else "/v1/auth/register",
        json={"name": "OTP User", "email": email, "password": password},
    )


async def release_cooldown(email=EMAIL):
    async with database() as db:
        await db.execute(update(EmailSendAttempt).where(EmailSendAttempt.email == email).values(
            created_at=datetime.now(timezone.utc) - timedelta(minutes=2)
        ))
        await db.commit()


@pytest.mark.asyncio
async def test_signup_verification_login_and_opaque_storage(auth_client, emails, monkeypatch):
    monkeypatch.setattr("app.services.email_otp_service.new_email_otp", lambda: "000042")
    result = await register(auth_client, email=EMAIL.upper())
    assert result.status_code == 202
    assert set(result.json()) == {"message"}
    assert emails == [(EMAIL, "000042", "verify_email_otp")]
    async with database() as db:
        action = await db.scalar(select(EmailActionToken))
        assert len(action.otp_digest) == 64
        assert "000042" != action.otp_digest
        assert len(action.token_hash) == 64
        assert action.failed_attempts == 0
        expires = action.expires_at.replace(tzinfo=timezone.utc)
        assert timedelta(minutes=9) < expires - datetime.now(timezone.utc) <= timedelta(minutes=10)
        assert (await db.scalar(select(Project))).is_general
        delivery = await db.scalar(select(EmailDelivery).where(EmailDelivery.purpose == "verify_email_otp"))
        assert delivery.sent_at is not None
        assert delivery.encrypted_token is None
        assert delivery.token_hash == action.token_hash
    blocked = await auth_client.post("/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert blocked.status_code == 403
    verified = await auth_client.post("/v1/auth/verify-email/otp", json={"email": EMAIL, "code": "000042"})
    assert verified.status_code == 200
    replay = await auth_client.post("/v1/auth/verify-email/otp", json={"email": EMAIL, "code": "000042"})
    assert replay.status_code == 400
    login = await auth_client.post("/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert login.status_code == 200
    me = await auth_client.get("/v1/auth/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email_verified"] is True


@pytest.mark.asyncio
async def test_five_wrong_guesses_are_committed_and_exhaust_the_code(auth_client, emails):
    await register(auth_client)
    code = emails[-1][1]
    wrong = "111111" if code != "111111" else "222222"
    for expected in range(1, 6):
        response = await auth_client.post("/v1/auth/verify-email/otp", json={"email": EMAIL, "code": wrong})
        assert response.status_code == 400
        async with database() as db:
            action = await db.scalar(select(EmailActionToken))
            assert action.failed_attempts == expected
            assert (action.used_at is not None) == (expected == 5)
    assert (await auth_client.post("/v1/auth/verify-email/otp", json={"email": EMAIL, "code": code})).status_code == 400
    async with database() as db:
        assert (await db.scalar(select(User))).email_verified_at is None


@pytest.mark.asyncio
async def test_expired_code_and_unknown_account_have_the_same_failure(auth_client, emails):
    await register(auth_client)
    async with database() as db:
        await db.execute(update(EmailActionToken).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        await db.commit()
    actual = await auth_client.post("/v1/auth/verify-email/otp", json={"email": EMAIL, "code": emails[-1][1]})
    unknown = await auth_client.post("/v1/auth/verify-email/otp", json={"email": "unknown@example.com", "code": "123456"})
    assert actual.status_code == unknown.status_code == 400
    assert actual.json() == unknown.json() == {"detail": "Invalid or expired code"}


@pytest.mark.asyncio
async def test_resend_cooldown_and_old_code_replacement(auth_client, emails, monkeypatch):
    codes = iter(["000001", "000002"])
    monkeypatch.setattr("app.services.email_otp_service.new_email_otp", lambda: next(codes))
    await register(auth_client)
    response = await auth_client.post("/v1/auth/resend-verification/otp", json={"email": EMAIL})
    assert response.status_code == 202
    assert len(emails) == 1
    await release_cooldown()
    assert (await auth_client.post("/v1/auth/resend-verification/otp", json={"email": EMAIL})).status_code == 202
    assert len(emails) == 2
    assert (await auth_client.post("/v1/auth/verify-email/otp", json={"email": EMAIL, "code": "000001"})).status_code == 400
    assert (await auth_client.post("/v1/auth/verify-email/otp", json={"email": EMAIL, "code": "000002"})).status_code == 200


@pytest.mark.asyncio
async def test_hourly_limit_is_shared_by_link_and_otp_emails(auth_client, emails):
    await register(auth_client)
    for index in range(6):
        await release_cooldown()
        path = "/v1/auth/resend-verification" + ("/otp" if index % 2 else "")
        assert (await auth_client.post(path, json={"email": EMAIL})).status_code == 202
    assert len(emails) == 5


@pytest.mark.asyncio
async def test_unknown_existing_and_verified_addresses_keep_generic_responses(auth_client, emails):
    first = await register(auth_client)
    second = await register(auth_client)
    assert first.json() == second.json()
    assert len(emails) == 1
    known = await auth_client.post("/v1/auth/resend-verification/otp", json={"email": EMAIL})
    missing = await auth_client.post("/v1/auth/resend-verification/otp", json={"email": "missing@example.com"})
    assert known.json() == missing.json()
    await auth_client.post("/v1/auth/verify-email/otp", json={"email": EMAIL, "code": emails[0][1]})
    await release_cooldown()
    await auth_client.post("/v1/auth/resend-verification/otp", json={"email": EMAIL})
    assert len(emails) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [123456, "12345", "1234567", "１２３４５６", "12345a", " 123456", "123456\n"])
async def test_code_validation_requires_exactly_six_ascii_digit_characters(auth_client, code):
    response = await auth_client.post("/v1/auth/verify-email/otp", json={"email": EMAIL, "code": code})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_codes_are_isolated_by_account_and_action(auth_client, emails, monkeypatch):
    codes = iter(["000001", "000002", "000003"])
    monkeypatch.setattr("app.services.email_otp_service.new_email_otp", lambda: next(codes))
    await register(auth_client)
    await register(auth_client, email="other@example.com")
    assert (await auth_client.post("/v1/auth/verify-email/otp", json={"email": "other@example.com", "code": "000001"})).status_code == 400
    assert (await auth_client.post("/v1/auth/password-reset/otp/confirm", json={"email": EMAIL, "code": "000001", "new_password": NEW_PASSWORD})).status_code == 400
    assert (await auth_client.post("/v1/auth/verify-email/otp", json={"email": EMAIL, "code": "000001"})).status_code == 200
    await release_cooldown()
    assert (await auth_client.post("/v1/auth/password-reset/otp/request", json={"email": EMAIL})).status_code == 202
    assert (await auth_client.post("/v1/auth/verify-email/otp", json={"email": EMAIL, "code": "000003"})).status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("password,new_password", [(PASSWORD, NEW_PASSWORD), ("abcde", "vwxyz")])
async def test_reset_in_one_confirmation_revokes_all_sessions(auth_client, emails, password, new_password):
    assert (await register(auth_client, password=password)).status_code == 202
    await auth_client.post("/v1/auth/verify-email/otp", json={"email": EMAIL, "code": emails[-1][1]})
    sessions = [(await auth_client.post("/v1/auth/login", json={"email": EMAIL, "password": password})).json() for _ in range(2)]
    await release_cooldown()
    request = await auth_client.post("/v1/auth/password-reset/otp/request", json={"email": EMAIL})
    unknown = await auth_client.post("/v1/auth/password-reset/otp/request", json={"email": "unknown@example.com"})
    assert request.status_code == unknown.status_code == 202
    assert request.json() == unknown.json()
    assert emails[-1][2] == "reset_password_otp"
    body = {"email": EMAIL, "code": emails[-1][1], "new_password": new_password}
    for invalid_password in ("abcd", "x" * 129):
        invalid = await auth_client.post(
            "/v1/auth/password-reset/otp/confirm", json={**body, "new_password": invalid_password}
        )
        assert invalid.status_code == 422
        async with database() as db:
            action = await db.scalar(select(EmailActionToken).where(EmailActionToken.purpose == "reset_password"))
            assert action.used_at is None
            assert action.failed_attempts == 0
    assert (await auth_client.post("/v1/auth/password-reset/otp/confirm", json=body)).status_code == 200
    assert (await auth_client.post("/v1/auth/password-reset/otp/confirm", json=body)).status_code == 400
    for pair in sessions:
        assert (await auth_client.get("/v1/auth/me", headers={"Authorization": f"Bearer {pair['access_token']}"})).status_code == 401
        assert (await auth_client.post("/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})).status_code == 401
    assert (await auth_client.post("/v1/auth/login", json={"email": EMAIL, "password": new_password})).status_code == 200


@pytest.mark.asyncio
async def test_legacy_password_reset_accepts_five_characters(auth_client, emails):
    assert (await register(auth_client, otp=False, password="abcde")).status_code == 202
    assert (await auth_client.post("/v1/auth/verify-email", json={"token": emails[-1][1]})).status_code == 200
    await release_cooldown()
    assert (await auth_client.post("/v1/auth/password-reset/request", json={"email": EMAIL})).status_code == 202
    assert (await auth_client.post("/v1/auth/password-reset/confirm", json={
        "token": emails[-1][1], "new_password": "vwxyz",
    })).status_code == 200
    assert (await auth_client.post("/v1/auth/login", json={"email": EMAIL, "password": "vwxyz"})).status_code == 200


@pytest.mark.asyncio
async def test_switching_between_links_and_codes_invalidates_the_old_method(auth_client, emails):
    await register(auth_client, otp=False)
    link = emails[-1][1]
    await release_cooldown()
    await auth_client.post("/v1/auth/resend-verification/otp", json={"email": EMAIL})
    assert (await auth_client.post("/v1/auth/verify-email", json={"token": link})).status_code == 400
    code = emails[-1][1]
    await release_cooldown()
    await auth_client.post("/v1/auth/resend-verification", json={"email": EMAIL})
    assert (await auth_client.post("/v1/auth/verify-email/otp", json={"email": EMAIL, "code": code})).status_code == 400
    assert (await auth_client.post("/v1/auth/verify-email", json={"token": emails[-1][1]})).status_code == 200


@pytest.mark.asyncio
async def test_google_link_retires_otp_and_discards_unverified_password(auth_client, emails, monkeypatch):
    email = "google-otp@gmail.com"
    await register(auth_client, email=email)
    code = emails[-1][1]
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "otp-mobile-client")
    monkeypatch.setattr("app.infrastructure.container.auth_service.verify_google_id_token", lambda *_: {
        "sub": "google-otp-subject", "email": email, "email_verified": True,
    })
    assert (await auth_client.post("/v1/auth/google", json={"id_token": "a-google-identity-token-value"})).status_code == 200
    assert (await auth_client.post("/v1/auth/verify-email/otp", json={"email": email, "code": code})).status_code == 400
    async with database() as db:
        user = await db.scalar(select(User))
        assert user.password_hash is None


@pytest.mark.asyncio
async def test_otp_delivery_retry_is_durable_and_expired_delivery_is_cancelled(auth_client, emails, monkeypatch):
    from app.infrastructure.container import auth_service
    from app.repositories.email import EmailOutboxRepository
    from app.services.auth_ports import EmailDeliveryError
    from app.services.email_delivery_service import EmailDeliveryService

    async def unavailable(*_):
        raise EmailDeliveryError

    monkeypatch.setattr(auth_service, "send_action_email", unavailable)
    assert (await register(auth_client)).status_code == 202
    async with database() as db:
        delivery = await db.scalar(select(EmailDelivery).where(EmailDelivery.purpose == "verify_email_otp"))
        delivery_id = delivery.id
        assert delivery.sent_at is None
        assert delivery.encrypted_token is not None
        code = auth_service._reveal_email_token(delivery.encrypted_token)
        assert len(code) == 6 and code.isdigit()
        delivery.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()

    async def capture(email, code, purpose):
        emails.append((email, code, purpose))

    sender = EmailDeliveryService(capture, unavailable, auth_service._reveal_email_token, EmailOutboxRepository())
    async with database() as db:
        assert await sender.deliver(db, delivery_id)
    assert emails == [(EMAIL, code, "verify_email_otp")]
    await release_cooldown()
    await auth_client.post("/v1/auth/resend-verification/otp", json={"email": EMAIL})
    async with database() as db:
        delivery = await db.scalar(select(EmailDelivery).where(EmailDelivery.sent_at.is_(None)))
        delivery_id = delivery.id
        delivery.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.execute(update(EmailActionToken).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        await db.commit()
    async with database() as db:
        assert not await sender.deliver(db, delivery_id)
        delivery = await db.get(EmailDelivery, delivery_id)
        assert delivery.cancelled_at is not None
        assert delivery.encrypted_token is None
    assert len(emails) == 1
