# Tavily CLI

CLI and agent tools for the [Tavily API](https://docs.tavily.com) — search, extract, crawl, map, and research from the command line.

> **Note:** This package provides the `tvly` command-line tool. It depends on
> [`tavily-python`](https://pypi.org/project/tavily-python/), the official Tavily Python SDK.

## Features

- **One-Command Setup** — Authenticate, install Tavily skills for supported agents, and verify a live search
- **Interactive REPL** — Run `tvly` with no arguments for a chat-like shell experience
- **CLI for Humans & AI Agents** — Rich-formatted output for humans, `--json` for agents
- **Web Search** — LLM-optimized search with domain/date filtering and relevance scoring
- **Content Extraction** — Extract clean markdown from any URL
- **Website Crawling** — Crawl sites with depth/breadth control and path filtering
- **URL Discovery** — Map all URLs on a site without content extraction
- **Deep Research** — AI-powered research with citations and structured output
- **Feedback** — Score search results and share request or session feedback
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
separately with the [appropriate setup options](#non-interactive-mode-for-ai-agents--scripts).

### Package manager

Choose the package manager you use:

```bash
# uv
uv tool install tavily-cli

# pipx
pipx install tavily-cli

# pip (inside a virtual environment)
pip install tavily-cli
```

Then run `tvly init` to complete setup.

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

### Initialize Tavily

The guided installer starts setup automatically on a fresh interactive desktop
installation. After a package-manager, source, or non-interactive install, run:

```bash
tvly init
tvly search "latest AI trends"
```

`tvly init` guides you through:

1. **Authentication:** reuse an existing credential or sign in through browser OAuth.
2. **Agent setup:** detect Claude Code, Codex, and Cursor and install Tavily's eight core agent skills.
3. **Verification:** check the CLI and installed skills, then run a live search.

No Node.js is required. Restart your agent after new skills are installed.

```bash
# Set up one or more supported agents
tvly init --agent codex
tvly init --agent claude-code --agent cursor

# Set up every detected agent without the agent-selection prompt
tvly init --all --yes

# Authenticate with an API key instead of OAuth
tvly init --api-key tvly-YOUR_KEY

# Install skills and verify keyless search if no credential is configured
tvly init --skip-auth

# Authenticate and verify the CLI without installing agent skills
tvly init --skip-skills
```

Skills are installed in `~/.agents/skills` and exposed to the selected agents.
If no supported agent is detected, setup still installs the shared skills.
Rerunning `tvly init` checks existing skills and installs the version selected
by your CLI release. Use `tvly update` followed by `tvly init` to get the
skills shipped with a newer release.

### Try search and extract without signing in

You can skip setup and run `tvly search` or `tvly extract` immediately after
installation. When no credential is configured, these commands use keyless
access with a fair-use rate-limit cap.

```bash
tvly search "latest AI trends"
tvly extract https://example.com
```

If you reach the cap, the CLI prints continuation options. Run `tvly login`
to authenticate, then retry your command. `crawl`, `map`, `research`, and
`feedback` require authentication.

### Manage authentication

Use `tvly login` when you only want to manage authentication without running
the full setup and agent-skill installation:

```bash
# Browser OAuth (no Node.js required)
tvly login

# Print the OAuth URL and wait for authorization
tvly login --no-browser

# Or set API key directly
tvly login --api-key tvly-YOUR_KEY

# Or use environment variable
export TAVILY_API_KEY=tvly-YOUR_KEY

# Check auth status
tvly auth

# Revoke stored OAuth tokens and clear saved credentials
tvly logout
```

`--no-browser` is also available on `tvly init`. It prints the login URL but
still waits for a callback to `127.0.0.1` on the machine running the CLI.
For SSH sessions, arrange port forwarding or use an API key; for CI, supply
`TAVILY_API_KEY` through your secret manager. `tvly logout` cannot unset an
environment variable, so unset `TAVILY_API_KEY` separately if needed.

For the full API option set, use API-key authentication. Browser OAuth uses
Tavily's MCP endpoint, where some CLI options and research operations have
[known compatibility gaps](https://github.com/tavily-ai/tavily-cli/issues/24).

## Examples

### Interactive Mode

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

### Search the Web

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

### Extract Content from URLs

```bash
# Extract a single URL
tvly extract https://example.com/article

# Extract multiple URLs with a focus query
tvly extract https://example.com https://other.com --query "pricing information"

# Advanced extraction for JS-heavy pages
tvly extract https://spa-app.com --extract-depth advanced
```

### Crawl a Website

```bash
# Basic crawl
tvly crawl https://docs.example.com

# Deep crawl with filters
tvly crawl https://docs.example.com --max-depth 2 --limit 100 --select-paths "/api/.*,/guides/.*"

# Semantic focus with per-page chunks (API-key authentication)
tvly crawl https://docs.example.com --instructions "Find authentication docs" --chunks-per-source 3

# Save pages as markdown files
tvly crawl https://docs.example.com --output-dir ./docs
```

### Map URLs

```bash
# Discover all URLs on a site
tvly map https://example.com

# Filter by path
tvly map https://example.com --select-paths "/blog/.*" --limit 500
```

### Deep Research

Use API-key authentication for streaming, structured output, and async
research. Replace `REQUEST_ID` with the ID returned by your research request.

```bash
# Run research and wait for results
tvly research "Competitive landscape of AI code assistants"

# Use pro model for comprehensive analysis
tvly research "Electric vehicle market analysis" --model pro

# Stream results in real-time
tvly research "AI market trends" --stream

# Async: start and poll separately (API-key auth only)
tvly research "topic" --no-wait --json        # returns request_id
tvly research status REQUEST_ID --json        # check status
tvly research poll REQUEST_ID --json          # wait and get result

# Structured output
tvly research "AI market size" --output-schema schema.json --json
```

### Submit Feedback

Replace `REQUEST_ID`, `SESSION_ID`, and the example result IDs with values
from your requests.

```bash
# Score a search request overall and per result
tvly feedback --request-id REQUEST_ID --agent-score 0.9 \
  --urls-scores '[{"id": "r1", "agent_score": 0.9}, {"id": "r2", "agent_score": 0.2, "comment": "outdated"}]'

# Feedback on a whole session, with the answer you produced
tvly feedback --session-id SESSION_ID --agent-score 1 --response-delivered "..." --used-ids '["r1", "r3"]'
```

### Save Results

`--output` writes the structured JSON response, regardless of the filename
extension. For extraction, `--format markdown` controls the content inside
that response; it does not turn the output file into plain Markdown.

```bash
tvly search "AI news" --output results.json
tvly extract https://example.com --format markdown --output extracted.json

# Save individual crawled pages as Markdown files
tvly crawl https://docs.example.com --output-dir ./docs
```

## CLI Overview

```
tvly
├── (no command)                # Interactive REPL
├── init                        # Guided auth, agent skills, and verification
├── login                       # Authenticate (OAuth or API key)
├── logout                      # Clear stored credentials
├── auth                        # Check authentication status
├── search <query>              # Web search
├── extract <urls...>           # Extract content from URLs
├── crawl <url>                 # Crawl a website
├── map <url>                   # Discover URLs (no content)
├── update                      # Check for or install CLI updates
├── research <query>            # Deep research (async)
│   ├── run <query>             # Start a research task (same as above)
│   ├── status <id>             # Check task status
│   └── poll <id>               # Poll until completion
└── feedback                    # Submit feedback on a request or session
```

## Non-Interactive Mode (for AI Agents & Scripts)

Use `--json` for structured output. For unattended setup, provide a credential
through `TAVILY_API_KEY` or choose `--skip-auth` for keyless search and extract.
`--yes` accepts agent selection; it does not complete browser authentication.

```bash
# With TAVILY_API_KEY already supplied by your environment
tvly init --all --yes --json

# Keyless setup for Codex when no credential is configured
tvly init --agent codex --skip-auth --yes --json

# Verify the CLI without installing skills (uses existing credentials, if any)
tvly init --skip-auth --skip-skills --json
```

Setup JSON includes `ok`, `mode`, `auth`, `skills`, and `verification`. On
failure, `error.stage` identifies the failed step. Setup always performs a
live search, so it needs network access even with `--skip-skills`.

```bash
# Every command supports --json for structured output
tvly search "query" --json
tvly auth --json
tvly extract https://example.com --json
tvly update --check --json

# Read input from stdin with "-"
echo "What is the latest funding for Anthropic?" | tvly search - --json
echo "Research question" | tvly research - --json

# Async research: launch then poll separately (API-key auth only)
tvly research "question" --no-wait --json        # returns request_id
tvly research status REQUEST_ID --json           # check status
tvly research poll REQUEST_ID --json             # wait and get result

# Global options
tvly --version         # show version
tvly --status          # show version + auth status
tvly --status --json   # structured status
```

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Local setup or update error |
| 2 | Invalid input / usage error |
| 3 | Authentication failure, keyless rate-limit cap, or handled API usage/plan limit |
| 4 | API error, setup live-search failure, or update-check error |

## Command Reference

### `tvly init`

Run first-time setup, install or update the Tavily skills selected by your
CLI release, and verify the CLI with a live search.

| Option | Description |
|--------|-------------|
| `--agent` | Install skills for `claude-code`, `codex`, or `cursor`; repeat to select more than one |
| `--all` | Install skills for every detected agent |
| `--yes` | Accept detected agents without prompting |
| `--skip-auth` | Skip login when no credential is configured; existing credentials are still used |
| `--skip-skills` | Skip agent-skill installation and updates |
| `--api-key` | Authenticate with an API key instead of OAuth |
| `--browser` / `--no-browser` | Open OAuth in a browser or print the URL; both require the local callback |
| `--json` | Return structured setup and verification results |

Use either `--all` or `--agent`. `--skip-auth` and `--api-key` cannot be
combined. `--json` selects detected agents without prompting, but does not
skip authentication.

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
| `--include-image-descriptions` | Include AI image descriptions |
| `--chunks-per-source` | Chunks per source (advanced/fast depth only) |
| `-o` / `--output` | Save output to file |
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
| `--select-domains` | Regex patterns for domains to include |
| `--exclude-domains` | Regex patterns for domains to exclude |
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

Asynchronous research (`--no-wait`, `status`, `poll`) requires API-key authentication. Browser (OAuth) credentials are routed through the MCP endpoint, which runs research to completion in a single call and issues no request_id.

### `tvly research status`

Check research task status by request ID. Supports `--client-name`. Requires API-key authentication.

### `tvly research poll`

Poll until completion and return results. Same `--poll-interval`, `--timeout`, `-o`, and `--client-name` options as `run`. Requires API-key authentication.

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
