# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2021-2026 DALICC - Verein zur Foerderung der Rechtssicherheit in der Datenbewirtschaftung (ZVR 1249185710)
"""The ``dalicc`` command line tool.

    dalicc search apache
    dalicc get MIT --format ttl
    dalicc check MIT GPL-3.0-only
    dalicc consistency CC-BY-4.0
    dalicc compose my-license.json
    dalicc translate terms.txt --title "Terms of use" --consent
    dalicc graphs

Every command takes ``--base-url`` and ``--api-key``, which default to
``DALICC_BASE_URL`` and ``DALICC_API_KEY``, and ``--json``, which prints the answer of
the service instead of the readable summary, so the tool can be put in a pipe.

Exit codes, so that a script can tell the cases apart:

==  ====================================================
0   the call succeeded
1   the service or the network failed
2   the command line could not be read (argparse's own)
3   authentication: no token, or one that may not do this
4   nothing with that identifier
5   the request was refused: a bad body, or a contradiction
6   a rate limit or a quota; the message says how long
==  ====================================================

Only the standard library and the client itself are used here: no extra dependency
comes in through the command line tool.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
import sys
from typing import Any

from dalicc import __version__, errors
from dalicc.client import DEFAULT_BASE_URL, Client

__all__ = ["main"]

#: What the shell learns from the exit code.
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_AUTH = 3
EXIT_NOT_FOUND = 4
EXIT_REFUSED = 5
EXIT_RATE_LIMITED = 6


def build_parser() -> argparse.ArgumentParser:
    """The argument parser, which is also what ``dalicc --help`` prints."""
    parser = argparse.ArgumentParser(
        prog="dalicc",
        description=(
            "Search the DALICC license library, read a license, check licenses for "
            "conflicts and compose new ones."
        ),
        epilog="Nothing this tool returns is legal advice.",
    )
    parser.add_argument("--version", action="version", version=f"dalicc {__version__}")
    parser.add_argument(
        "--base-url",
        default=None,
        help=f"The deployment to call. Default: DALICC_BASE_URL, else {DEFAULT_BASE_URL}",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="A personal API token (account page, API tokens). Default: DALICC_API_KEY",
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="Seconds to wait for an answer."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print the answer of the service instead of a summary.",
    )
    commands = parser.add_subparsers(dest="command", metavar="command", required=True)

    search = commands.add_parser("search", help="Find licenses by a word in their title.")
    search.add_argument("keyword", nargs="?", default=None, help="A word of the title.")
    search.add_argument("--limit", type=int, default=20, help="How many rows to print.")
    search.add_argument(
        "--ports",
        choices=("include", "exclude", "only"),
        default="include",
        help="Jurisdiction ports: include them, leave them out, or list only them.",
    )
    search.set_defaults(run=_search)

    get = commands.add_parser("get", help="Print one license document.")
    get.add_argument("license_id", help="An identifier (MIT) or a full DALICC IRI.")
    get.add_argument(
        "--format",
        choices=("json-ld", "ttl", "rdf-xml"),
        default="json-ld",
        help="The serialisation to ask for.",
    )
    get.add_argument("--version", type=int, default=None, help="An earlier version of the model.")
    get.set_defaults(run=_get)

    check = commands.add_parser("check", help="Check two or more licenses for conflicts.")
    check.add_argument("licenses", nargs="+", help="Identifiers or full DALICC IRIs.")
    check.add_argument(
        "--graph", default=None, help="The dependency graph to reason with (id or IRI)."
    )
    check.set_defaults(run=_check)

    consistency = commands.add_parser(
        "consistency", help="Check one license for contradictions within itself."
    )
    consistency.add_argument("license_id", help="An identifier or a full DALICC IRI.")
    consistency.add_argument(
        "--graph", default=None, help="The dependency graph to reason with (id or IRI)."
    )
    consistency.set_defaults(run=_consistency)

    compose = commands.add_parser("compose", help="Compose a license from a file (needs a token).")
    compose.add_argument("file", help="A composer document as .json, or one odrl:Set as .ttl.")
    compose.add_argument(
        "--draft",
        action="store_true",
        help="Store a private draft instead of publishing.",
    )
    compose.set_defaults(run=_compose)

    translate = commands.add_parser(
        "translate", help="Read a licence text and propose a model (needs a token)."
    )
    translate.add_argument("file", help="A text file with the licence text.")
    translate.add_argument("--title", default=None, help="Title for the result.")
    translate.add_argument(
        "--consent",
        action="store_true",
        help="Required: the text is sent to an external model provider for processing.",
    )
    translate.add_argument(
        "--save-draft",
        action="store_true",
        help="Store the proposal as a private draft owned by your account.",
    )
    translate.set_defaults(run=_translate)

    graphs = commands.add_parser("graphs", help="List the dependency graphs you may see.")
    graphs.add_argument(
        "--axioms",
        default=None,
        metavar="GRAPH",
        help="Print the statements of one graph instead of the list.",
    )
    graphs.set_defaults(run=_graphs)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command and return the exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        with Client(
            base_url=args.base_url, api_key=args.api_key, timeout=args.timeout
        ) as api:
            return int(args.run(api, args))
    except errors.RateLimitedError as limited:
        _fail(_rate_limit_message(limited))
        return EXIT_RATE_LIMITED
    except (errors.AuthError, errors.ForbiddenError) as refused:
        _fail(str(refused.detail))
        if isinstance(refused, errors.AuthError):
            _fail("Create a token on /account/tokens and pass it as --api-key.")
        return EXIT_AUTH
    except errors.NotFoundError as missing:
        _fail(str(missing.detail))
        return EXIT_NOT_FOUND
    except (errors.ValidationError, errors.ConflictError, errors.PayloadTooLarge) as refused:
        _fail(str(refused.detail))
        for conflict in getattr(refused, "conflicts", []):
            _fail("  " + str(conflict.get("reason", "")))
        return EXIT_REFUSED
    except errors.DaliccError as failure:
        _fail(str(failure))
        return EXIT_FAILED
    except ValueError as refused:
        _fail(str(refused))
        return EXIT_REFUSED
    except OSError as failure:
        _fail(str(failure))
        return EXIT_FAILED


# ---------------------------------------------------------------------------
# the commands
# ---------------------------------------------------------------------------


def _search(api: Client, args: argparse.Namespace) -> int:
    """Print the licenses whose title matches."""
    listing = api.licenses.list(keyword=args.keyword, limit=args.limit, ports=args.ports)
    if args.as_json:
        return _print_json(listing.raw)
    if not listing.licenses:
        print("No license matches.")
        return EXIT_OK
    width = max(len(ref.id) for ref in listing.licenses)
    for ref in listing.licenses:
        print(f"{ref.id.ljust(width)}  {ref.title}")
    print(f"\n{len(listing.licenses)} licenses.")
    return EXIT_OK


def _get(api: Client, args: argparse.Namespace) -> int:
    """Print one license document, or one version of it."""
    if args.version is None:
        document = api.licenses.get(args.license_id, format=args.format)
    else:
        document = api.licenses.version(args.license_id, args.version, format=args.format)
    if isinstance(document, dict):
        return _print_json(dict(document))
    print(str(document).rstrip())
    return EXIT_OK


def _check(api: Client, args: argparse.Namespace) -> int:
    """Check a set of licenses for conflicts."""
    verdict = api.reasoning.compatibility(args.licenses, dependency_graph=args.graph)
    if args.as_json:
        return _print_json(verdict.raw)
    if verdict.dependency_graph:
        print(f"Dependency graph: {verdict.dependency_graph}")
    if not verdict.has_conflicts:
        print("No conflicts between: " + ", ".join(args.licenses))
        return EXIT_OK
    print(f"{len(verdict.conflicts)} conflicts:")
    for conflict in verdict.conflicts:
        print(f"  [{conflict.kind}] {conflict.reason}")
        if conflict.statement_1:
            print("    " + " ".join(conflict.statement_1))
            print("    " + " ".join(conflict.statement_2))
    return EXIT_OK


def _consistency(api: Client, args: argparse.Namespace) -> int:
    """Check one license for contradictions within itself."""
    verdict = api.reasoning.consistency(args.license_id, dependency_graph=args.graph)
    if args.as_json:
        return _print_json(verdict.raw)
    if verdict.consistent:
        print(f"{args.license_id} does not contradict itself.")
        return EXIT_OK
    print(f"{args.license_id} contradicts itself:")
    for conflict in verdict.conflicts:
        print(f"  {conflict.reason}")
    return EXIT_OK


def _compose(api: Client, args: argparse.Namespace) -> int:
    """Compose a license from a JSON or Turtle file."""
    path = Path(args.file)
    text = path.read_text(encoding="utf-8")
    document: Any
    if path.suffix.lower() in {".ttl", ".turtle"}:
        document = text
    else:
        try:
            document = json.loads(text)
        except ValueError as broken:
            raise ValueError(f"{path} is not valid JSON: {broken}") from broken
    result = api.composing.create(document, publish=not args.draft)
    if args.as_json:
        return _print_json(result.raw)
    print(f"{result.status}: {result.uri}")
    print(f"Identifier: {result.id} (keep it, a composed license is unlisted)")
    return EXIT_OK


def _translate(api: Client, args: argparse.Namespace) -> int:
    """Read a licence text and print the proposed model."""
    if not args.consent:
        raise ValueError(
            "Add --consent. The text is sent to an external model provider for "
            "processing; do not submit confidential texts."
        )
    path = Path(args.file)
    result = api.translate.text(
        path.read_text(encoding="utf-8"),
        title=args.title if args.title is not None else path.stem,
        save_draft=args.save_draft,
        consent=True,
    )
    if args.as_json:
        return _print_json(result.raw)
    print(f"Title: {result.title or '(none)'}")
    if result.parts_total > 1:
        print(f"Parts translated: {result.parts_done} of {result.parts_total}")
    if result.partial:
        print("Parts that failed: " + ", ".join(str(part) for part in result.parts_failed))
    print(f"Statements: {len(result.statements)}")
    for statement in result.statements:
        mark = "" if statement.known_term else " (not in the vocabulary)"
        print(f"  {statement.kind}: {statement.term} [{statement.confidence:.2f}]{mark}")
        if statement.evidence:
            print(f"      {_shorten(statement.evidence)}")
    for clause in result.unmodelled:
        print(f"  unmodelled: {clause.proposed_term or '(no term proposed)'}")
        print(f"      {_shorten(clause.clause_quote)}")
    for warning in result.warnings:
        print(f"  warning: {warning}")
    for conflict in result.conflicts:
        print(f"  conflict: {conflict.reason}")
    if result.draft_id:
        print(f"Saved as draft {result.draft_id}: {result.draft_uri}")
    print(_quota_line(result))
    print("Nothing was published. Review every statement against the text.")
    return EXIT_OK


def _graphs(api: Client, args: argparse.Namespace) -> int:
    """List the dependency graphs, or print the statements of one."""
    if args.axioms is not None:
        axioms = api.dependency_graphs.list_axioms(graph=args.axioms or None)
        if args.as_json:
            return _print_json(axioms.raw)
        for axiom in axioms:
            print(f"{axiom.subject} {axiom.predicate} {axiom.object}")
        print(f"\n{len(axioms)} statements.")
        return EXIT_OK
    listing = api.dependency_graphs.graphs()
    if args.as_json:
        return _print_json(listing.raw)
    if not listing.graphs:
        print("No dependency graph is visible to you.")
        return EXIT_OK
    for graph in listing.graphs:
        marks = [graph.kind, graph.status]
        if graph.default:
            marks.append("default")
        if graph.role:
            marks.append(graph.role)
        print(f"{graph.id}  {graph.title}  ({', '.join(part for part in marks if part)})")
        print(f"    {graph.axioms} statements, version {graph.version}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# printing
# ---------------------------------------------------------------------------


def _print_json(payload: Any) -> int:
    """Print a body as indented JSON."""
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False))
    return EXIT_OK


def _fail(message: str) -> None:
    """Write one line to standard error."""
    sys.stderr.write(message.rstrip() + "\n")


def _shorten(text: str, width: int = 100) -> str:
    """One line of a quotation, cut where it gets too long."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= width else flat[: width - 3] + "..."


