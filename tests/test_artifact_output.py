from __future__ import annotations

import json
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from tavily_cli.commands.research import _render_stream
from tavily_cli.commands.search import search
from tavily_cli.output import (
    print_extract_results,
    print_map_results,
    print_research_result,
    print_search_results,
    validate_artifact_options,
)

SEARCH_RESPONSE = {
    "answer": "A concise answer.",
    "results": [
        {
            "title": "Example result",
            "url": "https://example.com/article",
            "score": 0.9,
            "content": "Result snippet.",
            "raw_content": "Complete search content.",
        }
    ],
}


def test_output_extension_selects_json_or_markdown(tmp_path: Path) -> None:
    json_path = tmp_path / "result.json"
    markdown_path = tmp_path / "result.md"
    legacy_path = tmp_path / "result.txt"

    print_search_results(SEARCH_RESPONSE, json_mode=False, output_file=str(json_path))
    print_search_results(SEARCH_RESPONSE, json_mode=False, output_file=str(markdown_path))
    print_search_results(SEARCH_RESPONSE, json_mode=False, output_file=str(legacy_path))

    assert json.loads(json_path.read_text()) == SEARCH_RESPONSE
    assert json.loads(legacy_path.read_text()) == SEARCH_RESPONSE
    markdown = markdown_path.read_text()
    assert markdown.startswith("# Search Results")
    assert "Example result" in markdown
    assert "Complete search content." in markdown


def test_extract_markdown_contains_complete_content(tmp_path: Path) -> None:
    raw_content = "x" * 3_500 + " END"
    output_path = tmp_path / "article.md"

    print_extract_results(
        {"results": [{"url": "https://example.com", "raw_content": raw_content}]},
        json_mode=False,
        output_file=str(output_path),
    )

    assert output_path.read_text().endswith(" END\n")


def test_json_flag_forces_json_regardless_of_extension(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    output_path = tmp_path / "map.md"
    response = {"base_url": "https://example.com", "results": ["https://example.com/a"]}

    print_map_results(response, json_mode=True, output_file=str(output_path))

    assert json.loads(output_path.read_text()) == response
    summary = json.loads(capsys.readouterr().out)
    assert summary["saved"] is True
    assert summary["artifacts"] == [str(output_path)]


def test_existing_output_is_refused_unless_forced(tmp_path: Path) -> None:
    output_path = tmp_path / "result.json"
    output_path.write_text("original")

    with pytest.raises(click.ClickException, match="Refusing to overwrite"):
        print_search_results(SEARCH_RESPONSE, json_mode=False, output_file=str(output_path))

    assert output_path.read_text() == "original"
    print_search_results(SEARCH_RESPONSE, json_mode=False, output_file=str(output_path), force=True)
    assert json.loads(output_path.read_text()) == SEARCH_RESPONSE
    assert not list(tmp_path.glob(".*.tmp"))


def test_save_generates_authoritative_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    response = {"results": [{"url": "https://example.com", "raw_content": "full content"}]}

    print_extract_results(response, json_mode=False, save=True)

    artifacts = list((tmp_path / ".tavily" / "extract").glob("*.json"))
    assert len(artifacts) == 1
    assert json.loads(artifacts[0].read_text()) == response


def test_research_save_writes_report_and_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    response = {
        "status": "completed",
        "content": "# Findings\n\nComplete report.",
        "sources": [{"title": "Source", "url": "https://example.com"}],
        "response_time": 4.2,
    }

    print_research_result(response, json_mode=False, save=True)

    artifact_dirs = list((tmp_path / ".tavily" / "research").iterdir())
    assert len(artifact_dirs) == 1
    assert "Complete report." in (artifact_dirs[0] / "report.md").read_text()
    assert json.loads((artifact_dirs[0] / "report.json").read_text()) == response


def test_streamed_research_can_be_saved_as_complete_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    chunks = [
        "data:"
        + json.dumps({"choices": [{"delta": {"content": "Complete "}}]}),
        "data:"
        + json.dumps(
            {
                "choices": [
                    {
                        "delta": {
                            "content": "report.",
                            "sources": [{"title": "Source", "url": "https://example.com"}],
                        }
                    }
                ]
            }
        ),
    ]

    _render_stream(chunks, save=True)

    artifact_dir = next((tmp_path / ".tavily" / "research").iterdir())
    assert "Complete report." in (artifact_dir / "report.md").read_text()
    metadata = json.loads((artifact_dir / "report.json").read_text())
    assert metadata["content"] == "Complete report."
    assert metadata["sources"][0]["url"] == "https://example.com"


def test_validation_rejects_conflicting_flags_and_existing_files(tmp_path: Path) -> None:
    with pytest.raises(click.UsageError, match="either --output or --save"):
        validate_artifact_options(output_file="result.json", save=True, force=False)

    output_path = tmp_path / "result.json"
    output_path.write_text("original")
    with pytest.raises(click.ClickException, match="Refusing to overwrite"):
        validate_artifact_options(
            output_file=str(output_path),
            save=False,
            force=False,
        )


def test_search_command_wires_artifact_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def search(self, **kwargs):
            assert kwargs["query"] == "artifact test"
            return SEARCH_RESPONSE

    monkeypatch.setattr(
        "tavily_cli.config.get_client_or_keyless",
        lambda **kwargs: (FakeClient(), True),
    )
    output_path = tmp_path / "search.md"

    result = CliRunner().invoke(search, ["artifact test", "-o", str(output_path)])

    assert result.exit_code == 0, result.output
    assert "Artifact:" in result.output
    assert "Complete search content." in output_path.read_text()
