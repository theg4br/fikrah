"""ASGI request/first-body timing, including streaming and disconnects."""

import asyncio
from time import perf_counter

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.telemetry import correlation, observe


class RequestTimingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = perf_counter()
        status_code = None
        first_body = False
        response_finished = False
        disconnected = False
        outcome = "ok"

        def metadata():
            route = getattr(scope.get("route"), "path", "unmatched")
            method = scope.get("method")
            if method not in {
                "GET",
                "POST",
                "PATCH",
                "PUT",
                "DELETE",
                "HEAD",
                "OPTIONS",
            }:
                method = "OTHER"
            return {"route": route, "method": method, "status_code": status_code}

        async def timed_send(message):
            nonlocal status_code, first_body, response_finished
            await send(message)
            if message["type"] == "http.response.start":
                status_code = message["status"]
            elif (
                message["type"] == "http.response.body"
                and message.get("body")
                and not first_body
            ):
                first_body = True
                observe(
                    "http.first_body",
                    perf_counter() - started,
                    outcome="error"
                    if status_code is not None and status_code >= 400
                    else "ok",
                    **metadata(),
                )
            if message["type"] == "http.response.body" and not message.get(
                "more_body", False
            ):
                response_finished = True

        async def timed_receive():
            nonlocal disconnected
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
            return message

        with correlation():
            try:
                await self.app(scope, timed_receive, timed_send)
            except asyncio.CancelledError:
                outcome = "cancelled"
                raise
            except Exception:
                outcome = "error"
                raise
            finally:
                if disconnected and not response_finished:
                    outcome = "cancelled"
                if status_code is not None and status_code >= 400 and outcome == "ok":
                    outcome = "error"
                observe(
                    "http.request",
                    perf_counter() - started,
                    outcome=outcome,
                    **metadata(),
                )
