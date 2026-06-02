"""Shared CLI utilities."""

from __future__ import annotations

import json
import functools
import re

import click

from tavily import TavilyKeylessLimitError

from tavily_cli.keyless import format_keyless_envelope_for_terminal


# C0/C1 control and escape bytes, minus tab (\x09), newline (\x0a), and
# carriage return (\x0d). Stripping these from server- and web-derived text
# defeats ANSI/OSC terminal-escape injection (screen clears, cursor moves,
# window-title and clipboard writes) before content reaches a terminal.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def sanitize_control(value: object) -> str:
    """Strip terminal control/escape bytes from untrusted content.

    Rich does not sanitize raw escape sequences embedded in rendered strings
    (verified against Markdown(), Text.append(), and f-string markup), so any
    field that originates from the web, the API, or an MCP response must be
    passed through this before it is printed.
    """
    text = value if isinstance(value, str) else str(value)
    return _CONTROL_CHARS.sub("", text)


class TavilyAPIError(Exception):
    """Structured error from the Tavily API."""

    def __init__(self, message: str, *, status: int | None = None, docs: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.docs = docs


def handle_keyless_cap_hit(e: TavilyKeylessLimitError, json_mode: bool) -> None:
    """Render a keyless rate-limit cap-hit and exit non-zero."""
    if json_mode:
        click.echo(json.dumps({
            "error": {
                "code": e.code,
                "message": e.message,
                "window": e.window,
                "retry_after_seconds": e.retry_after_seconds,
                "next_actions": e.next_actions,
            }
        }))
        raise SystemExit(3)

    from tavily_cli.theme import err_console

    block = format_keyless_envelope_for_terminal(
        message=e.message,
        retry_after_seconds=e.retry_after_seconds,
        next_actions=e.next_actions,
    )
    err_console.print()
    for i, line in enumerate(block.splitlines()):
        if i == 0:
            err_console.print(f"  [#FFC769]>[/#FFC769] [bold]{line}[/bold]")
        elif not line:
            err_console.print()
        else:
            err_console.print(f"    {line}", markup=False, highlight=False)
    err_console.print()
    err_console.print(
        "  [dim]Run [/dim][#9BC0AE]tvly login[/#9BC0AE][dim] to authenticate "
        "and remove this cap.[/dim]"
    )
    err_console.print()
    raise SystemExit(3)


def json_option(func):
    """Add --json flag to a command and resolve from parent context if not set."""
    @click.option("--json", "json_output", is_flag=True, default=False, help="Output as JSON.")
    @functools.wraps(func)
    def wrapper(*args, json_output: bool = False, **kwargs):
        ctx = click.get_current_context()
        if not json_output:
            json_output = (ctx.parent and ctx.parent.obj or {}).get("json_output", False)
        kwargs["json_output"] = json_output
        return func(*args, **kwargs)
    return wrapper


# Status codes that represent usage/plan limits rather than real errors.
_LIMIT_STATUSES = {429, 432}


def handle_api_error(e: Exception, json_mode: bool) -> None:
    """Print an API error and exit."""
    if json_mode:
        click.echo(json.dumps({"error": str(e)}))
        raise SystemExit(4)

    from urllib.parse import urlparse

    from rich.markup import escape

    from tavily_cli.theme import err_console

    message = escape(sanitize_control(e))

    if isinstance(e, TavilyAPIError) and e.status in _LIMIT_STATUSES:
        err_console.print()
        err_console.print(f"  [#FFC769]>[/#FFC769] {message}")
        err_console.print()
        err_console.print("  [dim]Upgrade your plan at[/dim] [#9BC0AE link=https://tavily.com]tavily.com[/#9BC0AE link]")
        if e.docs:
            docs = sanitize_control(e.docs)
            safe_docs = escape(docs)
            if urlparse(docs).scheme in ("http", "https") and "[" not in docs and "]" not in docs:
                err_console.print(f"  [dim]Docs:[/dim] [dim link={docs}]{safe_docs}[/dim link]")
            else:
                err_console.print(f"  [dim]Docs:[/dim] {safe_docs}")
        err_console.print()
        raise SystemExit(3)

    err_console.print(f"  [#FAA2FB]> Error:[/#FAA2FB] {message}")
    raise SystemExit(4)
