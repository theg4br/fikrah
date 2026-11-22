"""Bounded JSON timing-log delivery without blocking application work on stdout."""

import json
import logging
import os
import sys
from queue import Empty, Full, Queue
from threading import Thread

from app.core.telemetry import LOGGER_NAME


class BufferedTimingHandler(logging.Handler):
    """Drop excess events instead of blocking application work on a slow sink."""

    def __init__(self, sink=None, capacity: int = 1024) -> None:
        super().__init__()
        self.owner_pid = os.getpid()
        self.queue = Queue(maxsize=capacity)
        self.sink = sink if sink is not None else sys.stdout
        self.dropped_records = 0
        self._stopped = False
        self._thread = Thread(target=self._drain, name="performance-logs", daemon=True)
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        if self._stopped:
            return
        fields = dict(record.measurement)
        fields["dropped_records"] = self.dropped_records
        try:
            self.queue.put_nowait(fields)
        except Full:
            self.dropped_records += 1

    def _drain(self) -> None:
        while (fields := self.queue.get()) is not None:
            try:
                self.sink.write(json.dumps(fields, separators=(",", ":")) + "\n")
                self.sink.flush()
            except Exception:  # noqa: BLE001, S110 - never log a sink failure recursively
                pass

    def close(self) -> None:
        with self.lock:
            if not self._stopped and self.owner_pid == os.getpid():
                self._stopped = True
                try:
                    self.queue.put_nowait(None)
                except Full:
                    try:
                        self.queue.get_nowait()
                    except Empty:
                        pass
                    self.queue.put_nowait(None)
                self._thread.join(timeout=0.5)
        super().close()


def start_timing_logs(*, sink=None, **_kwargs) -> None:
    """Create one sink per process; prefork workers must replace inherited queues."""
    logger = logging.getLogger(LOGGER_NAME)
    # Alembic/Celery or a host application's logging configuration may disable
    # existing named loggers. Re-establish our explicit startup configuration.
    logger.disabled = False
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        if isinstance(handler, BufferedTimingHandler):
            if handler.owner_pid == os.getpid() and not handler._stopped:
                return
            logger.removeHandler(handler)
    logger.addHandler(BufferedTimingHandler(sink=sink))


def stop_timing_logs(**_kwargs) -> None:
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        if isinstance(handler, BufferedTimingHandler):
            handler.close()
            logger.removeHandler(handler)
