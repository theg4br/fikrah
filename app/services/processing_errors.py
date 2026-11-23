"""Failures crossing the note-processing retry boundary."""


class RetryableProcessingError(RuntimeError):
    """An attempt was released after a transient failure and may be retried."""
