"""tvly usage — API-key and account credit usage."""

from __future__ import annotations

import click

from tavily_cli.common import emit_error, handle_api_error, handle_oauth_refresh_error, json_option


@click.command("usage")
@json_option
def usage_command(json_output: bool) -> None:
    """Show API-key, plan and PAYGO credits. Requires API-key authentication."""
    from tavily_cli.config import credential_mode, get_api_key
    from tavily_cli.keyless import KEYLESS_NOTICE
    from tavily_cli.oauth import OAuthError
    from tavily_cli.output import emit
    from tavily_cli.usage import USAGE_API_KEY_HINT, UsageError, fetch_usage, usage_lines

    try:
        key = get_api_key()
    except OAuthError as exc:
        handle_oauth_refresh_error(exc, json_output)
    mode = credential_mode(key)
    if mode != "api_key":
        message = f"{KEYLESS_NOTICE} {USAGE_API_KEY_HINT}" if mode == "keyless" else USAGE_API_KEY_HINT
        if json_output:
            emit_error("usage_requires_api_key", message, stage="auth", retryable=False, mode=mode)
        else:
            click.echo(message, err=True)
        raise SystemExit(3)
    try:
        data = fetch_usage(key)
    except UsageError as exc:
        handle_api_error(exc, json_output, stage="usage", retryable=exc.retryable)
    if json_output:
        emit(data, json_mode=True, pretty=True)
    else:
        for line in usage_lines(data):
            click.echo(line)
