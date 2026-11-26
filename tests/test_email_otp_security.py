import re

import pytest

from app.core.email_otp import email_otp_digest, matches_email_otp, new_email_otp


def test_otp_preserves_leading_zeroes(monkeypatch):
    monkeypatch.setattr("app.core.email_otp.secrets.randbelow", lambda _limit: 42)
    assert new_email_otp() == "000042"


def test_otp_generator_returns_six_ascii_digits():
    assert all(re.fullmatch(r"[0-9]{6}", new_email_otp()) for _ in range(100))


def test_otp_digest_is_keyed_and_bound_to_the_action(monkeypatch):
    monkeypatch.setenv("FIKRAH_JWT_SECRET", "a-private-otp-test-secret-with-32-characters")
    digest = email_otp_digest("000042", "action-one")
    assert matches_email_otp("000042", "action-one", digest)
    assert not matches_email_otp("000043", "action-one", digest)
    assert not matches_email_otp("000042", "action-two", digest)
    monkeypatch.setenv("FIKRAH_JWT_SECRET", "another-private-test-secret-with-32-characters")
    assert not matches_email_otp("000042", "action-one", digest)


def test_otp_digest_rejects_unsafe_signing_secret(monkeypatch):
    monkeypatch.setenv("FIKRAH_JWT_SECRET", "short")
    with pytest.raises(RuntimeError, match="at least 32"):
        email_otp_digest("123456", "action")
