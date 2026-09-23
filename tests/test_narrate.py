# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2021-2026 DALICC - Verein zur Foerderung der Rechtssicherheit in der Datenbewirtschaftung (ZVR 1249185710)
"""``api.translate.narrate`` and ``api.translate.narrate_model``.

The provider is stubbed the way the service's own suite stubs it: the function that
builds the provider client is replaced, so a call travels through the router and the
service without any network. What is tested here is the client side of it: that the
answer is read into a :class:`dalicc.Narration`, that the coverage of the model comes
back with it, and that the deterministic reading is reachable without a token.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from dalicc import AuthError, NotFoundError

pytest.importorskip(
    "app.services.translate.narrate",
    reason="License-to-Text is not part of this build",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
APACHE = REPO_ROOT / "licensedata" / "licenses" / "Apache-2.0.ttl"

PROVIDER_HEADERS = {
    "x-ratelimit-limit-requests": "1000",
    "x-ratelimit-remaining-requests": "997",
    "x-ratelimit-reset-requests": "2m59.56s",
    "x-ratelimit-limit-tokens": "8000",
    "x-ratelimit-remaining-tokens": "7000",
    "x-ratelimit-reset-tokens": "7.66s",
}


def _answer_for(prompt: str) -> dict:
    """The double's answer: one section naming every term the prompt carried."""
    terms = sorted(
        {
            word.strip().strip(".,;|")
            for word in prompt.split()
            if ":" in word and word.split(":", 1)[0] in {"odrl", "cc", "dalicc", "dct", "spdx"}
        }
    )
    return {
        "title": "Apache License, Version 2.0",
        "preamble": "This licence covers software, datasets and creative works.",
        "sections": [
            {
                "heading": "What you may do",
                "text": "You may copy the work, change it and share it.",
                "statements": terms,
            }
        ],
        "closing": "Read the licence itself before you rely on this.",
    }


@pytest.fixture()
def assistant(settings, monkeypatch):
    """A deployment with a key, whose provider answers from a stub."""
    from app.services.translate import provider as provider_module

    monkeypatch.setattr(settings, "groq_api_key", "test-key", raising=False)
    monkeypatch.setattr(settings, "groq_model", "test-model", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        prompt = " ".join(message.get("content", "") for message in body.get("messages") or [])
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": json.dumps(_answer_for(prompt))}}],
                "usage": {"prompt_tokens": 400, "completion_tokens": 300},
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


def test_narrate_reads_a_library_licence(authed, assistant) -> None:
    narration = authed.translate.narrate("Apache-2.0")
    assert narration.title
    assert narration.produced_by == "provider"
    assert [section.heading for section in narration.sections] == ["What you may do"]
    assert narration.complete
    assert narration.statements > 0
    assert narration.covered == narration.statements
    assert "not legal advice" in narration.closing


def test_narrate_accepts_the_full_address(authed, assistant) -> None:
    """Both identifier spellings, as every other method of the client takes them."""
    narration = authed.translate.narrate("https://dalicc.net/licenselibrary/Apache-2.0")
    assert narration.sections


def test_the_text_property_reads_as_a_document(authed, assistant) -> None:
    narration = authed.translate.narrate("Apache-2.0")
    body = narration.text
    assert body.startswith(narration.title)
    assert "1. What you may do" in body
    assert body.rstrip().endswith(narration.closing)
    assert str(narration) == body


def test_narrate_model_reads_a_model_you_hold(authed, assistant) -> None:
    narration = authed.translate.narrate_model(APACHE.read_text(encoding="utf-8"))
    assert narration.license_id == "Apache-2.0"
    assert narration.sections


def test_narrate_can_leave_the_names_out(authed, assistant) -> None:
    turtle = (
        "@prefix odrl: <http://www.w3.org/ns/odrl/2/> .\n"
        "@prefix dct: <http://purl.org/dc/terms/> .\n"
        "<https://dalicc.net/licenselibrary/Composed> a odrl:Set ;\n"
        '    dct:title "A composed licence" ;\n'
        '    dct:creator "Mia Member" ;\n'
        "    odrl:permission [ a odrl:Permission ; odrl:action odrl:distribute ] .\n"
    )
    narration = authed.translate.narrate_model(turtle, without_names=True)
    assert narration.names_removed is True
    assert "Mia Member" not in narration.text


def test_a_deployment_without_a_key_still_answers(authed, settings, monkeypatch) -> None:
    """There is always a text; ``produced_by`` says where it came from."""
    monkeypatch.setattr(settings, "groq_api_key", "", raising=False)
    narration = authed.translate.narrate("Apache-2.0")
    assert narration.produced_by == "fallback"
    assert narration.complete
    assert narration.sections


def test_a_short_text_reports_what_it_missed(authed, settings, monkeypatch) -> None:
    """The coverage of the model travels with the text, not only on the website."""
    from app.services.translate import provider as provider_module

    monkeypatch.setattr(settings, "groq_api_key", "test-key", raising=False)
    monkeypatch.setattr(settings, "groq_model", "test-model", raising=False)
    payload = {
        "title": "Apache License, Version 2.0",
        "preamble": "",
        "sections": [
            {
                "heading": "What you may do",
                "text": "You may share it.",
                "statements": ["odrl:distribute"],
            }
        ],
        "closing": "",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": json.dumps(payload)}}],
                "usage": {"prompt_tokens": 400, "completion_tokens": 100},
            },
            headers=PROVIDER_HEADERS,
        )

    stubbed = httpx.MockTransport(handler)
    real_build = provider_module.build_client
    monkeypatch.setattr(
        provider_module,
        "build_client",
        lambda db=None, current_settings=None, *, model="", transport=None,
        provider="": real_build(
            db,
            current_settings or settings,
            model="test-model",
            transport=stubbed,
            provider=provider,
        ),
    )
    narration = authed.translate.narrate("Apache-2.0")
    assert not narration.complete
    assert narration.covered == 1
    assert narration.missing
    assert narration.coverage_note
    assert narration.coverage_note in narration.text


def test_narrate_needs_a_token(api, assistant) -> None:
    with pytest.raises(AuthError):
        api.translate.narrate("Apache-2.0")


def test_narrate_reports_an_unknown_licence(authed, assistant) -> None:
    with pytest.raises(NotFoundError):
        authed.translate.narrate("no-such-licence")


def test_the_plain_reading_needs_no_token(api) -> None:
    """``format=text`` asks no provider and spends no allowance, so it is open."""
    document = api.licenses.get("Apache-2.0", format="text")
    assert "What you may do" in str(document)
    assert "not legal advice" in str(document)


def test_an_unknown_format_is_refused(api) -> None:
    with pytest.raises(ValueError, match="json-ld, ttl, rdf-xml or text"):
        api.licenses.get("Apache-2.0", format="pdf")
