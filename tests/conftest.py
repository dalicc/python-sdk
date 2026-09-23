# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2021-2026 DALICC - Verein zur Foerderung der Rechtssicherheit in der Datenbewirtschaftung (ZVR 1249185710)
"""Fixtures for the client's suite, outside the DALICC service repository.

Most of this suite runs the client against the real DALICC application, in the same
process, through an ASGI transport.  That is on purpose: it is the only way to prove
that the client and the service agree about paths, parameters, envelopes and status
codes, and a suite of recorded answers would keep passing after the API changed.  The
application lives in the service repository, where the client is developed, and it is
not here.

So the fixtures that need it skip instead of failing, and the file that imports the
service at module level is left uncollected.  What runs here is everything that needs no
service: the models, the paging, the identifier handling, the error mapping, the
rate-limit headers and the transport, driven by a mock transport.  Running the whole
suite means running it in the service repository, with ``make sdk-test``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import sys
from typing import Any

import httpx
import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dalicc import Client  # noqa: E402

#: The application never sees this, but httpx needs a base URL to build from.
BASE_URL = "http://testserver"

#: ``test_reasoning.py`` imports ``app.routers.compatibilitycheck`` at module level, so
#: pytest cannot even collect it without the service.  Every other module imports the
#: service inside a fixture or a test body, and those skip through the fixtures below.
collect_ignore = ["test_reasoning.py"]

_NO_SERVICE = (
    "needs the DALICC application in the same process; run this suite in the service "
    "repository with `make sdk-test`"
)


@pytest.fixture()
def client() -> Any:
    """The application under test, which this repository does not carry."""
    pytest.skip(_NO_SERVICE)


@pytest.fixture()
def settings() -> Any:
    """The application's settings, which this repository does not carry."""
    pytest.skip(_NO_SERVICE)


@pytest.fixture()
def api() -> Any:
    """An anonymous client talking to the application in this process."""
    pytest.skip(_NO_SERVICE)


@pytest.fixture()
def authed() -> Any:
    """A client that sends the token of a fixture account."""
    pytest.skip(_NO_SERVICE)


@pytest.fixture()
def token() -> str:
    """A live API token of a plain member account."""
    pytest.skip(_NO_SERVICE)


@pytest.fixture()
def mock_client() -> Callable[..., Client]:
    """Build a client whose answers come from a handler instead of the application.

    For the cases the application cannot be made to produce on demand: a rate limit with
    its headers, a body that is not JSON, a transport that fails.  This is the one
    fixture that works without the service, and it is what the tests that run here use.
    """

    def factory(handler: Callable[[httpx.Request], httpx.Response], **kwargs: Any) -> Client:
        kwargs.setdefault("api_key", "dalicc_" + "x" * 40)
        return Client(base_url=BASE_URL, transport=httpx.MockTransport(handler), **kwargs)

    return factory
