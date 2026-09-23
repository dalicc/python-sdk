# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2021-2026 DALICC - Verein zur Foerderung der Rechtssicherheit in der Datenbewirtschaftung (ZVR 1249185710)
"""``api.composing``: the endpoints behind a personal API token."""

from __future__ import annotations

import pytest

from dalicc import AuthError, ConflictError, NotFoundError

DOCUMENT = {
    "title": "Terms of use for the sample dataset",
    "licensor": "A Company",
    "targets": ["dataset"],
    "permissions": [
        {"action": "odrl:distribute", "duties": ["cc:Attribution"]},
        {"action": "odrl:reproduce", "duties": []},
    ],
    "prohibitions": ["dalicc:promote"],
    "duties": ["cc:ShareAlike"],
}

CONTRADICTION = {
    "title": "A license that contradicts itself",
    "permissions": [{"action": "odrl:distribute", "duties": []}],
    "prohibitions": ["odrl:distribute"],
}

TURTLE = """
@prefix cc: <http://creativecommons.org/ns#> .
@prefix dalicc: <https://dalicc.net/ns#> .
@prefix dalicclib: <https://dalicc.net/licenselibrary/> .
@prefix dcmitype: <http://purl.org/dc/dcmitype/> .
@prefix dct: <http://purl.org/dc/terms/> .
@prefix odrl: <http://www.w3.org/ns/odrl/2/> .

dalicclib:TurtleFixtureLicense000000000001 a odrl:Set ;
    dct:title "A license sent as Turtle" ;
    cc:jurisdiction dalicc:worldwide ;
    dalicc:validityType dalicc:perpetual ;
    odrl:permission [ a odrl:Permission ;
            odrl:action odrl:distribute ;
            odrl:duty [ a odrl:Duty ; odrl:action cc:Attribution ] ] ;
    odrl:target [ a odrl:AssetCollection ; dct:type dcmitype:Dataset ] .
"""


# ---------------------------------------------------------------------------
# authentication
# ---------------------------------------------------------------------------


def test_composing_without_a_token_is_refused(api) -> None:
    with pytest.raises(AuthError) as raised:
        api.composing.create(DOCUMENT)
    assert raised.value.status_code == 401
    assert "API token" in raised.value.detail


def test_the_other_token_endpoints_are_refused_too(api) -> None:
    for call in (
        lambda: api.composing.mine(),
        lambda: api.composing.revisions("whatever"),
        lambda: api.composing.deprecate("whatever"),
        lambda: api.dependency_graphs.mine(),
    ):
        with pytest.raises(AuthError):
            call()


# ---------------------------------------------------------------------------
# composing
# ---------------------------------------------------------------------------


def test_a_published_license_comes_back_with_its_permanent_address(authed) -> None:
    result = authed.composing.create(DOCUMENT)
    assert result.status == "published"
    assert result.version == 1
    assert result.conflicts == []
    assert len(result.id) == 32
    assert result.uri.endswith(result.id)


def test_a_draft_is_private_and_not_published(authed) -> None:
    result = authed.composing.create(DOCUMENT, publish=False)
    assert result.status == "draft"
    assert result.id


def test_turtle_is_accepted_as_a_body(authed) -> None:
    result = authed.composing.create(TURTLE)
    assert result.status == "published"
    # DALICC always mints a fresh identifier; the one in the document is ignored.
    assert result.id != "TurtleFixtureLicense000000000001"


def test_a_license_that_contradicts_itself_is_refused_and_nothing_is_written(authed) -> None:
    before = len(authed.composing.mine())
    with pytest.raises(ConflictError) as raised:
        authed.composing.create(CONTRADICTION)
    assert raised.value.status_code == 409
    assert raised.value.conflicts
    assert raised.value.raw["status"] == "rejected"
    assert len(authed.composing.mine()) == before


def test_a_body_that_cannot_be_validated_is_refused(authed) -> None:
    from dalicc import ValidationError

    with pytest.raises(ValidationError):
        authed.composing.create({"title": "x", "permissions": [{"action": "odrl:fly"}]})


# ---------------------------------------------------------------------------
# what happens to a license afterwards
# ---------------------------------------------------------------------------


def test_my_licenses_lists_what_this_account_composed(authed) -> None:
    created = authed.composing.create(DOCUMENT)
    listing = authed.composing.mine()
    entry = next(item for item in listing if item.id == created.id)
    assert entry.role == "owner"
    assert entry.status == "published"
    assert entry.title == DOCUMENT["title"]
    assert entry.uri == created.uri


def test_every_saved_state_of_a_license_is_kept(authed) -> None:
    created = authed.composing.create(DOCUMENT)
    revisions = authed.composing.revisions(created.id)
    assert revisions.id == created.id
    assert revisions.current == len(revisions.revisions)
    assert revisions.revisions[0].kind == "publish"
    assert revisions.revisions[0].actor == "Sam Sdk"
    assert "@" not in revisions.revisions[0].actor


def test_a_license_of_somebody_else_is_not_found(authed) -> None:
    with pytest.raises(NotFoundError):
        authed.composing.revisions("MIT")


def test_a_published_license_can_be_withdrawn(authed) -> None:
    old = authed.composing.create(DOCUMENT)
    new = authed.composing.create(DOCUMENT)
    result = authed.composing.deprecate(old.id, reason="Superseded", replaced_by=new.id)
    assert result.status == "deprecated"
    entry = next(item for item in authed.composing.mine() if item.id == old.id)
    assert entry.status == "deprecated"
    assert entry.replaced_by == new.id


def test_a_license_cannot_replace_itself(authed) -> None:
    from dalicc import ValidationError

    created = authed.composing.create(DOCUMENT)
    with pytest.raises(ValidationError):
        authed.composing.deprecate(created.id, replaced_by=created.id)


def test_a_composed_license_resolves_at_its_own_address(authed) -> None:
    created = authed.composing.create(DOCUMENT)
    document = authed.licenses.get(created.uri)
    assert document["@graph"]


def test_the_account_helper_is_the_same_listing(authed) -> None:
    authed.composing.create(DOCUMENT)
    assert authed.account.licenses().licenses == authed.composing.mine().licenses
