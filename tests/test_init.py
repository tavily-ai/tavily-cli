"""Tests for one-shot CLI and agent skill setup."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from tavily_cli.cli import cli
from tavily_cli.commands import init as init_module
from tavily_cli.init_skills import (
    CORE_SKILLS,
    SkillsInstallResult,
    detect_agents,
    install_skills,
    verify_skills,
)


def _skills_archive(*, changed_skill: str | None = None, symlink: bool = False) -> bytes:
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as archive:
        for skill_name in CORE_SKILLS:
            content = f"---\nname: {skill_name}\n---\n"
            if skill_name == changed_skill:
                content += "updated\n"
            _add_file(archive, f"skills-main/skills/{skill_name}/SKILL.md", content.encode())
        _add_file(
            archive,
            "skills-main/skills/tavily-cli/scripts/check.sh",
            b"#!/bin/sh\nexit 99\n",
            mode=0o755,
        )
        if symlink:
            member = tarfile.TarInfo("skills-main/skills/tavily-cli/references/unsafe")
            member.type = tarfile.SYMTYPE
            member.linkname = "/etc/passwd"
            archive.addfile(member)
    return payload.getvalue()


def _add_file(archive: tarfile.TarFile, name: str, content: bytes, *, mode: int = 0o644) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(content)
    member.mode = mode
    archive.addfile(member, io.BytesIO(content))


def test_detect_agents_only_returns_existing_markers(tmp_path: Path) -> None:
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".config" / "opencode").mkdir(parents=True)

    assert detect_agents(tmp_path) == ("codex",)


def test_skill_install_honors_custom_agent_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default_home = tmp_path / "home"
    codex_home = tmp_path / "custom-codex"
    claude_home = tmp_path / "custom-claude"
    default_home.mkdir()
    codex_home.mkdir()
    claude_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: default_home)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))

    assert detect_agents() == ("claude-code", "codex")

    install_skills(("claude-code", "codex"), archive=_skills_archive())

    assert verify_skills(("claude-code", "codex"))
    assert (claude_home / "skills" / "tavily-search" / "SKILL.md").is_file()
    assert (codex_home / "skills" / "tavily-search" / "SKILL.md").is_file()
    assert not (default_home / ".claude").exists()
    assert not (default_home / ".codex").exists()


def test_skill_install_is_complete_and_idempotent(tmp_path: Path) -> None:
    archive = _skills_archive()

    first = install_skills(("codex",), home=tmp_path, archive=archive)

    assert first.installed == len(CORE_SKILLS)
    assert first.updated == 0
    assert first.linked == len(CORE_SKILLS)
    assert first.restart_required
    assert verify_skills(("codex",), home=tmp_path)
    script = tmp_path / ".agents" / "skills" / "tavily-cli" / "scripts" / "check.sh"
    assert script.read_text() == "#!/bin/sh\nexit 99\n"
    assert script.stat().st_mode & 0o111

    second = install_skills(("codex",), home=tmp_path, archive=archive)

    assert second.installed == 0
    assert second.updated == 0
    assert second.unchanged == len(CORE_SKILLS)
    assert second.linked == 0
    assert not second.restart_required


def test_skill_install_without_agent_uses_shared_directory(tmp_path: Path) -> None:
    result = install_skills((), home=tmp_path, archive=_skills_archive())

    assert result.agents == ()
    assert result.installed == len(CORE_SKILLS)
    assert result.linked == 0
    assert verify_skills((), home=tmp_path)


def test_skill_install_updates_only_changed_core_skill(tmp_path: Path) -> None:
    install_skills(("claude-code",), home=tmp_path, archive=_skills_archive())

    updated = install_skills(
        ("claude-code",),
        home=tmp_path,
        archive=_skills_archive(changed_skill="tavily-search"),
    )

    assert updated.updated == 1
    assert updated.unchanged == len(CORE_SKILLS) - 1
    assert "updated" in (tmp_path / ".agents" / "skills" / "tavily-search" / "SKILL.md").read_text()


def test_skill_install_rejects_archive_links(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported archive entry"):
        install_skills((), home=tmp_path, archive=_skills_archive(symlink=True))


def test_skill_install_checks_conflicts_before_writing(tmp_path: Path) -> None:
    conflict = tmp_path / ".agents" / "skills" / CORE_SKILLS[-1]
    conflict.parent.mkdir(parents=True)
    conflict.write_text("owned by the user")

    with pytest.raises(ValueError, match="non-directory path"):
        install_skills((), home=tmp_path, archive=_skills_archive())

    assert not (tmp_path / ".agents" / "skills" / CORE_SKILLS[0]).exists()


def test_init_json_reuses_environment_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(init_module, "detect_agents", lambda: ("codex",))
    monkeypatch.setattr(
        init_module,
        "install_skills",
        lambda agents: SkillsInstallResult(agents, 8, 0, 0, 8, True),
    )
    monkeypatch.setattr(init_module, "verify_skills", lambda agents: True)
    monkeypatch.setattr(init_module, "_verify_cli", lambda: True)
    monkeypatch.setattr(init_module, "_verify_live_search", lambda: True)

    result = CliRunner().invoke(cli, ["init", "--json"], env={"TAVILY_API_KEY": "tvly-test"})

    assert result.exit_code == 0
    output = json.loads(result.stdout)
    assert output["ok"] is True
    assert output["auth"] == {"authenticated": True, "method": "env"}
    assert output["skills"]["agents"] == ["codex"]
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
    monkeypatch.setattr(init_module, "_verify_cli", lambda: True)
    monkeypatch.setattr(init_module, "_verify_live_search", lambda: False)

    result = CliRunner().invoke(cli, ["init", "--skip-auth", "--skip-skills", "--json"])

    assert result.exit_code == 4
    output = json.loads(result.stdout)
    assert output["error"]["stage"] == "live_search"
