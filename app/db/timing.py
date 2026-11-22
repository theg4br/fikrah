"""SQL execution timing without recording statements, parameters, or results."""

from time import perf_counter

from sqlalchemy import event

from app.core.telemetry import observe


def instrument_database(engine) -> None:
    """Measure cursor execution/lock waits, never SQL text, parameters, or results."""
    if event.contains(engine, "before_cursor_execute", _before_query):
        return
    event.listen(engine, "before_cursor_execute", _before_query)
    event.listen(engine, "after_cursor_execute", _after_query)
    event.listen(engine, "handle_error", _query_error)


def _before_query(_connection, _cursor, _statement, _parameters, context, _many):
    context._fikrah_query_started = perf_counter()


def _after_query(_connection, _cursor, _statement, _parameters, context, _many):
    observe("db.query", perf_counter() - context._fikrah_query_started)


def _query_error(context):
    started = getattr(context.execution_context, "_fikrah_query_started", None)
    if started is not None:
        observe("db.query", perf_counter() - started, outcome="error")
