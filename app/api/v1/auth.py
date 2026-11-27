"""HTTP presentation layer for authentication endpoints."""

from collections.abc import Awaitable
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import AuthenticatedUser, AuthServiceDependency, Db
from app.contracts.auth import (
    ActionToken,
    AuthMessage,
    AuthTokenPair,
    EmailAddress,
    EmailOtpConfirmation,
    EmailPassword,
    GoogleIdentityToken,
    PasswordReset,
    PasswordResetOtp,
    RefreshToken,
    RegisterAccount,
    UpdateProfileName,
)
from app.schemas.auth import (
    AuthTokens,
    Credentials,
    EmailOtpRequest,
    EmailRequest,
    GoogleLoginRequest,
    Message,
    ProfileNameUpdate,
    RefreshRequest,
    Registration,
    ResetPasswordOtpRequest,
    ResetPasswordRequest,
    TokenRequest,
    UserView,
)
from app.services.auth_errors import AuthFailure, AuthServiceError
from app.services.request_budgets import RequestBudgetExceeded, RequestBudgetUnavailable

router = APIRouter(prefix="/v1/auth", tags=["authentication"])
Result = TypeVar("Result")

AUTH_FAILURE_STATUSES = {
    AuthFailure.INVALID_ACCESS_TOKEN: 401,
    AuthFailure.INVALID_ACTION_TOKEN: 400,
    AuthFailure.INVALID_EMAIL_OTP: 400,
    AuthFailure.INVALID_CREDENTIALS: 401,
    AuthFailure.EMAIL_NOT_VERIFIED: 403,
    AuthFailure.INVALID_GOOGLE_IDENTITY: 401,
    AuthFailure.GOOGLE_IDENTITY_CONFLICT: 409,
    AuthFailure.GOOGLE_UNAVAILABLE: 503,
    AuthFailure.GOOGLE_BUSY: 429,
    AuthFailure.NAME_REQUIRED: 422,
    AuthFailure.INVALID_REFRESH_TOKEN: 401,
    AuthFailure.LOGIN_BUDGET_EXCEEDED: 429,
    AuthFailure.LOGIN_BUDGET_UNAVAILABLE: 503,
    AuthFailure.PASSWORD_BUSY: 429,
}


async def _execute(workflow: Awaitable[Result]) -> Result:
    try:
        return await workflow
    except AuthServiceError as error:
        headers = None
        if error.code == AuthFailure.INVALID_CREDENTIALS:
            headers = {"WWW-Authenticate": "Bearer"}
        elif error.retry_after is not None:
            headers = {"Retry-After": str(error.retry_after)}
        raise HTTPException(
            status_code=AUTH_FAILURE_STATUSES[error.code],
            detail=error.message,
            headers=headers,
        ) from error


async def _message(workflow: Awaitable[AuthMessage]) -> Message:
    result = await _execute(workflow)
    return Message(message=result.message)


async def _tokens(workflow: Awaitable[AuthTokenPair]) -> AuthTokens:
    result = await _execute(workflow)
    return AuthTokens(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        token_type=result.token_type,
        expires_in=result.expires_in,
    )


@router.post("/register", response_model=Message, status_code=status.HTTP_202_ACCEPTED)
async def register(
    body: Registration, db: Db, service: AuthServiceDependency
) -> Message:
    return await _message(
        service.register(
            RegisterAccount(email=str(body.email), password=body.password, name=body.name), db
        )
    )


@router.post("/register/otp", response_model=Message, status_code=status.HTTP_202_ACCEPTED)
async def register_with_otp(
    body: Registration, db: Db, service: AuthServiceDependency
) -> Message:
    """Create an account and email a six-digit activation code; then verify and log in."""
    return await _message(service.register(
        RegisterAccount(email=str(body.email), password=body.password, name=body.name), db, use_otp=True
    ))


@router.post("/verify-email/otp", response_model=Message, responses={400: {"description": "Invalid, expired, used or exhausted code"}})
async def verify_email_otp(
    body: EmailOtpRequest, db: Db, service: AuthServiceDependency
) -> Message:
    """Verify mailbox ownership using email and code; does not issue session tokens."""
    return await _message(service.verify_email_otp(
        EmailOtpConfirmation(email=str(body.email), code=body.code), db
    ))


@router.post("/resend-verification/otp", response_model=Message, status_code=202)
async def resend_verification_otp(
    body: EmailRequest, db: Db, service: AuthServiceDependency
) -> Message:
    """Request an activation code; generic acknowledgment also covers send suppression."""
    return await _message(service.resend_verification(
        EmailAddress(email=str(body.email)), db, use_otp=True
    ))


