# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2021-2026 DALICC - Verein zur Foerderung der Rechtssicherheit in der Datenbewirtschaftung (ZVR 1249185710)
"""The client itself: configuration, headers, error mapping and rate limits."""

from __future__ import annotations

import httpx
import pytest

from dalicc import (
    AuthError,
    Client,
    ConflictError,
    DaliccError,
    ForbiddenError,
    NotFoundError,
    PayloadTooLarge,
    RateLimitedError,
    ServerError,
    TransportError,
    ValidationError,
    __version__,
)

OK = {"content-type": "application/json"}


def answer(payload, status=200, headers=None):
    """A handler that always answers the same thing."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload, headers=headers or {})

    return handler


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


def test_the_public_service_is_the_default() -> None:
    with Client(api_key="") as api:
        assert api.base_url == "https://api.dalicc.net"


def test_the_environment_names_the_deployment_and_the_token(monkeypatch) -> None:
    monkeypatch.setenv("DALICC_BASE_URL", "https://dalicc.example/")
    monkeypatch.setenv("DALICC_API_KEY", "dalicc_from_the_environment")
    with Client() as api:
        assert api.base_url == "https://dalicc.example"
        assert api.api_key == "dalicc_from_the_environment"


def test_an_empty_key_stays_anonymous_even_with_the_variable_set(monkeypatch) -> None:
    monkeypatch.setenv("DALICC_API_KEY", "dalicc_from_the_environment")
    with Client(api_key="") as api:
        assert api.api_key == ""
        assert "Authorization" not in api._http.headers


def test_the_token_becomes_a_bearer_header_and_is_never_in_the_repr() -> None:
    with Client(api_key="dalicc_secret_value") as api:
        assert api._http.headers["Authorization"] == "Bearer dalicc_secret_value"
        assert "secret" not in repr(api)
        assert "authenticated=True" in repr(api)


def test_the_user_agent_names_the_sdk_and_can_be_replaced() -> None:
    with Client(api_key="") as api:
        assert __version__ in api._http.headers["User-Agent"]
    with Client(api_key="", user_agent="my-tool/1.0") as api:
        assert api._http.headers["User-Agent"] == "my-tool/1.0"


def test_the_client_closes_its_pool_on_the_way_out() -> None:
    with Client(api_key="") as api:
        pass
    assert api._http.is_closed


# ---------------------------------------------------------------------------
# error mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, AuthError),
        (403, ForbiddenError),
        (404, NotFoundError),
        (409, ConflictError),
        (413, PayloadTooLarge),
        (422, ValidationError),
        (429, RateLimitedError),
        (500, ServerError),
        (502, ServerError),
        (503, ServerError),
        (504, ServerError),
        (418, DaliccError),
    ],
)
def test_every_documented_status_becomes_its_own_exception(mock_client, status, expected) -> None:
    with mock_client(answer({"detail": "no"}, status)) as api:
        with pytest.raises(expected) as raised:
            api.licenses.list()
    assert raised.value.status_code == status
    assert raised.value.detail == "no"
    assert raised.value.raw == {"detail": "no"}


def test_the_detail_sentence_survives_into_the_message(mock_client) -> None:
    with mock_client(answer({"detail": "Unknown license id: NOPE-123"}, 404)) as api:
        with pytest.raises(NotFoundError) as raised:
            api.licenses.get("NOPE-123")
    assert "Unknown license id" in str(raised.value)
    assert str(raised.value).startswith("404: ")


def test_a_conflict_carries_the_conflicting_statements(mock_client) -> None:
    body = {
        "id": "",
        "uri": "",
        "status": "rejected",
        "version": 0,
        "conflicts": [{"kind": "direct", "reason": "Distribute is permitted and prohibited."}],
        "detail": "The license contains contradicting statements.",
    }
    with mock_client(answer(body, 409)) as api, pytest.raises(ConflictError) as raised:
        api.composing.create({"title": "x"})
    assert raised.value.conflicts[0]["reason"].startswith("Distribute")
    assert raised.value.detail == "The license contains contradicting statements."


def test_a_fastapi_validation_body_is_turned_into_a_sentence(mock_client) -> None:
    body = {"detail": [{"loc": ["body", "licenses"], "msg": "field required"}]}
    with mock_client(answer(body, 422)) as api, pytest.raises(ValidationError) as raised:
        api.licenses.list()
    assert "field required" in raised.value.message
    assert "body.licenses" in raised.value.message


def test_a_body_that_is_not_json_still_produces_a_message(mock_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>Bad gateway</html>")

    with mock_client(handler) as api, pytest.raises(ServerError) as raised:
        api.licenses.list()
    assert "Bad gateway" in raised.value.message


def test_a_connection_that_never_happened_is_a_transport_error(mock_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    with mock_client(handler) as api, pytest.raises(TransportError) as raised:
        api.licenses.list()
    assert "could not be reached" in str(raised.value)
    assert raised.value.status_code is None


# ---------------------------------------------------------------------------
# rate limits
# ---------------------------------------------------------------------------

LIMIT_BODY = {
    "detail": "You have used your DALICC API allowance for this minute: at most 60 "
    "requests per minute. The window restarts in 19 seconds.",
    "window": "minute",
    "limit": 60,
    "remaining": 0,
    "reset_at": "2026-09-16T12:31:00Z",
}
LIMIT_HEADERS = {
    "Retry-After": "19",
    "X-RateLimit-Limit": "60",
    "X-RateLimit-Remaining": "0",
    "X-RateLimit-Reset": "1789567260",
}


def test_a_429_carries_the_window_the_limit_and_the_countdown(mock_client) -> None:
    with mock_client(answer(LIMIT_BODY, 429, LIMIT_HEADERS)) as api:
        with pytest.raises(RateLimitedError) as raised:
            api.licenses.list()
    limited = raised.value
    assert limited.retry_after == 19.0
    assert limited.window == "minute"
    assert limited.limit == 60
    assert limited.remaining == 0
    assert limited.reset_at is not None
    assert limited.reset_at.isoformat().startswith("2026-09-16T12:31:00")


def test_a_429_without_a_body_still_reads_the_headers(mock_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="", headers={"Retry-After": "5"})

    with mock_client(handler) as api, pytest.raises(RateLimitedError) as raised:
        api.licenses.list()
    assert raised.value.retry_after == 5.0
    assert raised.value.window is None


def test_without_the_option_a_rate_limit_is_raised_at_once(mock_client) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, json=LIMIT_BODY, headers=LIMIT_HEADERS)

    with mock_client(handler) as api, pytest.raises(RateLimitedError):
        api.licenses.list()
    assert len(calls) == 1


def test_with_the_option_the_client_waits_and_tries_again(mock_client) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json=LIMIT_BODY, headers=LIMIT_HEADERS)
        return httpx.Response(200, json={"results": {"bindings": []}}, headers=OK)

    waited: list[float] = []
    with mock_client(handler, retry_on_rate_limit=True, max_wait=60.0) as api:
        api._sleep = waited.append
        listing = api.licenses.list()
    assert len(calls) == 2
    assert waited == [19.0]
    assert len(listing) == 0


def test_a_countdown_longer_than_max_wait_is_not_waited_out(mock_client) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, json=LIMIT_BODY, headers={**LIMIT_HEADERS, "Retry-After": "900"})

    waited: list[float] = []
    with mock_client(handler, retry_on_rate_limit=True, max_wait=60.0) as api:
        api._sleep = waited.append
        with pytest.raises(RateLimitedError):
            api.licenses.list()
    assert len(calls) == 1
    assert waited == []


def test_the_client_gives_up_after_the_configured_number_of_retries(mock_client) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, json=LIMIT_BODY, headers=LIMIT_HEADERS)

    with mock_client(handler, retry_on_rate_limit=True, retries=2) as api:
        api._sleep = lambda seconds: None
        with pytest.raises(RateLimitedError):
            api.licenses.list()
    assert len(calls) == 3  # the first call and two retries


def test_the_rate_limit_headers_of_a_good_answer_are_remembered(mock_client) -> None:
    headers = {
        **OK,
        "X-RateLimit-Limit": "60",
        "X-RateLimit-Remaining": "41",
        "X-RateLimit-Reset": "1789567260",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"licenses": []}, headers=headers)

    with mock_client(handler) as api:
        assert api.account.limits_known() is False
        status = api.account.limits()
    assert (status.limit, status.remaining) == (60, 41)
    assert api.account.limits_known() is True
    # A second call answers from what was remembered.
    assert api.account.limits() is status


# ---------------------------------------------------------------------------
# the escape hatch
# ---------------------------------------------------------------------------


def test_any_endpoint_can_be_called_directly(mock_client) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"status": "ok"}, headers=OK)

    with mock_client(handler) as api:
        document = api.json("/healthz")
        reply = api.request("GET", "/whatever", params={"q": "1"})
    assert document["status"] == "ok"
    assert document.headers["content-type"] == "application/json"
    assert reply.status_code == 200
    assert seen == ["http://testserver/healthz", "http://testserver/whatever?q=1"]


# ---------------------------------------------------------------------------
# the asynchronous mirror
# ---------------------------------------------------------------------------


def test_every_method_of_the_async_client_is_awaited(client) -> None:
    """The mirror is the same object, one ``await`` away."""
    import asyncio

    from dalicc import AsyncClient
    from dalicc._transport import ASGITransport

    async def scenario() -> tuple[list[str], dict]:
        async with AsyncClient(
            base_url="http://testserver", api_key="", transport=ASGITransport(client.app)
        ) as api:
            listing = await api.licenses.list(keyword="mit")
            document = await api.licenses.get("MIT")
            assert "authenticated=False" in repr(api)
            assert api.base_url == "http://testserver"
            return listing.ids, document

    ids, document = asyncio.run(scenario())
    assert "MIT" in ids
    assert "@graph" in document


def test_the_async_mirror_keeps_the_names_and_the_docstrings() -> None:
    """``help()`` on the mirror shows the documentation of the method it wraps."""
    import asyncio

    from dalicc import AsyncClient, Client

    mirror = AsyncClient(api_key="")
    try:
        assert asyncio.iscoroutinefunction(mirror.licenses.list)
        assert mirror.licenses.list.__doc__ == Client(api_key="").licenses.list.__doc__
        assert "list" in dir(mirror.licenses)
        assert "Licenses" in repr(mirror.licenses)
    finally:
        asyncio.run(mirror.aclose())
