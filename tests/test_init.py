"""Tests for one-shot CLI and agent skill setup."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from tavily_cli import init_skills as init_skills_module
from tavily_cli.cli import cli
from tavily_cli.commands import init as init_module
from tavily_cli.init_skills import SkillsInstallResult, detect_agents, install_agent_skills

SKILLS = ("tavily-search", "tavily-research")


def test_detect_agents_only_returns_existing_markers(tmp_path: Path) -> None:
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".config" / "opencode").mkdir(parents=True)

    assert detect_agents(tmp_path) == ("codex",)


def test_detect_agents_honors_custom_agent_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    default_home = tmp_path / "home"
    codex_home = tmp_path / "custom-codex"
    claude_home = tmp_path / "custom-claude"
    for path in (default_home, codex_home, claude_home):
        path.mkdir()
    monkeypatch.setattr(Path, "home", lambda: default_home)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))

    assert detect_agents() == ("claude-code", "codex")


def _fake_npx_install(home: Path, calls: list[list[str]], *, returncode: int = 0, content: str = ""):
    """Mimic the skills.sh CLI writing skills to the shared directory."""

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if returncode == 0:
            for skill_name in SKILLS:
                skill_dir = home / ".agents" / "skills" / skill_name
                skill_dir.mkdir(parents=True, exist_ok=True)
                (skill_dir / "SKILL.md").write_text(f"---\nname: {skill_name}\n---\n{content}")
        return subprocess.CompletedProcess(command, returncode, "", "Error: repo not found\n")

    return run


def test_agent_skills_install_runs_npx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(init_skills_module.shutil, "which", lambda name: "/usr/bin/npx")
    calls: list[list[str]] = []
    monkeypatch.setattr(init_skills_module.subprocess, "run", _fake_npx_install(tmp_path, calls))

    agents = ("claude-code", "codex", "cursor")
    first = install_agent_skills(agents)

    command = calls[0]
    assert command[:5] == ["/usr/bin/npx", "--yes", "skills@1.7.4", "add", "tavily-ai/skills"]
    assert command[command.index("--agent") + 1 : command.index("--agent") + 4] == list(agents)
    assert "--global" in command and command.count("--yes") == 2
    assert all(" " not in arg and '"' not in arg for arg in command)
    assert first.restart_required

    second = install_agent_skills(agents)

    assert not second.restart_required

    monkeypatch.setattr(init_skills_module.subprocess, "run", _fake_npx_install(tmp_path, calls, content="new"))
    third = install_agent_skills(agents)

    assert third.restart_required


def test_agent_skills_install_counts_new_agent_links(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(init_skills_module.shutil, "which", lambda name: "/usr/bin/npx")
    calls: list[list[str]] = []
    monkeypatch.setattr(init_skills_module.subprocess, "run", _fake_npx_install(tmp_path, calls))
    install_agent_skills(("codex",))

    def link_skills(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        for skill_name in SKILLS:
            link = tmp_path / ".claude" / "skills" / skill_name
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(tmp_path / ".agents" / "skills" / skill_name, target_is_directory=True)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(init_skills_module.subprocess, "run", link_skills)

    assert install_agent_skills(("claude-code", "codex")).restart_required


def test_agent_skills_install_requires_npx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(init_skills_module.shutil, "which", lambda name: None)

    with pytest.raises(RuntimeError, match="requires Node.js"):
        install_agent_skills(("codex",))


def test_agent_skills_install_reports_npx_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(init_skills_module.shutil, "which", lambda name: "/usr/bin/npx")
    calls: list[list[str]] = []
    monkeypatch.setattr(init_skills_module.subprocess, "run", _fake_npx_install(tmp_path, calls, returncode=1))

    with pytest.raises(RuntimeError, match="npx skills failed: Error: repo not found"):
        install_agent_skills(("codex",))


def test_init_skips_skills_without_detected_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(init_module, "detect_agents", lambda: ())
    monkeypatch.setattr(init_module, "install_agent_skills", lambda agents: pytest.fail("skills installed"))
    monkeypatch.setattr(init_module, "_verify_cli", lambda: True)
    monkeypatch.setattr(init_module, "_verify_live_search", lambda: True)

    result = CliRunner().invoke(cli, ["init", "--json"], env={"TAVILY_API_KEY": "tvly-test"})

    assert result.exit_code == 0
    assert json.loads(result.stdout)["skills"]["skipped"] is True


def test_init_json_reuses_environment_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(init_module, "detect_agents", lambda: ("codex",))
    monkeypatch.setattr(
        init_module,
        "install_agent_skills",
        lambda agents: SkillsInstallResult(agents, True),
    )
    monkeypatch.setattr(init_module, "_verify_cli", lambda: True)
    monkeypatch.setattr(init_module, "_verify_live_search", lambda: True)

    result = CliRunner().invoke(cli, ["init", "--json"], env={"TAVILY_API_KEY": "tvly-test"})

    assert result.exit_code == 0
    output = json.loads(result.stdout)
    assert output["ok"] is True
    assert output["auth"] == {"authenticated": True, "method": "env"}
    assert output["skills"]["agents"] == ["codex"]
    assert output["skills"]["restart_required"] is True
    assert output["skills"]["source"] == "tavily-ai/skills"
    assert output["verification"]["live_search"] is True


def test_init_noninteractive_auth_failure_is_json(monkeypatch: pytest.MonkeyPatch) -> None:
    from tavily_cli import config

    monkeypatch.setattr(config, "get_api_key", lambda: None)
    result = CliRunner().invoke(cli, ["init", "--skip-skills", "--json"])

    assert result.exit_code == 3
    output = json.loads(result.stdout)
    assert output["ok"] is False
    assert output["error"]["stage"] == "auth"
    assert output["skills"]["skipped"] is True


def test_init_live_search_failure_exits_four(monkeypatch: pytest.MonkeyPatch) -> None:
    from tavily_cli import config

    monkeypatch.setattr(config, "get_api_key", lambda: None)
    monkeypatch.setattr(init_module, "_verify_cli", lambda: True)
    monkeypatch.setattr(init_module, "_verify_live_search", lambda: False)

    result = CliRunner().invoke(cli, ["init", "--skip-auth", "--skip-skills", "--json"])

    assert result.exit_code == 4
    output = json.loads(result.stdout)
    assert output["error"]["stage"] == "live_search"
