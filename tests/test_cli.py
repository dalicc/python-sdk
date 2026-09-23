# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2021-2026 DALICC - Verein zur Foerderung der Rechtssicherheit in der Datenbewirtschaftung (ZVR 1249185710)
"""The ``dalicc`` command line tool, driven through ``main()``.

The tool builds its own client, so the ASGI transport is injected by replacing the
class it builds: the command then runs against the application in this process,
exactly as it would against a deployment.
"""

from __future__ import annotations

import functools
import json

import pytest

from dalicc import Client, cli
from dalicc._transport import ASGITransport

LIB = "https://dalicc.net/licenselibrary/"


@pytest.fixture()
def run(client, monkeypatch):
    """Run one command line against the application; returns (code, out, err)."""
    monkeypatch.setattr(
        cli, "Client", functools.partial(Client, transport=ASGITransport(client.app))
    )

    def call(*argv: str, capsys=None):
        return cli.main(["--base-url", "http://testserver", *argv])

    return call


def test_search_prints_a_table_and_a_count(run, capsys) -> None:
    assert run("search", "apache") == 0
    printed = capsys.readouterr().out
    assert "Apache-2.0" in printed
    assert printed.rstrip().endswith("licenses.")


def test_search_without_a_keyword_lists_the_library(run, capsys) -> None:
    assert run("search") == 0
    assert "MIT" in capsys.readouterr().out


def test_search_can_print_the_envelope(run, capsys) -> None:
    assert run("--json", "search", "mit") == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["results"]["bindings"]


def test_get_prints_json_ld_by_default(run, capsys) -> None:
    assert run("get", "MIT") == 0
    document = json.loads(capsys.readouterr().out)
    assert "@graph" in document


def test_get_prints_turtle_when_asked(run, capsys) -> None:
    assert run("get", "MIT", "--format", "ttl") == 0
    assert capsys.readouterr().out.startswith("@prefix")


def test_get_accepts_a_full_iri(run, capsys) -> None:
    assert run("get", LIB + "MIT") == 0
    assert "@graph" in capsys.readouterr().out


def test_get_can_print_an_earlier_version(run, capsys) -> None:
    assert run("get", "MIT", "--version", "1", "--format", "ttl") == 0
    assert "@prefix" in capsys.readouterr().out


def test_an_unknown_id_exits_with_the_not_found_code(run, capsys) -> None:
    assert run("get", "NOPE-123") == cli.EXIT_NOT_FOUND
    assert "Unknown license id" in capsys.readouterr().err


def test_check_reports_no_conflicts(run, capsys, monkeypatch) -> None:
    _stub_reasoner(monkeypatch, {"conflicting_statements": {"direct": {}, "derived": {}}})
    assert run("check", "MIT", "Apache-2.0") == 0
    assert "No conflicts" in capsys.readouterr().out


def test_check_prints_every_conflict(run, capsys, monkeypatch) -> None:
    _stub_reasoner(
        monkeypatch,
        {
            "conflicting_statements": {
                "direct": {
                    "0": {
                        "statement_1": [LIB + "A", "odrl:permission", "x"],
                        "statement_2": [LIB + "B", "odrl:prohibition", "x"],
                        "reason": "Direct permission-prohibition conflict.",
                    }
                },
                "derived": {},
            }
        },
    )
    assert run("check", "Apache-2.0", "GPL-3.0-only") == 0
    printed = capsys.readouterr().out
    assert "1 conflicts" in printed
    assert "[direct] Direct permission-prohibition conflict." in printed


def test_consistency_reports_a_clean_license(run, capsys) -> None:
    assert run("consistency", "CC-BY-4.0") == 0
    assert "does not contradict itself" in capsys.readouterr().out


def test_graphs_lists_the_core_graph(run, capsys) -> None:
    assert run("graphs") == 0
    printed = capsys.readouterr().out
    assert "dg_default" in printed
    assert "core" in printed


def test_graphs_can_print_the_statements(run, capsys) -> None:
    assert run("graphs", "--axioms", "dg_default") == 0
    printed = capsys.readouterr().out
    assert "odrl/2/" in printed
    assert printed.rstrip().endswith("statements.")


# ---------------------------------------------------------------------------
# the commands that need a token
# ---------------------------------------------------------------------------


def test_composing_without_a_token_exits_with_the_auth_code(run, capsys, tmp_path) -> None:
    document = tmp_path / "license.json"
    document.write_text(json.dumps({"title": "x", "targets": ["dataset"]}), encoding="utf-8")
    assert run("compose", str(document)) == cli.EXIT_AUTH
    printed = capsys.readouterr().err
    assert "API token" in printed
    assert "/account/tokens" in printed


