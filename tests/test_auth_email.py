from email.message import EmailMessage

import pytest

from app.infrastructure.email.smtp import send_action_email


@pytest.mark.parametrize(
    ("username", "password", "expected_username", "expected_password"),
    [
        (None, None, None, None),
        ("", "", None, None),
        ("smtp-user", " secret with spaces ", "smtp-user", " secret with spaces "),
    ],
)
@pytest.mark.asyncio
async def test_smtp_authentication_is_optional(
    monkeypatch: pytest.MonkeyPatch,
    username: str | None,
    password: str | None,
    expected_username: str | None,
    expected_password: str | None,
) -> None:
    from app.infrastructure.email.smtp import send_welcome_email

    monkeypatch.setenv("SMTP_HOST", "mailpit")
    monkeypatch.setenv("SMTP_FROM", "no-reply@fikrah.local")
    for key, value in {"SMTP_USERNAME": username, "SMTP_PASSWORD": password}.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    captured: dict[str, object] = {}

    async def capture_send(message: EmailMessage, **kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("app.infrastructure.email.smtp.aiosmtplib.send", capture_send)
    await send_welcome_email("person@example.test")

    assert captured["username"] == expected_username
    assert captured["password"] == expected_password


@pytest.mark.parametrize(
    ("purpose", "subject", "html_phrase"),
    [
        ("verify_email", "Verify your email to sign in", "Verify email and continue"),
        ("reset_password", "Reset your FIKRAH password", "Reset password"),
    ],
)
@pytest.mark.asyncio
async def test_action_email_contains_plain_text_and_html(
    purpose: str,
    subject: str,
    html_phrase: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_FROM", "FIKRAH <noreply@example.test>")
    monkeypatch.setenv(
        "EMAIL_ACTION_BASE_URL", "https://app.example.test/action?source=email"
    )
    monkeypatch.setenv("SMTP_START_TLS", "false")
    sent: list[EmailMessage] = []

    async def capture_send(message: EmailMessage, **_kwargs: object) -> None:
        sent.append(message)

    monkeypatch.setattr("app.infrastructure.email.smtp.aiosmtplib.send", capture_send)

    await send_action_email("person@example.test", "safe-token", purpose)

    message = sent[0]
    assert message["Subject"] == f"FIKRAH — {subject}"
    assert message.get_content_type() == "multipart/alternative"
    plain_part = message.get_body(preferencelist=("plain",))
    html_part = message.get_body(preferencelist=("html",))
    html_content = html_part.get_content()
    assert "source=email&amp;token=safe-token" in html_content
    assert html_phrase in html_content
    assert "source=email&token=safe-token" in plain_part.get_content()


@pytest.mark.asyncio
async def test_action_email_rejects_unknown_purpose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="Unsupported email action purpose"):
        await send_action_email("person@example.test", "token", "login")


@pytest.mark.asyncio
async def test_welcome_email_contains_plain_text_and_html(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.infrastructure.email.smtp import send_welcome_email

    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_FROM", "FIKRAH <noreply@example.test>")
    sent: list[EmailMessage] = []

    async def capture_send(message: EmailMessage, **_kwargs: object) -> None:
        sent.append(message)

    monkeypatch.setattr("app.infrastructure.email.smtp.aiosmtplib.send", capture_send)

    await send_welcome_email("person@example.test")

    message = sent[0]
    assert message["Subject"] == "Welcome to FIKRAH"
    assert message["To"] == "person@example.test"
    plain_part = message.get_body(preferencelist=("plain",))
    html_part = message.get_body(preferencelist=("html",))
    assert "Your personal knowledge space is ready" in plain_part.get_content()
    assert "Welcome to FIKRAH!" in html_part.get_content()


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", ["verify_email", "reset_password", "welcome"])
async def test_account_email_embeds_original_logo_with_matching_content_id(
    monkeypatch, purpose
):
    from email import policy
    from email.parser import BytesParser

    from app.infrastructure.email.smtp import (
        EMAIL_LOGO_CID,
        EMAIL_LOGO_PATH,
        send_welcome_email,
    )

    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_FROM", "FIKRAH <noreply@example.test>")
    monkeypatch.setenv("EMAIL_ACTION_BASE_URL", "https://app.example.test/action")
    sent = []

    async def capture_send(message, **_kwargs):
        sent.append(BytesParser(policy=policy.default).parsebytes(message.as_bytes()))

    monkeypatch.setattr("app.infrastructure.email.smtp.aiosmtplib.send", capture_send)
    if purpose == "welcome":
        await send_welcome_email("person@example.test")
    else:
        await send_action_email("person@example.test", "safe-token", purpose)

    message = sent[0]
    html = message.get_body(preferencelist=("html",)).get_content()
    assert f'src="cid:{EMAIL_LOGO_CID}"' in html
    assert 'alt="FIKRAH"' in html
    assert "data:image" not in html
    assert message.get_body(preferencelist=("plain",)) is not None
    assert message.get_content_type() == "multipart/alternative"
    assert list(message.iter_parts())[1].get_content_type() == "multipart/related"
    images = [part for part in message.walk() if part.get_content_type() == "image/png"]
    assert len(images) == 1
    logo = images[0]
    assert logo["Content-ID"] == f"<{EMAIL_LOGO_CID}>"
    assert logo.get_content_disposition() == "inline"
    assert logo.get_payload(decode=True) == EMAIL_LOGO_PATH.read_bytes()


@pytest.mark.asyncio
async def test_missing_logo_fails_delivery_instead_of_sending_broken_branding(
    monkeypatch, tmp_path
):
    from unittest.mock import AsyncMock

    from app.infrastructure.email.smtp import send_welcome_email
    from app.services.auth_ports import EmailDeliveryError

    send = AsyncMock()
    monkeypatch.setattr("app.infrastructure.email.smtp.aiosmtplib.send", send)
    monkeypatch.setattr(
        "app.infrastructure.email.smtp.EMAIL_LOGO_PATH", tmp_path / "missing-logo.png"
    )
    with pytest.raises(EmailDeliveryError):
        await send_welcome_email("person@example.test")
    send.assert_not_called()