@router.post("/verify-email", response_model=Message)
async def verify_email(
    body: TokenRequest, db: Db, service: AuthServiceDependency
) -> Message:
    return await _message(service.verify_email(ActionToken(token=body.token), db))


@router.post(
    "/resend-verification", response_model=Message, status_code=status.HTTP_202_ACCEPTED
)
async def resend_verification(
    body: EmailRequest, db: Db, service: AuthServiceDependency
) -> Message:
    return await _message(
        service.resend_verification(EmailAddress(email=str(body.email)), db)
    )


@router.post("/login", response_model=AuthTokens)
async def login(
    body: Credentials, db: Db, service: AuthServiceDependency
) -> AuthTokens:
    return await _tokens(
        service.login(EmailPassword(email=str(body.email), password=body.password), db)
    )


async def enforce_google_budget(request: Request) -> None:
    from app.infrastructure.container import request_budgets

    # ASGI client is authoritative. Do not interpret user-controlled forwarding
    # headers here; trusted proxy configuration belongs to the deployment server.
    client_ip = request.client.host if request.client is not None else "unknown"
    try:
        await request_budgets.google(client_ip)
    except RequestBudgetExceeded as error:
        raise HTTPException(
            status_code=429, detail="Too many Google login attempts",
            headers={"Retry-After": str(error.retry_after)},
        ) from error
    except RequestBudgetUnavailable as error:
        raise HTTPException(status_code=503, detail="Authentication is temporarily unavailable") from error


@router.post(
    "/google", response_model=AuthTokens, dependencies=[Depends(enforce_google_budget)],
    responses={401: {"description": "Invalid Google identity"},
               422: {"description": "Invalid request or name required for a new account"},
               429: {"description": "Attempt limit or verification capacity exceeded"},
               503: {"description": "Verification dependency is unavailable"}},
)
async def google_login(
    body: GoogleLoginRequest, db: Db, service: AuthServiceDependency
) -> AuthTokens:
    return await _tokens(
        service.google_login(GoogleIdentityToken(id_token=body.id_token, name=body.name), db)
    )


@router.post(
    "/password-reset/request",
    response_model=Message,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_password_reset(
    body: EmailRequest, db: Db, service: AuthServiceDependency
) -> Message:
    return await _message(
        service.request_password_reset(EmailAddress(email=str(body.email)), db)
    )


@router.post("/password-reset/confirm", response_model=Message)
async def confirm_password_reset(
    body: ResetPasswordRequest, db: Db, service: AuthServiceDependency
) -> Message:
    return await _message(
        service.confirm_password_reset(
            PasswordReset(token=body.token, new_password=body.new_password), db
        )
    )


@router.post("/password-reset/otp/request", response_model=Message, status_code=202)
async def request_password_reset_otp(
    body: EmailRequest, db: Db, service: AuthServiceDependency
) -> Message:
    """Email a reset code for an eligible account without disclosing account existence."""
    return await _message(service.request_password_reset(
        EmailAddress(email=str(body.email)), db, use_otp=True
    ))


@router.post("/password-reset/otp/confirm", response_model=Message, responses={400: {"description": "Invalid, expired, used or exhausted code"}, 429: {"description": "Password processing is busy"}})
async def confirm_password_reset_otp(
    body: ResetPasswordOtpRequest, db: Db, service: AuthServiceDependency
) -> Message:
    """Consume the reset code and change the password in one request; revoke all sessions."""
    return await _message(service.confirm_password_reset_otp(
        PasswordResetOtp(email=str(body.email), code=body.code, new_password=body.new_password), db
    ))


@router.post("/refresh", response_model=AuthTokens)
async def refresh(
    body: RefreshRequest, db: Db, service: AuthServiceDependency
) -> AuthTokens:
    return await _tokens(
        service.refresh(RefreshToken(refresh_token=body.refresh_token), db)
    )


@router.post("/logout", response_model=Message)
async def logout(
    body: RefreshRequest, db: Db, service: AuthServiceDependency
) -> Message:
    return await _message(
        service.logout(RefreshToken(refresh_token=body.refresh_token), db)
    )


@router.get("/me", response_model=UserView)
async def read_current_user(user: AuthenticatedUser) -> UserView:
    return UserView(
        id=user.id, email=user.email, email_verified=user.email_verified_at is not None,
        name=user.name,
    )


@router.patch("/me", response_model=UserView)
async def update_current_user_name(
    body: ProfileNameUpdate, user: AuthenticatedUser, db: Db,
    service: AuthServiceDependency,
) -> UserView:
    """Set the current account's display name; credentials and sessions are unchanged."""
    result = await _execute(service.update_profile_name(
        user.id, UpdateProfileName(name=body.name), db
    ))
    return UserView(
        id=result.id, email=result.email, email_verified=result.email_verified,
        name=result.name,
    )
