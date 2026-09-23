# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2021-2026 DALICC - Verein zur Foerderung der Rechtssicherheit in der Datenbewirtschaftung (ZVR 1249185710)
"""``api.licenses``, against the application in this process."""

from __future__ import annotations

import pytest

from dalicc import JsonDocument, NotFoundError, TextDocument, ValidationError

LIB = "https://dalicc.net/licenselibrary/"


# ---------------------------------------------------------------------------
# listing and searching
# ---------------------------------------------------------------------------


def test_the_listing_is_read_out_of_the_frozen_envelope(api) -> None:
    listing = api.licenses.list()
    assert "MIT" in listing.ids
    assert all(ref.uri.startswith(LIB) for ref in listing)
    assert listing.envelope["head"]["vars"] == ["id", "title"]
    mit = next(ref for ref in listing if ref.id == "MIT")
    assert mit.title


def test_a_keyword_narrows_the_listing(api) -> None:
    listing = api.licenses.list(keyword="apache")
    assert listing.licenses
    assert listing[0].id.startswith("Apache")


def test_skip_and_limit_are_only_sent_when_asked_for(api) -> None:
    everything = api.licenses.list()
    paged = api.licenses.list(limit=2)
    assert len(paged) == 2
    assert len(everything) > 2
    assert api.licenses.list(skip=1, limit=2).ids == everything.ids[1:3]


def test_the_ports_parameter_partitions_the_library(api) -> None:
    everything = set(api.licenses.list(ports="include").ids)
    parents = set(api.licenses.list(ports="exclude").ids)
    ports = set(api.licenses.list(ports="only").ids)
    assert parents | ports == everything
    assert not parents & ports


def test_an_unknown_ports_value_is_refused_by_the_service(api) -> None:
    with pytest.raises(ValidationError):
        api.licenses.list(ports="sideways")


def test_paging_walks_the_same_rows(api) -> None:
    walked = [ref.id for ref in api.licenses.iter_list(page_size=3, max_items=5)]
    assert walked == api.licenses.list().ids[:5]


def test_the_faceted_search_fills_the_groups_the_service_wants(api) -> None:
    listing = api.licenses.faceted_search(
        actions={"commercial_use": "permitted"},
        duties={"distribute_duty_attribution": "required"},
    )
    assert listing.envelope["results"]["bindings"] is not None
    assert all(ref.uri.startswith("https://") for ref in listing)


def test_the_faceted_search_pages_with_a_string_limit(api) -> None:
    assert len(api.licenses.faceted_search(limit=1)) <= 1


def test_an_unknown_facet_never_reaches_the_service(api) -> None:
    with pytest.raises(ValueError, match="Unknown duties facet"):
        api.licenses.faceted_search(duties={"pay_me": "required"})


# ---------------------------------------------------------------------------
# one document
# ---------------------------------------------------------------------------


def test_json_ld_comes_back_as_an_object(api) -> None:
    document = api.licenses.get("MIT")
    assert isinstance(document, JsonDocument)
    assert "@context" in document
    assert document["@graph"]


def test_turtle_comes_back_negotiated_as_real_turtle(api) -> None:
    document = api.licenses.get("MIT", format="ttl")
    assert isinstance(document, TextDocument)
    assert document.startswith("@prefix")
    assert document.media_type == "text/turtle"


def test_the_historical_json_string_gives_the_same_text(api) -> None:
    negotiated = api.licenses.get("MIT", format="ttl")
    quirk = api.licenses.get("MIT", format="ttl", raw=True)
    assert quirk.media_type == "application/json"
    assert str(quirk) == str(negotiated)


def test_rdf_xml_is_negotiated_too(api) -> None:
    document = api.licenses.get("MIT", format="rdf-xml")
    assert document.media_type == "application/rdf+xml"
    assert "<rdf:RDF" in document


def test_a_full_iri_addresses_the_same_document(api) -> None:
    assert api.licenses.get(LIB + "MIT") == api.licenses.get("MIT")


def test_an_unknown_id_is_a_not_found(api) -> None:
    with pytest.raises(NotFoundError) as raised:
        api.licenses.get("NOPE-123")
    assert raised.value.status_code == 404


def test_a_malformed_id_is_refused(api) -> None:
    with pytest.raises(ValidationError):
        api.licenses.get("not a license id!")


