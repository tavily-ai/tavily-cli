"""Offline tests for `tvly research` options, HTTP 201 creation and client_name transport.

The SDK path is checked at requests.Session.post/get and the OAuth path at httpx.post, so the
assertions cover the headers and JSON body that would leave the machine. No network, no credentials.
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

CREATED = {"request_id": "req-1", "status": "pending", "input": "q", "model": "mini"}
DONE = {"request_id": "req-1", "status": "completed", "content": "the report", "sources": [{"title": "t", "url": "https://e.x"}]}


class FakeResponse:
    text = ""

    def __init__(self, status: int, payload: dict) -> None:
        self.status_code = status
        self._payload = payload

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def run(*args: str):
    return CliRunner().invoke(cli, list(args))


@pytest.fixture
def research_requests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """SDK path; creation answers HTTP 201 like the public API, status polling answers 200."""
    monkeypatch.setattr(config_module, "CONFIG_FILE", tmp_path / "config.json")
    for name in ("TAVILY_CLIENT_NAME", "TAVILY_HUMAN_ID", "TAVILY_API_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", API_KEY)
    sent: list[dict] = []

    def post(self, url, data=None, headers=None, **kwargs):
        merged = {k.lower(): v for k, v in {**dict(self.headers), **(headers or {})}.items()}
        sent.append({"method": "POST", "url": url, "headers": merged, "body": json.loads(data)})
        return FakeResponse(201, CREATED)

    def get(self, url, headers=None, **kwargs):
        merged = {k.lower(): v for k, v in {**dict(self.headers), **(headers or {})}.items()}
        sent.append({"method": "GET", "url": url, "headers": merged})
        return FakeResponse(200, DONE)

    import requests

    monkeypatch.setattr(requests.Session, "post", post)
    monkeypatch.setattr(requests.Session, "get", get)
    return sent


@pytest.fixture
def oauth_research(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    monkeypatch.setattr(config_module, "CONFIG_FILE", tmp_path / "config.json")
    for name in ("TAVILY_CLIENT_NAME", "TAVILY_HUMAN_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", _jwt())
    sent: list[dict] = []

    class Response:
        text = "data: " + json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"structuredContent": DONE}})

        def raise_for_status(self) -> None:
            return None

    def post(url, json=None, headers=None, **kwargs):
        sent.append({"url": url, "headers": {k.lower(): v for k, v in headers.items()}, "body": json})
        return Response()

    import httpx

    monkeypatch.setattr(httpx, "post", post)
    return sent


def _posts(sent: list[dict]) -> list[dict]:
    return [s for s in sent if s["method"] == "POST"]


def test_creation_answered_with_http_201_is_polled_to_completion(research_requests: list[dict]) -> None:
    result = run("research", "topic", "--json", "--poll-interval", "0")

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["content"] == "the report"
    assert [s["method"] for s in research_requests] == ["POST", "GET"]
    assert research_requests[1]["url"].endswith("/research/req-1")


def test_no_wait_returns_the_request_id_after_a_201_creation(research_requests: list[dict]) -> None:
    result = run("research", "topic", "--no-wait", "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"request_id": "req-1", "status": "pending"}
    assert len(research_requests) == 1


def test_new_options_reach_the_request_body(research_requests: list[dict], tmp_path: Path) -> None:
    notes = tmp_path / "notes.md"
    notes.write_text("# notes\nsome words here")
    data = tmp_path / "data.json"
    data.write_text('{"a": 1}')

    result = run(
        "research", "topic", "--json", "--no-wait",
        "--include-domains", "sec.gov, ec.europa.eu", "--exclude-domains", "reddit.com",
        "--output-length", "long", "--file", str(notes), "--file", str(data),
        "--model", "pro", "--citation-format", "apa",
    )

    assert result.exit_code == 0, result.output
    body = _posts(research_requests)[0]["body"]
    assert body["input"] == "topic" and body["model"] == "pro" and body["citation_format"] == "apa"
    assert body["include_domains"] == ["sec.gov", "ec.europa.eu"]
    assert body["exclude_domains"] == ["reddit.com"]
    assert body["output_length"] == "long"
    assert [f["name"] for f in body["files"]] == ["notes.md", "data.json"]
    assert all(f["type"] == "base64" for f in body["files"])
    assert base64.b64decode(body["files"][0]["data"]).decode() == "# notes\nsome words here"


def test_options_are_omitted_when_not_given(research_requests: list[dict]) -> None:
    assert run("research", "topic", "--json", "--no-wait").exit_code == 0

    body = _posts(research_requests)[0]["body"]
    for key in ("include_domains", "exclude_domains", "output_length", "files"):
        assert key not in body


@pytest.mark.parametrize(
    "args,message",
    [
        (["--include-domains", ",".join(f"d{i}.com" for i in range(21))], "at most 20 domains"),
        (["--exclude-domains", ",".join(f"d{i}.com" for i in range(21))], "at most 20 domains"),
        (["--output-length", "huge"], "Invalid value"),
    ],
)
def test_invalid_values_fail_before_any_request(research_requests: list[dict], args: list[str], message: str) -> None:
    result = run("research", "topic", "--json", *args)

    assert result.exit_code != 0
    assert message in result.output
    assert research_requests == []


def test_file_validation_fails_before_any_request(research_requests: list[dict], tmp_path: Path) -> None:
    good = tmp_path / "a.txt"
    good.write_text("x")
    bad_type = tmp_path / "a.pdf"
    bad_type.write_text("x")
    big = tmp_path / "big.txt"
    big.write_text("word " * 80_001)

    assert "supported types" in run("research", "t", "--json", "--file", str(bad_type)).output
    assert "80000 per file" in run("research", "t", "--json", "--file", str(big)).output
    assert "at most 5 times" in run("research", "t", "--json", *sum((["--file", str(good)] for _ in range(6)), [])).output
    assert "No such file" in run("research", "t", "--json", "--file", str(tmp_path / "missing.md")).output
    assert research_requests == []


def test_client_name_flag_beats_env_and_config_on_research(research_requests: list[dict], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"api_key": API_KEY, "client_name": "from-config"}))
    monkeypatch.setenv("TAVILY_CLIENT_NAME", "from-env")

    assert run("research", "t", "--json", "--no-wait").exit_code == 0
    assert run("research", "t", "--json", "--no-wait", "--client-name", "from-flag").exit_code == 0

    posts = _posts(research_requests)
    assert posts[0]["headers"]["x-client-name"] == "from-env"
    assert posts[1]["headers"]["x-client-name"] == "from-flag"
    assert posts[0]["headers"]["x-client-source"] == "tavily-cli"


def test_status_and_poll_send_the_client_name(research_requests: list[dict], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_CLIENT_NAME", "from-env")

    assert run("research", "status", "req-1", "--json").exit_code == 0
    assert run("research", "poll", "req-1", "--json", "--poll-interval", "0").exit_code == 0

    assert [s["headers"]["x-client-name"] for s in research_requests] == ["from-env", "from-env"]


@pytest.mark.parametrize(
    "flag,value,expected",
    [
        ("--include-domains", "sec.gov", "--include-domains"),
        ("--exclude-domains", "reddit.com", "--exclude-domains"),
        ("--output-length", "long", "--output-length"),
        ("--citation-format", "apa", "--citation-format"),
    ],
)
def test_oauth_path_rejects_options_the_hosted_research_tool_cannot_send(oauth_research: list[dict], flag: str, value: str, expected: str) -> None:
    result = run("research", "topic", "--json", flag, value)

    assert result.exit_code != 0
    assert f"{expected} is not available with OAuth login" in result.output
    assert oauth_research == []


def test_oauth_path_rejects_file_stream_and_schema(oauth_research: list[dict], tmp_path: Path) -> None:
    note = tmp_path / "n.md"
    note.write_text("x")
    schema = tmp_path / "s.json"
    schema.write_text('{"type": "object"}')

    assert "--file is not available with OAuth login" in run("research", "t", "--json", "--file", str(note)).output
    assert "--output-schema is not available with OAuth login" in run("research", "t", "--json", "--output-schema", str(schema)).output
    assert "--stream is not available with OAuth login" in run("research", "t", "--stream", "--json").output
    assert oauth_research == []


def test_oauth_path_still_sends_model_and_client_name(oauth_research: list[dict], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_CLIENT_NAME", "from-env")

    result = run("research", "topic", "--json", "--model", "pro")

    assert result.exit_code == 0, result.output
    call = oauth_research[0]
    assert call["body"]["params"]["name"] == "tavily_research"
    assert call["body"]["params"]["arguments"] == {"input": "topic", "model": "pro"}
    assert call["headers"]["x-client-name"] == "from-env"
    assert call["headers"]["x-client-source"] == "tavily-cli"
