# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2021-2026 DALICC - Verein zur Foerderung der Rechtssicherheit in der Datenbewirtschaftung (ZVR 1249185710)
"""The DALICC client: one object, seven groups of methods.

    from dalicc import Client

    with Client() as api:
        for license in api.licenses.list(keyword="apache"):
            print(license.id, license.title)

The groups follow the API rather than the SDK's taste, so that a method and the
endpoint it calls can always be read side by side with ``docs/API.md``:

``api.licenses``            the license library: list, search, fetch, compare, history
``api.dependency_graphs``   the reasoning axioms and the graphs somebody owns
``api.reasoning``           the compatibility check and the consistency check
``api.composing``           composing, listing, versioning and deprecating licenses
``api.translate``           the translation assistant
``api.account``             what the account has left of its API allowance
``api.github``              the GitHub dependency checker

Three things are worth knowing before reading on.

**Identifiers.** The license library is addressed by bare identifier (``MIT``) and the
compatibility check by full IRI (``https://dalicc.net/licenselibrary/MIT``). That
asymmetry is part of the frozen contract, so every method here accepts either
spelling and converts it to the one its endpoint wants.

**Envelopes.** ``list`` and ``facetedsearch`` answer with Virtuoso's SPARQL-JSON
envelope, and the compatibility check answers with objects whose keys are stringified
integers. The client reads both into ordinary lists and keeps the original in
``.envelope`` and ``.raw``, so nothing is lost and nothing has to be parsed twice.

**Tokens.** Anything that writes, and anything that belongs to an account, needs a
personal API token from ``/account/tokens``, sent as ``api_key``. Without one those
methods raise :class:`dalicc.AuthError`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator, Mapping, Sequence
import functools
import os
import time
from typing import Any

import httpx

from dalicc import errors
from dalicc._transport import Reply, build_transport, user_agent
from dalicc.models import (
    DEFAULT_LICENSE_BASE_URI,
    AxiomList,
    CompatibilityResult,
    ComposeResult,
    ConsistencyResult,
    GraphList,
    GraphResult,
    JsonDocument,
    LicenseListing,
    LicenseRef,
    MineListing,
    Narration,
    RateLimitStatus,
    RevisionListing,
    TextDocument,
    TranslationResult,
    VersionListing,
    faceted_search_body,
    license_id,
    license_uri,
)
from dalicc.pagination import paginate

__all__ = ["AsyncClient", "Client"]

#: Where the public deployment answers. ``DALICC_BASE_URL`` overrides it.
DEFAULT_BASE_URL = "https://api.dalicc.net"

#: Media type per serialisation, for the ``Accept`` negotiation of the license endpoint.
_ACCEPT = {
    "json-ld": "application/ld+json",
    "ttl": "text/turtle",
    "rdf-xml": "application/rdf+xml",
    # --- License-to-Text ---------------------------------------------------
    # Not a serialisation of the graph but the licence written out as a text.  The
    # service answers text/plain for it, negotiated or not, so nothing is negotiated.
    "text": "text/plain",
    # --- end of the License-to-Text format ---------------------------------
}

#: Turtle, as sent on a request body.
_TURTLE = "text/turtle"


def _clean(params: Mapping[str, Any]) -> dict[str, Any]:
    """Drop the parameters the caller did not name.

    This matters more than it looks: ``GET /licenselibrary/list`` applies ``skip`` and
    ``limit`` **only when they are actually in the query string**, so sending
    ``skip=0`` would change the answer of an operation whose answer is frozen.
    """
    return {key: value for key, value in params.items() if value is not None}


def _bool(value: bool) -> str:
    """A query parameter the way FastAPI reads it."""
    return "true" if value else "false"


class Client:
    """A connection to one DALICC deployment.

    ``base_url`` defaults to ``DALICC_BASE_URL`` from the environment and then to the
    public service. ``api_key`` defaults to ``DALICC_API_KEY``; pass ``""`` to stay
    anonymous even when the variable is set.

    ``timeout`` is in seconds and covers the whole request. ``retries`` is how often a
    connection that never got established is tried again; a request that reached the
    service is never repeated on its own, because a ``POST`` that timed out may well
    have been carried out.

    With ``retry_on_rate_limit=True`` a ``429`` whose ``Retry-After`` is at most
    ``max_wait`` seconds is waited out and the call is repeated, at most ``retries``
    times; without it every ``429`` raises :class:`dalicc.RateLimitedError`, which
    carries the countdown.

    ``transport`` replaces the HTTP transport, which is how the test suite runs the
    client against the application in the same process (see
    :class:`dalicc._transport.ASGITransport`).

    The client holds a connection pool, so use it as a context manager or call
    :meth:`close` when you are done.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
        retries: int = 2,
        user_agent: str | None = None,
        transport: httpx.BaseTransport | None = None,
        *,
        retry_on_rate_limit: bool = False,
        max_wait: float = 60.0,
        license_base_uri: str = DEFAULT_LICENSE_BASE_URI,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Build the client and its connection pool."""
        from dalicc import __version__

        self.base_url = (base_url or os.environ.get("DALICC_BASE_URL") or DEFAULT_BASE_URL).rstrip(
            "/"
        )
        self.api_key = os.environ.get("DALICC_API_KEY", "") if api_key is None else api_key
        self.license_base_uri = license_base_uri
        self.retry_on_rate_limit = retry_on_rate_limit
        self.max_wait = float(max_wait)
        self.retries = max(0, int(retries))
        #: The ``X-RateLimit-*`` headers of the last answer that carried them.
        self.last_rate_limit = RateLimitStatus()

        request_headers = {
            "Accept": "application/json",
            "User-Agent": user_agent or _default_user_agent(__version__),
        }
        request_headers.update({str(k): str(v) for k, v in dict(headers or {}).items()})
        if self.api_key:
            request_headers["Authorization"] = f"Bearer {self.api_key}"

        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers=request_headers,
            transport=transport or build_transport(self.retries),
            follow_redirects=True,
        )
        self._sleep: Callable[[float], None] = time.sleep

        self.licenses = Licenses(self)
        self.dependency_graphs = DependencyGraphs(self)
        self.reasoning = Reasoning(self)
        self.composing = Composing(self)
        self.translate = Translate(self)
        self.account = Account(self)
        self.github = GitHub(self)

    # -- plumbing ----------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        content: bytes | str | None = None,
        headers: Mapping[str, str] | None = None,
        accept: str | None = None,
    ) -> Reply:
        """Call one endpoint and return the finished answer.

        The escape hatch: every method of this client goes through it, and an
        endpoint the SDK does not wrap yet can be called with it directly. A non-2xx
        answer raises the matching :mod:`dalicc.errors` exception.
        """
        call_headers = dict(headers or {})
        if accept:
            call_headers["Accept"] = accept
        attempts = 0
        while True:
            reply = self._send(method, path, params, json, content, call_headers)
            if reply.ok:
                return reply
            failure = errors.error_for(
                reply.status_code,
                url=reply.url,
                raw=reply.payload,
                headers=reply.headers,
                text=reply.text,
            )
            if not self._should_wait(failure, attempts):
                raise failure
            attempts += 1
            self._sleep(_wait_for(failure))

    def _send(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None,
        json: Any,
        content: bytes | str | None,
        headers: Mapping[str, str],
    ) -> Reply:
        """One round trip, with transport failures turned into a DALICC error."""
        try:
            response = self._http.request(
                method,
                path,
                params=dict(params or {}) or None,
                json=json,
                content=content,
                headers=dict(headers) or None,
            )
        except httpx.TransportError as exc:
            raise errors.TransportError(
                f"The DALICC API at {self.base_url} could not be reached: {exc}",
                url=self.base_url + path,
            ) from exc
        reply = Reply.of(response)
        status = RateLimitStatus.parse(reply.headers)
        if status.known:
            self.last_rate_limit = status
        return reply

    def _should_wait(self, failure: errors.DaliccError, attempts: int) -> bool:
        """Whether this refusal is a rate limit the client agreed to wait out."""
        if not isinstance(failure, errors.RateLimitedError):
            return False
        if not self.retry_on_rate_limit or attempts >= self.retries:
            return False
        return _wait_for(failure) <= self.max_wait

    def json(self, path: str, **params: Any) -> JsonDocument:
        """``GET`` one endpoint and return its JSON object."""
        reply = self.request("GET", path, params=_clean(params))
        return JsonDocument.build(reply.payload, reply.headers)

    def healthz(self) -> JsonDocument:
        """Whether the triple store and the reasoner answer: ``GET /healthz``.

        A degraded service answers ``503``, which is a :class:`dalicc.ServerError`
        carrying the same document in ``raw``.
        """
        return self.json("/healthz")

    def close(self) -> None:
        """Close the connection pool."""
        self._http.close()

    def __enter__(self) -> Client:
        """Use the client as a context manager."""
        return self

    def __exit__(self, *exc_info: Any) -> None:
        """Close the connection pool on the way out."""
        self.close()

    def __repr__(self) -> str:
        """Name the deployment and whether a token is in play, never the token."""
        return f"<dalicc.Client base_url={self.base_url!r} authenticated={bool(self.api_key)}>"

    # -- helpers shared by the namespaces ----------------------------------

    def _id(self, reference: Any) -> str:
        """The bare identifier of a license reference."""
        return license_id(reference, self.license_base_uri)

    def _uri(self, reference: Any) -> str:
        """The full IRI of a license reference."""
        return license_uri(reference, self.license_base_uri)


def _default_user_agent(version: str) -> str:
    """The ``User-Agent`` the client sends when the caller names none."""
    return user_agent(version)


def _wait_for(failure: errors.DaliccError) -> float:
    """How long to wait before repeating a refused request."""
    seconds = getattr(failure, "retry_after", 0.0) or 0.0
    return float(seconds) if seconds > 0 else 1.0


class _Namespace:
    """Base of the method groups; holds the client they call through."""

    def __init__(self, client: Client) -> None:
        """Remember the client."""
        self._client = client

    def __repr__(self) -> str:
        """Name the group."""
        return f"<dalicc.{type(self).__name__}>"


# ---------------------------------------------------------------------------
# the license library
# ---------------------------------------------------------------------------


class Licenses(_Namespace):
    """The license library: listing, searching, fetching and its model history."""

    def list(
        self,
        keyword: str | None = None,
        skip: int | None = None,
        limit: int | None = None,
        ports: str = "include",
    ) -> LicenseListing:
        """List the library: ``GET /licenselibrary/list``.

        ``keyword`` matches a substring of the title or of an alternative title, and
        sorts by how well it matched; without it the rows are sorted by title.

        ``skip`` and ``limit`` are applied **only when you pass them**. Leave them out
        and the service answers with every row, which is what it has always done and
        what existing clients depend on.

        ``ports`` decides what happens to the jurisdiction ports, the records that are
        the same license adapted to another legal system: ``include`` (the default,
        every record), ``exclude`` (only records that are not a port of another) or
        ``only`` (only the ports). The two test-fixture records are never listed.

        The answer keeps Virtuoso's SPARQL-JSON envelope in ``.envelope``; iterate over
        the result for :class:`dalicc.LicenseRef` rows.
        """
        reply = self._client.request(
            "GET",
            "/licenselibrary/list",
            params=_clean({"keyword": keyword, "skip": skip, "limit": limit, "ports": ports}),
        )
        return LicenseListing.parse(reply.payload, reply.headers)

    def iter_list(
        self,
        keyword: str | None = None,
        *,
        page_size: int = 100,
        max_items: int | None = None,
        ports: str = "include",
    ) -> Iterator[LicenseRef]:
        """Walk the library page by page instead of asking for all of it at once.

        The same rows :meth:`list` returns, fetched ``page_size`` at a time. Useful on
        a slow connection; it cannot return more rows than one unpaged call would.
        """
        def fetch(skip: int, limit: int) -> list[LicenseRef]:
            return self.list(keyword=keyword, skip=skip, limit=limit, ports=ports).licenses

        return paginate(fetch, limit=page_size, max_items=max_items)

    def get(
        self,
        license_reference: Any,
        format: str = "json-ld",
        raw: bool = False,
    ) -> JsonDocument | TextDocument:
        """One license document: ``GET /licenselibrary/license/{id}``.

        ``format`` is ``json-ld`` (a :class:`dalicc.JsonDocument`), ``ttl`` or
        ``rdf-xml`` (a :class:`dalicc.TextDocument`, which is a string).

        ``format="text"`` is not a serialisation: it is the licence written out as a
        plain English text, the deterministic reading of the model that
        :meth:`Translate.narrate` falls back to. It asks no provider and spends no
        allowance.

        A historical quirk sits behind ``raw``. Asking for ``?format=ttl`` answers
        with the Turtle **JSON-encoded into a string** and ``Content-Type:
        application/json``, and that is frozen. The client therefore negotiates on
        ``Accept`` instead, which gets the real serialisation with the real media
        type; ``raw=True`` takes the historical path. The text is the same either way.

        ``404`` for an identifier nothing resolves to, ``422`` for a malformed one,
        ``503`` when the record is not on disk and the store cannot be asked.
        """
        if format not in _ACCEPT:
            raise ValueError(
                f"Unknown format: {format}. Use json-ld, ttl, rdf-xml or text."
            )
        identifier = self._client._id(license_reference)
        accept = None if (raw or format in ("json-ld", "text")) else _ACCEPT[format]
        reply = self._client.request(
            "GET",
            f"/licenselibrary/license/{identifier}",
            params={"format": format},
            accept=accept,
        )
        return _document(reply, format)

    def faceted_search(
        self,
        target: Mapping[str, str] | None = None,
        actions: Mapping[str, str] | None = None,
        duties: Mapping[str, str] | None = None,
        license_wide_duties: Mapping[str, str] | None = None,
        skip: int | None = None,
        limit: int | None = None,
        ports: str = "include",
    ) -> LicenseListing:
        """Search by what a license allows, forbids and demands.

        ``POST /licenselibrary/facetedsearch``. Name only the facets you care about;
        the rest are filled with the neutral value (``yes`` for an asset type, ``na``
        for an action or a duty), which is what the service expects::

            api.licenses.faceted_search(
                actions={"commercial_use": "permitted", "modify": "permitted"},
                duties={"distribute_duty_attribution": "required"},
            )

        ``target`` values are ``yes`` or ``no``, ``actions`` are ``permitted``, ``na``
        or ``prohibited``, duties are ``required`` or ``na``. An unknown facet name
        raises ``ValueError`` before the request goes out.

        ``limit`` is a string on this endpoint and an integer on ``/list``; the
        asymmetry is in the published schema, so the client sends what the schema
        says and takes an integer from you either way.
        """
        body = faceted_search_body(target, actions, duties, license_wide_duties)
        params = _clean(
            {
                "skip": skip,
                "limit": None if limit is None else str(limit),
                "ports": ports,
            }
        )
        reply = self._client.request(
            "POST", "/licenselibrary/facetedsearch", params=params, json=body
        )
        return LicenseListing.parse(reply.payload, reply.headers)

    def compare(self, license_references: Sequence[Any]) -> JsonDocument:
        """Compare 2 to 20 licenses side by side: ``GET /licenselibrary/compare``.

        Returns the vectors, the flat ``rows`` matrix and the ``groups`` of the
        License Comparator, each row with a ``differs`` flag. Fewer than two or more
        than twenty identifiers is a ``422``.
        """
        ids = [self._client._id(reference) for reference in license_references]
        reply = self._client.request(
            "GET", "/licenselibrary/compare", params={"ids": ",".join(ids)}
        )
        return JsonDocument.build(reply.payload, reply.headers)

    def actions(
        self, duties_only: bool = False, include_reasoning_terms: bool = False
    ) -> JsonDocument:
        """The controlled action vocabulary: ``GET /licenselibrary/actions``.

        Every term the composer accepts, with its IRI, its compact spelling, a label,
        a description and the group it belongs to, plus the asset types.
        ``duties_only`` restricts it to the terms usable as a duty;
        ``include_reasoning_terms`` adds the ones that occur only in the dependency
        graph.  The two combine: both together give every term that may act as a duty,
        offered by the composer or not.
        """
        reply = self._client.request(
            "GET",
            "/licenselibrary/actions",
            params={
                "duties_only": _bool(duties_only),
                "include_reasoning_terms": _bool(include_reasoning_terms),
            },
        )
        return JsonDocument.build(reply.payload, reply.headers)

    def spdx(self, spdx_id: str) -> JsonDocument:
        """Resolve an SPDX identifier: ``GET /licenselibrary/spdx/{spdx_id}``.

        Case-insensitive; ``404`` when no record declares it. Most jurisdiction ports
        carry no SPDX identifier, and that absence is deliberate.
        """
        reply = self._client.request("GET", f"/licenselibrary/spdx/{spdx_id}")
        return JsonDocument.build(reply.payload, reply.headers)

    def spdx_mapping(self) -> JsonDocument:
        """The whole SPDX mapping, both directions: ``GET /licenselibrary/spdx-mapping``.

        ``dalicc_to_spdx`` maps an identifier onto the SPDX identifier the record
        declares; ``spdx_to_dalicc`` is the reverse index and is list-valued, because
        one SPDX identifier could describe more than one record.
        """
        reply = self._client.request("GET", "/licenselibrary/spdx-mapping")
        return JsonDocument.build(reply.payload, reply.headers)

    def review(self, license_reference: Any) -> JsonDocument:
        """The content-review record of one license: ``GET /licenselibrary/review/{id}``.

        The verdict, the summary and the ten-point findings of the content review,
        with the severity of each and whether it was applied to the record or only
        proposed. ``404`` when a record has no review file. Nothing in it is legal
        advice.
        """
        identifier = self._client._id(license_reference)
        reply = self._client.request("GET", f"/licenselibrary/review/{identifier}")
        return JsonDocument.build(reply.payload, reply.headers)

    def versions(self, license_reference: Any) -> VersionListing:
        """The versions of one curated model, newest first.

        ``GET /licenselibrary/license/{id}/versions``. A record that has never been
        corrected lists one version.
        """
        identifier = self._client._id(license_reference)
        reply = self._client.request("GET", f"/licenselibrary/license/{identifier}/versions")
        return VersionListing.parse(reply.payload, reply.headers)

    def version(
        self,
        license_reference: Any,
        version: int,
        format: str = "json-ld",
        raw: bool = False,
    ) -> JsonDocument | TextDocument:
        """One version of a curated model, in the formats of :meth:`get`.

        ``GET /licenselibrary/license/{id}/versions/{n}``. Asking for the current
        version serves the live record, so the two answers are identical. ``404`` for
        a version that was never published.
        """
        if format not in _ACCEPT:
            raise ValueError(f"Unknown format: {format}. Use json-ld, ttl or rdf-xml.")
        identifier = self._client._id(license_reference)
        accept = None if (raw or format == "json-ld") else _ACCEPT[format]
        reply = self._client.request(
            "GET",
            f"/licenselibrary/license/{identifier}/versions/{int(version)}",
            params={"format": format},
            accept=accept,
        )
        return _document(reply, format)

    def changelog(self, license_reference: Any) -> JsonDocument:
        """The change log of one curated model: what changed, why, and where the reason comes from.

        ``GET /licenselibrary/license/{id}/changelog``. ``404`` for a record that has
        never been corrected.
        """
        identifier = self._client._id(license_reference)
        reply = self._client.request("GET", f"/licenselibrary/license/{identifier}/changelog")
        return JsonDocument.build(reply.payload, reply.headers)

    def history(self, skip: int = 0, limit: int = 50) -> JsonDocument:
        """Every record that has a history, most recently changed first.

        ``GET /licenselibrary/history``. ``limit`` defaults to 50 and is capped at 500
        by the service. ``total`` counts the records that **have** a history, not the
        size of the library.
        """
        reply = self._client.request(
            "GET", "/licenselibrary/history", params={"skip": skip, "limit": limit}
        )
        return JsonDocument.build(reply.payload, reply.headers)

    def iter_history(
        self, *, page_size: int = 50, max_items: int | None = None
    ) -> Iterator[dict[str, Any]]:
        """Walk :meth:`history` page by page and yield one record at a time."""
        def fetch(skip: int, limit: int) -> list[dict[str, Any]]:
            page = self.history(skip=skip, limit=limit)
            return [row for row in page.get("records", []) if isinstance(row, dict)]

        return paginate(fetch, limit=page_size, max_items=max_items)

    def annotator_sidecar(
        self,
        license_reference: Any,
        asset: str | None = None,
        attribution: str | None = None,
    ) -> JsonDocument:
        """The License Annotator sidecar: ``GET /license-library/{id}/license.json``.

        A small JSON file to ship next to a dataset or a repository: the license IRI,
        its title, the SPDX identifier, the asset it applies to and, when the license
        carries an attribution duty, the attribution line. An ``asset`` that is not an
        absolute ``http(s)`` URL is ignored by the service.

        Note the path: this one is on the website, at ``/license-library/``, not on
        the API prefix ``/licenselibrary/``.
        """
        identifier = self._client._id(license_reference)
        reply = self._client.request(
            "GET",
            f"/license-library/{identifier}/license.json",
            params=_clean({"asset": asset, "attribution": attribution}),
        )
        return JsonDocument.build(reply.payload, reply.headers)

    def badge_svg(self, license_reference: Any) -> TextDocument:
        """The README badge of one license: ``GET /license-library/{id}/badge.svg``.

        Returns the SVG as text, with ``image/svg+xml`` in ``.media_type``.
        """
        identifier = self._client._id(license_reference)
        reply = self._client.request(
            "GET", f"/license-library/{identifier}/badge.svg", accept="image/svg+xml"
        )
        return TextDocument.build(reply.text, reply.headers)


def _document(reply: Reply, format: str) -> JsonDocument | TextDocument:
    """A license serialisation as the right kind of object.

    ``json-ld`` gives an object; the two RDF formats give text, whether they arrived
    negotiated (the real media type) or through the historical JSON-encoded string.
    """
    if format == "json-ld":
        return JsonDocument.build(reply.payload, reply.headers)
    if isinstance(reply.payload, str):
        return TextDocument.build(reply.payload, reply.headers)
    return TextDocument.build(reply.text, reply.headers)


# ---------------------------------------------------------------------------
# dependency graphs
# ---------------------------------------------------------------------------


class DependencyGraphs(_Namespace):
    """The deontic dependency graphs the compatibility check reasons with."""

    def list_axioms(self, graph: str | None = None) -> AxiomList:
        """The statements of one dependency graph: ``GET /dependencygraph/list``.

        Without ``graph`` this is the graph the deployment is configured with, which
        is ``dg_default`` on the public service, and the answer is the one this
        operation has always given. With it, the same dump of another graph, named by
        identifier or by IRI: a published core graph is readable by anybody, a graph
        belonging to an account needs that account's token.
        """
        reply = self._client.request(
            "GET", "/dependencygraph/list", params=_clean({"graph": graph})
        )
        return AxiomList.parse(reply.payload, reply.headers)

    def graphs(self) -> GraphList:
        """The dependency graphs the caller may see: ``GET /dependencygraph/graphs``.

        The published core graphs for everybody, plus the caller's own and the ones
        shared with them when a token is sent. A graph somebody composed is never
        listed to anybody else.
        """
        reply = self._client.request("GET", "/dependencygraph/graphs")
        return GraphList.parse(reply.payload, reply.headers)

    def mine(self) -> GraphList:
        """Every graph of the account behind the token: ``GET /dependencygraph/mine``.

        Drafts included, with the per-graph role. ``401`` without a token.
        """
        reply = self._client.request("GET", "/dependencygraph/mine")
        return GraphList.parse(reply.payload, reply.headers)

    def create(
        self,
        axioms: Sequence[Any] | None = None,
        turtle: str | None = None,
        publish: bool = False,
        title: str = "",
        description: str = "",
    ) -> GraphResult:
        """Create a dependency graph: ``POST /dependencygraph`` (token only).

        Name the statements either as ``axioms`` or as ``turtle``, not both. An axiom
        is a mapping ``{"subject", "relation", "object"}`` or a three-item sequence in
        that order; IRIs and compact spellings (``cc:Attribution``) both work::

            api.dependency_graphs.create(
                axioms=[("cc:Attribution", "odrl:implies", "cc:Notice")],
                title="Attribution implies a notice",
            )

        Both sides have to be actions of the DALICC vocabulary, the relation one of
        ``odrl:includedIn``, ``odrl:implies``, ``owl:sameAs`` or
        ``dalicc:contradicts``; an action may not relate to itself and no statement
        may be repeated. A cycle is reported in ``warnings`` rather than refused.

        ``publish=False`` (the default) keeps the graph a private draft. A published
        graph is unlisted: the identifier is the only way back to it.
        """
        if (axioms is None) == (turtle is None):
            raise ValueError("Name either axioms or turtle, not both and not neither.")
        params = _clean({"publish": _bool(publish), "title": title or None})
        if turtle is not None:
            reply = self._client.request(
                "POST",
                "/dependencygraph",
                params=params,
                content=turtle,
                headers={"Content-Type": _TURTLE},
            )
        else:
            body = {
                "title": title,
                "description": description,
                "axioms": [_axiom(entry) for entry in axioms or ()],
            }
            reply = self._client.request("POST", "/dependencygraph", params=params, json=body)
        return GraphResult.parse(reply.payload, reply.headers)

    def versions(self, graph: str | None = None) -> VersionListing:
        """The versions of a dependency graph, newest first.

        ``GET /dependencygraph/{id}/versions``, or ``GET /dependencygraph/versions``
        for the graph the deployment reasons with when ``graph`` is left out.
        """
        reply = self._client.request("GET", _graph_path(graph, "versions"))
        return VersionListing.parse(reply.payload, reply.headers)

    def version(self, version: int, graph: str | None = None) -> TextDocument:
        """One archived version of a dependency graph, as Turtle.

        A graph is a Turtle document, so there is nothing to negotiate here. ``404``
        for a version that never existed, ``403`` for a graph the caller may not see.
        """
        reply = self._client.request(
            "GET", _graph_path(graph, f"versions/{int(version)}"), accept=_TURTLE
        )
        return TextDocument.build(reply.text, reply.headers)

    def changelog(self, graph: str | None = None) -> JsonDocument:
        """The change log of a dependency graph, as JSON."""
        reply = self._client.request("GET", _graph_path(graph, "changelog"))
        return JsonDocument.build(reply.payload, reply.headers)


def _graph_path(graph: str | None, tail: str) -> str:
    """The history path of a named graph, or of the deployment's own."""
    if graph:
        return f"/dependencygraph/{graph}/{tail}"
    return f"/dependencygraph/{tail}"


