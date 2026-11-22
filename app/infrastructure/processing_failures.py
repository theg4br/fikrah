"""Classify dependency failures at the worker composition boundary.

Unknown failures are terminal, so configuration, validation and programming
errors do not repeatedly spend the LLM budget. No SDK types enter RAG ports.
"""

import httpx
from celery.exceptions import SoftTimeLimitExceeded
from google.genai.errors import APIError as GeminiAPIError
from openai import APIConnectionError, APIStatusError
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from sqlalchemy.exc import DBAPIError


def _transient_status(status: int | None) -> bool:
    return status in {408, 409, 429} or (status is not None and 500 <= status < 600)


def is_retryable_processing_failure(error: Exception) -> bool:
    """Allow timeouts, transport failures, overload and retryable SQL failures."""
    if isinstance(error, (APIStatusError, UnexpectedResponse)):
        return _transient_status(error.status_code)
    if isinstance(error, GeminiAPIError):
        return _transient_status(error.code)
    if isinstance(error, ResponseHandlingException):
        return is_retryable_processing_failure(error.source)
    if isinstance(error, DBAPIError):
        sqlstate = getattr(error.orig, "sqlstate", None) or getattr(
            error.orig, "pgcode", None
        )
        return error.connection_invalidated or (
            isinstance(sqlstate, str)
            and (
                sqlstate.startswith("08")
                or sqlstate
                in {"40001", "40P01", "55P03", "53300", "57P01", "57P02", "57P03"}
            )
        )
    return isinstance(
        error,
        (
            SoftTimeLimitExceeded,
            APIConnectionError,
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
            ConnectionError,
            TimeoutError,
        ),
    )
