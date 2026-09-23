# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2021-2026 DALICC - Verein zur Foerderung der Rechtssicherheit in der Datenbewirtschaftung (ZVR 1249185710)
"""``api.reasoning``: the compatibility check and the consistency check.

The compatibility check talks to the solver container, which is not running here, so
the outgoing call is captured and answered the way the reasoner would. The consistency
check reasons in process and needs no such help.
"""

from __future__ import annotations

import json
from typing import Any

from app.routers import compatibilitycheck
import pytest

from dalicc import ServerError, ValidationError

LIB = "https://dalicc.net/licenselibrary/"
EMPTY = {"conflicting_statements": {"direct": {}, "derived": {}}}
ONE_CONFLICT = {
    "conflicting_statements": {
        "direct": {
            "0": {
                "statement_1": [LIB + "Apache-2.0", "odrl:permission", "dalicc:ChangeLicense"],
                "statement_2": [LIB + "GPL-3.0-only", "odrl:prohibition", "dalicc:ChangeLicense"],
                "reason": "Direct permission-prohibition conflict.",
            }
        },
        "derived": {},
    }
}


class _Answer:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> Any:
        return self._payload


@pytest.fixture()
def reasoner(monkeypatch):
    """Capture the call to the solver and decide what it answers."""
    calls: list[dict[str, Any]] = []
    state: dict[str, Any] = {"response": _Answer(EMPTY)}

    def fake_post(url: str, **kwargs: Any) -> _Answer:
        calls.append({"url": url, **kwargs})
        answer = state["response"]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(compatibilitycheck.requests, "post", fake_post)
    return type("Reasoner", (), {"calls": calls, "state": state})


# ---------------------------------------------------------------------------
# compatibility
# ---------------------------------------------------------------------------


def test_bare_ids_are_sent_as_full_iris(api, reasoner) -> None:
    """The endpoint takes IRIs while the license endpoints take bare ids."""
    verdict = api.reasoning.compatibility(["MIT", "Apache-2.0"])
    assert reasoner.calls[0]["json"] == {"licenses": [LIB + "MIT", LIB + "Apache-2.0"]}
    assert verdict.has_conflicts is False
    assert verdict.raw == EMPTY


def test_a_license_ref_from_a_listing_can_be_passed_straight_back(api, reasoner) -> None:
    refs = api.licenses.list(keyword="mit").licenses[:1]
    api.reasoning.compatibility([*refs, "Apache-2.0"])
    assert reasoner.calls[0]["json"]["licenses"][0].startswith(LIB)


def test_conflicts_are_read_out_of_the_keyed_objects(api, reasoner) -> None:
    reasoner.state["response"] = _Answer(ONE_CONFLICT)
    verdict = api.reasoning.compatibility(["Apache-2.0", "GPL-3.0-only"])
    assert verdict.has_conflicts is True
    assert len(verdict.direct) == 1
    assert verdict.direct[0].reason.startswith("Direct permission-prohibition")
    assert verdict.direct[0].statement_1[0] == LIB + "Apache-2.0"
    assert verdict.derived == []


def test_a_chosen_dependency_graph_travels_in_the_body(mock_client) -> None:
    """The field the client sends; what the service then forwards is its business."""
    import httpx

    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=EMPTY, headers={"content-type": "application/json"})

    with mock_client(handler) as api:
        api.reasoning.compatibility(["MIT"], dependency_graph="dg_default")
    assert seen[0] == {"licenses": [LIB + "MIT"], "dependency_graph": "dg_default"}


def test_choosing_the_default_graph_changes_nothing_in_the_answer(api, reasoner) -> None:
    """A caller that names the deployment's own graph gets the frozen shape back."""
    verdict = api.reasoning.compatibility(["MIT", "Apache-2.0"], dependency_graph="dg_default")
    assert verdict.raw == EMPTY
    assert verdict.dependency_graph == ""


def test_nothing_is_added_to_the_body_when_no_graph_is_chosen(api, reasoner) -> None:
    api.reasoning.compatibility(["MIT", "Apache-2.0"])
    assert "dependency_graph" not in reasoner.calls[0]["json"]


def test_a_malformed_reference_is_refused_by_the_service(api, reasoner) -> None:
    with pytest.raises(ValidationError):
        api.reasoning.compatibility(["ftp://example.org/x"])
    assert not reasoner.calls


def test_a_failing_solver_is_a_server_error(api, reasoner) -> None:
    reasoner.state["response"] = _Answer({"detail": "solver died"}, status_code=500)
    with pytest.raises(ServerError) as raised:
        api.reasoning.compatibility(["MIT", "Apache-2.0"])
    assert raised.value.status_code == 502


# ---------------------------------------------------------------------------
# consistency
# ---------------------------------------------------------------------------


def test_an_existing_license_does_not_contradict_itself(api) -> None:
    verdict = api.reasoning.consistency("CC-BY-4.0")
    assert verdict.consistent is True
    assert bool(verdict) is True
    assert verdict.conflicts == []


def test_a_full_iri_is_accepted_as_well(api) -> None:
    assert api.reasoning.consistency(LIB + "CC-BY-4.0").consistent is True


def test_a_composed_document_that_contradicts_itself_is_caught(api) -> None:
    verdict = api.reasoning.consistency(
        composer_input={
            "title": "A license that contradicts itself",
            "permissions": [{"action": "odrl:distribute", "duties": []}],
            "prohibitions": ["odrl:distribute"],
        }
    )
    assert verdict.consistent is False
    assert verdict.conflicts
    assert verdict.conflicts[0].reason


def test_naming_both_or_neither_is_refused_before_the_request(api) -> None:
    with pytest.raises(ValueError, match="either a license or a composer_input"):
        api.reasoning.consistency()
    with pytest.raises(ValueError):
        api.reasoning.consistency("MIT", composer_input={"title": "x"})


def test_a_license_outside_the_library_is_refused(api) -> None:
    with pytest.raises(ValidationError):
        api.reasoning.consistency("https://example.org/licenses/X")
