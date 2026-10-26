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
@click.option("--max-results", type=click.IntRange(0, 20), default=None, help="Maximum results, 0-20 (API default: 10).")
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
@click.option("--chunks-per-source", type=click.IntRange(1, 3), default=None, help="Chunks per source, 1-3 (basic, advanced and fast depth).")
@click.option("--include-domains-mode", type=click.Choice(["prefer", "restrict"]), default=None, help="With --include-domains: prefer boosts them, restrict returns only those hosts and subdomains. API-key login only.")
@click.option("--language", default=None, help="Boost results in this language (for example english). API-key login only.")
@click.option("--filter-by-language", is_flag=True, default=False, help="Return only results in --language (requires --language). API-key login only.")
@click.option("--safe-search", is_flag=True, default=False, help="Filter unsafe content (not available with fast or ultra-fast depth). API-key login only.")
@click.option("--exact-match", is_flag=True, default=False, help="Require the quoted phrases in the query to match exactly.")
@click.option("--auto-parameters", is_flag=True, default=False, help="Let Tavily choose search parameters from the query (may use advanced depth, 2 credits). API-key login only.")
@click.option("--include-favicon", is_flag=True, default=False, help="Include each result's favicon URL.")
@click.option("--include-usage", is_flag=True, default=False, help="Include credit usage in the response. API-key login only.")
@click.option("--include-published-date", is_flag=True, default=False, help="Include published_date per result (beta). API-key login only.")
@click.option("--filter-by-published-date", is_flag=True, default=False, help="Drop results outside the date window, including undated ones; implies --include-published-date. API-key login only.")
@click.option("--output", "-o", "output_file", default=None, help="Save output to file.")
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
    include_domains_mode: str | None,
    language: str | None,
    filter_by_language: bool,
    safe_search: bool,
    exact_match: bool,
    auto_parameters: bool,
    include_favicon: bool,
    include_usage: bool,
    include_published_date: bool,
    filter_by_published_date: bool,
    output_file: str | None,
    client_name: str | None,
    json_output: bool,
) -> None:
    """Search the web using Tavily.

    QUERY is the search query. Use "-" to read from stdin.

    Works without an API key (subject to a rate-limit cap). Run
    `tvly login` to authenticate and remove the cap.
    """
    from tavily_cli.config import get_client_or_keyless
    from tavily_cli.output import print_search_results

    if query == "-":
        query = sys.stdin.read(100_000).strip()
    if not query:
        raise click.UsageError("QUERY is required. Pass a query string or use '-' to read from stdin.")

    if include_domains_mode and not include_domains:
        raise click.UsageError("--include-domains-mode requires --include-domains.")
    if filter_by_language and not language:
        raise click.UsageError("--filter-by-language requires --language.")
    if safe_search and search_depth in ("fast", "ultra-fast"):
        raise click.UsageError("--safe-search is not available with --depth fast or ultra-fast.")

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
    if include_domains_mode is not None:
        kwargs["include_domains_mode"] = include_domains_mode
    if language is not None:
        kwargs["language"] = language
    if filter_by_language:
        kwargs["filter_by_language"] = True
    if safe_search:
        kwargs["safe_search"] = True
    if exact_match:
        kwargs["exact_match"] = True
    if auto_parameters:
        kwargs["auto_parameters"] = True
    if include_favicon:
        kwargs["include_favicon"] = True
    if include_usage:
        kwargs["include_usage"] = True
    if include_published_date:
        kwargs["include_published_date"] = True
    if filter_by_published_date:
        kwargs["filter_by_published_date"] = True

    from tavily_cli.oauth import OAuthError
    from tavily_cli.theme import spinner

    try:
        client, _is_keyless = get_client_or_keyless(client_name=client_name)
        with spinner("Searching...", json_mode=json_output):
            response = client.search(**kwargs)
    except TavilyKeylessLimitError as e:
        handle_keyless_cap_hit(e, json_output)
    except OAuthError as e:
        handle_oauth_refresh_error(e, json_output)
    except Exception as e:
        handle_api_error(e, json_output)

    print_search_results(response, json_mode=json_output, output_file=output_file)
