"""tavily search — web search via the Tavily API."""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor

import click
from tavily import TavilyKeylessLimitError

from tavily_cli.common import (
    client_name_option,
    handle_api_error,
    handle_keyless_cap_hit,
    handle_oauth_refresh_error,
    json_option,
)

MAX_QUERIES = 5
DEFAULT_CHARS = 600


@click.command()
@click.argument("queries", nargs=-1)
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
@click.option("--output", "-o", "output_file", default=None, help="Save the full JSON response to file.")
@click.option("--max-chars", type=click.IntRange(min=0), default=None, help="Characters of each result's content to print (default 600, shared across queries: 300 each for 2, 200 for 3); 0 prints it whole. --json and -o files keep it whole.")
@client_name_option
@json_option
def search(
    queries: tuple[str, ...],
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
    max_chars: int | None,
    client_name: str | None,
    json_output: bool,
) -> None:
    """Search the web using Tavily.

    QUERIES are one or more search queries (up to 5), run in parallel with the same
    options. Use "-" to read a single query from stdin.

    Works without an API key (subject to a rate-limit cap). Run
    `tvly login` to authenticate and remove the cap.
    """
    from tavily_cli.config import get_client_or_keyless
    from tavily_cli.output import print_multi_search_results, print_search_results

    if queries == ("-",):
        queries = (sys.stdin.read(100_000).strip(),)
    queries = tuple(q for q in queries if q.strip())
    if not queries:
        raise click.UsageError("QUERY is required. Pass a query string or use '-' to read from stdin.")
    if len(queries) > MAX_QUERIES:
        raise click.UsageError(f"At most {MAX_QUERIES} queries per command, got {len(queries)}.")

    if max_chars is None:
        # One search's worth of text per command, however many queries it holds.
        max_chars = DEFAULT_CHARS // len(queries)

    kwargs: dict = {}
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

    if len(queries) > 1:
        try:
            client, _is_keyless = get_client_or_keyless(client_name=client_name)
        except OAuthError as e:
            handle_oauth_refresh_error(e, json_output)
        except Exception as e:
            handle_api_error(e, json_output)

        def run(q: str) -> dict:
            try:
                return client.search(query=q, **kwargs)
            except Exception as e:  # one failed query must not lose the others' results
                return {"query": q, "error": str(e)}

        with spinner("Searching...", json_mode=json_output), ThreadPoolExecutor(len(queries)) as pool:
            responses = list(pool.map(run, queries))
        print_multi_search_results(responses, json_mode=json_output, output_file=output_file, max_chars=max_chars)
        if all("error" in r for r in responses):
            raise SystemExit(4)
        return

    try:
        client, _is_keyless = get_client_or_keyless(client_name=client_name)
        with spinner("Searching...", json_mode=json_output):
            response = client.search(query=queries[0], **kwargs)
    except TavilyKeylessLimitError as e:
        handle_keyless_cap_hit(e, json_output)
    except OAuthError as e:
        handle_oauth_refresh_error(e, json_output)
    except Exception as e:
        handle_api_error(e, json_output)

    print_search_results(response, json_mode=json_output, output_file=output_file, max_chars=max_chars)
