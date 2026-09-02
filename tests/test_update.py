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


def expose_entrypoint(environment: Path, bin_dir: Path, *, name: str = "tvly") -> Path:
    target = environment / "bin" / "tvly"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("")
    target.chmod(0o755)
    bin_dir.mkdir(parents=True, exist_ok=True)
    entrypoint = bin_dir / name
    entrypoint.symlink_to(target)
    return entrypoint


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
            (
                ("UV_TOOL_DIR", str(Path("/home/user/.local/share/uv/tools").resolve())),
                ("UV_TOOL_BIN_DIR", "/home/user/.local/bin"),
            ),
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
            (
                ("PIPX_HOME", str(Path("/home/user/.local/pipx").resolve())),
                ("PIPX_BIN_DIR", "/home/user/.local/bin"),
            ),
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
        process_environment={
            "PIPX_BIN_DIR": "/home/user/.local/bin",
            "UV_TOOL_BIN_DIR": "/home/user/.local/bin",
        },
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
    bin_dir = tmp_path / "custom-bin"
    entrypoint = expose_entrypoint(environment, bin_dir)

    detected = detect_install(
        distribution=FakeDistribution(),  # type: ignore[arg-type]
        executable=environment / "bin" / "python",
        prefix=environment,
        entrypoint=entrypoint,
        process_environment={},
        which=lambda command: f"/tools/{command}",
    )

    assert detected == InstallInfo(
        "uv",
        ("/tools/uv", "tool", "upgrade", "tavily-cli"),
        (("UV_TOOL_DIR", str(tool_dir)), ("UV_TOOL_BIN_DIR", str(bin_dir))),
    )


def test_detect_pipx_receipt_preserves_custom_home(tmp_path: Path) -> None:
    pipx_home = tmp_path / "custom-pipx"
    environment = pipx_home / "venvs" / "tavily-cli"
    environment.mkdir(parents=True)
    (environment / "pipx_metadata.json").write_text("")
    bin_dir = tmp_path / "custom-bin"
    entrypoint = expose_entrypoint(environment, bin_dir)

    detected = detect_install(
        distribution=FakeDistribution(),  # type: ignore[arg-type]
        executable=environment / "bin" / "python",
        prefix=environment,
        entrypoint=entrypoint,
        process_environment={},
        which=lambda command: f"/tools/{command}",
    )

    assert detected == InstallInfo(
        "pipx",
        ("/tools/pipx", "upgrade", "tavily-cli"),
        (("PIPX_HOME", str(pipx_home)), ("PIPX_BIN_DIR", str(bin_dir))),
    )


def test_detect_pipx_recovers_invoked_entrypoint_from_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipx_home = tmp_path / "custom-pipx"
    environment = pipx_home / "venvs" / "tavily-cli"
    environment.mkdir(parents=True)
    (environment / "pipx_metadata.json").write_text("")
    bin_dir = tmp_path / "custom-bin"
    expose_entrypoint(environment, bin_dir)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(update_module.sys, "argv", ["tvly"])

    detected = detect_install(
        distribution=FakeDistribution(),  # type: ignore[arg-type]
        executable=environment / "bin" / "python",
        prefix=environment,
        process_environment={},
        which=lambda command: f"/tools/{command}",
    )

    assert detected.environment == (
        ("PIPX_HOME", str(pipx_home)),
        ("PIPX_BIN_DIR", str(bin_dir)),
    )


def test_detect_pipx_suffixed_install_uses_active_environment_name(tmp_path: Path) -> None:
    pipx_home = tmp_path / "custom-pipx"
    environment = pipx_home / "venvs" / "tavily-cli-old"
    environment.mkdir(parents=True)
    (environment / "pipx_metadata.json").write_text("")
    bin_dir = tmp_path / "custom-bin"
    entrypoint = expose_entrypoint(environment, bin_dir, name="tvly-old")

    detected = detect_install(
        distribution=FakeDistribution(),  # type: ignore[arg-type]
        executable=environment / "bin" / "python",
        prefix=environment,
        entrypoint=entrypoint,
        process_environment={},
        which=lambda command: f"/tools/{command}",
    )

    assert detected == InstallInfo(
        "pipx",
        ("/tools/pipx", "upgrade", "tavily-cli-old"),
        (("PIPX_HOME", str(pipx_home)), ("PIPX_BIN_DIR", str(bin_dir))),
    )


