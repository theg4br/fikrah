"""Email code lifecycle; callers own the account lock and transaction boundary."""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.datetimes import ensure_timezone
from app.core.email_otp import (
    OTP_LIFETIME,
    OTP_MAX_ATTEMPTS,
    email_otp_digest,
    matches_email_otp,
    new_email_otp,
)
from app.core.security import hash_opaque_token, new_opaque_token
from app.models import EmailActionToken, User
from app.repositories.auth import AuthRepository


@dataclass(frozen=True, slots=True)
class IssuedEmailOtp:
    code: str
    token_hash: str


class EmailOtpService:
    def __init__(self, repository: AuthRepository) -> None:
        self._repository = repository

    async def issue(
        self, db: AsyncSession, user: User, purpose: str
    ) -> IssuedEmailOtp:
        now = datetime.now(timezone.utc)
        await self._repository.retire_open_action_tokens(db, user.id, purpose, now)
        code = new_email_otp()
        token_hash = hash_opaque_token(new_opaque_token())
        await self._repository.add_action_token(
            db,
            EmailActionToken(
                user_id=user.id,
                token_hash=token_hash,
                purpose=purpose,
                otp_digest=email_otp_digest(code, token_hash),
                expires_at=now + OTP_LIFETIME,
            ),
        )
        return IssuedEmailOtp(code=code, token_hash=token_hash)

    async def consume(
        self, db: AsyncSession, email: str, code: str, purpose: str
    ) -> User | None:
        # The same user-before-action lock order is used by links, Google,
        # refresh and password reset. Concurrent submissions cannot reuse a code.
        user = await self._repository.find_user_by_email(db, email, lock=True)
        if user is None:
            return None
        action = await self._repository.find_open_email_otp(db, user.id, purpose)
        now = datetime.now(timezone.utc)
        if action is None or ensure_timezone(action.expires_at) <= now:
            return None
        if action.failed_attempts >= OTP_MAX_ATTEMPTS:
            action.used_at = now
            return None
        if not matches_email_otp(code, action.token_hash, action.otp_digest):
            action.failed_attempts += 1
            if action.failed_attempts >= OTP_MAX_ATTEMPTS:
                action.used_at = now
            return None
        action.used_at = now
        return user
