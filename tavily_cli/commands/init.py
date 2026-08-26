"""One-shot Tavily CLI and agent skill setup."""

from __future__ import annotations

import json
import os
import shutil
import sys
from typing import Any

import click

from tavily_cli import __version__
from tavily_cli.common import json_option
from tavily_cli.init_skills import (
    SKILLS_SOURCE,
    agent_specs,
    detect_agents,
    install_skills,
    verify_skills,
)

_AGENT_CHOICES = tuple(agent_specs())


@click.command("init")
@click.option(
    "--agent",
    "agents",
    type=click.Choice(_AGENT_CHOICES, case_sensitive=False),
    multiple=True,
    help="Install skills for an agent. Repeat to select more than one.",
)
@click.option("--all", "all_agents", is_flag=True, help="Install skills for every detected agent.")
@click.option("--yes", "assume_yes", is_flag=True, help="Accept detected agents without prompting.")
@click.option("--skip-auth", is_flag=True, help="Keep keyless mode when no credential is configured.")
@click.option("--skip-skills", is_flag=True, help="Do not install or update Tavily agent skills.")
@click.option("--api-key", default=None, help="Authenticate with a Tavily API key instead of OAuth.")
@click.option(
    "--browser/--no-browser",
    default=None,
    help="Open OAuth in a browser, or print the URL and wait for its local callback.",
)
@json_option
def init_command(
    agents: tuple[str, ...],
    all_agents: bool,
    assume_yes: bool,
    skip_auth: bool,
    skip_skills: bool,
    api_key: str | None,
    browser: bool | None,
    json_output: bool,
) -> None:
    """Authenticate, install Tavily skills, and verify a working CLI."""
    if all_agents and agents:
        raise click.UsageError("Use either --all or --agent, not both.")
    if skip_auth and api_key:
        raise click.UsageError("Use either --skip-auth or --api-key, not both.")

    selected_agents = _select_agents(agents, all_agents, assume_yes, skip_skills, json_output)
    result = _empty_result(selected_agents)
    result["skills"]["skipped"] = skip_skills

    try:
        auth = _ensure_auth(api_key=api_key, skip_auth=skip_auth, browser=browser, json_output=json_output)
    except Exception as exc:
        _fail(result, "auth", str(exc), 3, json_output)
        return
    result["auth"] = auth
    result["mode"] = "authenticated" if auth["authenticated"] else "keyless"
    result["verification"]["auth"] = auth["authenticated"]

    if not skip_skills:
        try:
            skills_result = install_skills(selected_agents)
            result["skills"].update({
                "installed": skills_result.installed,
                "updated": skills_result.updated,
                "unchanged": skills_result.unchanged,
                "total": skills_result.total,
                "linked": skills_result.linked,
                "restart_required": skills_result.restart_required,
            })
            result["verification"]["skills"] = verify_skills(selected_agents)
            if not result["verification"]["skills"]:
                raise RuntimeError("Installed Tavily skills could not be verified.")
        except Exception as exc:
            _fail(result, "skills", str(exc), 1, json_output)
            return

    result["verification"]["cli"] = _verify_cli()
    if not result["verification"]["cli"]:
        _fail(result, "cli", "tvly is not available on PATH.", 1, json_output)
        return

    try:
        result["verification"]["live_search"] = _verify_live_search()
    except Exception as exc:
        _fail(result, "live_search", str(exc), 4, json_output)
        return
    if not result["verification"]["live_search"]:
        _fail(result, "live_search", "Tavily search returned no result payload.", 4, json_output)
        return

    result["ok"] = True
    _print_result(result, json_output=json_output)


def _select_agents(
    requested: tuple[str, ...],
    all_agents: bool,
    assume_yes: bool,
    skip_skills: bool,
    json_output: bool,
) -> tuple[str, ...]:
    if skip_skills:
        return ()
    if requested:
        return tuple(dict.fromkeys(name.lower() for name in requested))

    detected = detect_agents()
    if not detected or all_agents or assume_yes or json_output or not sys.stdin.isatty():
        return detected

    label = ", ".join(detected)
    if click.confirm(f"Install Tavily skills for detected agents ({label})?", default=True):
        return detected
    return ()