def test_an_unknown_format_never_leaves_the_client(api) -> None:
    with pytest.raises(ValueError, match="Unknown format"):
        api.licenses.get("MIT", format="n-triples")


# ---------------------------------------------------------------------------
# comparison and the vocabulary
# ---------------------------------------------------------------------------


def test_two_licenses_are_compared_side_by_side(api) -> None:
    matrix = api.licenses.compare(["MIT", LIB + "Apache-2.0"])
    assert matrix["ids"] == ["MIT", "Apache-2.0"]
    assert matrix["rows"]
    assert matrix["groups"]


def test_fewer_than_two_ids_is_refused(api) -> None:
    with pytest.raises(ValidationError):
        api.licenses.compare(["MIT"])


def test_the_action_vocabulary_is_readable(api) -> None:
    vocabulary = api.licenses.actions()
    assert vocabulary["actions"]
    assert {"iri", "curie", "label"} <= set(vocabulary["actions"][0])
    assert vocabulary["asset_types"]
    duties = api.licenses.actions(duties_only=True)
    assert len(duties["actions"]) < len(vocabulary["actions"])


# ---------------------------------------------------------------------------
# SPDX
# ---------------------------------------------------------------------------


def test_an_spdx_id_resolves_to_a_record(api) -> None:
    match = api.licenses.spdx("mit")
    assert match["id"] == "MIT"
    assert match["uri"] == LIB + "MIT"


def test_an_unknown_spdx_id_is_a_not_found(api) -> None:
    with pytest.raises(NotFoundError):
        api.licenses.spdx("Not-A-License-1.0")


def test_the_whole_spdx_mapping_has_both_directions(api) -> None:
    mapping = api.licenses.spdx_mapping()
    assert mapping["dalicc_to_spdx"]["MIT"] == "MIT"
    assert "MIT" in mapping["spdx_to_dalicc"]["MIT"]


# ---------------------------------------------------------------------------
# the review record and the model history
# ---------------------------------------------------------------------------


def test_the_review_record_of_a_license_is_readable(api) -> None:
    review = api.licenses.review("MIT")
    assert review["id"] == "MIT"
    assert review["verdict"]
    assert isinstance(review["findings"], list)


def test_the_version_list_is_newest_first(api) -> None:
    listing = api.licenses.versions("MIT")
    assert listing.id == "MIT"
    assert listing.current >= 1
    assert [entry.version for entry in listing] == sorted(
        (entry.version for entry in listing), reverse=True
    )
    assert listing.versions[0].current is True


def test_one_version_is_served_in_the_formats_of_the_license_endpoint(api) -> None:
    current = api.licenses.versions("MIT").current
    document = api.licenses.version("MIT", current)
    assert "@graph" in document
    turtle = api.licenses.version("MIT", current, format="ttl")
    assert turtle.media_type == "text/turtle"


def test_a_version_that_never_existed_is_a_not_found(api) -> None:
    with pytest.raises(NotFoundError):
        api.licenses.version("MIT", 99)


def test_the_change_log_says_what_changed_and_why(api) -> None:
    listing = api.licenses.versions("MIT")
    if listing.current < 2:
        pytest.skip("this fixture record has never been corrected")
    log = api.licenses.changelog("MIT")
    assert log["id"] == "MIT"
    assert log["entries"][0]["changes"]


def test_the_history_index_pages(api) -> None:
    page = api.licenses.history(limit=2)
    assert page["limit"] == 2
    assert len(page["records"]) <= 2
    walked = list(api.licenses.iter_history(page_size=2, max_items=3))
    assert len(walked) <= 3
    if walked:
        assert {"id", "version"} <= set(walked[0])


# ---------------------------------------------------------------------------
# the two website endpoints the SDK wraps
# ---------------------------------------------------------------------------


def test_the_annotator_sidecar_names_the_license(api) -> None:
    sidecar = api.licenses.annotator_sidecar("MIT", asset="https://example.org/data.csv")
    assert sidecar["license-uri"] == LIB + "MIT"
    assert sidecar["asset"] == "https://example.org/data.csv"


def test_the_badge_is_an_svg(api) -> None:
    badge = api.licenses.badge_svg("MIT")
    assert badge.startswith("<svg")
    assert badge.media_type == "image/svg+xml"
