# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2021-2026 DALICC - Verein zur Foerderung der Rechtssicherheit in der Datenbewirtschaftung (ZVR 1249185710)
"""The Python client for DALICC, the Data Licenses Clearance Center.

DALICC models licenses as machine-readable policies and reasons over them: which
licenses can be combined, whether one contradicts itself, what a composed license
would have to say. This package is the client for that service and the ``dalicc``
command line tool that comes with it.

    pip install dalicc

    from dalicc import Client

    with Client() as api:
        verdict = api.reasoning.compatibility(["MIT", "GPL-3.0-only"])
        print("conflicts" if verdict.has_conflicts else "no conflicts")

Reading a license, searching the library and checking compatibility need no
credentials. Composing, drafts, the model history of your own licenses and the
translation assistant need a personal API token from ``/account/tokens``, passed as
``Client(api_key=...)`` or in ``DALICC_API_KEY``.

The full reference, with an example per method, is ``docs/SDK.md`` in the DALICC
repository and https://dalicc.net/documentation/sdk on the site.

This package is licensed Apache-2.0, although the DALICC service itself is
AGPL-3.0-only: a client that only talks to a public API has no reason to carry the
service's terms with it. See LICENSE and NOTICE. Questions go to the association
(``tassilo.pellegrini@ustp.at`` or ``giray.havur@ustp.at``). Nothing this client
returns is legal advice.
"""

from dalicc.client import DEFAULT_BASE_URL, AsyncClient, Client
from dalicc.errors import (
    AuthError,
    ConflictError,
    DaliccError,
    ForbiddenError,
    NotFoundError,
    PayloadTooLarge,
    RateLimitedError,
    ServerError,
    TransportError,
    ValidationError,
)
from dalicc.models import (
    DEFAULT_LICENSE_BASE_URI,
    Axiom,
    AxiomList,
    CompatibilityResult,
    ComposeResult,
    Conflict,
    ConsistencyResult,
    GraphEntry,
    GraphList,
    GraphResult,
    JsonDocument,
    LicenseListing,
    LicenseRef,
    MineEntry,
    MineListing,
    Narration,
    NarrationSection,
    RateLimitStatus,
    Revision,
    RevisionListing,
    TextDocument,
    TranslationQuota,
    TranslationResult,
    TranslationStatement,
    UnmodelledClause,
    VersionEntry,
    VersionListing,
    faceted_search_body,
    license_id,
    license_uri,
)
from dalicc.pagination import paginate

#: Kept in step with the DALICC service it speaks to; see ``docs/SDK.md``.
__version__ = "2.0.0rc1"

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_LICENSE_BASE_URI",
    "AsyncClient",
    "AuthError",
    "Axiom",
    "AxiomList",
    "Client",
    "CompatibilityResult",
    "ComposeResult",
    "Conflict",
    "ConflictError",
    "ConsistencyResult",
    "DaliccError",
    "ForbiddenError",
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
    "NotFoundError",
    "PayloadTooLarge",
    "RateLimitStatus",
    "RateLimitedError",
    "Revision",
    "RevisionListing",
    "ServerError",
    "TextDocument",
    "TranslationQuota",
    "TranslationResult",
    "TranslationStatement",
    "TransportError",
    "UnmodelledClause",
    "ValidationError",
    "VersionEntry",
    "VersionListing",
    "__version__",
    "faceted_search_body",
    "license_id",
    "license_uri",
    "paginate",
]
