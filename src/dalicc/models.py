# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2021-2026 DALICC - Verein zur Foerderung der Rechtssicherheit in der Datenbewirtschaftung (ZVR 1249185710)
"""What the client hands back.

Plain dataclasses, no third-party dependency: the package needs ``httpx`` and nothing
else, so that it can be installed next to any web framework without an argument about
versions.

Two conventions run through the module.

* **Nothing is lost.** Every result carries ``raw`` (the parsed body exactly as the
  service sent it) and ``headers`` (the response headers). A field this SDK does not
  model yet is still in ``raw``, and a caller that needs the frozen SPARQL envelope
  reads it from ``LicenseListing.envelope``.
* **Two identifier spellings.** The license library is addressed by bare identifier
  (``MIT``) and the reasoning endpoints by full IRI
  (``https://dalicc.net/licenselibrary/MIT``). :func:`license_id` and
  :func:`license_uri` convert between the two, and every method of the client accepts
  either spelling.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
import datetime as dt
import re
from typing import Any

__all__ = [
    "DEFAULT_LICENSE_BASE_URI",
    "Axiom",
    "AxiomList",
    "CompatibilityResult",
    "ComposeResult",
    "Conflict",
    "ConsistencyResult",
    "GraphEntry",
    "GraphList",
    "GraphResult",
    "JsonDocument",
    "LicenseListing",
    "LicenseRef",
    "MineEntry",
    "MineListing",
    "Narration",
    "NarrationSection",
    "RateLimitStatus",
    "Revision",
    "RevisionListing",
    "TextDocument",
    "TranslationQuota",
    "TranslationResult",
    "TranslationStatement",
    "UnmodelledClause",
    "VersionEntry",
    "VersionListing",
    "faceted_search_body",
    "license_id",
    "license_uri",
]

#: Where the license library lives. A deployment can publish under another namespace;
#: ``Client(license_base_uri=...)`` overrides it.
DEFAULT_LICENSE_BASE_URI = "https://dalicc.net/licenselibrary/"


# ---------------------------------------------------------------------------
# identifiers
# ---------------------------------------------------------------------------


#: An absolute URI: a scheme, a colon, and whatever follows.
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def license_id(reference: Any, base: str = DEFAULT_LICENSE_BASE_URI) -> str:
    """The bare identifier of a license, whatever spelling went in.

    ``"MIT"``, ``"https://dalicc.net/licenselibrary/MIT"`` and a
    :class:`LicenseRef` all give ``"MIT"``. The license endpoints take this form.

    A URI that is **not** in the license library is handed back whole rather than cut
    down to its last path segment, so that the service can refuse it with the ``422``
    it deserves instead of answering about some unrelated record whose identifier
    happened to match.
    """
    text = _reference_text(reference)
    for prefix in _library_prefixes(base):
        if text.startswith(prefix):
            return text[len(prefix) :].strip("/")
    if _SCHEME.match(text):
        return text
    return text.rstrip("/").rsplit("/", 1)[-1]


def license_uri(reference: Any, base: str = DEFAULT_LICENSE_BASE_URI) -> str:
    """The full IRI of a license, whatever spelling went in.

    The compatibility check takes this form. Anything that is already an absolute URI
    is passed through untouched, so a license of another deployment stays addressable
    and a mistyped scheme is refused by the service rather than hidden by the client.
    """
    text = _reference_text(reference)
    if _SCHEME.match(text):
        return text
    return base.rstrip("/") + "/" + text.lstrip("/")


def _library_prefixes(base: str) -> tuple[str, ...]:
    """The spellings of the library namespace, ``http`` and ``https`` both.

    The data mixes the two (the faceted search normalises ``http:`` to ``https:`` on
    the way out for exactly that reason), so both have to be recognised here.
    """
    prefixes = set()
    for candidate in (base, DEFAULT_LICENSE_BASE_URI):
        if not candidate:
            continue
        one = candidate if candidate.endswith("/") else candidate + "/"
        prefixes.add(one)
        if one.startswith("https://"):
            prefixes.add("http://" + one[len("https://") :])
        elif one.startswith("http://"):
            prefixes.add("https://" + one[len("http://") :])
    return tuple(sorted(prefixes, key=len, reverse=True))


def _reference_text(reference: Any) -> str:
    """The string behind a reference: a ``LicenseRef``, a mapping or a string."""
    if isinstance(reference, LicenseRef):
        return (reference.uri or reference.id).strip()
    if isinstance(reference, Mapping):
        for key in ("uri", "id", "value"):
            value = reference.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return str(reference or "").strip()


# ---------------------------------------------------------------------------
# bodies the service returns as they are
# ---------------------------------------------------------------------------


class JsonDocument(dict):
    """A JSON object, with the response headers on the side.

    It behaves exactly like the ``dict`` the service sent, which is what most callers
    want; ``.headers`` and ``.raw`` are there when they want more.
    """

    headers: dict[str, str]

    @classmethod
    def build(cls, payload: Any, headers: Mapping[str, str] | None = None) -> JsonDocument:
        """Wrap a parsed body; a body that is not an object becomes ``{"value": ...}``."""
        document = cls(payload if isinstance(payload, dict) else {"value": payload})
        document.headers = dict(headers or {})
        return document

    @property
    def raw(self) -> dict[str, Any]:
        """The parsed body, which is this object."""
        return dict(self)


class TextDocument(str):
    """A text body (Turtle, RDF/XML, SVG), with the response headers on the side."""

    headers: dict[str, str]
    media_type: str

    @classmethod
    def build(cls, text: str, headers: Mapping[str, str] | None = None) -> TextDocument:
        """Wrap a text body and remember what it was sent as."""
        document = cls(text)
        document.headers = dict(headers or {})
        document.media_type = document.headers.get("content-type", "").split(";")[0].strip()
        return document

    @property
    def raw(self) -> str:
        """The body, which is this object."""
        return str(self)


# ---------------------------------------------------------------------------
# the license library
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LicenseRef:
    """One row of a listing: the identifier, the address and the title."""

    id: str
    uri: str = ""
    title: str = ""

    def __str__(self) -> str:
        """The identifier, so a reference can be dropped into a path."""
        return self.id


@dataclass
class LicenseListing:
    """The answer of ``/licenselibrary/list`` and ``/licenselibrary/facetedsearch``.

    The service returns Virtuoso's SPARQL-JSON envelope, which is part of the frozen
    contract and is therefore kept whole in :attr:`envelope`. :attr:`licenses` is the
    same rows read into :class:`LicenseRef` objects, which is what a caller normally
    wants. The object iterates over the rows and has a length.
    """

    licenses: list[LicenseRef] = field(default_factory=list)
    envelope: dict[str, Any] = field(default_factory=dict)
    raw: Any = None
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, payload: Any, headers: Mapping[str, str] | None = None) -> LicenseListing:
        """Read the envelope; an answer that is not one gives an empty listing."""
        envelope = payload if isinstance(payload, dict) else {}
        rows = envelope.get("results", {}).get("bindings", [])
        licenses = [
            _ref_from_binding(row) for row in rows if isinstance(row, dict)
        ]
        return cls(
            licenses=[ref for ref in licenses if ref is not None],
            envelope=envelope,
            raw=payload,
            headers=dict(headers or {}),
        )

    @property
    def ids(self) -> list[str]:
        """The bare identifiers, in the order the service sorted them."""
        return [ref.id for ref in self.licenses]

    def __iter__(self) -> Iterator[LicenseRef]:
        """Iterate over the rows."""
        return iter(self.licenses)

    def __len__(self) -> int:
        """How many rows the service returned."""
        return len(self.licenses)

    def __getitem__(self, index: int) -> LicenseRef:
        """One row by position."""
        return self.licenses[index]


def _ref_from_binding(row: Mapping[str, Any]) -> LicenseRef | None:
    """One SPARQL binding as a :class:`LicenseRef`."""
    uri = str(row.get("id", {}).get("value", "") or "")
    title = str(row.get("title", {}).get("value", "") or "")
    if not uri and not title:
        return None
    return LicenseRef(id=license_id(uri) if uri else "", uri=uri, title=title)


#: The faceted search wants all four groups; these are the values that select nothing.
_FACET_DEFAULTS: dict[str, dict[str, str]] = {
    "target": {"creativework": "yes", "dataset": "yes", "software": "yes"},
    "actions": {
        "reproduce": "na",
        "distribute": "na",
        "modify": "na",
        "derive": "na",
        "commercial_use": "na",
        "charge_distribution_fee": "na",
        "change_license": "na",
    },
    "duties": {
        "distribute_duty_attribution": "na",
        "distribute_duty_notice": "na",
        "distribute_duty_source_code": "na",
        "modify_duty_rename": "na",
        "modify_duty_attribution": "na",
        "modify_duty_modification_notice": "na",
        "modify_duty_notice": "na",
        "modify_duty_source_code": "na",
        "derive_duty_rename": "na",
        "derive_duty_attribution": "na",
        "derive_duty_modification_notice": "na",
        "derive_duty_notice": "na",
        "derive_duty_source_code": "na",
        "change_license_duty_compliant_license": "na",
    },
    "license_wide_duties": {"share_alike": "na"},
}


def faceted_search_body(
    target: Mapping[str, str] | None = None,
    actions: Mapping[str, str] | None = None,
    duties: Mapping[str, str] | None = None,
    license_wide_duties: Mapping[str, str] | None = None,
) -> dict[str, dict[str, str]]:
    """Build the four-part body of the faceted search from what the caller named.

    All four groups are required by the service and every facet inside them has a
    neutral value (``yes`` for an asset type, ``na`` for an action or a duty), so a
    caller names only the facets it cares about::

        faceted_search_body(actions={"commercial_use": "permitted"},
                            duties={"distribute_duty_attribution": "required"})

    A facet name the service does not know raises ``ValueError`` here rather than
    being silently ignored on the server, which is what used to happen.
    """
    given = {
        "target": target,
        "actions": actions,
        "duties": duties,
        "license_wide_duties": license_wide_duties,
    }
    body: dict[str, dict[str, str]] = {}
    for group, defaults in _FACET_DEFAULTS.items():
        chosen = dict(defaults)
        for name, value in dict(given[group] or {}).items():
            if name not in defaults:
                known = ", ".join(sorted(defaults))
                raise ValueError(f"Unknown {group} facet: {name}. Known facets: {known}")
            chosen[name] = str(value)
        body[group] = chosen
    return body


# ---------------------------------------------------------------------------
# reasoning
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Conflict:
    """One contradiction, in either of the two shapes the service uses.

    The compatibility check names two RDF statements (:attr:`statement_1` and
    :attr:`statement_2`, each a subject, predicate, object triple). The consistency
    check and the composer name two actions instead (:attr:`action_1`,
    :attr:`action_2`, with their labels). :attr:`reason` is the sentence in both cases
    and :attr:`kind` says whether the conflict is ``direct`` or ``derived``.
    """

    reason: str = ""
    kind: str = ""
    statement_1: tuple[str, ...] = ()
    statement_2: tuple[str, ...] = ()
    action_1: str = ""
    action_2: str = ""
    label_1: str = ""
    label_2: str = ""
    raw: Any = None

    @classmethod
    def parse(cls, payload: Any, kind: str = "") -> Conflict:
        """Read either shape; anything unknown is still kept in ``raw``."""
        data = payload if isinstance(payload, dict) else {}
        return cls(
            reason=str(data.get("reason", "") or ""),
            kind=str(data.get("kind", kind) or kind),
            statement_1=_triple(data.get("statement_1")),
            statement_2=_triple(data.get("statement_2")),
            action_1=str(data.get("action_1", "") or ""),
            action_2=str(data.get("action_2", "") or ""),
            label_1=str(data.get("label_1", "") or ""),
            label_2=str(data.get("label_2", "") or ""),
            raw=payload,
        )

    def __str__(self) -> str:
        """The reason, which is the part a person reads."""
        return self.reason


def _triple(value: Any) -> tuple[str, ...]:
    """A statement as a tuple of strings."""
    if isinstance(value, (list, tuple)):
        return tuple(str(part) for part in value)
    return ()


@dataclass
class CompatibilityResult:
    """The answer of ``POST /compatibilitycheck/``.

    The service sends ``direct`` and ``derived`` as JSON **objects whose keys are
    stringified integers**, and an empty result as ``{}`` rather than ``[]``. That
    shape is frozen, so it stays in :attr:`raw`; here the two are ordinary lists,
    sorted by the numeric key.
    """

    direct: list[Conflict] = field(default_factory=list)
    derived: list[Conflict] = field(default_factory=list)
    dependency_graph: str = ""
    raw: Any = None
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def parse(
        cls, payload: Any, headers: Mapping[str, str] | None = None
    ) -> CompatibilityResult:
        """Read the keyed objects into lists."""
        body = payload if isinstance(payload, dict) else {}
        statements = body.get("conflicting_statements")
        if not isinstance(statements, dict):
            statements = {}
        return cls(
            direct=_keyed(statements.get("direct"), "direct"),
            derived=_keyed(statements.get("derived"), "derived"),
            dependency_graph=str(body.get("dependency_graph", "") or ""),
            raw=payload,
            headers=dict(headers or {}),
        )

    @property
    def conflicts(self) -> list[Conflict]:
        """Direct and derived conflicts together."""
        return [*self.direct, *self.derived]

    @property
    def has_conflicts(self) -> bool:
        """True when the licenses cannot be combined without a contradiction."""
        return bool(self.direct or self.derived)

    def __bool__(self) -> bool:
        """True when there is **no** conflict, so ``if result:`` reads as expected."""
        return not self.has_conflicts


def _keyed(value: Any, kind: str) -> list[Conflict]:
    """The ``{"0": {...}, "1": {...}}`` shape as a list, in numeric key order."""
    if isinstance(value, dict):
        def order(item: tuple[str, Any]) -> tuple[int, str]:
            key = item[0]
            return (int(key), "") if str(key).isdigit() else (1 << 30, str(key))

        return [Conflict.parse(entry, kind) for _key, entry in sorted(value.items(), key=order)]
    if isinstance(value, list):
        return [Conflict.parse(entry, kind) for entry in value]
    return []


@dataclass
class ConsistencyResult:
    """The answer of ``POST /licenselibrary/consistencycheck``."""

    consistent: bool = True
    conflicts: list[Conflict] = field(default_factory=list)
    raw: Any = None
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, payload: Any, headers: Mapping[str, str] | None = None) -> ConsistencyResult:
        """Read the verdict and the conflicts."""
        body = payload if isinstance(payload, dict) else {}
        entries = body.get("conflicts")
        return cls(
            consistent=bool(body.get("consistent", True)),
            conflicts=[Conflict.parse(entry) for entry in entries or []],
            raw=payload,
            headers=dict(headers or {}),
        )

    def __bool__(self) -> bool:
        """True when the license does not contradict itself."""
        return self.consistent


# ---------------------------------------------------------------------------
# composing
# ---------------------------------------------------------------------------


@dataclass
class ComposeResult:
    """The answer of the composer API and of ``deprecate``."""

    id: str = ""
    uri: str = ""
    status: str = ""
    version: int = 0
    conflicts: list[Conflict] = field(default_factory=list)
    raw: Any = None
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, payload: Any, headers: Mapping[str, str] | None = None) -> ComposeResult:
        """Read the five documented keys."""
        body = payload if isinstance(payload, dict) else {}
        return cls(
            id=str(body.get("id", "") or ""),
            uri=str(body.get("uri", "") or ""),
            status=str(body.get("status", "") or ""),
            version=int(body.get("version", 0) or 0),
            conflicts=[Conflict.parse(entry) for entry in body.get("conflicts") or []],
            raw=payload,
            headers=dict(headers or {}),
        )


@dataclass(frozen=True)
class MineEntry:
    """One license of the account behind the API token."""

    id: str = ""
    uri: str = ""
    title: str = ""
    status: str = ""
    version: int = 1
    visibility: str = ""
    role: str = ""
    created_at: str = ""
    published_at: str = ""
    replaces: str = ""
    replaced_by: str = ""
    raw: Any = None

    @classmethod
    def parse(cls, payload: Any) -> MineEntry:
        """Read one entry of ``GET /licenselibrary/mine``."""
        body = payload if isinstance(payload, dict) else {}
        return cls(
            id=str(body.get("id", "") or ""),
            uri=str(body.get("uri", "") or ""),
            title=str(body.get("title", "") or ""),
            status=str(body.get("status", "") or ""),
            version=int(body.get("version", 1) or 1),
            visibility=str(body.get("visibility", "") or ""),
            role=str(body.get("role", "") or ""),
            created_at=str(body.get("created_at", "") or ""),
            published_at=str(body.get("published_at", "") or ""),
            replaces=str(body.get("replaces", "") or ""),
            replaced_by=str(body.get("replaced_by", "") or ""),
            raw=payload,
        )


@dataclass
class MineListing:
    """The answer of ``GET /licenselibrary/mine``."""

    licenses: list[MineEntry] = field(default_factory=list)
    raw: Any = None
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, payload: Any, headers: Mapping[str, str] | None = None) -> MineListing:
        """Read the list."""
        body = payload if isinstance(payload, dict) else {}
        return cls(
            licenses=[MineEntry.parse(entry) for entry in body.get("licenses") or []],
            raw=payload,
            headers=dict(headers or {}),
        )

    def __iter__(self) -> Iterator[MineEntry]:
        """Iterate over the licenses."""
        return iter(self.licenses)

    def __len__(self) -> int:
        """How many licenses the account has."""
        return len(self.licenses)


@dataclass(frozen=True)
class Revision:
    """One saved state of a license."""

    revision: int = 0
    kind: str = ""
    saved_at: str = ""
    actor: str = ""
    actor_role: str = ""
    note: str = ""
    url: str = ""
    raw: Any = None

    @classmethod
    def parse(cls, payload: Any) -> Revision:
        """Read one revision entry."""
        body = payload if isinstance(payload, dict) else {}
        return cls(
            revision=int(body.get("revision", 0) or 0),
            kind=str(body.get("kind", "") or ""),
            saved_at=str(body.get("saved_at", "") or ""),
            actor=str(body.get("actor", "") or ""),
            actor_role=str(body.get("actor_role", "") or ""),
            note=str(body.get("note", "") or ""),
            url=str(body.get("url", "") or ""),
            raw=payload,
        )


@dataclass
class RevisionListing:
    """The answer of ``GET /licenselibrary/mine/{id}/revisions``."""

    id: str = ""
    uri: str = ""
    current: int = 0
    revisions: list[Revision] = field(default_factory=list)
    raw: Any = None
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, payload: Any, headers: Mapping[str, str] | None = None) -> RevisionListing:
        """Read the list."""
        body = payload if isinstance(payload, dict) else {}
        return cls(
            id=str(body.get("id", "") or ""),
            uri=str(body.get("uri", "") or ""),
            current=int(body.get("current", 0) or 0),
            revisions=[Revision.parse(entry) for entry in body.get("revisions") or []],
            raw=payload,
            headers=dict(headers or {}),
        )

    def __iter__(self) -> Iterator[Revision]:
        """Iterate over the revisions, newest first."""
        return iter(self.revisions)

    def __len__(self) -> int:
        """How many revisions there are."""
        return len(self.revisions)


# ---------------------------------------------------------------------------
# model history
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VersionEntry:
    """One version of a curated model."""

    version: int = 0
    date: str = ""
    summary: str = ""
    current: bool = False
    url: str = ""
    api_url: str = ""
    raw: Any = None

    @classmethod
    def parse(cls, payload: Any) -> VersionEntry:
        """Read one version entry."""
        body = payload if isinstance(payload, dict) else {}
        return cls(
            version=int(body.get("version", 0) or 0),
            date=str(body.get("date", "") or ""),
            summary=str(body.get("summary", "") or ""),
            current=bool(body.get("current", False)),
            url=str(body.get("url", "") or ""),
            api_url=str(body.get("api_url", "") or ""),
            raw=payload,
        )


@dataclass
class VersionListing:
    """The answer of the ``/versions`` endpoints, newest version first."""

    id: str = ""
    title: str = ""
    current: int = 0
    versions: list[VersionEntry] = field(default_factory=list)
    raw: Any = None
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, payload: Any, headers: Mapping[str, str] | None = None) -> VersionListing:
        """Read the list."""
        body = payload if isinstance(payload, dict) else {}
        return cls(
            id=str(body.get("id", "") or ""),
            title=str(body.get("title", "") or ""),
            current=int(body.get("current", 0) or 0),
            versions=[VersionEntry.parse(entry) for entry in body.get("versions") or []],
            raw=payload,
            headers=dict(headers or {}),
        )

    def __iter__(self) -> Iterator[VersionEntry]:
        """Iterate over the versions."""
        return iter(self.versions)

    def __len__(self) -> int:
        """How many versions the model has had."""
        return len(self.versions)


# ---------------------------------------------------------------------------
# dependency graphs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Axiom:
    """One statement of a dependency graph."""

    subject: str = ""
    predicate: str = ""
    object: str = ""
    raw: Any = None

    @classmethod
    def parse(cls, payload: Any) -> Axiom:
        """Read one statement out of the SPARQL-style term objects."""
        body = payload if isinstance(payload, dict) else {}
        return cls(
            subject=_term(body.get("subject")),
            predicate=_term(body.get("predicate")),
            object=_term(body.get("object")),
            raw=payload,
        )

    def __str__(self) -> str:
        """The statement as a line of three IRIs."""
        return f"{self.subject} {self.predicate} {self.object}"


def _term(value: Any) -> str:
    """The value of an RDF term object, or the plain string it already was."""
    if isinstance(value, Mapping):
        return str(value.get("value", "") or "")
    return str(value or "")


@dataclass
class AxiomList:
    """The answer of ``GET /dependencygraph/list``."""

    axioms: list[Axiom] = field(default_factory=list)
    raw: Any = None
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, payload: Any, headers: Mapping[str, str] | None = None) -> AxiomList:
        """Read the statements."""
        body = payload if isinstance(payload, dict) else {}
        entries = body.get("dependency_graph_statements") or []
        return cls(
            axioms=[Axiom.parse(entry) for entry in entries],
            raw=payload,
            headers=dict(headers or {}),
        )

    def __iter__(self) -> Iterator[Axiom]:
        """Iterate over the statements."""
        return iter(self.axioms)

    def __len__(self) -> int:
        """How many statements the graph holds."""
        return len(self.axioms)


@dataclass(frozen=True)
class GraphEntry:
    """One dependency graph, as the listing describes it."""

    id: str = ""
    title: str = ""
    description: str = ""
    kind: str = ""
    status: str = ""
    version: int = 1
    iri: str = ""
    axioms: int = 0
    default: bool = False
    role: str = ""
    raw: Any = None

    @classmethod
    def parse(cls, payload: Any) -> GraphEntry:
        """Read one entry of ``/dependencygraph/graphs`` or ``/mine``."""
        body = payload if isinstance(payload, dict) else {}
        return cls(
            id=str(body.get("id", "") or ""),
            title=str(body.get("title", "") or ""),
            description=str(body.get("description", "") or ""),
            kind=str(body.get("kind", "") or ""),
            status=str(body.get("status", "") or ""),
            version=int(body.get("version", 1) or 1),
            iri=str(body.get("iri", "") or ""),
            axioms=int(body.get("axioms", 0) or 0),
            default=bool(body.get("default", False)),
            role=str(body.get("role", "") or ""),
            raw=payload,
        )


@dataclass
class GraphList:
    """The answer of ``GET /dependencygraph/graphs`` and ``/dependencygraph/mine``."""

    graphs: list[GraphEntry] = field(default_factory=list)
    raw: Any = None
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, payload: Any, headers: Mapping[str, str] | None = None) -> GraphList:
        """Read the list."""
        body = payload if isinstance(payload, dict) else {}
        return cls(
            graphs=[GraphEntry.parse(entry) for entry in body.get("graphs") or []],
            raw=payload,
            headers=dict(headers or {}),
        )

    def __iter__(self) -> Iterator[GraphEntry]:
        """Iterate over the graphs."""
        return iter(self.graphs)

    def __len__(self) -> int:
        """How many graphs the caller may see."""
        return len(self.graphs)


@dataclass
class GraphResult:
    """The answer of ``POST /dependencygraph``."""

    id: str = ""
    uri: str = ""
    iri: str = ""
    status: str = ""
    version: int = 1
    axioms: int = 0
    warnings: list[str] = field(default_factory=list)
    raw: Any = None
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, payload: Any, headers: Mapping[str, str] | None = None) -> GraphResult:
        """Read the seven documented keys."""
        body = payload if isinstance(payload, dict) else {}
        return cls(
            id=str(body.get("id", "") or ""),
            uri=str(body.get("uri", "") or ""),
            iri=str(body.get("iri", "") or ""),
            status=str(body.get("status", "") or ""),
            version=int(body.get("version", 1) or 1),
            axioms=int(body.get("axioms", 0) or 0),
            warnings=[str(entry) for entry in body.get("warnings") or []],
            raw=payload,
            headers=dict(headers or {}),
        )


# ---------------------------------------------------------------------------
# the translation assistant
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TranslationStatement:
    """One statement the assistant proposes, with the sentence it read it from."""

    kind: str = ""
    term: str = ""
    iri: str = ""
    label: str = ""
    attached_to: str = ""
    evidence: str = ""
    evidence_all: tuple[str, ...] = ()
    confidence: float = 0.0
    note: str = ""
    known_term: bool = True
    raw: Any = None

    @classmethod
    def parse(cls, payload: Any) -> TranslationStatement:
        """Read one proposed statement."""
        body = payload if isinstance(payload, dict) else {}
        return cls(
            kind=str(body.get("kind", "") or ""),
            term=str(body.get("term", "") or ""),
            iri=str(body.get("iri", "") or ""),
            label=str(body.get("label", "") or ""),
            attached_to=str(body.get("attached_to", "") or ""),
            evidence=str(body.get("evidence", "") or ""),
            evidence_all=tuple(str(quote) for quote in body.get("evidence_all") or ()),
            confidence=float(body.get("confidence", 0.0) or 0.0),
            note=str(body.get("note", "") or ""),
            known_term=bool(body.get("known_term", True)),
            raw=payload,
        )


@dataclass(frozen=True)
class UnmodelledClause:
    """A clause the DALICC vocabulary cannot express yet."""

    clause_quote: str = ""
    proposed_term: str = ""
    note: str = ""
    raw: Any = None

    @classmethod
    def parse(cls, payload: Any) -> UnmodelledClause:
        """Read one unmodelled clause."""
        body = payload if isinstance(payload, dict) else {}
        return cls(
            clause_quote=str(body.get("clause_quote", "") or ""),
            proposed_term=str(body.get("proposed_term", "") or ""),
            note=str(body.get("note", "") or ""),
            raw=payload,
        )


@dataclass(frozen=True)
class TranslationQuota:
    """What the provider and the account have left.

    ``provider_*`` is what the model provider reported on the last call,
    ``user_*_today`` the account's own daily allowance and ``global_*_today`` the
    allowance of the whole deployment.
    """

    configured: bool = False
    provider_limit_requests: int | None = None
    provider_remaining_requests: int | None = None
    provider_reset_at: dt.datetime | None = None
    provider_blocked_until: dt.datetime | None = None
    provider_limit_tokens: int | None = None
    provider_remaining_tokens: int | None = None
    user_limit_today: int | None = None
    user_used_today: int | None = None
    user_remaining_today: int | None = None
    global_limit_today: int | None = None
    global_used_today: int | None = None
    global_remaining_today: int | None = None
    raw: Any = None

    @classmethod
    def parse(cls, payload: Any) -> TranslationQuota:
        """Read the quota block; timestamps become aware datetimes."""
        body = payload if isinstance(payload, dict) else {}
        return cls(
            configured=bool(body.get("configured", False)),
            provider_limit_requests=_optional_int(body.get("provider_limit_requests")),
            provider_remaining_requests=_optional_int(
                body.get("provider_remaining_requests")
            ),
            provider_reset_at=parse_moment(body.get("provider_reset_at")),
            provider_blocked_until=parse_moment(body.get("provider_blocked_until")),
            provider_limit_tokens=_optional_int(body.get("provider_limit_tokens")),
            provider_remaining_tokens=_optional_int(body.get("provider_remaining_tokens")),
            user_limit_today=_optional_int(body.get("user_limit_today")),
            user_used_today=_optional_int(body.get("user_used_today")),
            user_remaining_today=_optional_int(body.get("user_remaining_today")),
            global_limit_today=_optional_int(body.get("global_limit_today")),
            global_used_today=_optional_int(body.get("global_used_today")),
            global_remaining_today=_optional_int(body.get("global_remaining_today")),
            raw=payload,
        )

    @property
    def provider_reset_in(self) -> float | None:
        """Seconds until the provider's request window restarts, for a countdown."""
        if self.provider_reset_at is None:
            return None
        gap = (self.provider_reset_at - dt.datetime.now(dt.timezone.utc)).total_seconds()
        return max(0.0, gap)


