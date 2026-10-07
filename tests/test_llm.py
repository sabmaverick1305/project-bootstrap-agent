"""parse_intent runs through the real Anthropic SDK against a fake HTTP transport."""

import json

import anthropic
import httpx2
import pytest

from app.agent import llm
from app.agent.llm import LLMError, parse_intent, propose_repair

DRAFT = {
    "is_project_request": True,
    "project_name": None,
    "requested": [
        {"category": "language", "value": "python", "version": None},
        {"category": "framework", "value": "fastapi", "version": None},
    ],
    "clarification_question": None,
}


def _message(text: str, stop_reason: str = "end_turn") -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": llm.MODEL,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


def _client(response: httpx2.Response, captured: list | None = None) -> anthropic.Anthropic:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if captured is not None:
            captured.append(request)
        return response

    return anthropic.Anthropic(
        api_key="test-key",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )


def test_parse_intent_returns_validated_draft():
    client = _client(httpx2.Response(200, json=_message(json.dumps(DRAFT))))

    draft = parse_intent("Setup a Python + FastAPI project for me", client)

    assert draft.is_project_request
    assert [(i.category, i.value) for i in draft.requested] == [
        ("language", "python"),
        ("framework", "fastapi"),
    ]


def test_request_shape():
    captured = []
    client = _client(httpx2.Response(200, json=_message(json.dumps(DRAFT))), captured)

    parse_intent("  Setup a Python + FastAPI project  ", client)

    request = captured[0]
    body = json.loads(request.content)
    assert body["model"] == "claude-opus-5-5"
    assert body["fallbacks"] == "default"
    assert llm.FALLBACK_BETA in request.headers["anthropic-beta"]
    assert body["output_config"]["effort"] == "medium"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["system"] == llm.SYSTEM_PROMPT
    user_text = body["messages"][0]["content"]
    assert "<request>\nSetup a Python + FastAPI project\n</request>" in user_text
    assert "PARSE INTENT" in user_text


def test_empty_request_is_rejected_without_calling_api():
    captured = []
    with pytest.raises(LLMError, match="empty"):
        parse_intent("   ", _client(httpx2.Response(500), captured))
    assert captured == []


@pytest.mark.parametrize(
    ("text", "stop_reason", "message"),
    [
        ("", "refusal", "valid structured result"),  # empty reply fails inside parse()
        ('{"is_project_request": tr', "max_tokens", "valid structured result"),
        (json.dumps(DRAFT), "refusal", "declined"),  # parsed, but the stop reason is a refusal
        (json.dumps(DRAFT), "max_tokens", "cut off"),
    ],
)
def test_unusable_responses(text, stop_reason, message):
    client = _client(httpx2.Response(200, json=_message(text, stop_reason)))
    with pytest.raises(LLMError, match=message):
        parse_intent("Build a FastAPI app", client)


@pytest.mark.parametrize(
    ("status", "message"),
    [(401, "credentials"), (429, "rate limit"), (500, "HTTP 500")],
)
def test_api_errors_become_llm_errors(status, message):
    error = {"type": "error", "error": {"type": "api_error", "message": "boom"}}
    client = _client(httpx2.Response(status, json=error))
    with pytest.raises(LLMError, match=message):
        parse_intent("Build a FastAPI app", client)


def test_propose_repair_request_shape():
    proposal = {
        "diagnosis": "Typo in import",
        "can_repair": True,
        "edits": [{"path": "src/fastapi_app/main.py", "content": "from fastapi import FastAPI\n"}],
    }
    captured = []
    client = _client(httpx2.Response(200, json=_message(json.dumps(proposal))), captured)

    result = propose_repair("<failed_step>boom</failed_step>", client)

    assert result.can_repair and result.edits[0].path == "src/fastapi_app/main.py"
    body = json.loads(captured[0].content)
    assert body["output_config"]["effort"] == "high"
    assert body["fallbacks"] == "default"
    content = body["messages"][0]["content"]
    assert content.startswith("<failed_step>boom</failed_step>")
    assert "DIAGNOSE AND REPAIR" in content
