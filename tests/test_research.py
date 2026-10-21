"""Tests for research transport gating.

OAuth credentials are served by the MCP endpoint, which runs research to
completion in the initial call and exposes no research-lookup tool. The async
surface (--no-wait, status, poll) therefore cannot work there.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from tavily_cli import config
from tavily_cli.cli import cli
from tavily_cli.mcp_client import McpTavilyClient


class _SdkClient:
    """Stand-in for the API-key client, which does issue request ids."""

    def __init__(self, result: dict) -> None:
        self.result = result
        self.research_calls: list[dict] = []
        self.get_research_calls: list[str] = []

    def research(self, **kwargs: object) -> dict:
        self.research_calls.append(kwargs)
        return self.result

    def get_research(self, request_id: str) -> dict:
        self.get_research_calls.append(request_id)
        return {"status": "completed", "content": "done"}


class _OauthClient(_SdkClient, McpTavilyClient):
    """The MCP transport, with the network calls replaced.

    _SdkClient comes first so its stubs win over McpTavilyClient's real ones,
    while isinstance() still identifies this as the MCP transport.
    """

    def __init__(self, result: dict) -> None:
        McpTavilyClient.__init__(self, api_key="oauth-token")
        _SdkClient.__init__(self, result)


@pytest.fixture
def install_client(monkeypatch: pytest.MonkeyPatch):
    def install(client: _SdkClient) -> _SdkClient:
        monkeypatch.setattr(config, "require_api_key_friendly", lambda *a, **k: "key")
        monkeypatch.setattr(config, "get_client", lambda *a, **k: client)
        return client

    return install


COMPLETED = {"status": "completed", "content": "# Report", "sources": []}


def test_no_wait_is_rejected_before_spending_a_research_call(install_client) -> None:
    client = install_client(_OauthClient(COMPLETED))

    result = CliRunner().invoke(cli, ["research", "some topic", "--no-wait", "--json"])

    assert result.exit_code == 2
    assert "--no-wait is not supported with browser (OAuth) authentication" in result.output
    assert "tvly login --api-key" in result.output
    # The point of the flag is not to block; running it anyway would be the bug.
    assert client.research_calls == []


def test_research_without_no_wait_still_returns_the_synchronous_report(install_client) -> None:
    client = install_client(_OauthClient(COMPLETED))

    result = CliRunner().invoke(cli, ["research", "some topic", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["content"] == "# Report"
    assert client.research_calls == [{"input": "some topic"}]


def test_status_is_rejected_on_the_oauth_transport(install_client) -> None:
    client = install_client(_OauthClient(COMPLETED))

    result = CliRunner().invoke(cli, ["research", "status", "req-123", "--json"])

    assert result.exit_code == 2
    assert "tvly research status is not supported with browser (OAuth) authentication" in result.output
    assert client.get_research_calls == []


def test_poll_is_rejected_on_the_oauth_transport(install_client) -> None:
    client = install_client(_OauthClient(COMPLETED))

    result = CliRunner().invoke(cli, ["research", "poll", "req-123", "--json"])

    assert result.exit_code == 2
    assert "tvly research poll is not supported with browser (OAuth) authentication" in result.output
    assert client.get_research_calls == []


def test_no_wait_still_returns_a_request_id_with_an_api_key(install_client) -> None:
    client = install_client(_SdkClient({"request_id": "req-123", "status": "pending"}))

    result = CliRunner().invoke(cli, ["research", "some topic", "--no-wait", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"request_id": "req-123", "status": "pending"}
    assert client.get_research_calls == []


def test_status_still_works_with_an_api_key(install_client) -> None:
    client = install_client(_SdkClient({}))

    result = CliRunner().invoke(cli, ["research", "status", "req-123", "--json"])

    assert result.exit_code == 0
    assert client.get_research_calls == ["req-123"]
