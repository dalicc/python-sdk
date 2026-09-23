# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2021-2026 DALICC - Verein zur Foerderung der Rechtssicherheit in der Datenbewirtschaftung (ZVR 1249185710)
"""``api.dependency_graphs``: the reasoning axioms and the graphs somebody owns."""

from __future__ import annotations

import pytest

from dalicc import AuthError, NotFoundError, ValidationError

ODRL = "http://www.w3.org/ns/odrl/2/"


def test_the_default_graph_is_dumped_statement_by_statement(api) -> None:
    axioms = api.dependency_graphs.list_axioms()
    assert len(axioms) > 0
    first = axioms.axioms[0]
    assert first.subject.startswith("http")
    assert first.predicate.startswith("http")
    assert str(first).count(" ") == 2
    assert "dependency_graph_statements" in axioms.raw


def test_the_core_graph_can_be_named(api) -> None:
    assert api.dependency_graphs.list_axioms(graph="dg_default").axioms


def test_an_unknown_graph_is_a_not_found(api) -> None:
    with pytest.raises(NotFoundError):
        api.dependency_graphs.list_axioms(graph="no-such-graph")


def test_an_iri_outside_the_dalicc_spaces_is_refused(api) -> None:
    with pytest.raises(ValidationError):
        api.dependency_graphs.list_axioms(graph="https://example.org/graph")


def test_the_published_core_graphs_are_listed_to_anybody(api) -> None:
    listing = api.dependency_graphs.graphs()
    default = next(graph for graph in listing if graph.default)
    assert default.kind == "core"
    assert default.status == "published"
    assert default.axioms > 0
    assert default.iri.startswith("https://dalicc.net/")


def test_my_graphs_needs_a_token(api) -> None:
    with pytest.raises(AuthError):
        api.dependency_graphs.mine()


def test_a_graph_can_be_composed_and_kept_private(authed) -> None:
    result = authed.dependency_graphs.create(
        axioms=[("cc:Attribution", "odrl:implies", "cc:Notice")],
        title="Attribution implies a notice",
        description="For the SDK suite.",
    )
    assert result.status == "draft"
    assert result.axioms == 1
    assert result.id
    mine = authed.dependency_graphs.mine()
    assert result.id in [graph.id for graph in mine]


def test_a_published_graph_can_be_reasoned_with(authed) -> None:
    result = authed.dependency_graphs.create(
        axioms=[{"subject": "cc:Attribution", "relation": "odrl:implies", "object": "cc:Notice"}],
        title="Published for the SDK suite",
        publish=True,
    )
    assert result.status == "published"
    axioms = authed.dependency_graphs.list_axioms(graph=result.id)
    assert len(axioms) == 1
    assert axioms.axioms[0].predicate == ODRL + "implies"


def test_a_graph_of_somebody_else_is_refused(api, authed) -> None:
    """An anonymous caller may not read a graph that belongs to an account."""
    from dalicc import ForbiddenError

    result = authed.dependency_graphs.create(
        axioms=[("cc:Attribution", "odrl:implies", "cc:Notice")], title="Private"
    )
    with pytest.raises((ForbiddenError, NotFoundError)):
        api.dependency_graphs.list_axioms(graph=result.id)


def test_turtle_is_accepted_as_a_body(authed) -> None:
    turtle = (
        "@prefix cc: <http://creativecommons.org/ns#> .\n"
        "@prefix odrl: <http://www.w3.org/ns/odrl/2/> .\n"
        "cc:Attribution odrl:implies cc:Notice .\n"
    )
    result = authed.dependency_graphs.create(turtle=turtle, title="Sent as Turtle")
    assert result.axioms == 1


def test_naming_both_or_neither_is_refused_before_the_request(authed) -> None:
    with pytest.raises(ValueError, match="either axioms or turtle"):
        authed.dependency_graphs.create()
    with pytest.raises(ValueError):
        authed.dependency_graphs.create(axioms=[], turtle="x")


def test_an_axiom_that_is_not_a_triple_is_refused_before_the_request(authed) -> None:
    with pytest.raises(ValueError, match="subject, a relation and an object"):
        authed.dependency_graphs.create(axioms=[("cc:Attribution", "odrl:implies")])
    with pytest.raises(ValueError, match="missing"):
        authed.dependency_graphs.create(axioms=[{"subject": "cc:Attribution"}])


def test_an_action_may_not_relate_to_itself(authed) -> None:
    with pytest.raises(ValidationError):
        authed.dependency_graphs.create(
            axioms=[("cc:Attribution", "odrl:implies", "cc:Attribution")], title="Loop"
        )


# ---------------------------------------------------------------------------
# the model history of a graph
# ---------------------------------------------------------------------------


def test_the_default_graph_has_a_version_list(api) -> None:
    listing = api.dependency_graphs.versions()
    assert listing.current >= 1
    assert listing.versions


def test_one_archived_version_is_turtle(api) -> None:
    listing = api.dependency_graphs.versions()
    oldest = min(entry.version for entry in listing)
    document = api.dependency_graphs.version(oldest)
    assert "odrl" in document
    assert document.media_type == "text/turtle"


def test_a_version_that_never_existed_is_a_not_found(api) -> None:
    with pytest.raises(NotFoundError):
        api.dependency_graphs.version(99)


def test_the_change_log_of_the_default_graph_is_readable(api) -> None:
    log = api.dependency_graphs.changelog()
    assert "entries" in log
