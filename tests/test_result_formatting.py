"""Human layouts preserve provider data and leave machine output untouched."""

import copy
import json
from io import StringIO

import pytest
from rich.console import Console

from tavily_cli import output

FORMATTERS = [
    output.print_search_results,
    output.print_extract_results,
    output.print_crawl_results,
    output.print_map_results,
    output.print_research_result,
]


def render(monkeypatch, formatter, data, width=80):
    stream = StringIO()
    monkeypatch.setattr(output, "console", Console(file=stream, width=width, color_system=None))
    formatter(data, json_mode=False)
    return stream.getvalue()


@pytest.mark.parametrize("formatter", FORMATTERS)
def test_machine_output_and_files_preserve_every_field(formatter, capsys, tmp_path):
    data = {"results": [], "content": {"answer": "**unaltered**"}, "extra": "provider metadata", "response_time": 0}
    formatter(data, json_mode=True)
    assert json.loads(capsys.readouterr().out) == data
    path = tmp_path / "response.json"
    formatter(data, json_mode=False, output_file=str(path))
    assert json.loads(path.read_text()) == data


@pytest.mark.parametrize("formatter,unit", list(zip(FORMATTERS, ["results", "extracted", "pages", "URLs", "sources"], strict=True)))
def test_empty_results_are_explicit(monkeypatch, formatter, unit):
    rendered = render(monkeypatch, formatter, {"status": "completed", "response_time": 0})
    assert "No " in rendered
    assert f"0 {unit}" in rendered
    assert "0.00s" in rendered


@pytest.mark.parametrize("width", [40, 80, 140])
@pytest.mark.parametrize("formatter", [output.print_extract_results, output.print_crawl_results])
def test_page_previews_are_bounded_and_report_truncation(monkeypatch, width, formatter):
    data = {
        "results": [{"url": "https://example.com/a", "raw_content": "# A page\n\n" + "A useful sentence. " * 400}],
        "response_time": 1.25,
    }
    original = copy.deepcopy(data)
    rendered = render(monkeypatch, formatter, data, width)
    assert "https://example.com/a" in rendered
    assert "A useful sentence." in " ".join(rendered.split())
    assert "--json" in rendered
    assert "preview" in rendered.lower()
    assert len(rendered) < 6000
    assert all(len(line.rstrip()) <= min(width, 100) for line in rendered.splitlines())
    assert data == original


def test_extract_partial_failure_shows_each_error(monkeypatch):
    rendered = render(monkeypatch, output.print_extract_results, {
        "results": [{"url": "https://example.com/a", "raw_content": "content"}],
        "failed_results": [{"url": "https://example.com/b", "error": "Access denied"}],
    })
    assert "Access denied" in rendered
    assert "https://example.com/b" in rendered
    assert "1 extracted" in rendered and "1 failed" in rendered


def test_map_preserves_full_urls_queries_and_order(monkeypatch):
    urls = ["https://example.com/b?q=1", "https://example.com/a#part", "https://other.org/a"]
    rendered = render(monkeypatch, output.print_map_results, {"base_url": "https://example.com", "results": urls})
    assert all(url in rendered for url in urls)
    assert rendered.index(urls[0]) < rendered.index(urls[1]) < rendered.index(urls[2])
    assert "3 URLs" in rendered


def test_research_structured_report_and_sources(monkeypatch):
    rendered = render(monkeypatch, output.print_research_result, {
        "status": "completed", "content": {"finding": "Evidence", "count": 2},
        "sources": [{"title": "Source A", "url": "https://example.com/a"}],
    })
    assert '"finding": "Evidence"' in rendered
    assert '"count": 2' in rendered
    assert "Source A" in rendered and "https://example.com/a" in rendered


def test_research_report_is_complete_and_pending_task_can_be_resumed(monkeypatch):
    report = "# Report\n\n" + "Evidence. " * 400 + "FINAL FINDING [1]"
    rendered = render(monkeypatch, output.print_research_result, {"status": "completed", "content": report})
    assert "FINAL FINDING [1]" in rendered
    pending = render(monkeypatch, output.print_research_result, {"status": "pending", "request_id": "req-123"})
    assert "pending" in pending
    assert "tvly research poll req-123" in pending


def test_untrusted_markdown_cannot_inject_encoded_terminal_controls(monkeypatch):
    rendered = render(monkeypatch, output.print_research_result, {
        "status": "completed", "content": "# Safe\n\n&#27;[2Jtext [bold]literal[/bold]",
    })
    assert "\x1b" not in rendered
    assert "text [bold]literal[/bold]" in rendered


@pytest.mark.parametrize("args", [["research", "topic", "--no-wait"], ["research", "status", "req-123"]])
def test_research_receipts_are_readable_and_json_stays_raw(monkeypatch, args):
    from types import SimpleNamespace

    from click.testing import CliRunner

    from tavily_cli.cli import cli

    response = {"request_id": "req-123", "status": "pending"}
    client = SimpleNamespace(research=lambda **kwargs: response, get_research=lambda request_id: response)
    monkeypatch.setattr("tavily_cli.config.require_api_key_friendly", lambda *args, **kwargs: None)
    monkeypatch.setattr("tavily_cli.config.get_client", lambda **kwargs: client)
    human = CliRunner().invoke(cli, args)
    assert human.exit_code == 0, human.output
    assert "tvly research poll req-123" in human.stdout
    machine = CliRunner().invoke(cli, [*args, "--json"])
    assert machine.exit_code == 0, machine.output
    assert json.loads(machine.stdout) == response
    assert machine.stderr == ""


@pytest.mark.parametrize("formatter", FORMATTERS)
def test_long_urls_wrap_without_losing_characters(monkeypatch, formatter):
    url = "https://example.com/very/long/path/to/a/document?query=complete#section"
    data = {"status": "completed", "results": [{"title": "A", "url": url}], "sources": [{"url": url}]}
    if formatter is output.print_map_results:
        data["results"] = [url]
    rendered = render(monkeypatch, formatter, data, width=40)
    assert url in "".join(rendered.split())
