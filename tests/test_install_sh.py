"""End-to-end behavior tests for the POSIX shell installer."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

try:
    import pty
except ImportError:  # pragma: no cover - Windows does not run install.sh
    pty = None


INSTALL_SCRIPT = Path(__file__).parents[1] / "install.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _stub_environment(
    tmp_path: Path,
    *,
    installed: bool = False,
    install_exit: int = 0,
    init_exit: int = 0,
) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "calls.log"
    _write_executable(
        bin_dir / "uv",
        """#!/bin/sh
printf 'uv %s\\n' "$*" >> "$TVLY_INSTALL_TEST_LOG"
if [ "$1 $2" = "tool list" ]; then
    if [ "$TVLY_UV_INSTALLED" = "1" ]; then
        printf 'tavily-cli v0.1.7\\n- tvly\\n'
    fi
    exit 0
fi
if [ "$1 $2" = "tool install" ]; then
    exit "$TVLY_UV_INSTALL_EXIT"
fi
exit 0
""",
    )
    _write_executable(
        bin_dir / "tvly",
        """#!/bin/sh
printf 'tvly %s\\n' "$*" >> "$TVLY_INSTALL_TEST_LOG"
if [ "$1" = "--version" ]; then
    printf 'tavily-cli 0.1.8\\n'
    exit 0
fi
if [ "$1 $2" = "init --help" ]; then
    exit 0
fi
if [ "$1" = "init" ]; then
    exit "$TVLY_INIT_EXIT"
fi
exit 0
""",
    )
    return {
        **os.environ,
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "TVLY_INSTALL_TEST_LOG": str(log_file),
        "TVLY_UV_INSTALLED": "1" if installed else "0",
        "TVLY_UV_INSTALL_EXIT": str(install_exit),
        "TVLY_INIT_EXIT": str(init_exit),
        "CI": "",
        "SSH_CONNECTION": "",
        "SSH_TTY": "",
    }


def _run_with_terminal(env: dict[str, str], *, pipe_script: bool = False) -> tuple[int, str]:
    assert pty is not None
    pid, descriptor = pty.fork()
    if pid == 0:
        if pipe_script:
            os.execve(
                "/bin/sh",
                ["sh", "-c", 'cat "$1" | /bin/sh', "sh", str(INSTALL_SCRIPT)],
                env,
            )
        os.execve("/bin/sh", ["sh", str(INSTALL_SCRIPT)], env)

    output = bytearray()
    while True:
        try:
            chunk = os.read(descriptor, 4096)
        except OSError:
            break
        if not chunk:
            break
        output.extend(chunk)
    _, status = os.waitpid(pid, 0)
    os.close(descriptor)
    return os.waitstatus_to_exitcode(status), output.decode(errors="replace")


@pytest.mark.skipif(pty is None, reason="install.sh is a POSIX installer")
def test_fresh_interactive_install_hands_off_to_init(tmp_path: Path) -> None:
    env = _stub_environment(tmp_path)
    env["DISPLAY"] = ":0"

    status, output = _run_with_terminal(env, pipe_script=True)

    assert status == 0
    calls = (tmp_path / "calls.log").read_text().splitlines()
    assert calls == [
        "uv tool list",
        "uv tool install tavily-cli",
        "tvly --version",
        "tvly init --help",
        "tvly init",
    ]
    assert "Starting guided Tavily setup" in output
    assert "guided authentication and skill setup" not in output


def test_noninteractive_install_prints_init_without_running_it(tmp_path: Path) -> None:
    env = _stub_environment(tmp_path)

    result = subprocess.run(
        ["/bin/sh", str(INSTALL_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert (tmp_path / "calls.log").read_text().splitlines() == [
        "uv tool list",
        "uv tool install tavily-cli",
        "tvly --version",
    ]
    assert "tvly init" in result.stdout


@pytest.mark.skipif(pty is None, reason="install.sh is a POSIX installer")
def test_init_failure_does_not_fail_successful_install(tmp_path: Path) -> None:
    env = _stub_environment(tmp_path, init_exit=3)
    env["DISPLAY"] = ":0"

    status, output = _run_with_terminal(env)

    assert status == 0
    assert "Guided setup did not complete" in output
    assert "tvly init" in output


@pytest.mark.skipif(pty is None, reason="install.sh is a POSIX installer")
def test_existing_uv_install_upgrades_without_rerunning_init(tmp_path: Path) -> None:
    env = _stub_environment(tmp_path, installed=True)
    env["DISPLAY"] = ":0"

    status, output = _run_with_terminal(env)

    assert status == 0
    assert (tmp_path / "calls.log").read_text().splitlines() == [
        "uv tool list",
        "uv tool upgrade tavily-cli",
        "tvly --version",
    ]
    assert "Starting guided Tavily setup" not in output
    assert "tvly init" in output


@pytest.mark.skipif(pty is None, reason="install.sh is a POSIX installer")
def test_failed_fresh_uv_install_exits_without_running_init(tmp_path: Path) -> None:
    env = _stub_environment(tmp_path, install_exit=1)
    env["DISPLAY"] = ":0"

    status, output = _run_with_terminal(env)

    assert status == 1
    assert (tmp_path / "calls.log").read_text().splitlines() == [
        "uv tool list",
        "uv tool install tavily-cli",
    ]
    assert "Starting guided Tavily setup" not in output


@pytest.mark.skipif(pty is None, reason="install.sh is a POSIX installer")
@pytest.mark.parametrize("variable", ["CI", "SSH_CONNECTION", "SSH_TTY"])
def test_automation_and_remote_sessions_do_not_run_init(tmp_path: Path, variable: str) -> None:
    env = _stub_environment(tmp_path)
    env["DISPLAY"] = ":0"
    env[variable] = "set"

    status, output = _run_with_terminal(env)

    assert status == 0
    assert (tmp_path / "calls.log").read_text().splitlines() == [
        "uv tool list",
        "uv tool install tavily-cli",
        "tvly --version",
    ]
    assert "Starting guided Tavily setup" not in output
    assert "tvly init" in output


def test_pip_path_upgrades_the_package(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "calls.log"
    _write_executable(
        bin_dir / "python3",
        """#!/bin/sh