def _axiom(entry: Any) -> dict[str, str]:
    """One axiom as the service takes it, from a mapping or a three-item sequence."""
    if isinstance(entry, Mapping):
        missing = {"subject", "relation", "object"} - set(entry)
        if missing:
            raise ValueError(f"An axiom needs subject, relation and object; missing: {missing}")
        return {key: str(entry[key]) for key in ("subject", "relation", "object")}
    parts = list(entry)
    if len(parts) != 3:
        raise ValueError("An axiom is a subject, a relation and an object.")
    return {"subject": str(parts[0]), "relation": str(parts[1]), "object": str(parts[2])}


# ---------------------------------------------------------------------------
# reasoning
# ---------------------------------------------------------------------------


class Reasoning(_Namespace):
    """The two checks the solver answers: between licenses, and within one."""

    def compatibility(
        self,
        licenses: Sequence[Any],
        dependency_graph: str | None = None,
    ) -> CompatibilityResult:
        """Can these licenses be combined? ``POST /compatibilitycheck/``.

        The licenses are named by **full IRI** on this endpoint; pass bare identifiers
        or :class:`dalicc.LicenseRef` objects and the client converts them.

        ``dependency_graph`` picks the axioms to reason with, by identifier or IRI.
        Leave it out and the deployment's own graph is used, which is what this
        operation has always done. A graph belonging to an account needs that
        account's token.

        The answer has ``direct`` and ``derived`` conflicts, and ``has_conflicts``
        says whether there are any. The raw body keeps the frozen shape, in which the
        two are objects with stringified integer keys.
        """
        body: dict[str, Any] = {"licenses": [self._client._uri(item) for item in licenses]}
        if dependency_graph:
            body["dependency_graph"] = dependency_graph
        reply = self._client.request("POST", "/compatibilitycheck/", json=body)
        return CompatibilityResult.parse(reply.payload, reply.headers)

    def consistency(
        self,
        license: Any = None,
        composer_input: Mapping[str, Any] | None = None,
        dependency_graph: str | None = None,
    ) -> ConsistencyResult:
        """Does one license contradict itself? ``POST /licenselibrary/consistencycheck``.

        Name either an existing ``license`` (an identifier or its IRI) or a
        ``composer_input`` document that has not been published yet, not both.

        ``dependency_graph`` selects the axioms, exactly as on
        :meth:`compatibility`. ``422`` for a URI outside the DALICC library, ``404``
        for an identifier nothing resolves to.
        """
        if (license is None) == (composer_input is None):
            raise ValueError("Name either a license or a composer_input, not both.")
        if composer_input is not None:
            body: Any = dict(composer_input)
        else:
            body = {"license": self._client._id(license)}
        reply = self._client.request(
            "POST",
            "/licenselibrary/consistencycheck",
            params=_clean({"dependency_graph": dependency_graph}),
            json=body,
        )
        return ConsistencyResult.parse(reply.payload, reply.headers)


