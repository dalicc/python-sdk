# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2021-2026 DALICC - Verein zur Foerderung der Rechtssicherheit in der Datenbewirtschaftung (ZVR 1249185710)
"""The plumbing under :class:`dalicc.Client`: one reply object and two transports.

Nothing in here is part of the public interface; it is imported by
:mod:`dalicc.client` and by the test suite. The two things worth knowing about:

* :class:`Reply` is what one HTTP answer looks like inside the client: the status
  code, the headers, the parsed JSON body (``None`` when the body was not JSON) and
  the text. Every model is built from one of these, and every exception carries one.
* :class:`ASGITransport` drives an ASGI application in this process, so that a test
  suite can exercise the client against a real FastAPI app without a socket::

      from dalicc import Client
      from dalicc._transport import ASGITransport

      with Client(base_url="http://testserver", transport=ASGITransport(app)) as api:
          api.licenses.list(keyword="MIT")

  The repository's own suite does exactly that (``sdk/tests`` and
  ``tests/unit/test_sdk_smoke.py``).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json as jsonlib
from typing import Any

import httpx

__all__ = ["ASGITransport", "Reply", "build_transport", "user_agent"]


@dataclass(frozen=True)
class Reply:
    """One HTTP answer, already read."""

    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    payload: Any = None
    text: str = ""
    url: str = ""
    content: bytes = b""

    @classmethod
    def of(cls, response: httpx.Response) -> Reply:
        """Read an ``httpx`` response into the shape the client works with."""
        content_type = response.headers.get("content-type", "")
        payload: Any = None
        if "json" in content_type.lower():
            try:
                payload = response.json()
            except (ValueError, jsonlib.JSONDecodeError):
                payload = None
        return cls(
            status_code=response.status_code,
            headers={key.lower(): value for key, value in response.headers.items()},
            payload=payload,
            text=response.text,
            url=str(response.request.url) if response.request else str(response.url),
            content=response.content,
        )

    @property
    def ok(self) -> bool:
        """True for a 2xx answer."""
        return 200 <= self.status_code < 300

    @property
    def media_type(self) -> str:
        """The content type without its parameters."""
        return self.headers.get("content-type", "").split(";")[0].strip()


def user_agent(version: str) -> str:
    """The default ``User-Agent``, which names the SDK and its version."""
    return f"dalicc-python/{version} (+https://dalicc.net)"


def build_transport(retries: int) -> httpx.HTTPTransport:
    """The default transport, which retries a connection that never got established.

    ``httpx`` retries only connection failures here, never a request that reached the
    service: a ``POST`` that timed out may well have been carried out, and repeating
    it would compose a second license. Rate limits are retried explicitly by the
    client instead, which knows how long to wait.
    """
    return httpx.HTTPTransport(retries=max(0, int(retries)))


class ASGITransport(httpx.BaseTransport):
    """A synchronous ``httpx`` transport that calls an ASGI application directly.

    ``httpx.ASGITransport`` is asynchronous, and the DALICC client is synchronous, so
    this drives it on a private event loop: one request in, the whole answer out. It
    is meant for tests and for a service that mounts the DALICC app itself; a real
    deployment is reached over HTTP like anything else.

    It has to be called from ordinary synchronous code. Inside a running event loop,
    use :class:`httpx.ASGITransport` with an ``httpx.AsyncClient`` instead.
    """

    def __init__(
        self,
        app: Any,
        *,
        raise_app_exceptions: bool = True,
        root_path: str = "",
        client: tuple[str, int] = ("127.0.0.1", 123),
    ) -> None:
        """Wrap the application; the arguments are those of ``httpx.ASGITransport``."""
        self._inner = httpx.ASGITransport(
            app=app,
            raise_app_exceptions=raise_app_exceptions,
            root_path=root_path,
            client=client,
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """Run one request through the application and return the finished answer."""
        body = request.read()

        async def run() -> httpx.Response:
            call = httpx.Request(
                request.method,
                request.url,
                headers=request.headers,
                content=body,
                extensions=request.extensions,
            )
            answer = await self._inner.handle_async_request(call)
            try:
                await answer.aread()
            finally:
                await answer.aclose()
            return answer

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:  # pragma: no cover - a misuse that has to say why
            raise RuntimeError(
                "dalicc.Client is synchronous and cannot be driven from inside a "
                "running event loop. Call it from a worker thread, or use "
                "httpx.AsyncClient with httpx.ASGITransport directly."
            )
        finished = asyncio.run(run())
        return httpx.Response(
            finished.status_code,
            headers=finished.headers,
            content=finished.content,
            extensions=finished.extensions,
            request=request,
        )
