"""Install Tavily's maintained agent skills with the skills.sh CLI (``npx skills``).

Installs track the latest published skills and are counted on skills.sh.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SKILLS_REPO = "tavily-ai/skills"
# Exact version: npx runs whatever this resolves to with the user's permissions.
NPX_SKILLS_PACKAGE = "skills@1.7.4"

_NPX_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class AgentSpec:
    marker: Path
    skills_dir: Path


@dataclass(frozen=True)
class SkillsInstallResult:
    agents: tuple[str, ...]
    installed: int
    updated: int

    @property
    def restart_required(self) -> bool:
        return self.installed > 0 or self.updated > 0


def agent_specs(home: Path | None = None) -> dict[str, AgentSpec]:
    """Return supported agent locations rooted at the current user's home."""
    root = home or Path.home()
    if home is None:
        claude_root = Path(os.environ.get("CLAUDE_CONFIG_DIR", root / ".claude")).expanduser()
        codex_root = Path(os.environ.get("CODEX_HOME", root / ".codex")).expanduser()
    else:
        claude_root = root / ".claude"
        codex_root = root / ".codex"
    return {
        "claude-code": AgentSpec(claude_root, claude_root / "skills"),
        "codex": AgentSpec(codex_root, codex_root / "skills"),
        "cursor": AgentSpec(root / ".cursor", root / ".cursor" / "skills"),
    }


def detect_agents(home: Path | None = None) -> tuple[str, ...]:
    """Detect supported agents from their existing configuration directories."""
    return tuple(name for name, spec in agent_specs(home).items() if spec.marker.is_dir())


def install_agent_skills(agents: tuple[str, ...]) -> SkillsInstallResult:
    """Install every skill in the Tavily skills repo for the selected agents."""
    npx = shutil.which("npx")
    if not npx:
        raise RuntimeError("Installing Tavily skills requires Node.js (npx). Install Node.js, or rerun with --skip-skills.")

    # npx writes to the shared directory, or straight into an agent's own
    # directory when that agent is the only target.
    specs = agent_specs()
    skill_dirs = (Path.home() / ".agents" / "skills", *(specs[name].skills_dir for name in agents))
    before = _snapshot_skills(skill_dirs)

    # Keep every argument free of quotes and spaces: on Windows npx is a .cmd
    # shim, so the command line is re-parsed by cmd.exe.
    command = [
        npx, "--yes", NPX_SKILLS_PACKAGE, "add", SKILLS_REPO,
        "--skill", "*", "--global", "--agent", *agents, "--yes",
    ]
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=_NPX_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("npx skills timed out while installing Tavily skills.") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        raise RuntimeError(f"npx skills failed: {detail[-1] if detail else f'exit code {completed.returncode}'}")

    after = _snapshot_skills(skill_dirs)
    return SkillsInstallResult(
        agents=agents,
        installed=sum(1 for name in after if name not in before),
        updated=sum(1 for name in after if name in before and before[name] != after[name]),
    )


def _snapshot_skills(skill_dirs: tuple[Path, ...]) -> dict[str, str]:
    return {
        path.name: _directory_digest(path)
        for skill_dir in skill_dirs
        if skill_dir.is_dir()
        for path in skill_dir.iterdir()
        if path.is_dir()
    }


def _directory_digest(path: Path) -> str:
    digest = hashlib.sha256()
    for file_path in sorted(item for item in path.rglob("*") if item.is_file()):
        relative = file_path.relative_to(path).as_posix().encode("utf-8")
        content = file_path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()
