"""Offline tests for Research stream integrity, Search/Crawl/Map validation and the OAuth option guard.

No network and no credentials: the SDK path is checked at requests.Session.post, the OAuth path at httpx.post.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from tavily_cli import config as config_module
from tavily_cli.cli import cli
from tavily_cli.mcp_client import _API_KEY_ONLY_ARGUMENTS, _reject_api_key_only_arguments

API_KEY = "tvly-test-key-not-real"


def _jwt() -> str:
    def part(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part({'sub': 'tester', 'exp': 4102444800})}.sig"


def run(*args: str):
    return CliRunner().invoke(cli, list(args))


class FakeResponse:
    text = ""

    def __init__(self, payload: dict, status: int = 200) -> None:
        self.status_code = status
        self._payload = payload

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        return None


@pytest.fixture
def sdk_posts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    monkeypatch.setattr(config_module, "CONFIG_FILE", tmp_path / "config.json")
    for name in ("TAVILY_CLIENT_NAME", "TAVILY_HUMAN_ID", "TAVILY_API_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", API_KEY)
    sent: list[dict] = []

    def post(self, url, data=None, headers=None, **kwargs):
        sent.append({"url": url, "body": json.loads(data)})
        return FakeResponse({"results": [], "query": "q", "base_url": "https://e.x"})

    import requests

    monkeypatch.setattr(requests.Session, "post", post)
    return sent


@pytest.fixture
def oauth_posts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    monkeypatch.setattr(config_module, "CONFIG_FILE", tmp_path / "config.json")
    for name in ("TAVILY_CLIENT_NAME", "TAVILY_HUMAN_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", _jwt())
    sent: list[dict] = []

    class Response:
        text = 'data: {"jsonrpc":"2.0","id":1,"result":{"structuredContent":{"results":[],"query":"q"}}}'

        def raise_for_status(self) -> None:
            return None

    def post(url, json=None, headers=None, **kwargs):
        sent.append({"url": url, "body": json})
        return Response()

    import httpx

    monkeypatch.setattr(httpx, "post", post)
    return sent


# --- Research stream integrity -------------------------------------------------------------------

def _frame(delta: dict) -> str:
    return "event: chat.completion.chunk\ndata: " + json.dumps({"choices": [{"delta": delta}]}) + "\n\n"


FULL_STREAM = (
    _frame({"content": "Hello "})
    + _frame({"content": "wörld — report"})
    + _frame({"sources": [{"title": "Src", "url": "https://example.com/a"}]})
    + "event: done\n\n"
)


@pytest.fixture
def stream_with(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config_module, "CONFIG_FILE", tmp_path / "config.json")
    for name in ("TAVILY_CLIENT_NAME", "TAVILY_HUMAN_ID", "TAVILY_API_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", API_KEY)
    import tavily

    def install(chunks):
        monkeypatch.setattr(tavily.TavilyClient, "research", lambda self, **kw: iter(chunks))

    return install


def _split(text: str, size: int) -> list[bytes]:
    raw = text.encode("utf-8")
    return [raw[i : i + size] for i in range(0, len(raw), size)]


def test_complete_stream_is_rendered(stream_with) -> None:
    stream_with([FULL_STREAM.encode()])
    result = run("research", "q", "--stream")

    assert result.exit_code == 0, result.output
    assert "Hello wörld — report" in result.output


@pytest.mark.parametrize("size", [1, 3, 7, 50])
def test_records_split_across_transport_chunks_and_inside_characters_are_reassembled(stream_with, size: int) -> None:
    stream_with(_split(FULL_STREAM, size))
    result = run("research", "q", "--stream")

    assert result.exit_code == 0, result.output
    assert "Hello wörld — report" in result.output
    assert "https://example.com/a" in result.output


def test_crlf_framing_is_accepted(stream_with) -> None:
    stream_with(_split(FULL_STREAM.replace("\n", "\r\n"), 11))
    result = run("research", "q", "--stream")

    assert result.exit_code == 0, result.output
    assert "Hello wörld — report" in result.output


def test_unterminated_final_done_frame_counts_as_done(stream_with) -> None:
    stream_with([FULL_STREAM.rstrip("\n").encode()])
    assert run("research", "q", "--stream").exit_code == 0


def test_stream_without_done_event_fails_instead_of_reporting_completed(stream_with) -> None:
    stream_with([_frame({"content": "partial report"}).encode()])
    result = run("research", "q", "--stream")

    assert result.exit_code != 0
    assert "before the final 'done' event" in result.output


def test_empty_stream_fails(stream_with) -> None:
    stream_with([])
    result = run("research", "q", "--stream")

    assert result.exit_code != 0


def test_error_event_fails_with_its_message(stream_with) -> None:
    error = 'data: {"id": "x", "object": "error", "error": "An error occurred while streaming the research task"}\n\n'
    stream_with([_frame({"content": "partial"}).encode(), error.encode(), b"event: done\n\n"])
    result = run("research", "q", "--stream")

    assert result.exit_code != 0
    assert "Research stream failed" in result.output
    assert "An error occurred while streaming" in result.output


def test_structured_output_object_is_kept(stream_with) -> None:
    obj = {"company": "Acme", "summary": "growing"}
    stream_with([(_frame({"content": obj}) + "event: done\n\n").encode()])
    result = run("research", "q", "--stream")

    assert result.exit_code == 0, result.output
    assert "Acme" in result.output


def test_json_mode_stream_requires_done_and_emits_one_line_per_event(stream_with) -> None:
    stream_with(_split(FULL_STREAM, 5))
    ok = run("research", "q", "--stream", "--json")
    assert ok.exit_code == 0, ok.output
    lines = [json.loads(line) for line in ok.output.strip().splitlines()]
    assert len(lines) == 3

    stream_with([_frame({"content": "partial"}).encode()])
    partial = run("research", "q", "--stream", "--json")
    assert partial.exit_code != 0


# --- Search / Crawl / Map validation -------------------------------------------------------------

def test_search_rejects_chunks_with_ultra_fast_before_any_client_is_built(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **k):
        raise AssertionError("client must not be constructed")

    monkeypatch.setattr(config_module, "get_client_or_keyless", boom)
    result = run("search", "q", "--depth", "ultra-fast", "--chunks-per-source", "2")

    assert result.exit_code == 2
    assert "--chunks-per-source does not apply with --depth ultra-fast" in result.output


@pytest.mark.parametrize("depth", [None, "basic", "advanced", "fast"])
def test_search_allows_chunks_for_other_depths(sdk_posts: list[dict], depth: str | None) -> None:
    args = ["search", "q", "--chunks-per-source", "2", "--json"] + (["--depth", depth] if depth else [])
    assert run(*args).exit_code == 0
    assert sdk_posts[0]["body"]["chunks_per_source"] == 2


def test_crawl_chunks_require_instructions_and_are_bounded(sdk_posts: list[dict]) -> None:
    no_instructions = run("crawl", "https://example.com", "--chunks-per-source", "2", "--json")
    assert no_instructions.exit_code == 2 and "requires --instructions" in no_instructions.output
    too_many = run("crawl", "https://example.com", "--instructions", "docs", "--chunks-per-source", "6", "--json")
    assert too_many.exit_code == 2
    assert sdk_posts == []

    ok = run("crawl", "https://example.com", "--instructions", "docs", "--chunks-per-source", "5", "--json")
    assert ok.exit_code == 0, ok.output
    assert sdk_posts[0]["body"]["chunks_per_source"] == 5


@pytest.mark.parametrize("command", ["crawl", "map"])
@pytest.mark.parametrize("timeout", ["9", "151"])
def test_crawl_and_map_timeout_is_bounded(sdk_posts: list[dict], command: str, timeout: str) -> None:
    result = run(command, "https://example.com", "--timeout", timeout, "--json")

    assert result.exit_code == 2
    assert sdk_posts == []


@pytest.mark.parametrize("command", ["crawl", "map"])
@pytest.mark.parametrize("timeout", ["10", "150"])
def test_crawl_and_map_timeout_bounds_are_inclusive(sdk_posts: list[dict], command: str, timeout: str) -> None:
    assert run(command, "https://example.com", "--timeout", timeout, "--json").exit_code == 0
    assert sdk_posts[0]["body"]["timeout"] == float(timeout)


# --- OAuth guard against the pinned hosted MCP schemas ------------------------------------------

@pytest.mark.parametrize(
    "args, flag",
    [
        (["search", "q", "--chunks-per-source", "2"], "--chunks-per-source"),
        (["search", "q", "--topic", "finance"], "--topic finance"),
        (["search", "q", "--include-answer"], "--include-answer"),
        (["search", "q", "--include-raw-content"], "--include-raw-content"),
        (["extract", "https://example.com", "--query", "q", "--chunks-per-source", "2"], "--chunks-per-source"),
        (["extract", "https://example.com", "--timeout", "20"], "--timeout"),
        (["extract", "https://example.com", "--include-usage"], "--include-usage"),
        (["crawl", "https://example.com", "--instructions", "x", "--chunks-per-source", "2"], "--chunks-per-source"),
        (["crawl", "https://example.com", "--timeout", "20"], "--timeout"),
        (["crawl", "https://example.com", "--include-usage"], "--include-usage"),
        (["map", "https://example.com", "--timeout", "20"], "--timeout"),
        (["map", "https://example.com", "--include-usage"], "--include-usage"),
    ],
)
def test_oauth_rejects_arguments_the_pinned_hosted_tools_do_not_accept(oauth_posts: list[dict], args: list[str], flag: str) -> None:
    result = run(*args, "--json")

    assert result.exit_code != 0
    assert f"{flag} is not available with OAuth login" in result.output
    assert oauth_posts == []


@pytest.mark.parametrize(
    "args, key, value",
    [
        (["search", "q", "--topic", "news"], "topic", "news"),
        (["search", "q", "--topic", "general"], "topic", "general"),
        (["search", "q", "--exact-match", "--include-favicon"], "exact_match", True),
        (["crawl", "https://example.com", "--include-favicon"], "include_favicon", True),
        (["extract", "https://example.com", "--query", "q", "--include-favicon"], "include_favicon", True),
    ],
)
def test_oauth_accepts_arguments_the_hosted_tools_support(oauth_posts: list[dict], args: list[str], key: str, value) -> None:
    result = run(*args, "--json")

    assert result.exit_code == 0, result.output
    assert oauth_posts[0]["body"]["params"]["arguments"][key] == value


def test_guard_is_value_aware_for_topic_and_raw_content() -> None:
    _reject_api_key_only_arguments("tavily_search", {"topic": "news", "include_raw_content": True})
    with pytest.raises(Exception):
        _reject_api_key_only_arguments("tavily_search", {"include_raw_content": "markdown"})
    with pytest.raises(Exception):
        _reject_api_key_only_arguments("tavily_search", {"topic": "finance"})


def test_guard_covers_every_tool_with_unsupported_arguments() -> None:
    assert set(_API_KEY_ONLY_ARGUMENTS) == {"tavily_search", "tavily_research", "tavily_extract", "tavily_crawl", "tavily_map"}


# --- RJSCLI2: complete OAuth guard, CR/LF-safe SSE framing, consistent guard documentation --------

import re

from tavily_cli.commands.research import _sse_records
from tavily_cli.mcp_client import OAUTH_MARKER, guarded_flags, render_oauth_readme_block

# Arguments each hosted MCP tool accepts, read from tavily-remote-mcp commit 835129d (remote_mcp/tools/*.py).
HOSTED_ACCEPTED = {
    "tavily_search": {
        "query", "max_results", "search_depth", "topic", "time_range", "include_images", "include_image_descriptions",
        "include_raw_content", "include_domains", "exclude_domains", "country", "include_favicon", "start_date",
        "end_date", "exact_match",
    },
    "tavily_extract": {"urls", "extract_depth", "include_images", "format", "include_favicon", "query"},
    "tavily_crawl": {
        "url", "max_depth", "max_breadth", "limit", "instructions", "select_paths", "select_domains",
        "allow_external", "extract_depth", "format", "include_favicon",
    },
    "tavily_map": {"url", "max_depth", "max_breadth", "limit", "instructions", "select_paths", "select_domains", "allow_external"},
    "tavily_research": {"input", "model"},
}
COMMAND_MODULES = {
    "tavily_search": "search.py",
    "tavily_extract": "extract.py",
    "tavily_crawl": "crawl.py",
    "tavily_map": "map_cmd.py",
    "tavily_research": "research.py",
}


@pytest.mark.parametrize(
    "args, flag",
    [
        (["crawl", "https://example.com", "--exclude-paths", "/private/.*"], "--exclude-paths"),
        (["crawl", "https://example.com", "--exclude-domains", "ads\\."], "--exclude-domains"),
        (["crawl", "https://example.com", "--include-images"], "--include-images"),
        (["map", "https://example.com", "--exclude-paths", "/private/.*"], "--exclude-paths"),
        (["map", "https://example.com", "--exclude-domains", "ads\\."], "--exclude-domains"),
    ],
)
def test_oauth_rejects_crawl_and_map_arguments_missing_from_the_hosted_schemas(oauth_posts: list[dict], args: list[str], flag: str) -> None:
    result = run(*args, "--json")

    assert result.exit_code != 0
    assert f"{flag} is not available with OAuth login" in result.output
    assert oauth_posts == [], "no MCP request may be made before the error"


@pytest.mark.parametrize(
    "args, sent_keys",
    [
        (["crawl", "https://example.com", "--select-paths", "/docs/.*", "--select-domains", "docs\\.", "--include-favicon"],
         {"select_paths", "select_domains", "include_favicon"}),
        (["map", "https://example.com", "--select-paths", "/docs/.*", "--select-domains", "docs\\.", "--no-external"],
         {"select_paths", "select_domains", "allow_external"}),
    ],
)
def test_oauth_still_sends_the_supported_crawl_and_map_arguments(oauth_posts: list[dict], args: list[str], sent_keys: set[str]) -> None:
    result = run(*args, "--json")

    assert result.exit_code == 0, result.output
    assert sent_keys <= set(oauth_posts[0]["body"]["params"]["arguments"])


@pytest.mark.parametrize("tool", sorted(COMMAND_MODULES))
def test_every_argument_a_command_can_send_is_hosted_supported_or_guarded(tool: str) -> None:
    source = (Path(__file__).resolve().parent.parent / "tavily_cli" / "commands" / COMMAND_MODULES[tool]).read_text()
    sendable = set(re.findall(r'kwargs\["(\w+)"\]', source))
    unguarded_unsupported = sendable - HOSTED_ACCEPTED[tool] - set(_API_KEY_ONLY_ARGUMENTS.get(tool, {}))

    assert unguarded_unsupported == set(), f"{tool}: arguments the hosted tool does not accept and the guard does not stop"


def test_guard_table_only_names_arguments_the_hosted_tools_do_not_accept() -> None:
    for tool, table in _API_KEY_ONLY_ARGUMENTS.items():
        for argument in table:
            value_aware = table[argument][1] is not None
            assert value_aware or argument not in HOSTED_ACCEPTED[tool], f"{tool}.{argument} is accepted by the hosted tool"


# --- SSE framing -----------------------------------------------------------------------------------

def _records(chunks) -> list[tuple[str | None, str]]:
    return list(_sse_records(iter(chunks)))


STREAM_BODY = ["event: x", "data: 1", "data: 2", "", "event: y", "data: 3", "", "event: done", ""]


def _joined(ending: str) -> bytes:
    return (ending.join(STREAM_BODY) + ending).encode()


EXPECTED = [("x", "1\n2"), ("y", "3"), ("done", "")]


@pytest.mark.parametrize("ending", ["\r\n", "\n", "\r"], ids=["crlf", "lf", "cr"])
def test_records_are_identical_at_every_split_position(ending: str) -> None:
    raw = _joined(ending)
    assert _records([raw]) == EXPECTED
    for position in range(len(raw) + 1):
        assert _records([raw[:position], raw[position:]]) == EXPECTED, f"split at byte {position}"


@pytest.mark.parametrize("ending", ["\r\n", "\n", "\r"], ids=["crlf", "lf", "cr"])
def test_records_survive_single_byte_chunks(ending: str) -> None:
    raw = _joined(ending)

    assert _records([raw[i : i + 1] for i in range(len(raw))]) == EXPECTED


def test_a_split_between_cr_and_lf_does_not_create_a_blank_line_inside_a_record() -> None:
    chunks = [b"event: x\r\ndata: 1\r", b"\ndata: 2\r\n\r\n"]

    assert _records(chunks) == [("x", "1\n2")]


def test_trailing_cr_at_end_of_stream_terminates_the_final_record() -> None:
    assert _records([b"event: done\r", b"\r"]) == [("done", "")]
    assert _records([b"event: done\r\n\r"]) == [("done", "")]


def test_cr_split_with_str_chunks_and_empty_chunks() -> None:
    assert _records(["event: x\r", "", "\ndata: 1\r\n\r\n"]) == [("x", "1")]


# --- Guard documentation ---------------------------------------------------------------------------

def _command_options():
    commands = {
        "tavily_search": cli.commands["search"],
        "tavily_extract": cli.commands["extract"],
        "tavily_crawl": cli.commands["crawl"],
        "tavily_map": cli.commands["map"],
        "tavily_research": cli.commands["research"].commands["run"],
    }
    for tool, command in commands.items():
        for param in command.params:
            flags = [opt for opt in param.opts if opt.startswith("--")]
            if flags and getattr(param, "help", None) is not None:
                yield tool, flags[0], param.help


def test_help_carries_the_marker_for_exactly_the_guarded_options() -> None:
    checked = 0
    for tool, flag, help_text in _command_options():
        guarded = flag in guarded_flags(tool)
        assert (OAUTH_MARKER in help_text) == guarded, f"{tool} {flag}: marker {'missing' if guarded else 'unexpected'}"
        checked += 1
    assert checked > 40


def test_every_guarded_flag_exists_as_a_cli_option() -> None:
    existing = {(tool, flag) for tool, flag, _ in _command_options()}
    for tool in _API_KEY_ONLY_ARGUMENTS:
        for flag in guarded_flags(tool):
            assert (tool, flag) in existing, f"{tool} {flag} is guarded but is not a CLI option"


def test_crawl_include_favicon_is_not_labelled_api_key_only() -> None:
    helps = {flag: text for tool, flag, text in _command_options() if tool == "tavily_crawl"}

    assert OAUTH_MARKER not in helps["--include-favicon"]
    assert OAUTH_MARKER in helps["--include-images"]


def test_readme_guard_list_matches_the_guard_table() -> None:
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
    block = readme.split("<!-- oauth-guard:start -->\n", 1)[1].split("\n<!-- oauth-guard:end -->", 1)[0]

    assert block == render_oauth_readme_block()


def test_readme_option_tables_mark_exactly_the_guarded_options() -> None:
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
    sections = {
        "### `tvly search`": "tavily_search",
        "### `tvly extract`": "tavily_extract",
        "### `tvly crawl`": "tavily_crawl",
        "### `tvly map`": "tavily_map",
        "### `tvly research <query>` / `tvly research run <query>`": "tavily_research",
    }
    seen = {tool: set() for tool in sections.values()}
    tool = None
    for line in readme.splitlines():
        if line.startswith("### "):
            tool = sections.get(line)
        row = re.match(r"^\| `(--[a-z-]+)`(?: / `--[a-z-]+`)? \| (.*) \|$", line)
        if tool and row:
            flag, description = row.groups()
            seen[tool].add(flag)
            guarded = flag in guarded_flags(tool)
            assert (OAUTH_MARKER.rstrip(".") in description) == guarded, f"{tool} {flag}: README marker mismatch"
    for tool, flags in seen.items():
        for flag in guarded_flags(tool):
            assert flag in flags, f"{tool} {flag}: guarded flag missing from the README table"