@dataclass
class TranslationResult:
    """The answer of ``POST /licenselibrary/translate``.

    :attr:`license` is the proposal in the shape ``POST /licenselibrary/composer``
    takes, so a caller can review it and send it straight back. Nothing is published:
    with ``save_draft=True`` the proposal is stored as a private draft and
    :attr:`draft_id` names it.
    """

    job_id: str = ""
    title: str = ""
    language: str = ""
    prompt_version: str = ""
    license: dict[str, Any] = field(default_factory=dict)
    statements: list[TranslationStatement] = field(default_factory=list)
    unmodelled: list[UnmodelledClause] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    parts_total: int = 0
    parts_translated: list[int] = field(default_factory=list)
    parts_failed: list[int] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    quota: TranslationQuota = field(default_factory=TranslationQuota)
    draft_id: str = ""
    draft_uri: str = ""
    #: ``queued``, ``running``, ``done`` or ``failed`` for a translation that runs in
    #: the background; empty for one that was answered in the request.
    status: str = ""
    #: Which part is being read, while one is.
    part_current: int = 0
    #: Why a background run failed, in the words the service uses.
    message: str = ""
    #: Which provider answered. The service tries several and falls over from one to
    #: the next, so this is the one that read most of the text; it is empty for an
    #: older service that does not report it.
    provider: str = ""
    #: The HTTP status of the answer this was read from: ``202`` means "still working".
    status_code: int = 200
    raw: Any = None
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def parse(
        cls,
        payload: Any,
        headers: Mapping[str, str] | None = None,
        status_code: int = 200,
    ) -> TranslationResult:
        """Read the answer."""
        body = payload if isinstance(payload, dict) else {}
        parts = body.get("parts") if isinstance(body.get("parts"), dict) else {}
        if not parts and body.get("parts_total"):
            # The progress of a background run says the same thing in flatter words.
            parts = {"total": body.get("parts_total"), "translated": [], "failed": []}
        return cls(
            job_id=str(body.get("job_id", "") or ""),
            title=str(body.get("title", "") or ""),
            language=str(body.get("language", "") or ""),
            prompt_version=str(body.get("prompt_version", "") or ""),
            license=dict(body.get("license") or {}),
            statements=[
                TranslationStatement.parse(entry) for entry in body.get("statements") or []
            ],
            unmodelled=[
                UnmodelledClause.parse(entry) for entry in body.get("unmodelled") or []
            ],
            warnings=[str(entry) for entry in body.get("warnings") or []],
            conflicts=[Conflict.parse(entry) for entry in body.get("conflicts") or []],
            parts_total=int(parts.get("total", 0) or 0),
            parts_translated=[int(part) for part in parts.get("translated") or []],
            parts_failed=[int(part) for part in parts.get("failed") or []],
            usage=dict(body.get("usage") or {}),
            quota=TranslationQuota.parse(body.get("quota")),
            draft_id=str(body.get("draft_id", "") or ""),
            draft_uri=str(body.get("draft_uri", "") or ""),
            status=str(body.get("status", "") or ""),
            # The finished answer carries it with the rest of the usage; the progress
            # of a background run carries it flat, next to the part being read.
            provider=str(
                body.get("provider")
                or (body.get("usage") or {}).get("provider")
                or ""
            ),
            part_current=int(body.get("part_current", 0) or 0),
            message=str(body.get("message", "") or ""),
            status_code=int(status_code),
            raw=payload,
            headers=dict(headers or {}),
        )

    @property
    def pending(self) -> bool:
        """True while a background translation is still being read.

        A long text is started rather than answered (``202``), and this is what says so:
        the proposal is not here yet and :attr:`job_id` is what collects it.
        """
        return self.status_code == 202 or self.status in ("queued", "running")

    @property
    def failed(self) -> bool:
        """True when a background run ended without a proposal."""
        return self.status == "failed"

    @property
    def partial(self) -> bool:
        """True when at least one part of a long text could not be read."""
        return bool(self.parts_failed)

    @property
    def parts_done(self) -> int:
        """How many parts of the text were read.

        ``parts_translated`` holds the 1-based numbers of those parts, because a retry
        of a failed part has to say which ones it brought back.
        """
        return len(self.parts_translated)

    def __iter__(self) -> Iterator[TranslationStatement]:
        """Iterate over the proposed statements."""
        return iter(self.statements)


