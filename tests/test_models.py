# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2021-2026 DALICC - Verein zur Foerderung der Rechtssicherheit in der Datenbewirtschaftung (ZVR 1249185710)
"""Parsing, normalising and paging, without a service in the way."""

from __future__ import annotations

import pytest

from dalicc import (
    CompatibilityResult,
    LicenseListing,
    LicenseRef,
    RateLimitStatus,
    faceted_search_body,
    license_id,
    license_uri,
    paginate,
)

LIB = "https://dalicc.net/licenselibrary/"

ENVELOPE = {
    "head": {"link": [], "vars": ["id", "title"]},
    "results": {
        "distinct": False,
        "ordered": True,
        "bindings": [
            {
                "id": {"type": "uri", "value": LIB + "AFL-3.0"},
                "title": {"type": "literal", "xml:lang": "en", "value": "Academic Free License"},
            },
            {
                "id": {"type": "uri", "value": LIB + "MIT"},
                "title": {"type": "literal", "value": "MIT License"},
            },
        ],
    },
}


# ---------------------------------------------------------------------------
# identifiers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "given",
    ["MIT", LIB + "MIT", LIB + "MIT/", LicenseRef(id="MIT", uri=LIB + "MIT"), {"id": "MIT"}],
)
def test_every_spelling_of_a_reference_gives_the_bare_id(given) -> None:
    assert license_id(given) == "MIT"


@pytest.mark.parametrize("given", ["MIT", LIB + "MIT", LicenseRef(id="MIT", uri=LIB + "MIT")])
def test_every_spelling_of_a_reference_gives_the_full_iri(given) -> None:
    assert license_uri(given) == LIB + "MIT"


def test_a_foreign_iri_is_left_alone() -> None:
    """A license of another deployment stays addressable."""
    assert license_uri("https://example.org/licenses/X") == "https://example.org/licenses/X"


def test_a_deployment_can_publish_under_another_namespace() -> None:
    assert license_uri("MIT", base="https://example.org/lib/") == "https://example.org/lib/MIT"


# ---------------------------------------------------------------------------
# the SPARQL envelope
# ---------------------------------------------------------------------------


def test_the_envelope_is_read_into_rows_and_kept_whole() -> None:
    listing = LicenseListing.parse(ENVELOPE, {"content-type": "application/json"})
    assert listing.ids == ["AFL-3.0", "MIT"]
    assert listing[1].title == "MIT License"
    assert listing[1].uri == LIB + "MIT"
    assert len(listing) == 2
    assert [ref.id for ref in listing] == ["AFL-3.0", "MIT"]
    # The frozen envelope is still there, untouched.
    assert listing.envelope is listing.raw
    assert listing.envelope["head"]["vars"] == ["id", "title"]
    assert listing.headers["content-type"] == "application/json"


def test_an_empty_envelope_is_an_empty_listing() -> None:
    assert len(LicenseListing.parse({"results": {"bindings": []}})) == 0
    assert len(LicenseListing.parse(None)) == 0


# ---------------------------------------------------------------------------
# the compatibility answer
# ---------------------------------------------------------------------------


def test_the_stringified_integer_keys_become_a_list_in_order() -> None:
    payload = {
        "conflicting_statements": {
            "direct": {
                "1": {
                    "statement_1": [LIB + "A", "p", "x"],
                    "statement_2": [LIB + "B", "q", "x"],
                    "reason": "second",
                },
                "0": {
                    "statement_1": [LIB + "A", "p", "y"],
                    "statement_2": [LIB + "B", "q", "y"],
                    "reason": "first",
                },
            },
            "derived": {},
        }
    }
    result = CompatibilityResult.parse(payload)
    assert [conflict.reason for conflict in result.direct] == ["first", "second"]
    assert result.direct[0].kind == "direct"
    assert result.direct[0].statement_1 == (LIB + "A", "p", "y")
    assert result.derived == []
    assert result.has_conflicts is True
    assert bool(result) is False


def test_an_empty_object_is_no_conflict() -> None:
    result = CompatibilityResult.parse(
        {"conflicting_statements": {"direct": {}, "derived": {}}}
    )
    assert result.has_conflicts is False
    assert bool(result) is True
    assert result.conflicts == []


def test_a_chosen_dependency_graph_is_reported() -> None:
    result = CompatibilityResult.parse(
        {"conflicting_statements": {"direct": {}, "derived": {}}, "dependency_graph": "iri"}
    )
    assert result.dependency_graph == "iri"


# ---------------------------------------------------------------------------
# the faceted search body
# ---------------------------------------------------------------------------


def test_the_search_body_fills_every_group_the_service_requires() -> None:
    body = faceted_search_body(actions={"commercial_use": "permitted"})
    assert set(body) == {"target", "actions", "duties", "license_wide_duties"}
    assert body["actions"]["commercial_use"] == "permitted"
    assert body["actions"]["reproduce"] == "na"
    assert body["target"] == {"creativework": "yes", "dataset": "yes", "software": "yes"}
    assert body["license_wide_duties"] == {"share_alike": "na"}


def test_an_unknown_facet_is_refused_before_the_request_goes_out() -> None:
    with pytest.raises(ValueError, match="Unknown actions facet"):
        faceted_search_body(actions={"fly": "permitted"})


# ---------------------------------------------------------------------------
# rate-limit headers
# ---------------------------------------------------------------------------


def test_the_rate_limit_headers_are_read_whatever_their_case() -> None:
    status = RateLimitStatus.parse(
        {
            "X-RateLimit-Limit": "60",
            "x-ratelimit-remaining": "41",
            "X-RateLimit-Reset": "1789567260",
        }
    )
    assert (status.limit, status.remaining) == (60, 41)
    assert status.known is True
    assert status.reset_at is not None
    assert status.reset_at.year >= 2026


def test_an_anonymous_answer_carries_no_limits() -> None:
    status = RateLimitStatus.parse({"content-type": "application/json"})
    assert status.known is False
    assert status.reset_in is None


# ---------------------------------------------------------------------------
# paging
# ---------------------------------------------------------------------------


def test_paging_stops_on_a_short_page() -> None:
    pages = {0: [1, 2, 3], 3: [4, 5]}
    calls: list[tuple[int, int]] = []

    def fetch(skip: int, limit: int) -> list[int]:
        calls.append((skip, limit))
        return pages.get(skip, [])

    assert list(paginate(fetch, limit=3)) == [1, 2, 3, 4, 5]
    assert calls == [(0, 3), (3, 3)]


def test_paging_stops_on_an_empty_page_and_honours_a_ceiling() -> None:
    full = {0: [1, 2], 2: [3, 4], 4: []}

    def fetch(skip: int, limit: int) -> list[int]:
        return full.get(skip, [])

    assert list(paginate(fetch, limit=2)) == [1, 2, 3, 4]
    assert list(paginate(fetch, limit=2, max_items=3)) == [1, 2, 3]


def test_paging_refuses_nonsense() -> None:
    with pytest.raises(ValueError, match="limit"):
        list(paginate(lambda skip, limit: [], limit=0))
    with pytest.raises(ValueError, match="skip"):
        list(paginate(lambda skip, limit: [], skip=-1))
