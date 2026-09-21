"""Usage requests, credential boundaries and optional status summaries."""

import json

import httpx
import pytest
from click.testing import CliRunner

from tavily_cli import config
from tavily_cli.cli import cli

USAGE = {
    "key": {"usage": 150, "limit": 1000, "search_usage": 100},
    "account": {"current_plan": "Bootstrap", "plan_usage": 500, "plan_limit": 15000,
                "paygo_usage": 25, "paygo_limit": 100},
}


@pytest.fixture
def usage_api(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "get_api_key", lambda: "tvly-test-secret")
    monkeypatch.setattr(config, "get_api_base_url", lambda: "https://api.example.test/")

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, json=USAGE, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    return calls


@pytest.mark.parametrize("args", [["usage", "--json"], ["--json", "usage"]])
def test_usage_json_returns_api_fields_and_uses_bearer_auth(usage_api, args):
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0
    assert json.loads(result.stdout) == USAGE
    assert usage_api[0][0] == "https://api.example.test/usage"
    assert usage_api[0][1]["headers"]["Authorization"] == "Bearer tvly-test-secret"
    assert usage_api[0][1]["timeout"] <= 10
    assert "tvly-test-secret" not in result.output


def test_usage_human_shows_plan_key_and_paygo(usage_api):
    result = CliRunner().invoke(cli, ["usage"])
    assert result.exit_code == 0
    for text in ["Bootstrap", "500 / 15,000", "150 / 1,000", "PAYGO", "25 / 100"]:
        assert text in result.stdout


@pytest.mark.parametrize(("key", "mode"), [(None, "keyless"), ("oauth-token", "oauth")])
def test_usage_never_sends_keyless_or_oauth_credentials(monkeypatch, key, mode):
    monkeypatch.setattr(config, "get_api_key", lambda: key)
    monkeypatch.setattr(config, "is_oauth_token", lambda value: value == "oauth-token")
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: pytest.fail("Usage network request must not be made"))
    result = CliRunner().invoke(cli, ["usage", "--json"])
    assert result.exit_code == 3
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "usage_requires_api_key"
    assert error["mode"] == mode
    assert "tvly login --api-key" in error["message"]
    assert "oauth-token" not in result.output


@pytest.mark.parametrize(("status", "code", "retryable"), [(401, "authentication_failed", False), (429, "api_limit_reached", True), (500, "api_error", True)])
def test_usage_http_errors_use_machine_contract(monkeypatch, usage_api, status, code, retryable):
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(status, json={"detail": {"error": "Failure"}}, request=httpx.Request("GET", url)))
    result = CliRunner().invoke(cli, ["usage", "--json"])
    assert result.exit_code != 0
    error = json.loads(result.stdout)["error"]
    assert error["code"] == code
    assert error["stage"] == "usage"
    assert error["retryable"] is retryable


@pytest.mark.parametrize("body", [[], {"key": []}, {"account": {}}, {"key": {}, "account": {"plan_usage": "oops"}}])
def test_usage_rejects_invalid_response_shapes(monkeypatch, usage_api, body):
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(200, json=body, request=httpx.Request("GET", url)))
    result = CliRunner().invoke(cli, ["usage", "--json"])
    assert result.exit_code == 4
    assert "invalid" in json.loads(result.stdout)["error"]["message"].lower()


def test_usage_does_not_turn_missing_values_into_zero(monkeypatch, usage_api):
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(200, json={"key": {}, "account": {}}, request=httpx.Request("GET", url)))
    result = CliRunner().invoke(cli, ["usage"])
    assert result.exit_code == 0
    assert "not reported" in result.stdout
    assert "0 /" not in result.stdout


@pytest.mark.parametrize("args", [["--status", "--json"], ["status", "--json"]])
def test_status_includes_usage_for_api_key(usage_api, args):
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["mode"] == "api_key"
    assert payload["usage"] == USAGE
    assert len(usage_api) == 1


def test_status_keeps_auth_state_when_usage_is_unreachable(monkeypatch, usage_api):
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("Network timeout")

    monkeypatch.setattr(httpx, "get", timeout)
    result = CliRunner().invoke(cli, ["status", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["authenticated"] is True
    assert payload["usage"] is None
    assert payload["usage_error"]["retryable"] is True
    assert "tvly-test-secret" not in result.output
    result = CliRunner().invoke(cli, ["usage", "--json"])
    assert result.exit_code == 4
    assert json.loads(result.stdout)["error"]["retryable"] is True


def test_oauth_status_explains_why_usage_is_unavailable(monkeypatch):
    monkeypatch.setattr(config, "get_api_key", lambda: "oauth-token")
    monkeypatch.setattr(config, "is_oauth_token", lambda key: True)
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: pytest.fail("OAuth usage must not call the API"))
    result = CliRunner().invoke(cli, ["status", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["mode"] == "oauth"
    assert payload["usage"] is None
    assert "API key" in payload["usage_unavailable_reason"]