def _ensure_auth(
    *,
    api_key: str | None,
    skip_auth: bool,
    browser: bool | None,
    json_output: bool,
) -> dict[str, Any]:
    from tavily_cli.config import get_api_key, save_api_key, save_oauth_session

    if api_key:
        save_api_key(api_key)
    key = get_api_key()
    if key:
        return {"authenticated": True, "method": _auth_method(key)}
    if skip_auth:
        return {"authenticated": False, "method": "none"}

    if browser is None and not sys.stdin.isatty():
        raise RuntimeError(
            "No Tavily credential found. Use --api-key, --browser, --no-browser, or --skip-auth in non-interactive runs."
        )

    from tavily_cli.oauth import looks_headless, run_browser_login

    open_browser = browser if browser is not None else not looks_headless()

    def show_url(url: str) -> None:
        click.echo(f"Open this URL to authenticate: {url}", err=True)

    session = run_browser_login(open_browser=open_browser, on_status=show_url)
    save_oauth_session(session)
    return {"authenticated": True, "method": "oauth"}


def _auth_method(key: str) -> str:
    from tavily_cli.config import has_stored_oauth, is_oauth_token

    if os.environ.get("TAVILY_API_KEY"):
        return "env"
    if is_oauth_token(key):
        return "oauth" if has_stored_oauth() else "oauth_legacy"
    return "api_key"


def _verify_cli() -> bool:
    return shutil.which("tvly") is not None


def _verify_live_search() -> bool:
    from tavily_cli.config import get_client_or_keyless

    client, _ = get_client_or_keyless(client_name="tavily-cli-init")
    response = client.search(query="Tavily Search API", max_results=1)
    return isinstance(response, dict) and isinstance(response.get("results"), list)


def _empty_result(agents: tuple[str, ...]) -> dict[str, Any]:
    return {
        "ok": False,
        "version": __version__,
        "mode": "unknown",
        "auth": {"authenticated": False, "method": "none"},
        "skills": {
            "source": SKILLS_SOURCE,
            "agents": list(agents),
            "installed": 0,
            "updated": 0,
            "unchanged": 0,
            "total": 0,
            "linked": 0,
            "restart_required": False,
            "skipped": False,
        },
        "verification": {
            "cli": False,
            "auth": False,
            "skills": None,
            "live_search": False,
        },
    }


def _fail(result: dict[str, Any], stage: str, message: str, exit_code: int, json_output: bool) -> None:
    result["error"] = {"stage": stage, "message": message}
    _print_result(result, json_output=json_output)
    raise click.exceptions.Exit(exit_code)


def _print_result(result: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        click.echo(json.dumps(result, sort_keys=True))
        return

    from rich.markup import escape

    from tavily_cli.common import sanitize_control
    from tavily_cli.theme import console

    console.print()
    if result["ok"]:
        console.print("  [bold #9BC0AE]> Tavily is ready[/bold #9BC0AE]")
    else:
        error = result.get("error", {})
        message = escape(sanitize_control(error.get("message", "Initialization failed.")))
        console.print(f"  [bold #FAA2FB]> Setup failed:[/bold #FAA2FB] {message}")
        console.print()
        return

    auth = result["auth"]
    if auth["authenticated"]:
        console.print(f"    [#9BC0AE]✓[/#9BC0AE] Authenticated via {auth['method']}")
    else:
        console.print("    [#FFC769]•[/#FFC769] Keyless mode")

    skills = result["skills"]
    if skills["skipped"]:
        console.print("    [dim]• Skills skipped[/dim]")
    else:
        agents = ", ".join(skills["agents"]) or "shared agent directory"
        console.print(f"    [#9BC0AE]✓[/#9BC0AE] {skills['total']} Tavily skills ready for {agents}")
    console.print("    [#9BC0AE]✓[/#9BC0AE] Live search verified")

    if skills["restart_required"] and skills["agents"]:
        console.print()
        console.print(f"    [dim]Restart {', '.join(skills['agents'])} to load newly installed skills.[/dim]")

    console.print()
    console.print("  Try:")
    console.print('    [#9BC0AE]tvly search "latest AI news"[/#9BC0AE]')
    console.print("    [#9BC0AE]tvly extract https://example.com[/#9BC0AE]")
    console.print("    [#9BC0AE]tvly research \"your topic\"[/#9BC0AE]")
    console.print()
