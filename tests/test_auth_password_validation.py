"""All password entry points share the user-approved length bounds."""

import pytest
from pydantic import ValidationError

from app.schemas.auth import (
    Credentials,
    Registration,
    ResetPasswordOtpRequest,
    ResetPasswordRequest,
)


@pytest.mark.parametrize("schema,field,extra", [
    (Credentials, "password", {"email": "password-test@example.com"}),
    (Registration, "password", {"email": "password-test@example.com", "name": "Name"}),
    (ResetPasswordOtpRequest, "new_password", {"email": "password-test@example.com", "code": "000042"}),
    (ResetPasswordRequest, "new_password", {"token": "a-valid-length-test-token"}),
])
@pytest.mark.parametrize("length,valid", [(4, False), (5, True), (128, True), (129, False)])
def test_password_bounds_are_consistent(schema, field, extra, length, valid):
    payload = {**extra, field: "x" * length}
    if valid:
        assert getattr(schema.model_validate(payload), field) == payload[field]
    else:
        with pytest.raises(ValidationError):
            schema.model_validate(payload)


def test_password_is_not_trimmed():
    assert Credentials(email="user@example.com", password=" abcd ").password == " abcd "
