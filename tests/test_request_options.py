"""Offline tests for search/extract/crawl/map options and client_name transport.

The SDK path is checked at requests.Session.post and the OAuth path at httpx.post, so the assertions
cover the headers and JSON body that would leave the machine. No network and no real credentials.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from tavily_cli import config as config_module
from tavily_cli.cli import cli

API_KEY = "tvly-test-key-not-real"


def _jwt() -> str:
    def part(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part({'sub': 'tester', 'exp': 4102444800})}.sig"


class FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


@pytest.fixture
def sdk_requests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Capture SDK HTTP calls; isolate config and environment."""
    monkeypatch.setattr(config_module, "CONFIG_FILE", tmp_path / "config.json")
    for name in ("TAVILY_CLIENT_NAME", "TAVILY_HUMAN_ID", "TAVILY_API_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", API_KEY)
    sent: list[dict] = []

    def post(self, url, data=None, headers=None, **kwargs):
        merged = {k.lower(): v for k, v in {**dict(self.headers), **(headers or {})}.items()}
        sent.append({"url": url, "headers": merged, "body": json.loads(data)})
        return FakeResponse({"query": "q", "results": [], "failed_results": [], "response_time": 0.1, "base_url": url})

    import requests

    monkeypatch.setattr(requests.Session, "post", post)
    return sent


@pytest.fixture
def oauth_requests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    monkeypatch.setattr(config_module, "CONFIG_FILE", tmp_path / "config.json")
    for name in ("TAVILY_CLIENT_NAME", "TAVILY_HUMAN_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", _jwt())
    sent: list[dict] = []

    class Response:
        text = 'data: {"jsonrpc":"2.0","id":1,"result":{"structuredContent":{"results":[],"query":"q"}}}'

        def raise_for_status(self) -> None:
            return None

    def post(url, json=None, headers=None, **kwargs):
        sent.append({"url": url, "headers": {k.lower(): v for k, v in headers.items()}, "body": json})
        return Response()

    import httpx

    monkeypatch.setattr(httpx, "post", post)
    return sent


def run(*args: str):
    return CliRunner().invoke(cli, list(args))


# ── Search options on the SDK path ──────────────────────────────────────────


def test_search_new_options_reach_the_request_body(sdk_requests: list[dict]) -> None:
    result = run(
        "search", "q", "--depth", "basic", "--chunks-per-source", "2",
        "--include-domains", "a.com,b.com", "--include-domains-mode", "restrict",
        "--language", "english", "--filter-by-language", "--safe-search", "--exact-match",
        "--auto-parameters", "--include-favicon", "--include-usage",
        "--include-published-date", "--filter-by-published-date", "--json",
    )

    assert result.exit_code == 0, result.output
    body = sdk_requests[0]["body"]
    assert body["chunks_per_source"] == 2
    assert body["include_domains"] == ["a.com", "b.com"]
    assert body["include_domains_mode"] == "restrict"
    assert body["language"] == "english" and body["filter_by_language"] is True
    assert body["safe_search"] is True and body["exact_match"] is True
    assert body["auto_parameters"] is True and body["include_favicon"] is True
    assert body["include_usage"] is True
    assert body["include_published_date"] is True and body["filter_by_published_date"] is True


def test_search_options_are_omitted_when_not_given(sdk_requests: list[dict]) -> None:
    assert run("search", "q", "--json").exit_code == 0
    assert sdk_requests[0]["body"] == {"query": "q"}


@pytest.mark.parametrize(
    "args, message",
    [
        (["--include-domains-mode", "prefer"], "requires --include-domains"),
        (["--filter-by-language"], "requires --language"),
        (["--safe-search", "--depth", "fast"], "not available with --depth fast or ultra-fast"),
        (["--chunks-per-source", "4"], "4 is not in the range 1<=x<=3"),
        (["--max-results", "21"], "21 is not in the range 0<=x<=20"),
    ],
)
def test_search_invalid_combinations_fail_before_any_request(sdk_requests: list[dict], args: list[str], message: str) -> None:
    result = run("search", "q", *args)

    assert result.exit_code == 2
    assert message in result.output
    assert sdk_requests == []


def test_search_chunks_per_source_is_allowed_with_basic_depth(sdk_requests: list[dict]) -> None:
    assert run("search", "q", "--depth", "basic", "--chunks-per-source", "3", "--json").exit_code == 0
    assert sdk_requests[0]["body"]["chunks_per_source"] == 3


# ── Extract / crawl / map ───────────────────────────────────────────────────


