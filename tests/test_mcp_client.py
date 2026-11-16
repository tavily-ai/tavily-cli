"""Tests for the MCP transport used with OAuth credentials."""

from __future__ import annotations

import json

import httpx
import pytest

from tavily_cli import mcp_client
from tavily_cli.common import TavilyAPIError
from tavily_cli.mcp_client import _call_mcp_tool

# Verbatim rejection returned by mcp.tavily.com (tavily-mcp 4.0.3) when the CLI
# forwards a flag the MCP tool does not declare.
UNEXPECTED_INCLUDE_ANSWER = (
    "Internal error: 1 validation error for call[tavily_search]\n"
    "include_answer\n"
    "  Unexpected keyword argument [type=unexpected_keyword_argument, "
    "input_value='basic', input_type=str]\n"
    "    For further information visit "
    "https://errors.pydantic.dev/2.13/v/unexpected_keyword_argument"
)

UNEXPECTED_TWO_ARGS = (
    "Internal error: 2 validation errors for call[tavily_research]\n"
    "citation_format\n"
    "  Unexpected keyword argument [type=unexpected_keyword_argument, "
    "input_value='numbered', input_type=str]\n"
    "output_schema\n"
    "  Unexpected keyword argument [type=unexpected_keyword_argument, "
    "input_value={}, input_type=dict]"
)


def _sse(payload: dict) -> str:
    return f"event: message\ndata: {json.dumps(payload)}\n\n"


def _respond(monkeypatch: pytest.MonkeyPatch, body: str, status: int = 200) -> None:
    def fake_post(url: str, **kwargs: object) -> httpx.Response:
        return httpx.Response(
            status,
            text=body,
            request=httpx.Request("POST", url),
            headers={"content-type": "text/event-stream"},
        )

    monkeypatch.setattr(mcp_client.httpx, "post", fake_post)


def test_unsupported_flag_is_named_with_the_api_key_route(monkeypatch: pytest.MonkeyPatch) -> None:
    _respond(monkeypatch, _sse({"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": UNEXPECTED_INCLUDE_ANSWER}}))

    with pytest.raises(TavilyAPIError) as excinfo:
        _call_mcp_tool("token", "tavily_search", {"query": "x", "include_answer": "basic"})

    message = str(excinfo.value)
    assert "--include-answer is not supported with browser (OAuth) authentication" in message
    assert "tavily_search" in message
    assert "tvly login --api-key" in message
    # The raw pydantic text is misleading as a user-facing error.
    assert "Unexpected keyword argument" not in message
    assert "Internal error" not in message


def test_multiple_unsupported_flags_are_listed_together(monkeypatch: pytest.MonkeyPatch) -> None:
    _respond(monkeypatch, _sse({"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": UNEXPECTED_TWO_ARGS}}))

    with pytest.raises(TavilyAPIError) as excinfo:
        _call_mcp_tool("token", "tavily_research", {"input": "x"})

    message = str(excinfo.value)
    assert "--citation-format, --output-schema are not supported" in message
    assert "drop the flags." in message


def test_unrelated_rpc_errors_are_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    _respond(monkeypatch, _sse({"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Tool not found"}}))

    with pytest.raises(RuntimeError, match="Tool not found") as excinfo:
        _call_mcp_tool("token", "tavily_get_research", {"request_id": "abc"})

    assert not isinstance(excinfo.value, TavilyAPIError)


def test_non_sse_rpc_errors_are_translated_too(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": UNEXPECTED_INCLUDE_ANSWER}})
    _respond(monkeypatch, body)

    with pytest.raises(TavilyAPIError, match="--include-answer"):
        _call_mcp_tool("token", "tavily_search", {"query": "x", "include_answer": "basic"})


def test_successful_call_still_returns_structured_content(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"jsonrpc": "2.0", "id": 1, "result": {"structuredContent": {"results": [{"url": "https://example.com"}]}}}
    _respond(monkeypatch, _sse(payload))

    assert _call_mcp_tool("token", "tavily_search", {"query": "x"}) == {"results": [{"url": "https://example.com"}]}