# ---------------------------------------------------------------------------
# composing
# ---------------------------------------------------------------------------


class Composing(_Namespace):
    """Composing licenses, and everything that happens to one afterwards."""

    def create(
        self,
        document: Mapping[str, Any] | str,
        publish: bool = True,
    ) -> ComposeResult:
        """Compose a license and publish it: ``POST /licenselibrary/composer``.

        ``document`` is either the composer document as a mapping or one ``odrl:Set``
        as a Turtle string. A token is required, and the license belongs to the
        account behind it.

        ``publish=True`` (the default) validates, runs the consistency check and
        writes; ``publish=False`` stores a private draft you can finish at
        ``/license-composer?draft=<id>`` on the website.

        A composed license is **unlisted**: it appears in no list, no search result
        and no suggestion, so keep the identifier that comes back.

        Contradicting terms are a ``409``, which raises
        :class:`dalicc.ConflictError`; its ``conflicts`` names the pairs, and nothing
        was written.
        """
        params = {"publish": _bool(publish)}
        if isinstance(document, str):
            reply = self._client.request(
                "POST",
                "/licenselibrary/composer",
                params=params,
                content=document,
                headers={"Content-Type": _TURTLE},
            )
        else:
            reply = self._client.request(
                "POST", "/licenselibrary/composer", params=params, json=dict(document)
            )
        return ComposeResult.parse(reply.payload, reply.headers)

    def mine(self) -> MineListing:
        """Every license of the account behind the token: ``GET /licenselibrary/mine``.

        Drafts and published versions, owned and shared, with the status, the version
        chain and the role. A license composed anonymously belongs to nobody and never
        appears here. ``401`` without a token.
        """
        reply = self._client.request("GET", "/licenselibrary/mine")
        return MineListing.parse(reply.payload, reply.headers)

    def revisions(self, license_reference: Any) -> RevisionListing:
        """Every saved state of one of your licenses, newest first.

        ``GET /licenselibrary/mine/{id}/revisions``. A draft is rewritten in place on
        every save, so the service keeps a snapshot of each one. The owner, an editor
        and an administrator may read it; a viewer gets ``403``.
        """
        identifier = self._client._id(license_reference)
        reply = self._client.request("GET", f"/licenselibrary/mine/{identifier}/revisions")
        return RevisionListing.parse(reply.payload, reply.headers)

    def deprecate(
        self,
        license_reference: Any,
        reason: str | None = None,
        replaced_by: Any | None = None,
    ) -> ComposeResult:
        """Withdraw one of your published licenses.

        ``POST /licenselibrary/mine/{id}/deprecate``. Nothing is deleted: the document
        keeps resolving at its own address and gains ``owl:deprecated``, the date and,
        when a successor is named, a pointer to it. Only the owner (or an
        administrator) may do this.
        """
        identifier = self._client._id(license_reference)
        params = _clean(
            {
                "reason": reason,
                "replaced_by": None if replaced_by is None else self._client._id(replaced_by),
            }
        )
        reply = self._client.request(
            "POST", f"/licenselibrary/mine/{identifier}/deprecate", params=params
        )
        return ComposeResult.parse(reply.payload, reply.headers)


