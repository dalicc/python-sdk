# Changelog

This is the change record for `dalicc`, the Python client for the DALICC API and the
`dalicc` command line tool. Changes to the DALICC service itself are recorded in the
service repository.

All notable changes to the client are documented here. This changelog follows the
principles of [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Releases use
[Semantic Versioning](https://semver.org/spec/v2.0.0.html); the client carries the
version of the service it was written for, so its number tracks the DALICC release it
was published beside.

## [Unreleased]

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
* **Apache-2.0.** This is the first release of the client published under the Apache
  License 2.0. Until now the client was distributed only inside the DALICC service
  repository, under that repository's AGPL-3.0-only, and was installed from a checkout
  of it. The association decided on 2026-09-23 that a client which only talks to a
  public API has no reason to carry the service's terms with it, and that a permissive
  licence is what people expect of an API client and what lets them link it into their
  own tooling. The service stays AGPL-3.0-only.

The version number of this section is cut and tagged when the package is first
published to a package index; until then the client is built from a checkout and
carries a development version.