def test_composing_publishes_and_prints_the_identifier(run, capsys, tmp_path, token) -> None:
    document = tmp_path / "license.json"
    document.write_text(
        json.dumps(
            {
                "title": "Terms from the command line",
                "targets": ["dataset"],
                "permissions": [{"action": "odrl:distribute", "duties": ["cc:Attribution"]}],
            }
        ),
        encoding="utf-8",
    )
    assert run("--api-key", token, "compose", str(document)) == 0
    printed = capsys.readouterr().out
    assert "published:" in printed
    assert "unlisted" in printed


def test_a_draft_stays_a_draft(run, capsys, tmp_path, token) -> None:
    document = tmp_path / "license.json"
    document.write_text(json.dumps({"title": "A draft", "targets": ["dataset"]}), encoding="utf-8")
    assert run("--api-key", token, "compose", str(document), "--draft") == 0
    assert "draft:" in capsys.readouterr().out


def test_a_file_that_is_not_json_is_refused_with_a_reason(run, capsys, tmp_path, token) -> None:
    document = tmp_path / "license.json"
    document.write_text("{not json", encoding="utf-8")
    assert run("--api-key", token, "compose", str(document)) == cli.EXIT_REFUSED
    assert "not valid JSON" in capsys.readouterr().err


def test_a_license_that_contradicts_itself_exits_with_the_refused_code(
    run, capsys, tmp_path, token
) -> None:
    document = tmp_path / "license.json"
    document.write_text(
        json.dumps(
            {
                "title": "Contradiction",
                "permissions": [{"action": "odrl:distribute", "duties": []}],
                "prohibitions": ["odrl:distribute"],
            }
        ),
        encoding="utf-8",
    )
    assert run("--api-key", token, "compose", str(document)) == cli.EXIT_REFUSED
    printed = capsys.readouterr().err
    assert "contradicting statements" in printed


def test_translating_without_consent_is_refused_before_anything_is_sent(
    run, capsys, tmp_path, token
) -> None:
    text = tmp_path / "terms.txt"
    text.write_text("Permission is hereby granted.", encoding="utf-8")
    assert run("--api-key", token, "translate", str(text)) == cli.EXIT_REFUSED
    printed = capsys.readouterr().err
    assert "--consent" in printed
    assert "external model provider" in printed


# ---------------------------------------------------------------------------
# the rate-limit message
# ---------------------------------------------------------------------------


def test_a_rate_limit_is_reported_with_a_countdown(monkeypatch, capsys) -> None:
    import httpx

    body = {
        "detail": "You have used your DALICC API allowance for this minute.",
        "window": "minute",
        "limit": 60,
        "remaining": 0,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json=body, headers={"Retry-After": "95"})

    monkeypatch.setattr(
        cli, "Client", functools.partial(Client, transport=httpx.MockTransport(handler))
    )
    assert cli.main(["search", "mit"]) == cli.EXIT_RATE_LIMITED
    printed = capsys.readouterr().err
    assert "Try again in 1 minutes and 35 seconds." in printed
    assert "Window: minute." in printed


def test_the_countdown_is_readable_at_every_scale() -> None:
    assert cli._countdown(19) == "19 seconds"
    assert cli._countdown(95) == "1 minutes and 35 seconds"
    assert cli._countdown(7200) == "2 hours and 0 minutes"


# ---------------------------------------------------------------------------
# the parser itself
# ---------------------------------------------------------------------------


def test_the_version_is_printed(capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["--version"])
    assert raised.value.code == 0
    assert "dalicc" in capsys.readouterr().out


def test_an_unknown_command_is_a_usage_error(capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["fly"])
    assert raised.value.code == cli.EXIT_USAGE


def test_no_command_at_all_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main([])
    assert raised.value.code == cli.EXIT_USAGE


def test_the_help_text_is_free_of_long_dashes() -> None:
    text = cli.build_parser().format_help()
    # The two long dashes, written as escapes so that this file does not carry one.
    assert chr(0x2013) not in text
    assert chr(0x2014) not in text


def _stub_reasoner(monkeypatch, payload) -> None:
    """Answer the solver call the way the reasoner would."""
    from app.routers import compatibilitycheck

    class _Answer:
        status_code = 200
        text = json.dumps(payload)

        def json(self):
            return payload

    monkeypatch.setattr(
        compatibilitycheck.requests, "post", lambda url, **kwargs: _Answer()
    )
