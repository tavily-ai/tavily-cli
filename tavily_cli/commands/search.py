"""tavily search — web search via the Tavily API."""

from __future__ import annotations

import sys

import click
from tavily import TavilyKeylessLimitError

from tavily_cli.common import (
    client_name_option,
    handle_api_error,
    handle_keyless_cap_hit,
    handle_oauth_refresh_error,
    json_option,
)


@click.command()
@click.argument("query", required=False)
@click.option("--depth", "search_depth", type=click.Choice(["ultra-fast", "fast", "basic", "advanced"]), default=None, help="Search depth (default: basic).")
@click.option("--max-results", type=int, default=None, help="Maximum results, 0-20 (default: 5).")
@click.option("--topic", type=click.Choice(["general", "news", "finance"]), default=None, help="Search topic.")
@click.option("--time-range", type=click.Choice(["day", "week", "month", "year"]), default=None, help="Relative time filter.")
@click.option("--start-date", default=None, help="Results after date (YYYY-MM-DD).")
@click.option("--end-date", default=None, help="Results before date (YYYY-MM-DD).")
@click.option("--include-domains", default=None, help="Comma-separated domains to include.")
@click.option("--exclude-domains", default=None, help="Comma-separated domains to exclude.")
@click.option("--country", default=None, help="Boost results from country.")
@click.option("--include-answer", is_flag=False, flag_value="basic", default=None, help="Include AI answer (pass 'basic' or 'advanced').")
@click.option("--include-raw-content", is_flag=False, flag_value="markdown", default=None, help="Include full page content ('markdown' or 'text').")
@click.option("--include-images", is_flag=True, default=False, help="Include image results.")
@click.option("--include-image-descriptions", is_flag=True, default=False, help="Include AI image descriptions.")
@click.option("--chunks-per-source", type=int, default=None, help="Chunks per source (advanced/fast depth only).")
@click.option("--output", "-o", "output_file", default=None, help="Save as JSON (.json) or Markdown (.md).")
@click.option("--save", is_flag=True, default=False, help="Save JSON under .tavily/search/.")
@click.option("--force", is_flag=True, default=False, help="Overwrite an existing output file.")
@click.option("--jsonl", is_flag=True, default=False, help="Output one search result per JSON line.")
@client_name_option
@json_option
def search(
    query: str | None,
    search_depth: str | None,
    max_results: int | None,
    topic: str | None,
    time_range: str | None,
    start_date: str | None,
    end_date: str | None,
    include_domains: str | None,
    exclude_domains: str | None,
    country: str | None,
    include_answer: str | None,
    include_raw_content: str | None,
    include_images: bool,
    include_image_descriptions: bool,
    chunks_per_source: int | None,
    output_file: str | None,
    save: bool,
    force: bool,
    jsonl: bool,
    client_name: str | None,
    json_output: bool,
) -> None:
    """Search the web using Tavily.

    QUERY is the search query. Use "-" to read from stdin.

    Works without an API key (subject to a rate-limit cap). Run
    `tvly login` to authenticate and remove the cap.
    """
    from tavily_cli.config import get_client_or_keyless
    from tavily_cli.output import print_search_results, validate_artifact_options

    if json_output and jsonl:
        raise click.UsageError("Use either --json or --jsonl, not both.")
    machine_mode = json_output or jsonl

    if query == "-":
        query = sys.stdin.read(100_000).strip()
    if not query:
        raise click.UsageError("QUERY is required. Pass a query string or use '-' to read from stdin.")

    validate_artifact_options(
        output_file=output_file,
        save=save,
        force=force,
    )

    kwargs: dict = {"query": query}
    if search_depth is not None:
        kwargs["search_depth"] = search_depth
    if max_results is not None:
        kwargs["max_results"] = max_results
    if topic is not None:
        kwargs["topic"] = topic
    if time_range is not None:
        kwargs["time_range"] = time_range
    if start_date is not None:
        kwargs["start_date"] = start_date
    if end_date is not None:
        kwargs["end_date"] = end_date
    if include_domains:
        kwargs["include_domains"] = [d.strip() for d in include_domains.split(",")]
    if exclude_domains:
        kwargs["exclude_domains"] = [d.strip() for d in exclude_domains.split(",")]
    if country is not None:
        kwargs["country"] = country
    if include_answer is not None:
        kwargs["include_answer"] = include_answer
    if include_raw_content is not None:
        kwargs["include_raw_content"] = include_raw_content
    if include_images:
        kwargs["include_images"] = True
    if include_image_descriptions:
        kwargs["include_image_descriptions"] = True
    if chunks_per_source is not None:
        kwargs["chunks_per_source"] = chunks_per_source

    from tavily_cli.oauth import OAuthError
    from tavily_cli.theme import spinner

    try:
        client, _is_keyless = get_client_or_keyless(client_name=client_name)
        with spinner("Searching...", json_mode=machine_mode):
            response = client.search(**kwargs)
    except TavilyKeylessLimitError as e:
        handle_keyless_cap_hit(e, machine_mode)
    except OAuthError as e:
        handle_oauth_refresh_error(e, machine_mode)
    except Exception as e:
        handle_api_error(e, machine_mode)

    print_search_results(
        response,
        json_mode=json_output,
        jsonl_mode=jsonl,
        output_file=output_file,
        save=save,
        force=force,
    )