@pytest.mark.parametrize(
    ("environment_parts", "receipt", "variable"),
    [
        (("custom-pipx", "venvs", "tavily-cli"), "pipx_metadata.json", "PIPX_BIN_DIR"),
        (("custom-uv", "tavily-cli"), "uv-receipt.toml", "UV_TOOL_BIN_DIR"),
    ],
)
def test_detect_manager_refuses_unverified_binary_directory(
    tmp_path: Path,
    environment_parts: tuple[str, ...],
    receipt: str,
    variable: str,
) -> None:
    environment = tmp_path.joinpath(*environment_parts)
    environment.mkdir(parents=True)
    (environment / receipt).write_text("")
    internal_entrypoint = environment / "bin" / "tvly"
    internal_entrypoint.parent.mkdir()
    internal_entrypoint.write_text("")

    detected = detect_install(
        distribution=FakeDistribution(),  # type: ignore[arg-type]
        executable=environment / "bin" / "python",
        prefix=environment,
        entrypoint=internal_entrypoint,
        process_environment={},
        which=lambda command: f"/tools/{command}",
    )

    assert detected.command is None
    assert detected.blocked_reason is not None
    assert variable in detected.blocked_reason


@pytest.mark.parametrize(
    ("environment", "process_environment"),
    [
        (Path("/home/user/.cache/uv/archive-v0/cache-id"), {}),
        (
            Path("/tmp/custom-uv-cache/future-layout/cache-id"),
            {"UV_CACHE_DIR": "/tmp/custom-uv-cache"},
        ),
    ],
)
def test_detect_uvx_cache_environment_refuses_mutation(
    environment: Path, process_environment: dict[str, str]
) -> None:
    detected = detect_install(
        distribution=FakeDistribution(installer="uv"),  # type: ignore[arg-type]
        executable=environment / "bin" / "python",
        prefix=environment,
        process_environment=process_environment,
        which=lambda command: f"/tools/{command}",
    )

    assert detected == InstallInfo("uvx", None)


def test_detect_uvx_takes_precedence_over_direct_url_install() -> None:
    environment = Path("/home/user/.cache/uv/archive-v0/cache-id")
    detected = detect_install(
        distribution=FakeDistribution(
            installer="uv",
            direct_url=json.dumps({"url": "https://example.com/tavily-cli.whl"}),
        ),  # type: ignore[arg-type]
        executable=environment / "bin" / "python",
        prefix=environment,
        process_environment={},
        which=lambda command: f"/tools/{command}",
    )

    assert detected == InstallInfo("uvx", None)


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
        "blocked_reason": None,
        "can_update": True,
        "current_version": "1.0.0",
        "install_method": "uv",
        "latest_version": "1.1.0",
        "ok": True,
        "update_available": True,
        "updated": False,
    }


def test_update_check_does_not_require_manager_binary_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_module, "__version__", "1.0.0")
    monkeypatch.setattr(update_module, "fetch_latest_version", lambda: "1.1.0")
    monkeypatch.setattr(
        update_module,
        "detect_install",
        lambda: InstallInfo("pipx", None, blocked_reason="Could not safely determine PIPX_BIN_DIR."),
    )

    result = CliRunner().invoke(cli, ["update", "--check", "--json"])

    assert result.exit_code == 0
    output = json.loads(result.stdout)
    assert output["update_available"] is True
    assert output["can_update"] is False
    assert output["blocked_reason"] == "Could not safely determine PIPX_BIN_DIR."