def _rate_limit_message(limited: errors.RateLimitedError) -> str:
    """What to tell somebody who ran into a limit, with the countdown."""
    parts = [limited.detail]
    seconds = int(limited.retry_after or limited.reset_in or 0)
    if seconds > 0:
        parts.append(f"Try again in {_countdown(seconds)}.")
    if limited.window:
        parts.append(f"Window: {limited.window}.")
    return " ".join(part for part in parts if part)


def _countdown(seconds: int) -> str:
    """Seconds as a readable countdown."""
    if seconds < 60:
        return f"{seconds} seconds"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} minutes and {rest} seconds"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} hours and {minutes} minutes"


def _quota_line(result: Any) -> str:
    """The one line about what is left of the assistant's allowance."""
    quota = result.quota
    pieces = []
    if quota.provider_remaining_requests is not None:
        of_total = (
            f" of {quota.provider_limit_requests}"
            if quota.provider_limit_requests is not None
            else ""
        )
        pieces.append(f"Provider requests left: {quota.provider_remaining_requests}{of_total}")
    if quota.provider_reset_in is not None:
        pieces.append(f"resets in {_countdown(int(quota.provider_reset_in))}")
    if quota.user_remaining_today is not None:
        pieces.append(f"your translations left today: {quota.user_remaining_today}")
    return "; ".join(pieces) if pieces else "Quota: not reported."


if __name__ == "__main__":  # pragma: no cover - the console script calls main()
    raise SystemExit(main())
