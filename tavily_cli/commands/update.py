"""Check for and install Tavily CLI updates."""

from __future__ import annotations

import json
import os
import shutil
import site
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

import click
import httpx
from packaging.version import InvalidVersion, Version

from tavily_cli import __version__
from tavily_cli.common import json_option, sanitize_control

PACKAGE_NAME = "tavily-cli"
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE_NAME}/json"


@dataclass(frozen=True)
class InstallInfo:
    """The package manager responsible for the active CLI installation."""

    method: str
    command: tuple[str, ...] | None
    environment: tuple[tuple[str, str], ...] = ()
    blocked_reason: str | None = None


@click.command("update")
@click.option("--check", "check_only", is_flag=True, help="Check for an update without installing it.")
@json_option
def update_command(check_only: bool, json_output: bool) -> None:
    """Check for or install the latest Tavily CLI release."""
    try:
        latest = fetch_latest_version()
        update_available = _is_newer(latest, __version__)
    except Exception as exc:
        _fail("check", str(exc), 4, json_output)
        return
    try:
        install = detect_install()
    except Exception as exc:
        _fail("install_method", str(exc), 1, json_output)
        return

    if check_only or not update_available:
        result = _result(
            current=__version__,
            latest=latest,
            install=install,
            updated=False,
        )
        _print_result(result, json_output=json_output, check_only=check_only)
        return

    if install.command is None:
        message = install.blocked_reason or _unsupported_install_message(install.method)
        _fail("install_method", message, 1, json_output)
        return

    if not json_output:
        click.echo(f"Updating Tavily CLI via {install.method}...")

    process_environment = os.environ.copy()
    process_environment.update(install.environment)
    try:
        completed = subprocess.run(
            install.command,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
            env=process_environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _fail("update", str(exc), 1, json_output)
        return

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"Exited with status {completed.returncode}."
        _fail("update", detail[-2000:], 1, json_output)
        return

    try:
        current = _installed_version()
        if _is_newer(latest, current):
            message = (
                f"The package manager completed, but Tavily CLI is still {current}; latest is {latest}. "
                "The installation may be pinned."
            )
            manager_hint = _manager_hint(completed)
            if manager_hint:
                message = f"{message} Package manager reported: {manager_hint}"
            raise RuntimeError(message)
    except Exception as exc:
        _fail("verify", str(exc), 1, json_output)
        return
    result = _result(
        current=current,
        latest=latest,
        install=install,
        updated=Version(current) != Version(__version__),
    )
    _print_result(result, json_output=json_output, check_only=False)


def fetch_latest_version(*, client: httpx.Client | None = None) -> str:
    """Return the latest stable Tavily CLI version published on PyPI."""
    http = client or httpx.Client(timeout=10.0, follow_redirects=True)
    close = client is None
    try:
        response = http.get(PYPI_URL)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError("Could not check PyPI for Tavily CLI updates.") from exc
    finally:
        if close:
            http.close()

    info = data.get("info") if isinstance(data, dict) else None
    latest = info.get("version") if isinstance(info, dict) else None
    if not isinstance(latest, str):
        raise RuntimeError("PyPI returned an invalid Tavily CLI release response.")
    try:
        Version(latest)
    except InvalidVersion as exc:
        raise RuntimeError("PyPI returned an invalid Tavily CLI version.") from exc
    return latest


def detect_install(
    *,
    distribution: metadata.Distribution | None = None,
    executable: Path | None = None,
    prefix: Path | None = None,
    entrypoint: Path | None = None,
    process_environment: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> InstallInfo:
    """Detect the manager for the active package without changing the environment."""
    dist = distribution or metadata.distribution(PACKAGE_NAME)
    # Keep the venv executable path intact. Resolving its symlink can point pip
    # at the base interpreter and update the wrong environment.
    python = (executable or Path(sys.executable)).absolute()
    environment = (prefix or Path(sys.prefix)).resolve()
    runtime_environment = os.environ if process_environment is None else process_environment
    installer = (dist.read_text("INSTALLER") or "").strip().lower()

    if installer == "uv" and _is_uvx_environment(environment, runtime_environment):
        return InstallInfo("uvx", None)

    if _is_source_install(dist):
        return InstallInfo("source", None)

    path = f"{python.as_posix()}:{environment.as_posix()}".lower()
    if (environment / "pipx_metadata.json").is_file() or "/pipx/venvs/" in path:
        pipx = which("pipx")
        manager_environment: list[tuple[str, str]] = []
        # sys.prefix is the active pipx venv. Its directory name includes any
        # suffix and is the identifier accepted by `pipx upgrade`.
        pipx_environment = environment.name
        if environment.parent.name.lower() == "venvs":
            manager_environment.append(("PIPX_HOME", str(environment.parent.parent)))
        bin_dir = _active_manager_bin_dir(
            environment=environment,
            entrypoint=entrypoint,
            configured=runtime_environment.get("PIPX_BIN_DIR"),
        )
        if bin_dir is not None:
            manager_environment.append(("PIPX_BIN_DIR", bin_dir))
        blocked_reason = _missing_manager_bin_dir_message("pipx", "PIPX_BIN_DIR") if pipx and bin_dir is None else None
        command = (pipx, "upgrade", pipx_environment) if pipx and blocked_reason is None else None
        return InstallInfo("pipx", command, tuple(manager_environment), blocked_reason)

    if (environment / "uv-receipt.toml").is_file() or "/uv/tools/" in path:
        uv = which("uv")
        manager_environment = [("UV_TOOL_DIR", str(environment.parent))]
        bin_dir = _active_manager_bin_dir(
            environment=environment,
            entrypoint=entrypoint,
            configured=runtime_environment.get("UV_TOOL_BIN_DIR"),
        )
        if bin_dir is not None:
            manager_environment.append(("UV_TOOL_BIN_DIR", bin_dir))
        blocked_reason = _missing_manager_bin_dir_message("uv", "UV_TOOL_BIN_DIR") if uv and bin_dir is None else None
        command = (uv, "tool", "upgrade", PACKAGE_NAME) if uv and blocked_reason is None else None
        return InstallInfo("uv", command, tuple(manager_environment), blocked_reason)

    if installer == "uv":
        uv = which("uv")
        command = (uv, "pip", "install", "--python", str(python), "--upgrade", PACKAGE_NAME) if uv else None
        return InstallInfo("uv", command)
    if installer in {"pip", "pip3"}:
        command = [str(python), "-m", "pip", "install", "--upgrade", PACKAGE_NAME]
        if _is_user_install(dist):
            command.insert(-2, "--user")
        return InstallInfo("pip", tuple(command))
    return InstallInfo("unknown", None)


def _active_manager_bin_dir(*, environment: Path, entrypoint: Path | None, configured: str | None) -> str | None:
    invoked = _invoked_entrypoint(entrypoint)
    if invoked is not None:
        try:
            # Resolve the parent separately so the final launcher symlink stays
            # visible while its target is checked against the active venv.
            launcher = invoked.parent.resolve(strict=True) / invoked.name
            target = launcher.resolve(strict=True)
            active_environment = environment.resolve(strict=True)
        except (OSError, RuntimeError):
            pass
        else:
            target_is_active = target == active_environment or active_environment in target.parents
            launcher_is_external = launcher != active_environment and active_environment not in launcher.parents
            if target_is_active and launcher_is_external:
                return str(launcher.parent)
    return configured or None


def _invoked_entrypoint(entrypoint: Path | None) -> Path | None:
    raw = str(entrypoint) if entrypoint is not None else sys.argv[0]
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    if os.sep in raw or (os.altsep is not None and os.altsep in raw):
        return (Path.cwd() / candidate).absolute()
    located = shutil.which(raw)
    return Path(located).absolute() if located else None


def _missing_manager_bin_dir_message(manager: str, variable: str) -> str:
    return (
        f"Could not safely determine {variable} for the active {manager} installation. "
        f"Re-run with {variable} set to the directory containing this installation's tvly command."
    )


def _is_uvx_environment(environment: Path, process_environment: Mapping[str, str]) -> bool:
    try:
        active_environment = environment.resolve()
    except (OSError, RuntimeError):
        active_environment = environment.absolute()

    configured_cache = process_environment.get("UV_CACHE_DIR")
    if configured_cache:
        try:
            cache_dir = Path(configured_cache).expanduser().resolve()
        except (OSError, RuntimeError):
            cache_dir = Path(configured_cache).expanduser().absolute()
        if active_environment == cache_dir or cache_dir in active_environment.parents:
            return True

    # uvx currently stores reusable run environments under an environments-vN
    # entry that resolves into an archive-vN cache directory.
    for part in active_environment.parts:
        bucket, separator, version = part.lower().rpartition("-v")
        if separator and bucket in {"archive", "environments"} and version.isdigit():
            return True
    return False


def _is_source_install(distribution: metadata.Distribution) -> bool:
    raw = distribution.read_text("direct_url.json")
    if not raw:
        return False
    try:
        data = json.loads(raw)
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    # PyPI installs do not have direct_url.json. Editable, VCS, local archive,
    # and other direct-URL installs do; preserve that provenance instead of
    # silently replacing it with a registry release.
    return isinstance(data.get("url"), str)


def _is_user_install(distribution: metadata.Distribution) -> bool:
    try:
        package_root = Path(distribution.locate_file("")).resolve()
        user_root = Path(site.getusersitepackages()).resolve()
        return package_root == user_root or user_root in package_root.parents
    except (AttributeError, OSError, RuntimeError):
        return False


def _is_newer(candidate: str, current: str) -> bool:
    try:
        return Version(candidate) > Version(current)
    except InvalidVersion as exc:
        raise RuntimeError("The installed Tavily CLI version is invalid.") from exc


def _installed_version() -> str:
    return metadata.version(PACKAGE_NAME)


def _unsupported_install_message(method: str) -> str:
    if method == "source":
        return "This is a source or direct-URL installation. Update it from its original source."
    if method == "uvx":
        return (
            "This CLI is running in a uvx cache environment and cannot update itself in place. "
            "Exit and rerun uvx with the desired Tavily CLI version instead."
        )
    if method in {"uv", "pipx"}:
        return f"This installation is managed by {method}, but {method} is not available on PATH."
    return "Could not determine how Tavily CLI was installed. Update it with the original package manager."


def _manager_hint(completed: subprocess.CompletedProcess[str]) -> str | None:
    """Return a bounded package-manager hint without echoing arbitrary command output."""
    for output in (completed.stderr, completed.stdout):
        for line in reversed(output.splitlines()):
            candidate = sanitize_control(line).strip()
            if candidate.lower().startswith("hint:"):
                return candidate[:1000]
    return None


def _update_blocked_reason(install: InstallInfo) -> str | None:
    if install.command is not None:
        return None
    return install.blocked_reason or _unsupported_install_message(install.method)


def _result(*, current: str, latest: str, install: InstallInfo, updated: bool) -> dict[str, object]:
    blocked_reason = _update_blocked_reason(install)
    return {
        "ok": True,
        "current_version": current,
        "latest_version": latest,
        "update_available": _is_newer(latest, current),
        "install_method": install.method,
        "can_update": blocked_reason is None,
        "blocked_reason": blocked_reason,
        "updated": updated,
    }


def _fail(stage: str, message: str, exit_code: int, json_output: bool) -> None:
    safe_message = sanitize_control(message)
    if json_output:
        click.echo(json.dumps({"ok": False, "error": {"stage": stage, "message": safe_message}}, sort_keys=True))
    else:
        from rich.markup import escape

        from tavily_cli.theme import err_console

        err_console.print(f"  [#FAA2FB]> Update failed:[/#FAA2FB] {escape(safe_message)}")
    raise click.exceptions.Exit(exit_code)


def _print_result(result: dict[str, object], *, json_output: bool, check_only: bool) -> None:
    if json_output:
        click.echo(json.dumps(result, sort_keys=True))
        return

    current = result["current_version"]
    latest = result["latest_version"]
    if result["updated"]:
        click.echo(f"Updated Tavily CLI: {__version__} -> {current}")
    elif result["update_available"]:
        click.echo(f"Tavily CLI update available: {current} -> {latest}")
        if check_only:
            if result["can_update"]:
                click.echo("Run `tvly update` to install it.")
            else:
                click.echo(f"Self-update unavailable: {result['blocked_reason']}")
    else:
        click.echo(f"Tavily CLI {current} is up to date.")
