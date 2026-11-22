"""Only known transient dependency failures qualify for worker retries."""

import httpx
import pytest
from celery.exceptions import SoftTimeLimitExceeded
from google.genai.errors import APIError as GeminiAPIError
from openai import APIConnectionError, APIStatusError
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from sqlalchemy.exc import OperationalError

from app.infrastructure.processing_failures import is_retryable_processing_failure


@pytest.mark.parametrize(
    "status", [400, 401, 403, 404, 408, 409, 422, 429, 500, 503, 599, 600]
)
@pytest.mark.parametrize("provider", ["openai", "gemini", "qdrant"])
def test_upstream_status_classification(status, provider):
    if provider == "openai":
        error = APIStatusError(
            "controlled",
            response=httpx.Response(
                status, request=httpx.Request("POST", "https://example.test")
            ),
            body=None,
        )
    elif provider == "gemini":
        error = GeminiAPIError(status, {"error": {"message": "controlled"}})
    else:
        error = UnexpectedResponse(status, "controlled", b"", httpx.Headers())
    assert is_retryable_processing_failure(error) is (
        status in {408, 409, 429} or 500 <= status < 600
    )


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError(),
        ConnectionResetError(),
        SoftTimeLimitExceeded(),
        httpx.ReadTimeout("controlled"),
        httpx.ConnectError("controlled"),
        httpx.RemoteProtocolError("controlled"),
        APIConnectionError(request=httpx.Request("POST", "https://example.test")),
    ],
)
def test_transient_transport_failures(error):
    assert is_retryable_processing_failure(error)
    assert is_retryable_processing_failure(ResponseHandlingException(error))


@pytest.mark.parametrize(
    "error",
    [
        ValueError(),
        RuntimeError(),
        TypeError(),
        PermissionError(),
        httpx.UnsupportedProtocol("controlled"),
    ],
)
def test_unknown_and_permanent_failures_do_not_retry(error):
    assert not is_retryable_processing_failure(error)
    assert not is_retryable_processing_failure(ResponseHandlingException(error))


@pytest.mark.parametrize(
    "code",
    [
        "08006",
        "40001",
        "40P01",
        "55P03",
        "53300",
        "57P01",
        "23505",
        "42501",
        "42P01",
        None,
    ],
)
def test_database_retry_policy_uses_connection_state_and_sqlstate(code):
    original = Exception("controlled")
    original.sqlstate = code
    error = OperationalError(None, None, original)
    expected = code in {"08006", "40001", "40P01", "55P03", "53300", "57P01"}
    assert is_retryable_processing_failure(error) is expected
    error.connection_invalidated = True
    assert is_retryable_processing_failure(error)
