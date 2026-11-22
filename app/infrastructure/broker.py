"""Bound broker socket waits without mutating Celery's shared configuration."""

from celery import Celery
from kombu import Connection


def bounded_write_connection(app: Celery, timeout: float = 0.5) -> Connection:
    options = dict(app.conf.broker_transport_options or {})
    options.update(
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
        retry_on_timeout=False,
        max_retries=0,
    )
    return app.connection_for_write(connect_timeout=timeout, transport_options=options)
