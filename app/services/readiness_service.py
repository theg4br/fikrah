"""Read-only dependency readiness with one overall deadline."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping


class DependencyUnavailable(Exception):
    """A configured dependency could not complete its read-only probe."""


class ReadinessService:
    def __init__(
        self, checks: Mapping[str, Callable[[], Awaitable[None]]], timeout: float = 3
    ) -> None:
        self._checks = dict(checks)
        self._timeout = timeout

    async def check(self) -> dict[str, bool]:
        # A failed/expired probe stays false. Cancel and join every unfinished
        # coroutine before returning, including when the HTTP caller disconnects.
        results = dict.fromkeys(self._checks, False)

        async def probe(name: str, check: Callable[[], Awaitable[None]]) -> None:
            try:
                await check()
            except DependencyUnavailable:
                return
            results[name] = True

        tasks = [asyncio.create_task(probe(name, check)) for name, check in self._checks.items()]
        try:
            async with asyncio.timeout(self._timeout):
                await asyncio.gather(*tasks)
        except TimeoutError:
            pass
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return results