def test_extract_new_options_reach_the_request_body(sdk_requests: list[dict]) -> None:
    result = run("extract", "https://example.com", "--query", "pricing", "--chunks-per-source", "5",
                 "--include-favicon", "--include-usage", "--timeout", "60", "--json")

    assert result.exit_code == 0, result.output
    body = sdk_requests[0]["body"]
    assert body["include_favicon"] is True and body["include_usage"] is True
    assert body["chunks_per_source"] == 5 and body["timeout"] == 60


@pytest.mark.parametrize("args, message", [
    (["--chunks-per-source", "3"], "requires --query"),
    (["--timeout", "0.5"], "0.5 is not in the range 1<=x<=60"),
    (["--timeout", "61"], "61.0 is not in the range 1<=x<=60"),
])
def test_extract_invalid_values_fail_before_any_request(sdk_requests: list[dict], args: list[str], message: str) -> None:
    result = run("extract", "https://example.com", *args)

    assert result.exit_code == 2 and message in result.output
    assert sdk_requests == []


def test_crawl_and_map_usage_flags_reach_the_request_body(sdk_requests: list[dict]) -> None:
    assert run("crawl", "https://example.com", "--include-favicon", "--include-usage", "--json").exit_code == 0
    assert run("map", "https://example.com", "--include-usage", "--json").exit_code == 0

    assert sdk_requests[0]["body"]["include_favicon"] is True and sdk_requests[0]["body"]["include_usage"] is True
    assert sdk_requests[1]["body"]["include_usage"] is True


# ── client_name precedence and transport ────────────────────────────────────


def test_client_name_flag_beats_env_and_config(sdk_requests: list[dict], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"client_name": "from-config"}))
    monkeypatch.setenv("TAVILY_CLIENT_NAME", "from-env")

    assert run("search", "q", "--client-name", "from-flag", "--json").exit_code == 0

    assert sdk_requests[0]["headers"]["x-client-name"] == "from-flag"


def test_client_name_env_beats_config(sdk_requests: list[dict], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"client_name": "from-config"}))
    monkeypatch.setenv("TAVILY_CLIENT_NAME", "from-env")

    assert run("search", "q", "--json").exit_code == 0

    assert sdk_requests[0]["headers"]["x-client-name"] == "from-env"


def test_client_name_config_is_used_when_nothing_else_is_set(sdk_requests: list[dict], tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"client_name": "from-config"}))

    assert run("search", "q", "--json").exit_code == 0

    assert sdk_requests[0]["headers"]["x-client-name"] == "from-config"


def test_no_client_name_header_when_unset_and_source_stays_the_cli(sdk_requests: list[dict]) -> None:
    assert run("search", "q", "--json").exit_code == 0

    headers = sdk_requests[0]["headers"]
    assert "x-client-name" not in headers
    assert headers["x-client-source"] == "tavily-cli"


def test_keyless_path_resolves_client_name_and_keeps_keyless_source(sdk_requests: list[dict], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY")
    monkeypatch.setattr(config_module, "_get_mcp_token", lambda: None)
    monkeypatch.setenv("TAVILY_CLIENT_NAME", "from-env")

    assert run("search", "q", "--json").exit_code == 0

    headers = sdk_requests[0]["headers"]
    assert headers["x-client-name"] == "from-env"
    assert headers["x-client-source"] == "tavily-cli-keyless"


def test_blank_env_client_name_is_ignored(sdk_requests: list[dict], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_CLIENT_NAME", "   ")

    assert run("search", "q", "--json").exit_code == 0

    assert "x-client-name" not in sdk_requests[0]["headers"]


def test_oauth_path_sends_resolved_client_name_with_the_cli_source(oauth_requests: list[dict], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_CLIENT_NAME", "from-env")

    assert run("search", "q", "--json").exit_code == 0
    assert run("search", "q", "--client-name", "from-flag", "--json").exit_code == 0

    assert oauth_requests[0]["headers"]["x-client-name"] == "from-env"
    assert oauth_requests[1]["headers"]["x-client-name"] == "from-flag"
    assert oauth_requests[0]["headers"]["x-client-source"] == "tavily-cli"


def test_oauth_path_rejects_api_key_only_options_by_flag_name(oauth_requests: list[dict]) -> None:
    result = run("search", "q", "--language", "english", "--json")

    assert result.exit_code != 0
    assert "--language is not available with OAuth login" in result.output
    assert oauth_requests == []


def test_oauth_path_still_accepts_options_the_hosted_tool_supports(oauth_requests: list[dict]) -> None:
    assert run("search", "q", "--exact-match", "--include-favicon", "--json").exit_code == 0

    arguments = oauth_requests[0]["body"]["params"]["arguments"]
    assert arguments["exact_match"] is True and arguments["include_favicon"] is True