# ---------------------------------------------------------------------------
# rate limits
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RateLimitStatus:
    """What the ``X-RateLimit-*`` headers of a token request said.

    The three headers describe the window closest to refusing the next call, so
    :attr:`remaining` is what a well behaved client paces itself by. Anonymous
    requests carry none of them and every field stays ``None``.
    """

    limit: int | None = None
    remaining: int | None = None
    reset_at: dt.datetime | None = None
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, headers: Mapping[str, str] | None) -> RateLimitStatus:
        """Read the three headers, whatever their case."""
        found = {str(key).lower(): str(value) for key, value in dict(headers or {}).items()}
        return cls(
            limit=_optional_int(found.get("x-ratelimit-limit")),
            remaining=_optional_int(found.get("x-ratelimit-remaining")),
            reset_at=_from_stamp(found.get("x-ratelimit-reset")),
            headers=found,
        )

    @property
    def known(self) -> bool:
        """True when the answer actually carried the headers."""
        return self.limit is not None

    @property
    def reset_in(self) -> float | None:
        """Seconds until the window restarts."""
        if self.reset_at is None:
            return None
        gap = (self.reset_at - dt.datetime.now(dt.timezone.utc)).total_seconds()
        return max(0.0, gap)


def parse_moment(value: Any) -> dt.datetime | None:
    """An ISO timestamp (with or without a trailing ``Z``) as an aware datetime."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment


def _from_stamp(value: Any) -> dt.datetime | None:
    """A UNIX timestamp in seconds as an aware datetime."""
    stamp = _optional_int(value)
    if stamp is None:
        return None
    try:
        return dt.datetime.fromtimestamp(stamp, dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    """A whole number, or ``None`` when the field was absent or not one."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def as_ids(references: Sequence[Any]) -> list[str]:
    """Bare identifiers for a sequence of references of any spelling."""
    return [license_id(reference) for reference in references]


