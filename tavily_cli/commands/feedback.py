"""tavily feedback — submit feedback on a search request or session via the Tavily API."""

from __future__ import annotations

import json
from pathlib import Path

import click

from tavily_cli.common import client_name_option, handle_api_error, json_option


def _parse_score(value: str | None) -> float | str | None:
    """Parse a score flag: numeric strings become floats, anything else passes through."""
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return value


def _parse_json_list(value: str | None, flag_name: str) -> list | None:
    """Parse a nested-list flag: inline JSON, or a path to a JSON file.

    Tries inline JSON first — an inline array can be long enough to exceed the
    OS filename length limit, which makes Path.is_file() raise OSError instead
    of returning False.
    """
    if value is None:
        return None
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        try:
            is_file = Path(value).is_file()
        except OSError:
            is_file = False
        if not is_file:
            raise click.UsageError(f"{flag_name} must be a JSON array or a path to a JSON file.")
        try:
            data = json.loads(Path(value).read_text())
        except json.JSONDecodeError as e:
            raise click.UsageError(f"{flag_name} must be a JSON array or a path to a JSON file: {e}")
    if not isinstance(data, list):
        raise click.UsageError(f"{flag_name} must be a JSON array.")
    return data


def _post_feedback(client, payload: dict) -> dict:
    """POST /feedback using the client's own authenticated session.

    tavily-python has no public .feedback() method yet; this reuses the
    client's session/base_url/error-handling so behavior matches every
    other command until the SDK adds one.
    """
    response = client.session.post(f"{client.base_url}/feedback", json=payload, timeout=30)
    if not response.ok:
        client._handle_error_response(response)
    return response.json()


@click.command()
@click.option("--session-id", default=None, help="The session to give feedback on. Optional if --request-id is provided.")
@click.option("--request-id", default=None, help="The search request to give feedback on. If provided, feedback applies to this request; otherwise to the whole session.")
@click.option("--agent-score", default=None, help="Overall score (1 perfect, 0 irrelevant, -1 harmful) for how useful the results were.")
@click.option("--human-score", default=None, help="Feedback from the end user, if available (e.g. like/dislike).")
@click.option("--comment", default=None, help="Free-text explanation of the feedback. Required when agent-score is below 0.5.")
@click.option("--response-delivered", default=None, help="The final answer you produced using the search results.")
@click.option("--used-urls", default=None, help="URLs of the results you actually used: a JSON array of strings, inline or a path to a JSON file. Alternative to --used-ids.")
@click.option("--used-ids", default=None, help="IDs of the results you actually used: a JSON array of strings, inline or a path to a JSON file. Alternative to --used-urls.")
@click.option("--used-citations", default=None, help="Content snippets you used from the results: a JSON array of strings, inline or a path to a JSON file.")
@click.option("--urls-scores", default=None, help="Per-result feedback: a JSON array of {id|url, agent_score, scores, comment}, inline or a path to a JSON file.")
@click.option("--extra-scores", default=None, help="Additional labeled scores: a JSON array of {label, value}, inline or a path to a JSON file.")
@client_name_option
@json_option
def feedback(
    session_id: str | None,
    request_id: str | None,
    agent_score: str | None,
    human_score: str | None,
    comment: str | None,
    response_delivered: str | None,
    used_urls: str | None,
    used_ids: str | None,
    used_citations: str | None,
    urls_scores: str | None,
    extra_scores: str | None,
    client_name: str | None,
    json_output: bool,
) -> None:
    """Submit feedback on a search request or session.

    Requires a Tavily API key. Sign up at https://tavily.com
    """
    from tavily_cli.config import get_client, require_api_key_friendly
    from tavily_cli.mcp_client import McpTavilyClient
    from tavily_cli.output import print_feedback_result

    if not session_id and not request_id:
        raise click.UsageError("Either --session-id or --request-id is required.")

    require_api_key_friendly("feedback")
    client = get_client(client_name=client_name)

    kwargs: dict = {}
    if session_id is not None:
        kwargs["session_id"] = session_id
    if request_id is not None:
        kwargs["request_id"] = request_id
    if agent_score is not None:
        kwargs["agent_score"] = _parse_score(agent_score)
    if human_score is not None:
        kwargs["human_score"] = _parse_score(human_score)
    if comment is not None:
        kwargs["comment"] = comment
    if response_delivered is not None:
        kwargs["response_delivered"] = response_delivered
    if used_urls is not None:
        kwargs["used_urls"] = _parse_json_list(used_urls, "--used-urls")
    if used_ids is not None:
        kwargs["used_ids"] = _parse_json_list(used_ids, "--used-ids")
    if used_citations is not None:
        kwargs["used_citations"] = _parse_json_list(used_citations, "--used-citations")
    if urls_scores is not None:
        kwargs["urls_scores"] = _parse_json_list(urls_scores, "--urls-scores")
    if extra_scores is not None:
        kwargs["extra_scores"] = _parse_json_list(extra_scores, "--extra-scores")

    from tavily_cli.theme import spinner

    try:
        with spinner("Submitting feedback...", json_mode=json_output):
            if isinstance(client, McpTavilyClient):
                response = client.feedback(**kwargs)
            else:
                response = _post_feedback(client, kwargs)
    except Exception as e:
        handle_api_error(e, json_output)

    print_feedback_result(response, json_mode=json_output)
