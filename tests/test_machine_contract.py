"""Stable machine output, failure exits, and explicit JSONL behavior."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from tavily import TavilyKeylessLimitError

from tavily_cli.cli import cli


class FakeResearchClient:
    def __init__(self, initial: dict, polled: dict | None = None) -> None:
        self.initial = initial
        self.polled = polled or initial
        self.research_kwargs: dict | None = None

    def research(self, **kwargs):
        self.research_kwargs = kwargs
        return self.initial

    def get_research(self, request_id: str):
        return self.polled


def _install_research_client(monkeypatch: pytest.MonkeyPatch, client: FakeResearchClient) -> None:
    monkeypatch.setattr("tavily_cli.config.require_api_key_friendly", lambda *args, **kwargs: "token")
    monkeypatch.setattr("tavily_cli.config.get_client", lambda **kwargs: client)


def test_research_timeout_is_structured_and_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_research_client(
        monkeypatch,
        FakeResearchClient({"request_id": "req-timeout", "status": "pending"}),
    )

    result = CliRunner().invoke(
        cli,
        ["research", "run", "topic", "--timeout", "0", "--json"],
    )

    assert result.exit_code == 4
    assert json.loads(result.stdout) == {
        "ok": False,
        "error": {
            "code": "research_timeout",
            "message": "Research timed out after 0s.",
            "stage": "poll",
            "retryable": True,
            "request_id": "req-timeout",
        },
    }
    assert result.stderr == ""


def test_failed_research_is_structured_and_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_research_client(
        monkeypatch,
        FakeResearchClient(
            {
                "request_id": "req-failed",
                "status": "failed",
                "error": "The research agent failed.",
            }
        ),
    )

    result = CliRunner().invoke(cli, ["research", "run", "topic", "--json"])

    assert result.exit_code == 4
    assert json.loads(result.stdout)["error"] == {
        "code": "research_failed",
        "message": "The research agent failed.",
        "stage": "submit",
        "retryable": False,
        "request_id": "req-failed",
    }


def test_research_status_failure_is_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeResearchClient({}, {"status": "failed", "error": "No result"})
    _install_research_client(monkeypatch, client)

    result = CliRunner().invoke(cli, ["research", "status", "req-status", "--json"])

    assert result.exit_code == 4
    assert json.loads(result.stdout)["error"]["stage"] == "status"
    assert json.loads(result.stdout)["error"]["request_id"] == "req-status"


def test_invalid_research_schema_is_structured_and_does_not_call_api(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema_path = tmp_path / "schema.json"
    schema_path.write_text('{"type":"object"}')
    monkeypatch.setattr(
        "tavily_cli.config.get_client",
        lambda **kwargs: pytest.fail("invalid schemas must fail before client creation"),
    )

    result = CliRunner().invoke(
        cli,
        ["research", "run", "topic", "--output-schema", str(schema_path), "--json"],
    )

    assert result.exit_code == 2
    assert json.loads(result.stdout) == {
        "ok": False,
        "error": {
            "code": "invalid_output_schema",
            "message": "Output schema must be a JSON object containing a properties object.",
            "stage": "validation",
            "retryable": False,
        },
    }


def test_research_schema_is_forwarded_to_client(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = {"properties": {"answer": {"type": "string"}}}
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps(schema))
    client = FakeResearchClient(
        {"request_id": "req-schema", "status": "completed", "content": {"answer": "done"}}
    )
    _install_research_client(monkeypatch, client)

    result = CliRunner().invoke(
        cli,
        ["research", "run", "topic", "--output-schema", str(schema_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    assert client.research_kwargs == {"input": "topic", "output_schema": schema}
    assert json.loads(result.stdout)["content"] == {"answer": "done"}


def test_research_stream_json_is_one_document(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = [
        'data:{"choices":[{"delta":{"content":"hello "}}]}',
        'data:{"choices":[{"delta":{"content":"world","sources":[{"url":"https://example.com"}]}}]}',
    ]
    _install_research_client(monkeypatch, FakeResearchClient(chunks))

    result = CliRunner().invoke(cli, ["research", "run", "topic", "--stream", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "status": "completed",
        "content": "hello world",
        "sources": [{"url": "https://example.com"}],
    }


def test_research_stream_jsonl_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    events = [
        {"choices": [{"delta": {"content": "hello"}}]},
        {"choices": [{"delta": {"content": " world"}}]},
    ]
    chunks = [f"data:{json.dumps(event)}" for event in events]
    _install_research_client(monkeypatch, FakeResearchClient(chunks))

    result = CliRunner().invoke(cli, ["research", "run", "topic", "--stream", "--jsonl"])

    assert result.exit_code == 0, result.output
    assert [json.loads(line) for line in result.stdout.splitlines()] == events


def test_extract_fail_on_partial_json_includes_data(monkeypatch: pytest.MonkeyPatch) -> None:
    response = {
        "results": [{"url": "https://good.example", "raw_content": "ok"}],
        "failed_results": [{"url": "https://bad.example", "error": "blocked"}],
    }

    class FakeExtractClient:
        def extract(self, **kwargs):
            return response

    monkeypatch.setattr(
        "tavily_cli.config.get_client_or_keyless",
        lambda **kwargs: (FakeExtractClient(), False),
    )

    result = CliRunner().invoke(
        cli,
        ["extract", "https://good.example", "https://bad.example", "--fail-on-partial", "--json"],
    )

    assert result.exit_code == 5
    payload = json.loads(result.stdout)
    assert payload["error"] == {
        "code": "extract_partial_failure",
        "message": "1 URL failed to extract.",
        "stage": "extract",
        "retryable": False,
    }
    assert payload["data"] == response


def test_extract_jsonl_has_records_summary_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeExtractClient:
        def extract(self, **kwargs):
            return {
                "results": [{"url": "https://good.example", "raw_content": "ok"}],
                "failed_results": [{"url": "https://bad.example", "error": "blocked"}],
                "response_time": 1.2,
            }

    monkeypatch.setattr(
        "tavily_cli.config.get_client_or_keyless",
        lambda **kwargs: (FakeExtractClient(), False),
    )

    result = CliRunner().invoke(
        cli,
        ["extract", "https://good.example", "--fail-on-partial", "--jsonl"],
    )

    assert result.exit_code == 5
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert [record.get("type") for record in records[:3]] == ["result", "failed_result", "summary"]
    assert records[-1]["error"]["code"] == "extract_partial_failure"


def test_json_and_jsonl_are_mutually_exclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    result = CliRunner().invoke(
        cli,
        ["extract", "https://example.com", "--json", "--jsonl"],
    )

    assert result.exit_code == 2
    assert "either --json or --jsonl" in result.stderr


def test_keyless_limit_uses_stable_error_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    class LimitedClient:
        def search(self, **kwargs):
            raise TavilyKeylessLimitError(
                "Daily allowance reached.",
                code="daily_cap_reached",
                window="day",
                retry_after_seconds=60,
                next_actions=[{"action": "login"}],
            )

    monkeypatch.setattr(
        "tavily_cli.config.get_client_or_keyless",
        lambda **kwargs: (LimitedClient(), True),
    )

    result = CliRunner().invoke(cli, ["search", "topic", "--json"])

    assert result.exit_code == 3
    assert json.loads(result.stdout) == {
        "ok": False,
        "error": {
            "code": "daily_cap_reached",
            "message": "Daily allowance reached.",
            "stage": "request",
            "retryable": True,
            "window": "day",
            "retry_after_seconds": 60,
            "next_actions": [{"action": "login"}],
        },
    }


def test_api_failure_uses_stable_error_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingClient:
        def search(self, **kwargs):
            raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(
        "tavily_cli.config.get_client_or_keyless",
        lambda **kwargs: (FailingClient(), False),
    )

    result = CliRunner().invoke(cli, ["search", "topic", "--json"])

    assert result.exit_code == 4
    assert json.loads(result.stdout) == {
        "ok": False,
        "error": {
            "code": "api_error",
            "message": "upstream unavailable",
            "stage": "request",
            "retryable": False,
        },
    }


def test_required_auth_failure_is_machine_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tavily_cli.config.get_api_key", lambda: None)

    result = CliRunner().invoke(cli, ["map", "https://example.com", "--json"])

    assert result.exit_code == 3
    assert json.loads(result.stdout) == {
        "ok": False,
        "error": {
            "code": "authentication_required",
            "message": "The map command requires authentication.",
            "stage": "auth",
            "retryable": False,
        },
    }


@pytest.mark.parametrize(
    ("args", "method_name", "response", "record_type"),
    [
        (
            ["search", "topic", "--jsonl"],
            "search",
            {"results": [{"url": "https://example.com/result"}], "response_time": 0.2},
            "result",
        ),
        (
            ["map", "https://example.com", "--jsonl"],
            "map",
            {"results": ["https://example.com/page"], "base_url": "https://example.com"},
            "url",
        ),
        (
            ["crawl", "https://example.com", "--jsonl"],
            "crawl",
            {"results": [{"url": "https://example.com/page", "raw_content": "full"}]},
            "page",
        ),
    ],
)
def test_list_commands_offer_explicit_jsonl(
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    method_name: str,
    response: dict,
    record_type: str,
) -> None:
    class FakeClient:
        pass

    client = FakeClient()
    setattr(client, method_name, lambda **kwargs: response)
    monkeypatch.setattr("tavily_cli.config.require_api_key_friendly", lambda *args, **kwargs: "token")
    monkeypatch.setattr("tavily_cli.config.get_client", lambda **kwargs: client)
    monkeypatch.setattr("tavily_cli.config.get_client_or_keyless", lambda **kwargs: (client, False))

    result = CliRunner().invoke(cli, args)

    assert result.exit_code == 0, result.output
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert records[0]["type"] == record_type
    assert records[-1]["type"] == "summary"
    assert records[-1]["data"]["result_count"] == 1