# ---------------------------------------------------------------------------
# the translation assistant
# ---------------------------------------------------------------------------


class Translate(_Namespace):
    """Reading a licence text and proposing the DALICC model for it."""

    def text(
        self,
        text: str,
        title: str | None = None,
        save_draft: bool = False,
        consent: bool = False,
        wait: bool = True,
        timeout: float = 1800.0,
        poll_interval: float = 5.0,
    ) -> TranslationResult:
        """Propose a model for a licence text: ``POST /licenselibrary/translate``.

        **Consent is required every time.** The text is sent to an external model
        provider for processing, so ``consent=True`` has to be passed on every call,
        and the client refuses with ``ValueError`` before anything leaves the machine
        when it is not. Do not submit confidential texts.

        Nothing is published. The answer carries the proposed ``license`` document in
        the shape the composer takes, every statement with the verbatim ``evidence``
        it was read from and a confidence, the clauses the vocabulary cannot express,
        the warnings, the conflicts the consistency check found, and the quotas: what
        the provider has left, what your account has left today and what the
        deployment has left today.

        With ``save_draft=True`` the proposal is stored as a private draft owned by
        your account and ``draft_id`` names it.

        A long text is read in parts; when a part fails the others still come back and
        ``parts_failed`` says which ones were lost. ``413`` for a text longer than the
        assistant reads, ``429`` when a quota is exhausted (the exception carries the
        countdown), ``503`` when the assistant is not configured on the deployment.

        **A text that needs more than one part** is not answered in the request: the
        service starts the work and answers ``202`` with a ``job_id``, because reading a
        long licence takes minutes and no proxy holds a request open that long. With
        ``wait=True`` (the default) this method then polls
        ``GET /licenselibrary/translate/jobs/{job_id}`` every ``poll_interval`` seconds
        until the run is over and returns the finished proposal, as if the call had been
        synchronous. With ``wait=False`` the answer comes back at once with
        :attr:`TranslationResult.job_id` set and :attr:`TranslationResult.pending` true,
        and :meth:`job` collects it later.

        ``timeout`` bounds the waiting (half an hour by default) and raises
        :class:`TimeoutError` when the run is still going; the run is not cancelled, and
        :meth:`job` can still collect it.
        """
        if consent is not True:
            raise ValueError(
                "The translation assistant sends the text to an external model "
                "provider, so it needs explicit consent: pass consent=True. Do not "
                "submit confidential texts."
            )
        body = {
            "text": text,
            "title": title or "",
            "save_draft": bool(save_draft),
            "consent": True,
        }
        reply = self._client.request("POST", "/licenselibrary/translate", json=body)
        result = TranslationResult.parse(reply.payload, reply.headers, reply.status_code)
        if reply.status_code != 202 or not wait or not result.job_id:
            return result
        return self.wait_for(
            result.job_id,
            save_draft=bool(save_draft),
            timeout=timeout,
            poll_interval=poll_interval,
        )

    def job(self, job_id: str, save_draft: bool = False) -> TranslationResult:
        """Ask once how a translation started earlier is getting on.

        ``GET /licenselibrary/translate/jobs/{job_id}``. While the run is going the
        answer carries the progress and :attr:`TranslationResult.pending` is true; when
        it is over it is the whole proposal. A run that failed comes back with
        ``pending`` false, no statements and the reason in
        :attr:`TranslationResult.message`.
        """
        params = {"save_draft": "true"} if save_draft else None
        reply = self._client.request(
            "GET", f"/licenselibrary/translate/jobs/{job_id}", params=params
        )
        return TranslationResult.parse(reply.payload, reply.headers, reply.status_code)

    def wait_for(
        self,
        job_id: str,
        save_draft: bool = False,
        timeout: float = 1800.0,
        poll_interval: float = 5.0,
    ) -> TranslationResult:
        """Poll a translation until it is over, and return what it produced.

        The interval is taken from the service's own ``Retry-After`` when it sends one,
        and never goes below a second: polling faster spends the account's API
        allowance without making the provider answer any sooner.
        """
        import time

        deadline = time.monotonic() + max(1.0, float(timeout))
        wait = max(1.0, float(poll_interval))
        while True:
            result = self.job(job_id, save_draft=save_draft)
            if not result.pending:
                return result
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"The translation {job_id} was still running after {timeout:.0f}s. "
                    "It has not been cancelled: collect it later with "
                    "client.translate.job(job_id)."
                )
            retry_after = result.headers.get("retry-after", "")
            if retry_after.strip().isdigit():
                wait = max(1.0, float(retry_after.strip()))
            time.sleep(min(wait, max(1.0, deadline - time.monotonic())))

    def file(
        self,
        path: Any,
        title: str | None = None,
        save_draft: bool = False,
        consent: bool = False,
        encoding: str = "utf-8",
        wait: bool = True,
        timeout: float = 1800.0,
    ) -> TranslationResult:
        """Read a licence text from a file and translate it, as :meth:`text` does."""
        from pathlib import Path

        document = Path(path)
        return self.text(
            document.read_text(encoding=encoding),
            title=title if title is not None else document.stem,
            save_draft=save_draft,
            consent=consent,
            wait=wait,
            timeout=timeout,
        )

    # --- License-to-Text ------------------------------------------------
    # The other direction: a model in, a licence text out.  Additive, and on the same
    # namespace because it is the same assistant; the allowance it spends is its own.

    def narrate(self, license_reference: Any, without_names: bool = False) -> Narration:
        """Write the licence text of a library licence: ``POST /licenselibrary/narrate``.

        The answer carries the title, the preamble, the numbered sections with the
        terms each one covers, the closing sentence and the coverage of the model by
        those sections. :attr:`Narration.produced_by` says whether the assistant wrote
        the text or whether it was written from the DALICC vocabulary alone, which is
        what happens when the assistant is not configured, the daily allowance is used
        up or the provider did not answer. There is always a text.

        The model is sent to an external model provider unless the answer says
        ``fallback``. A library record carries no personal data; ``without_names=True``
        replaces the creator, licensor and publisher names by "the licensor" first,
        which is what a composed licence should be read with.

        A narration is counted in an allowance of its own, so it never spends a
        translation and a translation never spends a narration. That allowance is
        ``DALICC_NARRATE_PER_DAY`` where the deployment sets it and the Text-to-License
        figure of the same account where it does not, while the provider's quota and
        the deployment's daily total are shared between the two directions. ``404``
        when the identifier resolves to nothing, ``401`` without a token.

        For a library record without any provider call and without spending the
        allowance, ``client.licenses.get(id, format="text")`` reads the deterministic
        text straight off ``GET /licenselibrary/license/{id}?format=text``.
        """
        body = {
            "license": self._client._id(license_reference),
            "without_names": bool(without_names),
        }
        reply = self._client.request("POST", "/licenselibrary/narrate", json=body)
        return Narration.parse(reply.payload, reply.headers)

    def narrate_model(self, turtle: str, without_names: bool = False) -> Narration:
        """Write the licence text of a model you hold: ``POST /licenselibrary/narrate``.

        ``turtle`` is a licence model as Turtle or as JSON-LD, which is what
        ``client.licenses.get(id, format="ttl")`` and the composer's download give you.
        It is how an unlisted composed licence, or one that never left your machine, is
        read. Everything else is :meth:`narrate`.
        """
        body = {"model": turtle, "without_names": bool(without_names)}
        reply = self._client.request("POST", "/licenselibrary/narrate", json=body)
        return Narration.parse(reply.payload, reply.headers)

    # --- end of the License-to-Text block --------------------------------


