"""Tests for top-level and interactive CLI guidance."""

from __future__ import annotations

import json
from io import StringIO

import pytest
from click.testing import CliRunner
from rich.console import Console

from tavily_cli import repl
from tavily_cli.cli import cli


def test_cli_help_promotes_guided_setup_and_browser_login() -> None:
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "First-time setup: tvly init" in result.stdout
    assert "Browser authentication: tvly login" in result.stdout
    assert "API-key authentication: tvly login --api-key" in result.stdout


def test_bare_cli_shows_overview_without_auth_or_shell(monkeypatch) -> None:
    def unexpected(*args, **kwargs):
        pytest.fail("Startup must not authenticate or enter the shell")

    monkeypatch.setattr(repl, "run_repl", unexpected)
    monkeypatch.setattr("tavily_cli.config.get_api_key", unexpected)
    result = CliRunner().invoke(cli, [])
    assert result.exit_code == 0
    for command in cli.commands:
        assert command in result.stdout
    assert "tvly shell" in result.stdout
    assert "Usage:" in result.stdout
    assert "\x1b" not in result.stdout


def test_bare_json_is_machine_readable() -> None:
    result = CliRunner().invoke(cli, ["--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["commands"] == sorted(cli.commands)
    assert result.stderr == ""


def test_shell_is_explicit(monkeypatch) -> None:
    called = []
    monkeypatch.setattr(repl, "run_repl", lambda: called.append(True))
    result = CliRunner().invoke(cli, ["shell"])
    assert result.exit_code == 0
    assert called == [True]


def test_repl_help_lists_init_and_update(monkeypatch: pytest.MonkeyPatch) -> None:
    output = StringIO()
    monkeypatch.setattr(repl, "err_console", Console(file=output, force_terminal=False, width=120))

    repl._print_help()

    rendered = output.getvalue()
    assert "init" in rendered
    assert "Authenticate, install Tavily skills" in rendered
    assert "update" in rendered
    assert "Check for or install the latest Tavily CLI release" in rendered


def test_repl_startup_promotes_init_and_browser_login(monkeypatch: pytest.MonkeyPatch) -> None:
    output = StringIO()
    monkeypatch.setattr(repl, "err_console", Console(file=output, force_terminal=False, width=120))
    monkeypatch.setattr(repl, "_prompt", lambda: "exit")

    repl.run_repl()

    rendered = output.getvalue()
    assert "First-time setup: tvly init" in rendered
    assert "Browser authentication: tvly login" in rendered
    assert "update" in rendered