# ---------------------------------------------------------------------------
# License-to-Text
# ---------------------------------------------------------------------------


@dataclass
class NarrationSection:
    """One numbered section of a licence text."""

    heading: str = ""
    text: str = ""
    #: The term identifiers of the model this section covers.
    statements: list[str] = field(default_factory=list)

    @classmethod
    def parse(cls, payload: Any) -> NarrationSection:
        """Read one section of the answer."""
        body = payload if isinstance(payload, dict) else {}
        return cls(
            heading=str(body.get("heading", "") or ""),
            text=str(body.get("text", "") or ""),
            statements=[str(entry) for entry in body.get("statements") or []],
        )


@dataclass
class Narration:
    """The answer of ``POST /licenselibrary/narrate``: a licence model as a text.

    :attr:`produced_by` is ``provider`` when the assistant wrote the text and
    ``fallback`` when it was written from the DALICC vocabulary alone, which is what
    happens when the assistant is not configured, the daily allowance is used up or the
    provider did not answer. Either way there is a text.

    :attr:`missing` lists the statements of the model that no section named. It is
    empty for a text that covered the model in full, and :attr:`coverage_note` is the
    sentence the website shows when it is not.
    """

    title: str = ""
    preamble: str = ""
    sections: list[NarrationSection] = field(default_factory=list)
    closing: str = ""
    produced_by: str = ""
    #: Which provider wrote the text, empty when the deterministic writer did or when
    #: the service does not report it.
    provider: str = ""
    license_id: str = ""
    prompt_version: str = ""
    names_removed: bool = False
    #: How many statements the model has, and how many the sections covered.
    statements: int = 0
    covered: int = 0
    missing: list[dict[str, Any]] = field(default_factory=list)
    coverage_note: str = ""
    raw: Any = None
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def parse(
        cls, payload: Any, headers: Mapping[str, str] | None = None
    ) -> Narration:
        """Read the answer."""
        body = payload if isinstance(payload, dict) else {}
        coverage = body.get("coverage") if isinstance(body.get("coverage"), dict) else {}
        return cls(
            title=str(body.get("title", "") or ""),
            preamble=str(body.get("preamble", "") or ""),
            sections=[NarrationSection.parse(entry) for entry in body.get("sections") or []],
            closing=str(body.get("closing", "") or ""),
            produced_by=str(body.get("produced_by", "") or ""),
            provider=str(body.get("provider", "") or ""),
            license_id=str(body.get("license_id", "") or ""),
            prompt_version=str(body.get("prompt_version", "") or ""),
            names_removed=bool(body.get("names_removed")),
            statements=int(coverage.get("statements", 0) or 0),
            covered=int(coverage.get("covered", 0) or 0),
            missing=[entry for entry in coverage.get("missing") or [] if isinstance(entry, dict)],
            coverage_note=str(coverage.get("note", "") or ""),
            raw=payload,
            headers=dict(headers or {}),
        )

    @property
    def complete(self) -> bool:
        """Whether every statement of the model is covered by a section."""
        return not self.missing

    @property
    def text(self) -> str:
        """The whole licence text as one plain document, sections numbered."""
        lines: list[str] = [self.title, ""] if self.title else []
        if self.preamble:
            lines += [self.preamble, ""]
        for number, section in enumerate(self.sections, start=1):
            lines += [f"{number}. {section.heading}", "", section.text, ""]
        if self.coverage_note:
            lines += [self.coverage_note, ""]
        if self.closing:
            lines.append(self.closing)
        return "\n".join(lines).rstrip() + "\n"

    def __str__(self) -> str:
        """The licence text itself, so ``print(narration)`` reads it."""
        return self.text
