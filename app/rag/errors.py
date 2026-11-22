"""Failures raised by retrieval infrastructure adapters."""


class SearchUnavailableError(RuntimeError):
    """Raised when semantic retrieval cannot reach an embedding/vector service."""


class RetryableProcessingError(RuntimeError):
    """An attempt was released after a transient failure and may be retried."""
