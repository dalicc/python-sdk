# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2021-2026 DALICC - Verein zur Foerderung der Rechtssicherheit in der Datenbewirtschaftung (ZVR 1249185710)
"""The exceptions the DALICC client raises.

One class per documented status code, so that a caller can react to the thing that
went wrong rather than to a number:

====  ==========================  ===========================================
Code  Exception                   Typical cause
====  ==========================  ===========================================
401   :class:`AuthError`          no API token, or a revoked one
403   :class:`ForbiddenError`     a token that may not see this object
404   :class:`NotFoundError`      an identifier nothing resolves to
409   :class:`ConflictError`      the composed license contradicts itself
413   :class:`PayloadTooLarge`    a text longer than the service reads
422   :class:`ValidationError`    a body or a parameter the service refused
429   :class:`RateLimitedError`   a rate limit or a daily quota
5xx   :class:`ServerError`        the service or one of its upstreams failed
====  ==========================  ===========================================

Every one of them carries the parsed body in ``raw``, the response headers in
``headers``, the status code in ``status_code`` and the address that was called in
``url``, so nothing of the answer is lost on the way up.

A failure that never reached the service (DNS, a refused connection, a timeout) is a
:class:`TransportError`; it has no status code. All of them derive from
:class:`DaliccError`, which is the one class to catch when the distinction does not
matter.
"""

from __future__ import annotations

from collections.abc import Mapping
import datetime as dt
from typing import Any

__all__ = [
    "AuthError",
    "ConflictError",
    "DaliccError",
    "ForbiddenError",
    "NotFoundError",
    "PayloadTooLarge",
    "RateLimitedError",
    "ServerError",
    "TransportError",
    "ValidationError",
    "error_for",
]


class DaliccError(Exception):
    """Anything the client could not turn into a result.

    ``message`` is the sentence the service sent in ``detail`` when it sent one, and a
    description of the failure otherwise.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        url: str = "",
        raw: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Keep everything the answer carried."""
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.url = url
        self.raw = raw
        self.headers: dict[str, str] = dict(headers or {})

    @property
    def detail(self) -> str:
        """The ``detail`` string of the JSON error body, or the message."""
        if isinstance(self.raw, dict) and isinstance(self.raw.get("detail"), str):
            return self.raw["detail"]
        return self.message

    def __str__(self) -> str:
        """The message, prefixed with the status code when there is one."""
        if self.status_code is None:
            return self.message
        return f"{self.status_code}: {self.message}"


class TransportError(DaliccError):
    """The request never got an answer: no connection, no route, a timeout."""


class AuthError(DaliccError):
    """``401``: no API token was sent, or the one that was sent is not live.

    Create a personal token on ``/account/tokens`` and pass it as ``api_key``.
    """


class ForbiddenError(DaliccError):
    """``403``: the account behind the token may not do this, or may not see it."""


class NotFoundError(DaliccError):
    """``404``: no license, version, graph or repository with that identifier."""


class ConflictError(DaliccError):
    """``409``: the license contradicts itself, and nothing was written.

    ``conflicts`` is the list the service sent: one entry per contradicting pair, each
    with ``kind``, ``action_1``, ``action_2``, ``label_1``, ``label_2`` and ``reason``.
    """

    @property
    def conflicts(self) -> list[dict[str, Any]]:
        """The contradicting statements, empty when the body carried none."""
        if isinstance(self.raw, dict):
            found = self.raw.get("conflicts")
            if isinstance(found, list):
                return [entry for entry in found if isinstance(entry, dict)]
        return []


class ValidationError(DaliccError):
    """``422``: a parameter or a body the service refused to read.

    ``detail`` is the sentence it refused with.
    """


class PayloadTooLarge(DaliccError):
    """``413``: the text is longer than the service reads in one call."""


