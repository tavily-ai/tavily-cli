"""Tests for Tavily CLI update checks and installer detection."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from tavily_cli.cli import cli
from tavily_cli.commands import update as update_module
from tavily_cli.commands.update import InstallInfo, detect_install, fetch_latest_version


class FakeDistribution:
    def __init__(
        self,
        *,
        installer: str = "pip",
        direct_url: str | None = None,
        root: Path = Path("/opt/tavily"),
    ) -> None:
        self.installer = installer
        self.direct_url = direct_url
        self.root = root

    def read_text(self, filename: str) -> str | None:
        if filename == "INSTALLER":
            return self.installer
        if filename == "direct_url.json":
            return self.direct_url
        return None

    def locate_file(self, path: str) -> Path:
        return self.root / path


def test_fetch_latest_version_from_pypi() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"info": {"version": "1.2.3"}}, request=request)
    )
    with httpx.Client(transport=transport) as client:
        assert fetch_latest_version(client=client) == "1.2.3"


def test_fetch_latest_version_rejects_invalid_response() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"info": {}}, request=request))
    with httpx.Client(transport=transport) as client, pytest.raises(RuntimeError, match="invalid.*release response"):
        fetch_latest_version(client=client)


@pytest.mark.parametrize(
    ("distribution", "executable", "expected_method", "expected_command", "expected_environment"),
    [
        (
            FakeDistribution(installer="uv"),
            Path("/home/user/.local/share/uv/tools/tavily-cli/bin/python"),
            "uv",
            ("/usr/bin/uv", "tool", "upgrade", "tavily-cli"),
            (("UV_TOOL_DIR", str(Path("/home/user/.local/share/uv/tools").resolve())),),
        ),
        (
            FakeDistribution(installer="uv"),
            Path("/workspace/.venv/bin/python"),
            "uv",
            (
                "/usr/bin/uv",
                "pip",
                "install",
                "--python",
                "/workspace/.venv/bin/python",
                "--upgrade",
                "tavily-cli",
            ),
            (),
        ),
        (
            FakeDistribution(),
            Path("/home/user/.local/pipx/venvs/tavily-cli/bin/python"),
            "pipx",
            ("/usr/bin/pipx", "upgrade", "tavily-cli"),
            (("PIPX_HOME", str(Path("/home/user/.local/pipx").resolve())),),
        ),
        (
            FakeDistribution(),
            Path("/opt/tavily/bin/python"),
            "pip",
            ("/opt/tavily/bin/python", "-m", "pip", "install", "--upgrade", "tavily-cli"),
            (),
        ),
    ],
)
def test_detect_install_managers(
    distribution: FakeDistribution,
    executable: Path,
    expected_method: str,
    expected_command: tuple[str, ...],
    expected_environment: tuple[tuple[str, str], ...],
) -> None:
    detected = detect_install(
        distribution=distribution,  # type: ignore[arg-type]
        executable=executable,
        prefix=executable.parent.parent,
        which=lambda command: f"/usr/bin/{command}",
    )

    assert detected == InstallInfo(expected_method, expected_command, expected_environment)


def test_detect_pip_install_preserves_virtualenv_executable(tmp_path: Path) -> None:
    environment = tmp_path / "venv"
    executable = environment / "bin" / "python"
    executable.parent.mkdir(parents=True)
    executable.symlink_to(Path("/usr/bin/python3"))

    detected = detect_install(
        distribution=FakeDistribution(),  # type: ignore[arg-type]
        executable=executable,
        prefix=environment,
    )

    assert detected == InstallInfo(
        "pip",
        (str(executable), "-m", "pip", "install", "--upgrade", "tavily-cli"),
    )


def test_detect_editable_install_refuses_self_update() -> None:
    distribution = FakeDistribution(
        direct_url=json.dumps({"url": "file:///workspace/tavily-cli", "dir_info": {"editable": True}})
    )

    detected = detect_install(
        distribution=distribution,  # type: ignore[arg-type]
        executable=Path("/workspace/.venv/bin/python"),
        prefix=Path("/workspace/.venv"),
    )

    assert detected == InstallInfo("source", None)


def test_detect_vcs_install_refuses_self_update() -> None:
    distribution = FakeDistribution(
        direct_url=json.dumps({"url": "https://github.com/tavily-ai/tavily-cli.git", "vcs_info": {"vcs": "git"}})
    )

    detected = detect_install(
        distribution=distribution,  # type: ignore[arg-type]
        executable=Path("/workspace/.venv/bin/python"),
        prefix=Path("/workspace/.venv"),
    )

    assert detected == InstallInfo("source", None)


def test_detect_local_wheel_install_refuses_self_update() -> None:
    distribution = FakeDistribution(
        direct_url=json.dumps({"url": "file:///tmp/tavily_cli.whl", "archive_info": {}})
    )

    detected = detect_install(
        distribution=distribution,  # type: ignore[arg-type]
        executable=Path("/tools/tavily-cli/bin/python"),
        prefix=Path("/tools/tavily-cli"),
    )

    assert detected == InstallInfo("source", None)


def test_detect_uv_receipt_preserves_custom_tool_dir(tmp_path: Path) -> None:
    tool_dir = tmp_path / "custom-uv"
    environment = tool_dir / "tavily-cli"
    environment.mkdir(parents=True)
    (environment / "uv-receipt.toml").write_text("")

    detected = detect_install(
        distribution=FakeDistribution(),  # type: ignore[arg-type]
        executable=environment / "bin" / "python",
        prefix=environment,
        which=lambda command: f"/tools/{command}",
    )

    assert detected == InstallInfo(
        "uv",
        ("/tools/uv", "tool", "upgrade", "tavily-cli"),
        (("UV_TOOL_DIR", str(tool_dir)),),
    )


def test_detect_pipx_receipt_preserves_custom_home(tmp_path: Path) -> None:
    pipx_home = tmp_path / "custom-pipx"
    environment = pipx_home / "venvs" / "tavily-cli"
    environment.mkdir(parents=True)
    (environment / "pipx_metadata.json").write_text("")

    detected = detect_install(
        distribution=FakeDistribution(),  # type: ignore[arg-type]
        executable=environment / "bin" / "python",
        prefix=environment,
        which=lambda command: f"/tools/{command}",
    )

    assert detected == InstallInfo(
        "pipx",
        ("/tools/pipx", "upgrade", "tavily-cli"),
        (("PIPX_HOME", str(pipx_home)),),
    )


def test_update_check_json_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_module, "__version__", "1.0.0")
    monkeypatch.setattr(update_module, "fetch_latest_version", lambda: "1.1.0")
    monkeypatch.setattr(update_module, "detect_install", lambda: InstallInfo("uv", ("uv", "tool", "upgrade")))
    monkeypatch.setattr(
        update_module.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("--check must not run an update command"),
    )

    result = CliRunner().invoke(cli, ["update", "--check", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "current_version": "1.0.0",
        "install_method": "uv",
        "latest_version": "1.1.0",
        "ok": True,
        "update_available": True,
        "updated": False,
    }


def test_update_check_failure_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        update_module,
        "fetch_latest_version",
        lambda: (_ for _ in ()).throw(RuntimeError("PyPI unavailable")),
    )

    result = CliRunner().invoke(cli, ["update", "--check", "--json"])

    assert result.exit_code == 4
    assert json.loads(result.stdout) == {
        "error": {"message": "PyPI unavailable", "stage": "check"},
        "ok": False,
    }


def test_install_detection_failure_is_not_reported_as_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_module, "fetch_latest_version", lambda: "1.1.0")
    monkeypatch.setattr(
        update_module,
        "detect_install",
        lambda: (_ for _ in ()).throw(RuntimeError("Package metadata unavailable")),
    )

    result = CliRunner().invoke(cli, ["update", "--check", "--json"])

    assert result.exit_code == 1
    output = json.loads(result.stdout)
    assert output["error"] == {"message": "Package metadata unavailable", "stage": "install_method"}


def test_update_does_nothing_when_current(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_module, "__version__", "1.1.0")
    monkeypatch.setattr(update_module, "fetch_latest_version", lambda: "1.1.0")
    monkeypatch.setattr(update_module, "detect_install", lambda: InstallInfo("pip", ("pip", "install")))
    monkeypatch.setattr(
        update_module.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("An up-to-date installation must not run an update command"),
    )

    result = CliRunner().invoke(cli, ["update", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["update_available"] is False


def test_update_runs_detected_manager_and_verifies_version(monkeypatch: pytest.MonkeyPatch) -> None:
    command = ("pipx", "upgrade", "tavily-cli")
    calls: list[tuple[str, ...]] = []
    environments: list[dict[str, str]] = []
    monkeypatch.delenv("PIPX_HOME", raising=False)
    monkeypatch.setattr(update_module, "__version__", "1.0.0")
    monkeypatch.setattr(update_module, "fetch_latest_version", lambda: "1.1.0")
    monkeypatch.setattr(
        update_module,
        "detect_install",
        lambda: InstallInfo("pipx", command, (("PIPX_HOME", "/custom/pipx"),)),
    )
    monkeypatch.setattr(update_module, "_installed_version", lambda: "1.1.0")

    def run(args: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        environments.append(environment)
        return subprocess.CompletedProcess(args, 0, stdout="updated", stderr="")

    monkeypatch.setattr(update_module.subprocess, "run", run)

    result = CliRunner().invoke(cli, ["update", "--json"])

    assert result.exit_code == 0
    assert calls == [command]
    assert environments[0]["PIPX_HOME"] == "/custom/pipx"
    assert environments[0]["PATH"] == os.environ["PATH"]
    assert json.loads(result.stdout) == {
        "current_version": "1.1.0",
        "install_method": "pipx",
        "latest_version": "1.1.0",
        "ok": True,
        "update_available": False,
        "updated": True,
    }


def test_update_refuses_editable_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_module, "__version__", "1.0.0")
    monkeypatch.setattr(update_module, "fetch_latest_version", lambda: "1.1.0")
    monkeypatch.setattr(update_module, "detect_install", lambda: InstallInfo("source", None))

    result = CliRunner().invoke(cli, ["update", "--json"])

    assert result.exit_code == 1
    output = json.loads(result.stdout)
    assert output["ok"] is False
    assert output["error"]["stage"] == "install_method"


def test_update_failure_is_structured_and_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_module, "__version__", "1.0.0")
    monkeypatch.setattr(update_module, "fetch_latest_version", lambda: "1.1.0")
    monkeypatch.setattr(update_module, "detect_install", lambda: InstallInfo("uv", ("uv", "tool", "upgrade")))
    monkeypatch.setattr(
        update_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 2, stdout="", stderr="failed\x1b[2J"),
    )

    result = CliRunner().invoke(cli, ["update", "--json"])

    assert result.exit_code == 1
    output = json.loads(result.stdout)
    assert output == {"error": {"message": "failed[2J", "stage": "update"}, "ok": False}


def test_update_verifies_package_manager_changed_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_module, "__version__", "1.0.0")
    monkeypatch.setattr(update_module, "fetch_latest_version", lambda: "1.1.0")
    monkeypatch.setattr(update_module, "detect_install", lambda: InstallInfo("pipx", ("pipx", "upgrade")))
    monkeypatch.setattr(update_module, "_installed_version", lambda: "1.0.0")
    monkeypatch.setattr(
        update_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="", stderr=""),
    )

    result = CliRunner().invoke(cli, ["update", "--json"])

    assert result.exit_code == 1
    output = json.loads(result.stdout)
    assert output["error"]["stage"] == "verify"
    assert "may be pinned" in output["error"]["message"]