# ---------------------------------------------------------------------------
# the account
# ---------------------------------------------------------------------------


class Account(_Namespace):
    """What the account behind the token has left of its API allowance."""

    def limits(self) -> RateLimitStatus:
        """The window closest to refusing the next call.

        The service publishes the per-account limits in the ``X-RateLimit-*`` headers
        of **every** answer to a token request rather than in an endpoint of its own,
        so this reads the headers of the last answer and, when there has not been one
        yet, makes the cheapest authenticated call there is to get them.

        The four windows (second, minute, hour and day) and the usage of each are on
        the ``/account/tokens`` page of the website, behind a session; there is no
        JSON endpoint for them, by design, because a rate-limit answer must never tell
        a caller that a token exists.

        ``401`` without a token. Anonymous requests carry no such headers, and the
        result is then a status with ``known`` set to ``False``.
        """
        if self.limits_known():
            return self._client.last_rate_limit
        self._client.request("GET", "/licenselibrary/mine")
        return self._client.last_rate_limit

    def limits_known(self) -> bool:
        """Whether a rate-limit answer has already been seen on this client."""
        return self._client.last_rate_limit.known

    def licenses(self) -> MineListing:
        """The licenses of this account, the same as ``api.composing.mine()``."""
        return self._client.composing.mine()


