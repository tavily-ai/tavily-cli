"""Keyless status and actionable CLI rate-limit guidance."""

import copy
import json
from urllib.parse import parse_qs, urlparse

import pytest
from click.testing import CliRunner
from tavily import TavilyKeylessLimitError

from tavily_cli.cli import cli
from tavily_cli.commands import auth
from tavily_cli.keyless import format_keyless_envelope_for_terminal


@pytest.mark.parametrize("args", [["auth"], ["--status"], ["status"]])
def test_keyless_status_explains_available_commands(monkeypatch, args):
    monkeypatch.setattr(auth, "get_api_key", lambda: None)
    monkeypatch.setattr("tavily_cli.config.get_api_key", lambda: None)
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0
    assert "Keyless" in result.stdout
    assert "search and extract" in result.stdout
    assert "cap" in result.stdout
    assert "tvly login" in result.stdout
    assert "Not authenticated" not in result.stdout


@pytest.mark.parametrize("args", [["auth", "--json"], ["--status", "--json"], ["status", "--json"], ["--json", "status"]])
def test_keyless_status_has_an_explicit_json_mode(monkeypatch, args):
    monkeypatch.setattr(auth, "get_api_key", lambda: None)
    monkeypatch.setattr("tavily_cli.config.get_api_key", lambda: None)
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["authenticated"] is False
    assert payload["mode"] == "keyless"
    assert "\n  " in result.stdout


@pytest.mark.parametrize(("seconds", "duration"), [(40, "40s"), (60, "1m"), (70, "1m 10s"), (120, "2m")])
def test_retry_duration_is_not_duplicated_or_truncated(seconds, duration):
    output = format_keyless_envelope_for_terminal(
        message="Allowance reached. Retry after the time in the Retry-After response header.",
        retry_after_seconds=seconds,
        next_actions=[{"type": "agentic_payment", "scheme": "x402", "details": "TBD"}],
    )
    assert f"Retry after: {duration}\n" in output
    assert f"({seconds}s)" not in output
    assert "Retry-After" not in output
    assert "TBD" not in output
    assert "x402" not in output


@pytest.mark.parametrize("output_flag", ["--json", "--jsonl"])
def test_cap_json_has_cli_actions_without_losing_server_details(monkeypatch, output_flag):
    actions = [
        {"type": "signup", "url": "https://tavily.com", "instructions": "Set the Authorization header."},
        {"type": "agentic_payment", "scheme": "x402", "details": "TBD"},
        {"type": "bonus_credits", "eligible": True, "questions": ["Use case?"], "credits_on_completion": 20},
    ]
    original = copy.deepcopy(actions)

    class LimitedClient:
        def search(self, **kwargs):
            raise TavilyKeylessLimitError(
                "Hourly allowance reached.", code="hourly_cap_reached", window="hour",
                retry_after_seconds=40, next_actions=actions,
            )

    monkeypatch.setattr("tavily_cli.config.get_client_or_keyless", lambda **kwargs: (LimitedClient(), True))
    result = CliRunner().invoke(cli, ["search", "topic", output_flag])
    assert result.exit_code == 3
    error = json.loads(result.stdout)["error"]
    assert error["window"] == "hour"
    assert error["retry_after_seconds"] == 40
    assert error["retryable"] is True
    assert error["code"] == "hourly_cap_reached"
    signup = next(a for a in error["next_actions"] if a.get("type") == "signup")
    url = urlparse(signup["url"])
    assert url.netloc == "app.tavily.com"
    assert parse_qs(url.query)["utm_source"] == ["tavily-cli"]
    assert any(a.get("command") == "tvly login" for a in error["next_actions"])
    assert "tvly login --api-key" in result.stdout
    assert "Authorization header" not in result.stdout
    assert "TBD" not in result.stdout
    assert actions[-1] in error["next_actions"]
    assert actions == original
    if output_flag == "--jsonl":
        assert len(result.stdout.splitlines()) == 1


def test_auth_refresh_failure_does_not_report_keyless(monkeypatch):
    from tavily_cli.oauth import OAuthError

    def expired():
        raise OAuthError("Refresh failed")

    monkeypatch.setattr("tavily_cli.config.get_api_key", expired)
    result = CliRunner().invoke(cli, ["status", "--json"])
    assert result.exit_code == 3
    assert json.loads(result.stdout)["error"]["code"] == "oauth_refresh_failed"
