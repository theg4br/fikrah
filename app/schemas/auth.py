"""Request and response schemas for authentication endpoints."""

from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

PASSWORD_MIN_LENGTH = 5


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class EmailRequest(BaseModel):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class TokenRequest(BaseModel):
    token: str = Field(min_length=20, max_length=512)


class EmailOtpRequest(EmailRequest):
    code: str = Field(pattern=r"^[0-9]{6}$", min_length=6, max_length=6)


class ResetPasswordOtpRequest(EmailOtpRequest):
    new_password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=128)


class ResetPasswordRequest(TokenRequest):
    new_password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=128)


class GoogleLoginRequest(BaseModel):
    id_token: str = Field(min_length=20, max_length=8192)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=512)


class AuthTokens(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 900


class Message(BaseModel):
    message: str


class UserView(BaseModel):
    id: UUID
    email: EmailStr
    email_verified: bool
