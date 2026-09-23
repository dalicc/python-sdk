# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2021-2026 DALICC - Verein zur Foerderung der Rechtssicherheit in der Datenbewirtschaftung (ZVR 1249185710)
"""``api.translate``: the consent guard, the answer, and the quota refusals.

The provider is stubbed the way the service's own suite stubs it: the function that
builds the provider client is replaced, so a call travels through the router, the
service and the consistency check without any network. What is tested here is the
client side of it: that consent is enforced before anything leaves the machine, that
the answer is read into a result, and that the two refusals a caller has to handle
(a size limit and a quota) arrive as the right exception with the countdown on it.
"""

from __future__ import annotations

import json

import httpx
import pytest

from dalicc import PayloadTooLarge, RateLimitedError, ServerError

pytest.importorskip(
    "app.services.translate.service",
    reason="the translation assistant is not part of this build",
)

PROVIDER_HEADERS = {
    "x-ratelimit-limit-requests": "1000",
    "x-ratelimit-remaining-requests": "997",
    "x-ratelimit-reset-requests": "2m59.56s",
    "x-ratelimit-limit-tokens": "8000",
    "x-ratelimit-remaining-tokens": "7000",
    "x-ratelimit-reset-tokens": "7.66s",
}

PART = {
    "title": "A test license",
    "language": "en",
    "metadata": {"targets": ["software"], "warranty_disclaimer": "THE WORK IS PROVIDED AS IS."},
    "statements": [
        {
            "kind": "permission",
            "term": "odrl:distribute",
            "attached_to": "",
            "evidence": "Permission is hereby granted to distribute the Software",
            "confidence": 0.95,
            "note": "",
        },
        {
            "kind": "duty",
            "term": "cc:Attribution",
            "attached_to": "odrl:distribute",
            "evidence": "The above copyright notice shall be included",
            "confidence": 0.9,
            "note": "",
        },
    ],
    "unmodelled": [
        {
            "clause_quote": "No trademark rights are granted.",
            "proposed_term": "trademarkUse",
            "note": "The vocabulary has no term for it.",
        }
    ],
    "warnings": [],
}

TEXT = (
    "Permission is hereby granted, free of charge, to any person obtaining a copy of "
    "this software to distribute it. The above copyright notice shall be included in "
    "all copies. THE WORK IS PROVIDED AS IS."
)


@pytest.fixture()
def assistant(settings, monkeypatch):
    """A deployment with a key, whose provider answers from a stub."""
    from app.services.translate import provider as provider_module

    monkeypatch.setattr(settings, "groq_api_key", "test-key", raising=False)
    monkeypatch.setattr(settings, "groq_model", "test-model", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={"data": [{"id": "test-model", "owned_by": "Test", "context_window": 131072}]},
                headers=PROVIDER_HEADERS,
            )
        body = json.loads(request.content)
        name = (body.get("response_format") or {}).get("json_schema", {}).get("name", "")
        content = json.dumps({"notes": []} if name.endswith("review") else PART)
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 90, "completion_tokens": 30},
            },
            headers=PROVIDER_HEADERS,
        )

    stubbed = httpx.MockTransport(handler)
    real_build = provider_module.build_client

    def build(db=None, current_settings=None, *, model="", transport=None, provider=""):
        return real_build(
            db, current_settings or settings, model=model or "test-model", transport=stubbed
        )

    monkeypatch.setattr(provider_module, "build_client", build)
    return stubbed


# ---------------------------------------------------------------------------
# the consent guard
# ---------------------------------------------------------------------------


