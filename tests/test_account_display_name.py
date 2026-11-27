"""Display names preserve mobile auth, ownership and legacy account access."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.core.security import hash_password
from app.db.session import get_db_session
from app.main import app
from app.models import EmailDelivery, Project, RefreshSession, User

PASSWORD = "abcde"


@asynccontextmanager
async def database():
    async for db in app.dependency_overrides[get_db_session]():
        yield db


@pytest.fixture
def deliveries(monkeypatch):
    actions = []

    async def action(email, token, purpose):
        actions.append((email, token, purpose))

    async def welcome(email):
        pass

    monkeypatch.setattr(
        "app.infrastructure.container.auth_service.send_action_email", action
    )
    monkeypatch.setattr(
        "app.infrastructure.container.auth_service.send_welcome_email", welcome
    )
    return actions


async def existing_account(client, email="legacy@example.com", name=None):
    async with database() as db:
        user = User(
            email=email,
            name=name,
            password_hash=hash_password(PASSWORD),
            email_verified_at=datetime.now(timezone.utc),
        )
        db.add(user)
        await db.commit()
        user_id = user.id
    response = await client.post(
        "/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200
    tokens = response.json()
    return user_id, tokens, {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/register", "/register/otp"])
@pytest.mark.parametrize("value", [None, "", " \t\n ", 123, "x" * 121, "A\x00B"])
async def test_registration_rejects_invalid_names_before_creating_rows(
    auth_client, path, value
):
    response = await auth_client.post(
        "/v1/auth" + path,
        json={"email": "new@example.com", "password": PASSWORD, "name": value},
    )
    assert response.status_code == 422
    async with database() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 0
        assert await db.scalar(select(func.count()).select_from(EmailDelivery)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/register", "/register/otp"])
async def test_registration_requires_name(auth_client, path):
    response = await auth_client.post(
        "/v1/auth" + path,
        json={"email": "new@example.com", "password": PASSWORD},
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "name"]


@pytest.mark.asyncio
@pytest.mark.parametrize("otp", [True, False])
async def test_registration_persists_trimmed_unicode_name_and_allows_duplicates(
    auth_client,
    deliveries,
    otp,
):
    for email in ("first@example.com", "second@example.com"):
        registered = await auth_client.post(
            "/v1/auth/register/otp" if otp else "/v1/auth/register",
            json={"name": "  علاء جابر  ", "email": email, "password": PASSWORD},
        )
        assert registered.status_code == 202
        token = deliveries[-1][1]
        verified = await auth_client.post(
            "/v1/auth/verify-email/otp" if otp else "/v1/auth/verify-email",
            json={"email": email, "code": token} if otp else {"token": token},
        )
        assert verified.status_code == 200
        login = await auth_client.post(
            "/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        assert login.status_code == 200
        me = await auth_client.get(
            "/v1/auth/me",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )
        assert me.json()["name"] == "علاء جابر"
        assert "username" not in me.json()
    async with database() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 2
        assert await db.scalar(select(func.count()).select_from(Project)) == 2


@pytest.mark.asyncio
async def test_legacy_account_can_set_name_without_changing_credentials_or_other_users(
    auth_client,
):
    user_id, tokens, headers = await existing_account(auth_client)
    _, _, other_headers = await existing_account(
        auth_client, "other@example.com", "Other User"
    )
    assert (await auth_client.get("/v1/auth/me", headers=headers)).json()[
        "name"
    ] is None
    response = await auth_client.patch(
        "/v1/auth/me", headers=headers, json={"name": "  A New Name  "}
    )
    assert response.status_code == 200
    assert response.json() == {
        "id": str(user_id),
        "email": "legacy@example.com",
        "email_verified": True,
        "name": "A New Name",
    }
    assert (await auth_client.get("/v1/auth/me", headers=headers)).json()[
        "name"
    ] == "A New Name"
    assert (await auth_client.get("/v1/auth/me", headers=other_headers)).json()[
        "name"
    ] == "Other User"
    # Updating the name does not rotate/revoke credentials or session families.
    assert (
        await auth_client.post(
            "/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
    ).status_code == 200
    assert (
        await auth_client.post(
            "/v1/auth/login", json={"email": "legacy@example.com", "password": PASSWORD}
        )
    ).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": None},
        {"name": " "},
        {"name": 123},
        {"name": "x" * 121},
        {"name": "Updated", "email": "someone@example.com"},
        {"name": "Updated", "user_id": "someone-else"},
        {"name": "Updated", "password": "other-password"},
    ],
)
async def test_name_update_rejects_invalid_or_unrelated_fields(auth_client, body):
    _, _, headers = await existing_account(auth_client, name="Original")
    response = await auth_client.patch("/v1/auth/me", headers=headers, json=body)
    assert response.status_code == 422
    assert (await auth_client.get("/v1/auth/me", headers=headers)).json()[
        "name"
    ] == "Original"


@pytest.mark.asyncio
async def test_name_update_requires_authentication(auth_client):
    assert (
        await auth_client.patch("/v1/auth/me", json={"name": "Some Name"})
    ).status_code == 401


@pytest.fixture
def google(monkeypatch, deliveries):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "mobile-client")
    claims = {
        "sub": "name-google-user",
        "email": "google.name@gmail.com",
        "email_verified": True,
    }
    monkeypatch.setattr(
        "app.infrastructure.container.auth_service.verify_google_id_token",
        lambda *_: dict(claims),
    )
    return claims


async def google_login(client, **extra):
    return await client.post(
        "/v1/auth/google", json={"id_token": "valid-mobile-google-id-token", **extra}
    )


@pytest.mark.asyncio
async def test_google_uses_verified_name_and_preserves_later_user_choice(
    auth_client, google
):
    google["name"] = "  Google Full Name  "
    first = await google_login(auth_client, name="Ignored Fallback")
    assert first.status_code == 200
    headers = {"Authorization": f"Bearer {first.json()['access_token']}"}
    assert (await auth_client.get("/v1/auth/me", headers=headers)).json()[
        "name"
    ] == "Google Full Name"
    assert (
        await auth_client.patch(
            "/v1/auth/me", headers=headers, json={"name": "My Choice"}
        )
    ).status_code == 200
    assert (await google_login(auth_client)).status_code == 200
    assert (await auth_client.get("/v1/auth/me", headers=headers)).json()[
        "name"
    ] == "My Choice"


@pytest.mark.asyncio
@pytest.mark.parametrize("claim", [None, "  ", 123, "x" * 121])
async def test_new_google_account_needs_fallback_name_when_claim_missing_or_invalid(
    auth_client, google, claim
):
    google["name"] = claim
    missing = await google_login(auth_client)
    assert missing.status_code == 422
    assert missing.json() == {"detail": "Name is required to create an account"}
    async with database() as db:
        for model in (User, Project, RefreshSession, EmailDelivery):
            assert await db.scalar(select(func.count()).select_from(model)) == 0
    completed = await google_login(auth_client, name="  اسم المستخدم  ")
    assert completed.status_code == 200
    headers = {"Authorization": f"Bearer {completed.json()['access_token']}"}
    assert (await auth_client.get("/v1/auth/me", headers=headers)).json()[
        "name"
    ] == "اسم المستخدم"


@pytest.mark.asyncio
async def test_legacy_google_account_can_login_without_a_name_and_fill_it_later(
    auth_client, google
):
    user_id, _, _ = await existing_account(auth_client, google["email"])
    response = await google_login(auth_client)
    assert response.status_code == 200
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    me = (await auth_client.get("/v1/auth/me", headers=headers)).json()
    assert me["id"] == str(user_id) and me["name"] is None
    google["name"] = "Provided Later"
    assert (await google_login(auth_client)).status_code == 200
    assert (await auth_client.get("/v1/auth/me", headers=headers)).json()[
        "name"
    ] == "Provided Later"


def test_name_schema_exposes_bounds_and_keeps_login_separate():
    from app.schemas.auth import Credentials, ProfileNameUpdate, Registration

    assert (
        Registration(
            name=" " + "ع" * 120 + " ", email="user@example.com", password=PASSWORD
        ).name
        == "ع" * 120
    )
    assert set(Credentials.model_fields) == {"email", "password"}
    assert (
        ProfileNameUpdate.model_json_schema()["properties"]["name"]["maxLength"] == 120
    )