printf 'python3 %s\\n' "$*" >> "$TVLY_INSTALL_TEST_LOG"
if [ "$1" = "--version" ]; then
    printf 'Python 3.11.0\\n'
elif [ "$1" = "-c" ]; then
    case "$2" in
        *version_info*) printf '3.11\\n' ;;
        *sys.prefix*) printf '1\\n' ;;
    esac
elif [ "$1 $2 $3" = "-m pip show" ]; then
    exit 1
fi
exit 0
""",
    )
    _write_executable(
        bin_dir / "tvly",
        """#!/bin/sh
printf 'tvly %s\\n' "$*" >> "$TVLY_INSTALL_TEST_LOG"
if [ "$1" = "--version" ]; then
    printf 'tavily-cli 0.1.8\\n'
fi
exit 0
""",
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "TVLY_INSTALL_TEST_LOG": str(log_file),
    }

    result = subprocess.run(
        ["/bin/sh", str(INSTALL_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "python3 -m pip install --upgrade tavily-cli" in log_file.read_text().splitlines()


@pytest.mark.skipif(pty is None, reason="install.sh is a POSIX installer")
def test_existing_pipx_install_upgrades_without_rerunning_init(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "calls.log"
    _write_executable(
        bin_dir / "python3",
        """#!/bin/sh
if [ "$1" = "--version" ]; then
    printf 'Python 3.11.0\\n'
elif [ "$1" = "-c" ]; then
    printf '3.11\\n'
fi
exit 0
""",
    )
    _write_executable(
        bin_dir / "pipx",
        """#!/bin/sh
printf 'pipx %s\\n' "$*" >> "$TVLY_INSTALL_TEST_LOG"
if [ "$1 $2" = "list --short" ]; then
    printf 'tavily-cli 0.1.7\\n'
elif [ "$1" = "install" ]; then
    exit 1
fi
exit 0
""",
    )
    _write_executable(
        bin_dir / "tvly",
        """#!/bin/sh
printf 'tvly %s\\n' "$*" >> "$TVLY_INSTALL_TEST_LOG"
if [ "$1" = "--version" ]; then
    printf 'tavily-cli 0.1.8\\n'
fi
exit 0
""",
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "TVLY_INSTALL_TEST_LOG": str(log_file),
        "DISPLAY": ":0",
        "CI": "",
        "SSH_CONNECTION": "",
        "SSH_TTY": "",
    }

    status, output = _run_with_terminal(env)

    assert status == 0
    assert log_file.read_text().splitlines() == [
        "pipx list --short",
        "pipx upgrade tavily-cli",
        "tvly --version",
    ]
    assert "Starting guided Tavily setup" not in output