class RateLimitedError(DaliccError):
    """``429``: a rate limit, a per-account window or a daily quota.

    ``retry_after`` is the number of seconds to wait, taken from the ``Retry-After``
    header. ``window`` (``second``, ``minute``, ``hour``, ``day`` or ``global``),
    ``limit``, ``remaining`` and ``reset_at`` come from the JSON body of the
    per-account limiter and from the ``X-RateLimit-*`` headers; a limiter that sends
    neither leaves them ``None``.

    See ``Client(retry_on_rate_limit=True)`` for waiting and retrying automatically.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = 429,
        url: str = "",
        raw: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Read the countdown out of the headers and the body."""
        super().__init__(
            message, status_code=status_code, url=url, raw=raw, headers=headers
        )
        body = raw if isinstance(raw, dict) else {}
        self.retry_after: float = _seconds(self.headers.get("retry-after"))
        self.window: str | None = _text(body.get("window"))
        self.limit: int | None = _whole(
            body.get("limit"), self.headers.get("x-ratelimit-limit")
        )
        self.remaining: int | None = _whole(
            body.get("remaining"), self.headers.get("x-ratelimit-remaining")
        )
        self.reset_at: dt.datetime | None = _moment(
            body.get("reset_at"), self.headers.get("x-ratelimit-reset")
        )
        if not self.retry_after and self.reset_at is not None:
            gap = (self.reset_at - dt.datetime.now(dt.timezone.utc)).total_seconds()
            self.retry_after = max(0.0, gap)

    @property
    def reset_in(self) -> float:
        """Seconds until the window restarts, never negative."""
        if self.reset_at is None:
            return self.retry_after
        gap = (self.reset_at - dt.datetime.now(dt.timezone.utc)).total_seconds()
        return max(0.0, gap)


class ServerError(DaliccError):
    """``5xx``: the service failed, or an upstream of it did.

    ``502`` is an upstream that answered with an error or with something that is not
    JSON, ``503`` a dependency that is not configured or not reachable, ``504`` one
    that did not answer in time.
    """


#: Status code to exception, for everything with a class of its own.
_BY_STATUS: dict[int, type[DaliccError]] = {
    401: AuthError,
    403: ForbiddenError,
    404: NotFoundError,
    409: ConflictError,
    413: PayloadTooLarge,
    422: ValidationError,
    429: RateLimitedError,
}


def error_for(
    status_code: int,
    *,
    url: str = "",
    raw: Any = None,
    headers: Mapping[str, str] | None = None,
    text: str = "",
) -> DaliccError:
    """Build the exception one answer deserves, without raising it."""
    message = _message(status_code, raw, text)
    kind = _BY_STATUS.get(status_code)
    if kind is None:
        kind = ServerError if status_code >= 500 else DaliccError
    return kind(message, status_code=status_code, url=url, raw=raw, headers=headers)


def _message(status_code: int, raw: Any, text: str) -> str:
    """The sentence to put in the exception."""
    if isinstance(raw, dict):
        detail = raw.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        if isinstance(detail, list) and detail:
            # FastAPI's own validation errors are a list of objects.
            first = detail[0]
            if isinstance(first, dict) and first.get("msg"):
                where = ".".join(str(part) for part in first.get("loc", ()))
                return f"{first['msg']} ({where})" if where else str(first["msg"])
        if isinstance(raw.get("message"), str):
            return raw["message"]
    stripped = (text or "").strip()
    if stripped:
        return stripped[:400]
    return f"The DALICC API answered {status_code}."


def _text(value: Any) -> str | None:
    """A non-empty string, or ``None``."""
    return value if isinstance(value, str) and value else None


def _seconds(value: Any) -> float:
    """A ``Retry-After`` header as seconds; ``0.0`` when it is absent or unreadable."""
    try:
        return max(0.0, float(str(value).strip()))
    except (TypeError, ValueError):
        return 0.0


def _whole(*candidates: Any) -> int | None:
    """The first candidate that reads as a whole number."""
    for value in candidates:
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            continue
    return None


def _moment(body_value: Any, header_value: Any) -> dt.datetime | None:
    """The reset time: an ISO timestamp from the body, else a UNIX one from a header."""
    if isinstance(body_value, str) and body_value.strip():
        text = body_value.strip().replace("Z", "+00:00")
        try:
            moment = dt.datetime.fromisoformat(text)
        except ValueError:
            moment = None
        if moment is not None:
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=dt.timezone.utc)
            return moment
    stamp = _whole(header_value)
    if stamp is None:
        return None
    try:
        return dt.datetime.fromtimestamp(stamp, dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