# ---------------------------------------------------------------------------
# the GitHub dependency checker
# ---------------------------------------------------------------------------


class GitHub(_Namespace):
    """Reading the licenses of a repository's dependencies."""

    def dependencies(self, owner: str, name: str | None = None) -> JsonDocument:
        """The dependency document of a repository, passed through verbatim.

        ``GET /githublicensechecker/dependencies/{owner}/{name}``, or the ``?github_url=``
        form when ``name`` is left out, which accepts
        ``https://github.com/owner/name``, ``github.com/owner/name`` and the bare
        ``owner/name``. Any other host is a ``400``.

        ``503`` when the deployment has no libraries.io key configured.
        """
        return self._call("dependencies", owner, name)

    def check(self, owner: str, name: str | None = None) -> JsonDocument:
        """Map the dependency licenses onto DALICC and check them for conflicts.

        ``GET /githublicensechecker/check/{owner}/{name}``, or the ``?github_url=``
        form. The answer names every dependency with its license and the DALICC IRI it
        resolved to (or ``null``), and carries the compatibility verdict for the
        resulting set. With fewer than two resolvable licenses the reasoner is not
        called and the verdict is empty.
        """
        return self._call("check", owner, name)

    def _call(self, operation: str, owner: str, name: str | None) -> JsonDocument:
        """Either of the two shapes of the same operation."""
        if name is None:
            reply = self._client.request(
                "GET", f"/githublicensechecker/{operation}", params={"github_url": owner}
            )
        else:
            reply = self._client.request("GET", f"/githublicensechecker/{operation}/{owner}/{name}")
        return JsonDocument.build(reply.payload, reply.headers)