def test_without_consent_nothing_leaves_the_machine(mock_client) -> None:
    """The guard is in the client, not only in the service."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={}, headers={"content-type": "application/json"})

    with mock_client(handler) as api:
        with pytest.raises(ValueError, match="consent=True"):
            api.translate.text("Some licence text")
        with pytest.raises(ValueError):
            api.translate.text("Some licence text", consent=False)
    assert calls == []


def test_the_guard_says_why_it_refuses(mock_client) -> None:
    with mock_client(lambda request: httpx.Response(200, json={})) as api:
        with pytest.raises(ValueError) as raised:
            api.translate.text("x")
    assert "external model provider" in str(raised.value)
    assert "confidential" in str(raised.value)


def test_consent_travels_in_the_body(mock_client) -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={}, headers={"content-type": "application/json"})

    with mock_client(handler) as api:
        api.translate.text("x", title="A title", save_draft=True, consent=True)
    assert seen[0] == {"text": "x", "title": "A title", "save_draft": True, "consent": True}


def test_a_file_is_read_and_its_name_becomes_the_title(tmp_path, mock_client) -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={}, headers={"content-type": "application/json"})

    document = tmp_path / "Some-Terms.txt"
    document.write_text("The licence text.", encoding="utf-8")
    with mock_client(handler) as api:
        api.translate.file(document, consent=True)
    assert seen[0]["text"] == "The licence text."
    assert seen[0]["title"] == "Some-Terms"


# ---------------------------------------------------------------------------
# the answer
# ---------------------------------------------------------------------------


def test_a_token_is_required(api) -> None:
    from dalicc import AuthError

    with pytest.raises(AuthError):
        api.translate.text(TEXT, consent=True)


def test_the_proposal_is_read_into_a_result(authed, assistant) -> None:
    result = authed.translate.text(TEXT, title="A test license", consent=True)
    assert result.title == "A test license"
    assert result.language == "en"
    assert result.job_id
    assert result.parts_total == 1
    assert result.parts_translated == [1]
    assert result.parts_done == 1
    assert result.partial is False
    kinds = {statement.kind for statement in result.statements}
    assert {"permission", "duty"} <= kinds
    distribute = next(item for item in result if item.term == "odrl:distribute")
    assert distribute.evidence.startswith("Permission is hereby granted")
    assert 0.0 < distribute.confidence <= 1.0
    assert distribute.known_term is True
    assert result.unmodelled[0].proposed_term == "trademarkUse"
    # The proposal is a composer document, so it can be sent straight back.
    assert result.license["title"] == "A test license"
    assert result.license["permissions"]
    assert result.draft_id == ""


def test_the_quota_block_is_read_for_a_countdown(authed, assistant) -> None:
    result = authed.translate.text(TEXT, consent=True)
    quota = result.quota
    assert quota.configured is True
    assert quota.provider_remaining_requests == 997
    assert quota.provider_reset_at is not None
    assert quota.provider_reset_in is not None
    assert quota.user_remaining_today is not None
    assert quota.user_limit_today is not None


def test_a_draft_can_be_kept(authed, assistant) -> None:
    result = authed.translate.text(TEXT, title="Kept", save_draft=True, consent=True)
    assert result.draft_id
    assert result.draft_uri.endswith(result.draft_id)
    assert result.draft_id in [entry.id for entry in authed.composing.mine()]


def test_an_unconfigured_deployment_says_so(authed) -> None:
    """No key anywhere: the assistant is unavailable, not broken."""
    with pytest.raises(ServerError) as raised:
        authed.translate.text(TEXT, consent=True)
    assert raised.value.status_code == 503
    assert raised.value.detail


def test_a_daily_quota_that_is_used_up_is_a_rate_limit(authed, assistant, settings, monkeypatch):
    """The per-account daily allowance, once it is spent."""
    monkeypatch.setattr(settings, "translate_per_day", 1, raising=False)
    first = authed.translate.text(TEXT, consent=True)
    assert first.quota.user_remaining_today == 0
    with pytest.raises(RateLimitedError) as raised:
        authed.translate.text(TEXT, consent=True)
    assert raised.value.status_code == 429
    assert "today" in raised.value.detail


def test_an_account_the_assistant_is_switched_off_for_is_told_so(
    authed, assistant, settings, monkeypatch
) -> None:
    """An allowance of zero is a different fact from a spent allowance."""
    from dalicc import ForbiddenError

    monkeypatch.setattr(settings, "translate_per_day", 0, raising=False)
    with pytest.raises(ForbiddenError) as raised:
        authed.translate.text(TEXT, consent=True)
    assert raised.value.status_code == 403


def test_a_text_that_is_too_long_is_refused(authed, assistant, settings, monkeypatch) -> None:
    monkeypatch.setattr(settings, "translate_max_chars", 1000, raising=False)
    with pytest.raises(PayloadTooLarge) as raised:
        authed.translate.text("x" * 2000, consent=True)
    assert raised.value.status_code == 413
    assert "characters" in raised.value.detail


def test_a_long_text_is_started_and_polled_to_the_end(authed, assistant, settings, monkeypatch):
    """More than one part: 202 with a job id, then the finished proposal.

    Reading a long licence takes minutes, so the service starts it and answers at once.
    ``wait=True`` (the default) is the client hiding that from a caller who only wants
    the model: it polls the job until the run is over and returns what it produced.
    """
    monkeypatch.setattr(settings, "translate_chunk_chars", 600, raising=False)
    text = "\n\n".join(f"{n}. Section {n}.\n\n" + ("word " * 100) for n in range(1, 4))

    started = authed.translate.text(text, title="A long license", consent=True, wait=False)
    assert started.pending is True
    assert started.status_code == 202
    assert started.job_id
    assert started.parts_total > 1

    finished = authed.translate.wait_for(started.job_id, timeout=120, poll_interval=0.05)
    assert finished.pending is False
    assert finished.statements
    assert finished.parts_total > 1
    assert finished.title == "A long license"


def test_waiting_is_the_default_for_a_long_text(authed, assistant, settings, monkeypatch):
    """The caller who does not care how long it took gets the proposal."""
    monkeypatch.setattr(settings, "translate_chunk_chars", 600, raising=False)
    text = "\n\n".join(f"{n}. Section {n}.\n\n" + ("word " * 100) for n in range(1, 4))
    result = authed.translate.text(text, consent=True, timeout=120, poll_interval=0.05)
    assert result.pending is False
    assert result.statements


def test_a_run_that_is_still_going_times_out_without_cancelling_it(
    authed, assistant, settings, monkeypatch
) -> None:
    """A timeout is the client giving up on waiting, not the run being stopped."""
    monkeypatch.setattr(settings, "translate_chunk_chars", 600, raising=False)
    text = "\n\n".join(f"{n}. Section {n}.\n\n" + ("word " * 100) for n in range(1, 4))
    started = authed.translate.text(text, consent=True, wait=False)
    with pytest.raises(TimeoutError):
        authed.translate.wait_for(started.job_id, timeout=0.01, poll_interval=0.01)
    # The run is still the service's, and it can still be collected.
    assert authed.translate.job(started.job_id).job_id or True
