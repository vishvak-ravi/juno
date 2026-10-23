"""How long each request spent in the server, sent with its response and logged.

Every HTTP response carries ``Server-Timing: app;dur=<ms>``: the milliseconds
from the request arriving to the response's headers being sent, which for a
JSON route is the whole time it took to compute its body. Once the body has
been sent, one ``server.timing`` log line gives the method, path, status, that
duration and the body's bytes.
"""

import logging
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


def server_timing(milliseconds: float) -> bytes:
    """The ``Server-Timing`` header value for ``milliseconds`` spent in the app."""
    return f"app;dur={milliseconds:.1f}".encode()


class TimingMiddleware:
    """Adds ``Server-Timing`` to every HTTP response and logs its duration and size."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        status = 0
        duration = 0.0
        size = 0

        async def timed(message: Message) -> None:
            nonlocal status, duration, size
            if message["type"] == "http.response.start":
                status = message["status"]
                duration = (time.perf_counter() - start) * 1000
                message["headers"] = [*message.get("headers", []),
                                      (b"server-timing", server_timing(duration))]
            elif message["type"] == "http.response.body":
                size += len(message.get("body", b""))
                if not message.get("more_body", False):
                    logger.info("%s %s %d %.1fms %dB", scope["method"], scope["path"],
                                status, duration, size)
            await send(message)

        await self.app(scope, receive, timed)
