"""Infrastructure failures at the note-processing notification boundary."""


class ProcessingDispatchUnavailable(Exception):
    """The durable job is saved, but immediate worker notification failed."""
