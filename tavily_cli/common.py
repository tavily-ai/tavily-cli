"""Shared CLI utilities."""

from __future__ import annotations

import json
import functools

import click


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


def handle_api_error(e: Exception, json_mode: bool) -> None:
    """Print an API error and exit with code 4."""
    if json_mode:
        click.echo(json.dumps({"error": str(e)}))
    else:
        from rich.console import Console
        Console(stderr=True).print(f"[red]API error:[/red] {e}")
    raise SystemExit(4)
