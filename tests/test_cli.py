"""Tests for top-level and interactive CLI guidance."""

from __future__ import annotations

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


def test_repl_help_lists_init_and_update(monkeypatch: pytest.MonkeyPatch) -> None:
    output = StringIO()
    monkeypatch.setattr(repl, "err_console", Console(file=output, force_terminal=False, width=120))

    repl._print_help()

    rendered = output.getvalue()
    assert "init" in rendered
    assert "Guided setup and skill installation" in rendered
    assert "update" in rendered
    assert "Check for or install CLI updates" in rendered


def test_repl_banner_promotes_init_and_browser_login(monkeypatch: pytest.MonkeyPatch) -> None:
    output = StringIO()
    monkeypatch.setattr(repl, "err_console", Console(file=output, force_terminal=False, width=120))
    monkeypatch.setattr(repl, "get_api_key", lambda: None)

    repl._print_banner()

    rendered = output.getvalue()
    assert "Type init for guided setup and skill installation." in rendered
    assert "Type login for browser authentication only." in rendered
    assert "update" in rendered
