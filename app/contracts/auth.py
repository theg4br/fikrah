"""Framework-independent data contracts for authentication use cases."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class EmailPassword:
    email: str
    password: str


@dataclass(frozen=True, slots=True)
class RegisterAccount(EmailPassword):
    name: str


@dataclass(frozen=True, slots=True)
class UpdateProfileName:
    name: str


@dataclass(frozen=True, slots=True)
class AccountProfile:
    id: UUID
    email: str
    email_verified: bool
    name: str | None


@dataclass(frozen=True, slots=True)
class EmailAddress:
    email: str


@dataclass(frozen=True, slots=True)
class ActionToken:
    token: str


@dataclass(frozen=True, slots=True)
class EmailOtpConfirmation:
    email: str
    code: str


@dataclass(frozen=True, slots=True)
class PasswordResetOtp(EmailOtpConfirmation):
    new_password: str


@dataclass(frozen=True, slots=True)
class GoogleIdentityToken:
    id_token: str
    name: str | None = None


@dataclass(frozen=True, slots=True)
class PasswordReset:
    token: str
    new_password: str


@dataclass(frozen=True, slots=True)
class RefreshToken:
    refresh_token: str


@dataclass(frozen=True, slots=True)
class AuthTokenPair:
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 900


@dataclass(frozen=True, slots=True)
class AuthMessage:
    message: str
