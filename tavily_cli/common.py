"""Shared CLI utilities."""

from __future__ import annotations

import functools
import json
import re
from typing import Any

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


def error_payload(
    code: str,
    message: object,
    *,
    stage: str,
    retryable: bool,
    request_id: str | None = None,
    **details: Any,
) -> dict[str, Any]:
    """Build the stable machine-readable failure envelope."""
    error: dict[str, Any] = {
        "code": code,
        "message": sanitize_control(message),
        "stage": stage,
        "retryable": retryable,
    }
    if request_id:
        error["request_id"] = request_id
    error.update({key: value for key, value in details.items() if value is not None})
    return {"ok": False, "error": error}


def emit_error(
    code: str,
    message: object,
    *,
    stage: str,
    retryable: bool,
    request_id: str | None = None,
    **details: Any,
) -> None:
    """Write one stable error document to stdout."""
    click.echo(
        json.dumps(
            error_payload(
                code,
                message,
                stage=stage,
                retryable=retryable,
                request_id=request_id,
                **details,
            ),
            ensure_ascii=False,
        )
    )


def handle_keyless_cap_hit(e: TavilyKeylessLimitError, json_mode: bool) -> None:
    """Render a keyless rate-limit cap-hit and exit non-zero."""
    if json_mode:
        emit_error(
            e.code,
            e.message,
            stage="request",
            retryable=e.retry_after_seconds is not None,
            window=e.window,
            retry_after_seconds=e.retry_after_seconds,
            next_actions=e.next_actions,
        )
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


def handle_oauth_refresh_error(e: Exception, json_mode: bool) -> None:
    """Render a stored OAuth refresh failure without treating it as logout."""
    message = sanitize_control(e)
    if json_mode:
        emit_error(
            "oauth_refresh_failed",
            message,
            stage="auth",
            retryable=True,
        )
        raise SystemExit(3)

    from rich.markup import escape

    from tavily_cli.theme import err_console

    err_console.print(f"  [#FAA2FB]> OAuth refresh failed:[/#FAA2FB] {escape(message)}")
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


def client_name_option(func):
    """Add an optional client_name for request attribution."""
    return click.option(
        "--client-name",
        "client_name",
        default=None,
        help="Set optional client_name for request attribution.",
    )(func)


# Status codes that represent usage/plan limits rather than real errors.
_LIMIT_STATUSES = {429, 432}


def handle_api_error(
    e: Exception,
    json_mode: bool,
    *,
    code: str | None = None,
    stage: str = "request",
    retryable: bool | None = None,
    request_id: str | None = None,
) -> None:
    """Print an API error and exit."""
    status = e.status if isinstance(e, TavilyAPIError) else None
    is_limit = status in _LIMIT_STATUSES
    if code is None:
        if status == 401:
            code = "authentication_failed"
        elif is_limit:
            code = "api_limit_reached"
        else:
            code = "api_error"
    if retryable is None:
        retryable = status == 429

    if json_mode:
        emit_error(
            code,
            e,
            stage=stage,
            retryable=retryable,
            request_id=request_id,
            status=status,
        )
        raise SystemExit(3 if is_limit else 4)

    from urllib.parse import urlparse

    from rich.markup import escape

    from tavily_cli.theme import err_console

    message = escape(sanitize_control(e))

    if isinstance(e, TavilyAPIError) and is_limit:
        err_console.print()
        err_console.print(f"  [#FFC769]>[/#FFC769] {message}")
        err_console.print()
        err_console.print("  [dim]Upgrade your plan at[/dim] [#9BC0AE link=https://tavily.com]tavily.com[/#9BC0AE link]")
        if e.docs:
            docs = sanitize_control(e.docs)
            safe_docs = escape(docs)
            if "[" not in docs and "]" not in docs and urlparse(docs).scheme in ("http", "https"):
                err_console.print(f"  [dim]Docs:[/dim] [dim link={docs}]{safe_docs}[/dim link]")
            else:
                err_console.print(f"  [dim]Docs:[/dim] {safe_docs}")
        err_console.print()
        raise SystemExit(3)

    err_console.print(f"  [#FAA2FB]> Error:[/#FAA2FB] {message}")
    raise SystemExit(4)
