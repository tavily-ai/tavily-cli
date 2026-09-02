"""tavily extract — extract content from URLs via the Tavily API."""

from __future__ import annotations

import click
from tavily import TavilyKeylessLimitError

from tavily_cli.common import (
    client_name_option,
    error_payload,
    handle_api_error,
    handle_keyless_cap_hit,
    handle_oauth_refresh_error,
    json_option,
)


@click.command()
@click.argument("urls", nargs=-1, required=True)
@click.option("--query", default=None, help="Rerank chunks by relevance to this query.")
@click.option("--chunks-per-source", type=int, default=None, help="Chunks per source (1-5, requires --query).")
@click.option("--extract-depth", type=click.Choice(["basic", "advanced"]), default=None, help="Extraction depth.")
@click.option("--format", "fmt", type=click.Choice(["markdown", "text"]), default=None, help="Output format.")
@click.option("--include-images", is_flag=True, default=False, help="Include image URLs.")
@click.option("--timeout", type=float, default=None, help="Max wait time in seconds (1-60).")
@click.option("--output", "-o", "output_file", default=None, help="Save as JSON (.json) or Markdown (.md).")
@click.option("--save", is_flag=True, default=False, help="Save JSON under .tavily/extract/.")
@click.option("--force", is_flag=True, default=False, help="Overwrite an existing output file.")
@click.option("--fail-on-partial", is_flag=True, default=False, help="Exit nonzero if any URL fails.")
@click.option("--jsonl", is_flag=True, default=False, help="Output one extraction record per JSON line.")
@client_name_option
@json_option
def extract(
    urls: tuple[str, ...],
    query: str | None,
    chunks_per_source: int | None,
    extract_depth: str | None,
    fmt: str | None,
    include_images: bool,
    timeout: float | None,
    output_file: str | None,
    save: bool,
    force: bool,
    fail_on_partial: bool,
    jsonl: bool,
    client_name: str | None,
    json_output: bool,
) -> None:
    """Extract content from one or more URLs.

    Provide URLs as positional arguments (max 20).

    Works without an API key (subject to a rate-limit cap). Run
    `tvly login` to authenticate and remove the cap.
    """
    from tavily_cli.config import get_client_or_keyless
    from tavily_cli.output import print_extract_results, validate_artifact_options

    if json_output and jsonl:
        raise click.UsageError("Use either --json or --jsonl, not both.")

    validate_artifact_options(
        output_file=output_file,
        save=save,
        force=force,
    )

    url_list = list(urls)
    if len(url_list) > 20:
        raise click.UsageError("Maximum 20 URLs per request.")

    kwargs: dict = {"urls": url_list}
    if query is not None:
        kwargs["query"] = query
    if chunks_per_source is not None:
        kwargs["chunks_per_source"] = chunks_per_source
    if extract_depth is not None:
        kwargs["extract_depth"] = extract_depth
    if fmt is not None:
        kwargs["format"] = fmt
    if include_images:
        kwargs["include_images"] = True
    if timeout is not None:
        kwargs["timeout"] = timeout

    from tavily_cli.oauth import OAuthError
    from tavily_cli.theme import spinner

    try:
        client, _is_keyless = get_client_or_keyless(client_name=client_name)
        with spinner(
            f"Extracting {len(url_list)} URL{'s' if len(url_list) > 1 else ''}...",
            json_mode=json_output or jsonl,
        ):
            response = client.extract(**kwargs)
    except TavilyKeylessLimitError as e:
        handle_keyless_cap_hit(e, json_output or jsonl)
    except OAuthError as e:
        handle_oauth_refresh_error(e, json_output or jsonl)
    except Exception as e:
        handle_api_error(e, json_output or jsonl)

    failed = response.get("failed_results") or []
    failure = None
    if fail_on_partial and failed:
        failure = error_payload(
            "extract_partial_failure",
            f"{len(failed)} URL{'s' if len(failed) != 1 else ''} failed to extract.",
            stage="extract",
            retryable=False,
        )

    print_extract_results(
        response,
        json_mode=json_output,
        jsonl_mode=jsonl,
        output_file=output_file,
        save=save,
        force=force,
        failure=failure,
    )
    if failure:
        raise click.exceptions.Exit(5)
