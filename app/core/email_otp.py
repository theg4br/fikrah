"""Generation and keyed verification of short-lived email action codes."""

import hashlib
import hmac
import secrets
from datetime import timedelta

from app.core.config import get_settings
from app.core.security import validate_jwt_secret

OTP_LIFETIME = timedelta(minutes=10)
OTP_MAX_ATTEMPTS = 5


def new_email_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def email_otp_digest(code: str, token_hash: str) -> str:
    # A DB-only leak must not permit enumeration of the million possible codes.
    # Each random action handle separates otherwise identical codes/accounts.
    secret = validate_jwt_secret(get_settings().fikrah_jwt_secret)
    return hmac.new(
        secret.encode(),
        f"fikrah-email-otp-v1:{token_hash}:{code}".encode(),
        hashlib.sha256,
    ).hexdigest()


def matches_email_otp(code: str, token_hash: str, expected_digest: str) -> bool:
    return hmac.compare_digest(email_otp_digest(code, token_hash), expected_digest)