def test_update_check_reports_source_install_as_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_module, "__version__", "1.0.0")
    monkeypatch.setattr(update_module, "fetch_latest_version", lambda: "1.1.0")
    monkeypatch.setattr(update_module, "detect_install", lambda: InstallInfo("source", None))

    result = CliRunner().invoke(cli, ["update", "--check", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "blocked_reason": "This is a source or direct-URL installation. Update it from its original source.",
        "can_update": False,
        "current_version": "1.0.0",
        "install_method": "source",
        "latest_version": "1.1.0",
        "ok": True,
        "update_available": True,
        "updated": False,
    }


def test_update_check_human_does_not_recommend_blocked_self_update(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_module, "__version__", "1.0.0")
    monkeypatch.setattr(update_module, "fetch_latest_version", lambda: "1.1.0")
    monkeypatch.setattr(update_module, "detect_install", lambda: InstallInfo("uvx", None))

    result = CliRunner().invoke(cli, ["update", "--check"])

    assert result.exit_code == 0
    assert "Self-update unavailable:" in result.stdout
    assert "rerun uvx" in result.stdout
    assert "Run `tvly update`" not in result.stdout


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
        lambda: InstallInfo(
            "pipx",
            command,
            (("PIPX_HOME", "/custom/pipx"), ("PIPX_BIN_DIR", "/custom/bin")),
        ),
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
    assert environments[0]["PIPX_BIN_DIR"] == "/custom/bin"
    assert environments[0]["PATH"] == os.environ["PATH"]
    assert json.loads(result.stdout) == {
        "blocked_reason": None,
        "can_update": True,
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


def test_update_reports_unsafe_manager_binary_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    reason = "Could not safely determine PIPX_BIN_DIR."
    monkeypatch.setattr(update_module, "__version__", "1.0.0")
    monkeypatch.setattr(update_module, "fetch_latest_version", lambda: "1.1.0")
    monkeypatch.setattr(update_module, "detect_install", lambda: InstallInfo("pipx", None, blocked_reason=reason))

    result = CliRunner().invoke(cli, ["update", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "error": {"message": reason, "stage": "install_method"},
        "ok": False,
    }


def test_update_refuses_uvx_cache_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_module, "__version__", "1.0.0")
    monkeypatch.setattr(update_module, "fetch_latest_version", lambda: "1.1.0")
    monkeypatch.setattr(update_module, "detect_install", lambda: InstallInfo("uvx", None))
    monkeypatch.setattr(
        update_module.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("uvx cache environments must not be mutated"),
    )

    result = CliRunner().invoke(cli, ["update", "--json"])

    assert result.exit_code == 1
    output = json.loads(result.stdout)
    assert output["error"]["stage"] == "install_method"
    assert "rerun uvx" in output["error"]["message"]


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


def test_update_surfaces_manager_hint_when_successful_upgrade_is_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_module, "__version__", "1.0.0")
    monkeypatch.setattr(update_module, "fetch_latest_version", lambda: "1.1.0")
    monkeypatch.setattr(
        update_module,
        "detect_install",
        lambda: InstallInfo("uv", ("uv", "tool", "upgrade", "tavily-cli")),
    )
    monkeypatch.setattr(update_module, "_installed_version", lambda: "1.0.0")
    monkeypatch.setattr(
        update_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            stdout="Nothing to upgrade\nhint: reinstall with 'uv tool install tavily-cli@latest'\n",
            stderr="",
        ),
    )

    result = CliRunner().invoke(cli, ["update", "--json"])

    assert result.exit_code == 1
    output = json.loads(result.stdout)
    assert output["error"]["stage"] == "verify"
    assert "Package manager reported: hint: reinstall with 'uv tool install tavily-cli@latest'" in output["error"][
        "message"
    ]


def test_update_does_not_echo_non_hint_output_after_successful_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_module, "__version__", "1.0.0")
    monkeypatch.setattr(update_module, "fetch_latest_version", lambda: "1.1.0")
    monkeypatch.setattr(update_module, "detect_install", lambda: InstallInfo("uv", ("uv", "tool", "upgrade")))
    monkeypatch.setattr(update_module, "_installed_version", lambda: "1.0.0")
    monkeypatch.setattr(
        update_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            stdout="Index URL: https://user:secret@example.com/simple\nNothing to upgrade\n",
            stderr="",
        ),
    )

    result = CliRunner().invoke(cli, ["update", "--json"])

    assert result.exit_code == 1
    assert "secret" not in result.stdout
