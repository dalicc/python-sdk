# Changelog

This is the change record for `dalicc`, the Python client for the DALICC API and the
`dalicc` command line tool. Changes to the DALICC service itself are recorded in the
service repository.

All notable changes to the client are documented here. This changelog follows the
principles of [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Releases use
[Semantic Versioning](https://semver.org/spec/v2.0.0.html); the client carries the
version of the service it was written for, so its number tracks the DALICC release it
was published beside.

## [2.0.0rc1] - 2026-09-23

The first release of the client on a package index, and a release candidate. It is
written for the 2026 generation of the DALICC API, which answers at
`https://api.dalicc.net` today; the website at `https://dalicc.net` is not yet serving
that generation. The candidate is published so that the package can be installed,
pinned and tried against the live API before the deployment. `2.0.0` follows the
deployment, and nothing but the version number is expected to differ.

### Added

* **The first public release of the Python client.** One client object carries seven
  groups of methods, named after the API rather than after the client: `licenses` for
  the license library, `dependency_graphs`, `reasoning` for compatibility and
  consistency, `composing`, `translate` for reading a licence text and writing one,
  `account` and `github`. `Client` and `AsyncClient` offer the same methods, the second
  with `await`. The `dalicc` command line tool installs with the package and covers the
  same ground from a shell, with `--json` so that it can go in a pipe and an exit code
  per kind of failure. What comes back is typed: `LicenseListing`, `LicenseRef`,
  `CompatibilityResult`, `ConsistencyResult` and the rest, each keeping the service's
  own envelope so that nothing the API answers is lost on the way. Failures arrive as an
  exception hierarchy under `DaliccError`, with `AuthError`, `ForbiddenError`,
  `NotFoundError`, `ConflictError`, `ValidationError`, `PayloadTooLarge`,
  `RateLimitedError`, `ServerError` and `TransportError` distinguishing the cases a
  caller acts on differently, and rate limits and quotas are read from the
  response headers so a program can pace itself instead of being refused. The client
  speaks the documented DALICC API contract, which does not change without a release
  that says so, and Python 3.10 or newer with `httpx` as the only runtime dependency is
  all it needs.
* **What the client reaches.** Everything the service offers over its API, which is
  everything the tools on the website are built out of:
  * **The license library.** Listing and paging it, reading one license as JSON-LD,
    Turtle or RDF/XML, its review record, its versions and its changelog, the action
    vocabulary behind every permission, prohibition and duty, the SPDX mapping and the
    lookup by SPDX identifier, the library's own history, the annotator sidecar and the
    badge of a record.
  * **Search.** The faceted search, with `faceted_search_body` to build a query out of
    permissions, prohibitions, duties and targets, and a keyword search over titles.
  * **The composer.** Composing a license from statements, listing the licenses you
    have composed, reading a composed license's revisions and deprecating one of them.
  * **The mixer.** The compatibility check the license mixer runs, over two or more
    licenses at a time, naming the conflicting statements and the axioms the verdict
    came from, against the dependency graph you choose.
  * **The comparator.** The side by side comparison of up to twenty licenses, with the
    rows, the groups and the `differs` flag the License Comparator lays out.
  * **The checker.** The consistency check that reads one license for contradictions
    within itself, and the GitHub license checker, which reads the dependencies of a
    repository and checks them against each other.
  * **Translation and narration.** Reading a licence text and proposing a DALICC model
    for it, with the quote behind every statement, the unmodelled clauses it could not
    place and the polling of a job that takes minutes waited out for you; and the other
    direction, narrating a license or a model of one as prose, with
    `without_names=True` to keep the creator, licensor and publisher names out of what
    is sent. Reading a text needs `consent=True` on every call, because the text goes
    to an external model provider.
  * **Dependency graphs.** The graphs you may see, the axioms in one, creating a graph
    of your own, and its versions and changelog.
  * **Tokens and limits.** Reading the library, searching it, comparing and reasoning
    need no credentials. Composing, your own drafts and the translation assistant need
    a personal API token from `/account/tokens`, passed as `Client(api_key=...)` or in
    `DALICC_API_KEY`. Every answer to a token request updates `last_rate_limit` from
    the `X-RateLimit-*` headers and `account.limits()` reports the window closest to
    refusing the next call, a translation result carries the provider's and the
    account's remaining quota with it, and a refusal arrives as `RateLimitedError`
    with the wait in it.
* **Apache-2.0.** This is the first release of the client published under the Apache
  License 2.0. Until now the client was distributed only inside the DALICC service
  repository, under that repository's AGPL-3.0-only, and was installed from a checkout
  of it. The association decided on 2026-09-23 that a client which only talks to a
  public API has no reason to carry the service's terms with it, and that a permissive
  licence is what people expect of an API client and what lets them link it into their
  own tooling. The service stays AGPL-3.0-only. The package docstring still named the
  service's terms, which is what `help(dalicc)` printed; it names the client's own now,
  as `LICENSE`, `NOTICE` and the package metadata already did.