# ---------------------------------------------------------------------------
# the asynchronous mirror
# ---------------------------------------------------------------------------


class _AsyncNamespace:
    """Every method of one namespace, awaitable."""

    def __init__(self, inner: _Namespace) -> None:
        """Wrap the synchronous namespace."""
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        """Return the attribute, wrapped in a coroutine function when it is callable."""
        attribute = getattr(self._inner, name)
        if not callable(attribute):
            return attribute

        @functools.wraps(attribute)
        async def call(*args: Any, **kwargs: Any) -> Any:
            return await asyncio.to_thread(attribute, *args, **kwargs)

        return call

    def __dir__(self) -> list[str]:
        """Offer the wrapped namespace's names to ``dir()`` and to a REPL."""
        return sorted(set(dir(self._inner)) | set(super().__dir__()))

    def __repr__(self) -> str:
        """Name the group it mirrors."""
        return f"<dalicc.Async{type(self._inner).__name__}>"


class AsyncClient:
    """The same client, awaitable, for code that runs in an event loop.

    Every method of :class:`Client` is here with the same name, the same arguments and
    the same docstring; each one is awaited instead of called::

        async with AsyncClient(api_key=token) as api:
            listing = await api.licenses.list(keyword="apache")
            verdict = await api.reasoning.compatibility(["MIT", "GPL-3.0-only"])

    The work happens in a worker thread, so the event loop is never blocked while a
    request is in flight. It is a thread per call in flight, not a coroutine per call,
    which is the right trade for an API that answers in milliseconds and is called a
    handful of times per request; a service that needs thousands of concurrent calls
    should use ``httpx.AsyncClient`` against the documented endpoints directly.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Build the underlying synchronous client; the arguments are :class:`Client`'s."""
        self._client = Client(*args, **kwargs)
        self.licenses = _AsyncNamespace(self._client.licenses)
        self.dependency_graphs = _AsyncNamespace(self._client.dependency_graphs)
        self.reasoning = _AsyncNamespace(self._client.reasoning)
        self.composing = _AsyncNamespace(self._client.composing)
        self.translate = _AsyncNamespace(self._client.translate)
        self.account = _AsyncNamespace(self._client.account)
        self.github = _AsyncNamespace(self._client.github)

    @property
    def base_url(self) -> str:
        """The deployment this client talks to."""
        return self._client.base_url

    @property
    def last_rate_limit(self) -> RateLimitStatus:
        """The ``X-RateLimit-*`` headers of the last answer that carried them."""
        return self._client.last_rate_limit

    async def request(self, method: str, path: str, **kwargs: Any) -> Reply:
        """Call one endpoint; the arguments are :meth:`Client.request`'s."""
        return await asyncio.to_thread(
            functools.partial(self._client.request, method, path, **kwargs)
        )

    async def json(self, path: str, **params: Any) -> JsonDocument:
        """``GET`` one endpoint and return its JSON object."""
        return await asyncio.to_thread(functools.partial(self._client.json, path, **params))

    async def healthz(self) -> JsonDocument:
        """Whether the triple store and the reasoner answer."""
        return await asyncio.to_thread(self._client.healthz)

    async def aclose(self) -> None:
        """Close the connection pool."""
        await asyncio.to_thread(self._client.close)

    async def __aenter__(self) -> AsyncClient:
        """Use the client as an asynchronous context manager."""
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        """Close the connection pool on the way out."""
        await self.aclose()

    def __repr__(self) -> str:
        """Name the deployment and whether a token is in play, never the token."""
        return (
            f"<dalicc.AsyncClient base_url={self._client.base_url!r} "
            f"authenticated={bool(self._client.api_key)}>"
        )
