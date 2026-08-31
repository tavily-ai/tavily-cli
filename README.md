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

## Installation

Requires **Python 3.10+**.

```bash
pip install tavily-cli
```

### From source

```bash
git clone https://github.com/tavily-ai/tavily-cli.git
cd tavily-cli
pip install -e .
```

## Quick Start

### Keyless mode

`tvly search` and `tvly extract` work without an API key — try them right
after installing. A fair-use rate-limit cap applies; when reached, the CLI
prints a clear message with sign-up and continuation options. All other
commands (`crawl`, `map`, `research`, `feedback`) require a key.

```bash
pip install tavily-cli
tvly search "latest AI trends"
tvly extract https://example.com
```

### 1. Authenticate

```bash
# Set API key directly
tvly login --api-key tvly-YOUR_KEY

# Or use environment variable
export TAVILY_API_KEY=tvly-YOUR_KEY

# Or OAuth (opens browser)
tvly login

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

### 8. Submit Feedback

```bash
# Score a search request overall and per result
tvly feedback --request-id <request_id> --agent-score 0.9 \
  --urls-scores '[{"id": "r1", "agent_score": 0.9}, {"id": "r2", "agent_score": 0.2, "comment": "outdated"}]'

# Feedback on a whole session, with the answer you produced
tvly feedback --session-id <session_id> --agent-score 1 --response-delivered "..." --used-ids '["r1", "r3"]'
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
├── research <query>            # Deep research (async)
│   ├── run <query>             # Start a research task (same as above)
│   ├── status <id>             # Check task status
│   └── poll <id>               # Poll until completion
└── feedback                    # Submit feedback on a request or session
```

## Non-Interactive Mode (for AI Agents & Scripts)

All commands support `--json` output and can be fully controlled via CLI arguments.

```bash
# Every command supports --json for structured output
tvly search "query" --json
tvly auth --json
tvly extract https://example.com --json

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

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 2 | Invalid input / usage error |
| 3 | Authentication error |
| 4 | API error |

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
| `-o` / `--output` | Save output to file |
| `--client-name` | Set optional `client_name` for request attribution |

### `tvly extract`

| Option | Description |
|--------|-------------|
| `--query` | Rerank chunks by relevance |
| `--chunks-per-source` | Chunks per source (1-5, requires `--query`) |
| `--extract-depth` | `basic` (default) or `advanced` |
| `--format` | `markdown` (default) or `text` |
| `--include-images` | Include image URLs |
| `--timeout` | Max wait (1-60 seconds) |
| `-o` / `--output` | Save output to file |
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
| `-o` / `--output` | Save output to file |
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
| `-o` / `--output` | Save output to file |
| `--client-name` | Set optional `client_name` for request attribution |

### `tvly research status`

Check research task status by request ID. Supports `--client-name`.

### `tvly research poll`

Poll until completion and return results. Same `--poll-interval`, `--timeout`, `-o`, and `--client-name` options as `run`.

### `tvly feedback`

Requires `--session-id` or `--request-id`. Requires a Tavily API key.

| Option | Description |
|--------|-------------|
| `--session-id` | Session to give feedback on |
| `--request-id` | Search request to give feedback on |
| `--agent-score` | Overall score: 1 perfect, 0 irrelevant, -1 harmful |
| `--human-score` | End-user feedback, if available (e.g. like/dislike) |
| `--comment` | Explanation, required when `--agent-score` is below 0.5 |
| `--response-delivered` | The final answer you produced using the results |
| `--used-urls` | URLs you actually used: JSON array of strings, inline or a file path |
| `--used-ids` | Result IDs you actually used: JSON array of strings, inline or a file path |
| `--used-citations` | Content snippets you used: JSON array of strings, inline or a file path |
| `--urls-scores` | Per-result feedback: JSON array of `{id\|url, agent_score, scores, comment}`, inline or a file path |
| `--extra-scores` | Additional labeled scores: JSON array of `{label, value}`, inline or a file path |
| `--client-name` | Set optional `client_name` for request attribution |

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
