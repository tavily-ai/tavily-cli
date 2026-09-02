"""OAuth MCP transport compatibility."""

from __future__ import annotations

import json

import pytest
from tavily import TavilyKeylessLimitError

from tavily_cli import mcp_client


def test_research_options_and_status_use_remote_compatibility_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_call(token, tool_name, arguments, **kwargs):
        calls.append((tool_name, arguments))
        return {"status": "completed"}

    monkeypatch.setattr(mcp_client, "_call_mcp_tool", fake_call)
    client = mcp_client.McpTavilyClient("oauth-token")
    schema = {"properties": {"answer": {"type": "string"}}}

    client.research(input="topic", output_schema=schema, citation_format="apa")
    client.get_research("req-123")

    assert calls == [
        (
            "tavily_research",
            {"input": "topic", "output_schema": schema, "citation_format": "apa"},
        ),
        ("tavily_get_research", {"request_id": "req-123"}),
    ]


def test_keyless_mcp_limit_becomes_typed_limit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "structuredContent": {
                "code": "daily_cap_reached",
                "message": "Daily allowance reached.",
                "auth_mode": "keyless",
                "retry_after_seconds": 60,
                "next_actions": [{"action": "login"}],
            }
        },
    }

    class FakeResponse:
        text = f"data:{json.dumps(payload)}"

        def raise_for_status(self):
            return None

    monkeypatch.setattr(mcp_client.httpx, "post", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(TavilyKeylessLimitError) as exc_info:
        mcp_client._call_mcp_tool("oauth-token", "tavily_search", {"query": "topic"})

    assert exc_info.value.code == "daily_cap_reached"
    assert exc_info.value.retry_after_seconds == 60


def test_failed_research_response_is_returned_for_exit_handling(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "structuredContent": {
                "request_id": "req-failed",
                "status": "failed",
                "error": "Research failed upstream.",
            }
        },
    }

    class FakeResponse:
        text = f"data:{json.dumps(payload)}"

        def raise_for_status(self):
            return None

    monkeypatch.setattr(mcp_client.httpx, "post", lambda *args, **kwargs: FakeResponse())

    assert mcp_client._call_mcp_tool("oauth-token", "tavily_get_research", {}) == {
        "request_id": "req-failed",
        "status": "failed",
        "error": "Research failed upstream.",
    }
