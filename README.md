# Tavily CLI

CLI and agent tools for the [Tavily API](https://docs.tavily.com) — search, extract, crawl, map, and research from the command line.

> **Note:** This package provides the `tvly` command-line tool. It depends on
> [`tavily-python`](https://pypi.org/project/tavily-python/), the official Tavily Python SDK.

## Features

- **Interactive REPL** — Run `tvly` with no arguments for a chat-like shell experience
- **CLI for Humans & AI Agents** — Rich-formatted output for humans, `--json` for agents
- **Web Search** — LLM-optimized search with domain/date filtering and relevance scoring
- **Content Extraction** — Extract clean markdown from any URL
- **Website Crawling** — Crawl sites with depth/breadth control and path filtering
- **URL Discovery** — Map all URLs on a site without content extraction
- **Deep Research** — AI-powered research with citations and structured output
- **Self-Update** — Check for and install CLI updates through the original package manager

## Installation

Requires **Python 3.10+**.

### Guided installer

```bash
curl -fsSL https://raw.githubusercontent.com/tavily-ai/tavily-cli/main/install.sh | sh
```

On a fresh interactive desktop installation, the installer starts `tvly init`
to guide authentication, agent detection, skill installation, and verification.
In CI, SSH/headless, and other non-interactive environments, run `tvly init`
separately after installation.

### Package manager

```bash
pip install tavily-cli
```

### From source

```bash
git clone https://github.com/tavily-ai/tavily-cli.git
cd tavily-cli
pip install -e .
```

### Updating

```bash
# Check without changing the installation
tvly update --check

# Update through uv, pipx, or pip
tvly update
```

Source and direct-URL installations are detected and must be updated from their original source.

## Quick Start

### Keyless mode

`tvly search` and `tvly extract` work without an API key — try them right
after installing. A fair-use rate-limit cap applies; when reached, the CLI
prints a clear message with sign-up and continuation options. All other
commands (`crawl`, `map`, `research`) require a key.

```bash
pip install tavily-cli
tvly search "latest AI trends"
tvly extract https://example.com
```

### 1. Authenticate

```bash
# Browser OAuth (no Node.js required)
tvly login

# Headless / SSH: print the URL instead of opening a browser
tvly login --no-browser

# Or set API key directly
tvly login --api-key tvly-YOUR_KEY

# Or use environment variable
export TAVILY_API_KEY=tvly-YOUR_KEY

# Check auth status
tvly auth
```

### 2. Interactive Mode

```bash
# Launch the interactive REPL
tvly
```

This opens a chat-like shell where you can run commands without the `tvly` prefix:

```
❯  search "latest AI trends"
❯  extract https://example.com
❯  help
```

### 3. Search the Web

```bash
# Basic search
tvly search "latest AI trends"

# Advanced search with filters
tvly search "quantum computing" --depth advanced --max-results 10 --time-range week

# Search specific domains
tvly search "SEC filings for Apple" --include-domains sec.gov,reuters.com

# JSON output for agents
tvly search "AI news" --json

# Set client_name for attribution
tvly search "latest AI news" --client-name x
```

### 4. Extract Content from URLs

```bash
# Extract a single URL
tvly extract https://example.com/article

# Extract multiple URLs with a focus query
tvly extract https://example.com https://other.com --query "pricing information"

# Advanced extraction for JS-heavy pages
tvly extract https://spa-app.com --extract-depth advanced
```

### 5. Crawl a Website

```bash
# Basic crawl
tvly crawl https://docs.example.com

# Deep crawl with filters
tvly crawl https://docs.example.com --max-depth 2 --limit 100 --select-paths "/api/.*,/guides/.*"

# Semantic focus
tvly crawl https://docs.example.com --instructions "Find authentication docs" --chunks-per-source 3

# Save pages as markdown files
tvly crawl https://docs.example.com --output-dir ./docs
```

### 6. Map URLs

```bash
# Discover all URLs on a site
tvly map https://example.com

# Filter by path
tvly map https://example.com --select-paths "/blog/.*" --limit 500
```

### 7. Deep Research

```bash
# Run research and wait for results
tvly research "Competitive landscape of AI code assistants"

# Use pro model for comprehensive analysis
tvly research "Electric vehicle market analysis" --model pro

# Stream results in real-time
tvly research "AI market trends" --stream

# Async: start and poll separately
tvly research "topic" --no-wait --json        # returns request_id
tvly research status <request_id> --json      # check status
tvly research poll <request_id> --json        # wait and get result

# Structured output
tvly research "AI market size" --output-schema schema.json --json
```

## CLI Overview

```
tvly
├── (no command)                # Interactive REPL
├── login                       # Authenticate (OAuth or API key)
├── logout                      # Clear stored credentials
├── auth                        # Check authentication status
├── search <query>              # Web search
├── extract <urls...>           # Extract content from URLs
├── crawl <url>                 # Crawl a website
├── map <url>                   # Discover URLs (no content)
├── update                      # Check for or install CLI updates
└── research <query>            # Deep research (async)
    ├── run <query>             # Start a research task (same as above)
    ├── status <id>             # Check task status
    └── poll <id>               # Poll until completion
```

## Non-Interactive Mode (for AI Agents & Scripts)

All commands support `--json` output and can be fully controlled via CLI arguments.

```bash
# Every command supports --json for structured output
tvly search "query" --json
tvly auth --json
tvly extract https://example.com --json
tvly update --check --json

# Explicit JSON Lines for result sets and research streams
tvly search "query" --jsonl
tvly extract https://example.com https://example.org --jsonl
tvly research "question" --stream --jsonl

# Durable artifacts: format follows the extension
tvly search "query" -o results.json
tvly extract https://example.com -o article.md

# Generate authoritative JSON under .tavily/<command>/
tvly search "query" --save

# Keep agent stdout bounded and inspect only the fields you need
summary=$(tvly search "query" --save --json)
artifact=$(printf '%s' "$summary" | jq -r '.artifacts[0]')
jq '.results[:5] | map({title, url, score})' "$artifact"

# Read input from stdin with "-"
echo "What is the latest funding for Anthropic?" | tvly search - --json
echo "Research question" | tvly research - --json

# Async research: launch then poll separately
tvly research "question" --no-wait --json        # returns request_id
tvly research status <id> --json                 # check status
tvly research poll <id> --json                   # wait and get result

# Global options
tvly --version         # show version
tvly --status          # show version + auth status
tvly --status --json   # structured status
```

`-o` writes Markdown for `.md`/`.markdown` paths and JSON otherwise. `--json`
always forces JSON. Saved commands print only a short summary and the artifact
path; existing files are preserved unless `--force` is provided.

Machine-readable failures use one stable envelope on stdout; progress and
diagnostics remain on stderr:

```json
{
  "ok": false,
  "error": {
    "code": "research_timeout",
    "message": "Research timed out after 600s.",
    "stage": "poll",
    "retryable": true,
    "request_id": "request-id"
  }
}
```

`--json` emits one JSON document. `--jsonl` emits typed result records followed
by a summary record; with research streaming, each API event is one line.

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Local setup or update error |
| 2 | Invalid input / usage error |
| 3 | Authentication or usage-limit error |
| 4 | API or update-check error |
| 5 | Partial result treated as failure by `--fail-on-partial` |

## Command Reference

### `tvly search`

| Option | Description |
|--------|-------------|
| `--depth` | `ultra-fast`, `fast`, `basic` (default), `advanced` |
| `--max-results` | Maximum results, 0-20 (default: 5) |
| `--topic` | `general` (default), `news`, `finance` |
| `--time-range` | `day`, `week`, `month`, `year` |
| `--start-date` | Results after date (YYYY-MM-DD) |
| `--end-date` | Results before date (YYYY-MM-DD) |
| `--include-domains` | Comma-separated domains to include |
| `--exclude-domains` | Comma-separated domains to exclude |
| `--country` | Boost results from country |
| `--include-answer` | Include AI answer (`basic` or `advanced`) |
| `--include-raw-content` | Include full page (`markdown` or `text`) |
| `--include-images` | Include image results |
| `--chunks-per-source` | Chunks per source (advanced/fast depth only) |
| `-o` / `--output` | Save JSON (`.json`) or Markdown (`.md`) |
| `--save` | Save JSON to a generated path under `.tavily/search/` |
| `--force` | Overwrite an existing `--output` file |
| `--jsonl` | Emit one result per JSON line, followed by a summary |
| `--client-name` | Set optional `client_name` for request attribution |

### `tvly update`

Check PyPI for the latest Tavily CLI release and update through the package
manager responsible for the active installation.

```bash
tvly update --check
tvly update
tvly update --check --json
```

`--check` is read-only. Source/direct-URL installations are reported without
being modified. JSON output includes `can_update` and `blocked_reason` so
automation can distinguish an available release from a supported self-update.

### `tvly extract`

| Option | Description |
|--------|-------------|
| `--query` | Rerank chunks by relevance |
| `--chunks-per-source` | Chunks per source (1-5, requires `--query`) |
| `--extract-depth` | `basic` (default) or `advanced` |
| `--format` | `markdown` (default) or `text` |
| `--include-images` | Include image URLs |
| `--timeout` | Max wait (1-60 seconds) |
| `-o` / `--output` | Save JSON (`.json`) or complete Markdown (`.md`) |
| `--save` | Save JSON to a generated path under `.tavily/extract/` |
| `--force` | Overwrite an existing `--output` file |
| `--fail-on-partial` | Exit 5 if any requested URL fails |
| `--jsonl` | Emit one extraction per JSON line, followed by a summary |
| `--client-name` | Set optional `client_name` for request attribution |

### `tvly crawl`

| Option | Description |
|--------|-------------|
| `--max-depth` | Levels deep (1-5, default: 1) |
| `--max-breadth` | Links per page (default: 20) |
| `--limit` | Total pages cap (default: 50) |
| `--instructions` | Natural language guidance |
| `--chunks-per-source` | Chunks per page (1-5, requires `--instructions`) |
| `--extract-depth` | `basic` or `advanced` |
| `--format` | `markdown` or `text` |
| `--select-paths` | Regex patterns for paths to include |
| `--exclude-paths` | Regex patterns for paths to exclude |
| `--select-domains` | Regex for domains to include |
| `--exclude-domains` | Regex for domains to exclude |
| `--allow-external` | Include external links (default: true) |
| `--include-images` | Include images |
| `--timeout` | Max wait (10-150 seconds) |
| `-o` / `--output` | Save JSON to file |
| `--output-dir` | Save each page as .md file in directory |
| `--jsonl` | Emit one crawled page per JSON line, followed by a summary |
| `--client-name` | Set optional `client_name` for request attribution |

### `tvly map`

| Option | Description |
|--------|-------------|
| `--max-depth` | Levels deep (1-5, default: 1) |
| `--max-breadth` | Links per page (default: 20) |
| `--limit` | Max URLs to discover (default: 50) |
| `--instructions` | Natural language guidance |
| `--select-paths` | Regex patterns for paths to include |
| `--exclude-paths` | Regex patterns for paths to exclude |
| `--allow-external` | Include external links |
| `--timeout` | Max wait (10-150 seconds) |
| `-o` / `--output` | Save JSON (`.json`) or Markdown (`.md`) |
| `--save` | Save JSON to a generated path under `.tavily/map/` |
| `--force` | Overwrite an existing `--output` file |
| `--jsonl` | Emit one discovered URL per JSON line, followed by a summary |
| `--client-name` | Set optional `client_name` for request attribution |

### `tvly research <query>` / `tvly research run <query>`

| Option | Description |
|--------|-------------|
| `--model` | `mini`, `pro`, or `auto` (default) |
| `--no-wait` | Return request_id immediately |
| `--stream` | Stream results in real-time |
| `--output-schema` | Path to JSON schema file |
| `--citation-format` | `numbered`, `mla`, `apa`, `chicago` |
| `--poll-interval` | Seconds between checks (default: 10) |
| `--timeout` | Max wait seconds (default: 600) |
| `-o` / `--output` | Save JSON (`.json`) or Markdown (`.md`) |
| `--save` | Save `report.md` and `report.json` under `.tavily/research/` |
| `--force` | Overwrite existing output files |
| `--jsonl` | Emit one streaming event per line (or one final non-stream result) |
| `--client-name` | Set optional `client_name` for request attribution |

### `tvly research status`

Check research task status by request ID. Supports `--client-name`.

### `tvly research poll`

Poll until completion and return results. Same `--poll-interval`, `--timeout`, `-o`, `--save`, `--force`, and `--client-name` options as `run`.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `TAVILY_API_KEY` | API key (highest priority, no login needed) |
| `TAVILY_HUMAN_ID` | Optional identifier attached to every request for usage attribution |

```bash
# Via env var (highest priority)
export TAVILY_HUMAN_ID=alice@example.com

# Or persist it in the config file
# (~/.tavily/config.json — set the "human_id" key)
```

## Related

- [`tavily-python`](https://pypi.org/project/tavily-python/) — Official Tavily Python SDK
- [Tavily Docs](https://docs.tavily.com) — Full API documentation

## License

MIT
