"""Tests for set-default / unset-default."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from tavily_cli.cli import cli
from tavily_cli.commands import defaults as defaults_module


@pytest.fixture
def homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    return tmp_path / ".claude" / "settings.json", tmp_path / ".codex" / "config.toml"


def test_set_default_creates_both_files(homes: tuple[Path, Path]) -> None:
    claude, codex = homes
    result = CliRunner().invoke(cli, ["set-default", "--claude", "--codex", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(claude.read_text()) == {"permissions": {"deny": ["WebSearch", "WebFetch"]}}
    assert codex.read_text() == 'web_search = "disabled"\n'
    assert all(r["changed"] for r in json.loads(result.stdout)["results"])


def test_claude_round_trip_preserves_existing_settings(homes: tuple[Path, Path]) -> None:
    claude, _ = homes
    claude.parent.mkdir()
    original = {"model": "opus", "permissions": {"allow": ["Bash(ls)"], "deny": ["Read(.env)", "WebFetch"]}}
    claude.write_text(json.dumps(original))

    assert defaults_module.set_claude_default(claude)
    assert json.loads(claude.read_text())["permissions"]["deny"] == ["Read(.env)", "WebFetch", "WebSearch"]
    assert not defaults_module.set_claude_default(claude)

    assert defaults_module.unset_claude_default(claude)
    assert json.loads(claude.read_text()) == {
        "model": "opus",
        "permissions": {"allow": ["Bash(ls)"], "deny": ["Read(.env)"]},
    }
    assert not defaults_module.unset_claude_default(claude)


def test_claude_unset_drops_empty_permissions(homes: tuple[Path, Path]) -> None:
    claude, _ = homes
    claude.parent.mkdir()
    claude.write_text(json.dumps({"model": "opus"}))

    defaults_module.set_claude_default(claude)
    defaults_module.unset_claude_default(claude)

    assert json.loads(claude.read_text()) == {"model": "opus"}


def test_claude_invalid_json_is_not_overwritten(homes: tuple[Path, Path]) -> None:
    claude, _ = homes
    claude.parent.mkdir()
    claude.write_text("{not json")

    result = CliRunner().invoke(cli, ["set-default", "--claude"])

    assert result.exit_code != 0
    assert claude.read_text() == "{not json"


def test_codex_round_trip_keeps_tables(homes: tuple[Path, Path]) -> None:
    _, codex = homes
    codex.parent.mkdir()
    original = 'model = "gpt-5"\n\n[profiles.work]\nweb_search = "live"\n'
    codex.write_text(original)

    assert defaults_module.set_codex_default(codex)
    assert codex.read_text() == 'web_search = "disabled"\n' + original
    assert not defaults_module.set_codex_default(codex)

    assert defaults_module.unset_codex_default(codex)
    assert codex.read_text() == original
    assert not defaults_module.unset_codex_default(codex)


def test_codex_set_replaces_existing_top_level_value(homes: tuple[Path, Path]) -> None:
    _, codex = homes
    codex.parent.mkdir()
    codex.write_text('model = "gpt-5"\nweb_search = "live"\n')

    assert defaults_module.set_codex_default(codex)
    assert codex.read_text() == 'model = "gpt-5"\nweb_search = "disabled"\n'


def test_prompts_when_no_flags(homes: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    claude, codex = homes
    # CliRunner swaps sys.stdin during invoke, so stub the module-level reference.
    monkeypatch.setattr(defaults_module, "sys", SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True)))

    result = CliRunner().invoke(cli, ["set-default"], input="y\nn\n")

    assert result.exit_code == 0, result.output
    assert "[Y/n]" in result.output
    assert claude.exists()
    assert not codex.exists()


def test_no_flags_non_interactive_is_usage_error(homes: tuple[Path, Path]) -> None:
    result = CliRunner().invoke(cli, ["unset-default"])

    assert result.exit_code == 2
    assert "--claude" in result.output


def test_project_scope_writes_to_cwd(
    homes: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_claude, global_codex = homes
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    result = CliRunner().invoke(cli, ["set-default", "--claude", "--codex", "--project", "--json"])

    assert result.exit_code == 0, result.output
    claude = project / ".claude" / "settings.local.json"
    codex = project / ".codex" / "config.toml"
    assert json.loads(claude.read_text()) == {"permissions": {"deny": ["WebSearch", "WebFetch"]}}
    assert codex.read_text() == 'web_search = "disabled"\n'
    assert not global_claude.exists()
    assert not global_codex.exists()
    assert {r["scope"] for r in json.loads(result.stdout)["results"]} == {"project"}

    result = CliRunner().invoke(cli, ["unset-default", "--claude", "--codex", "--project"])

    assert result.exit_code == 0, result.output
    assert json.loads(claude.read_text()) == {}
    assert codex.read_text() == ""
