# AI News Digest

Automated daily AI industry digest. A Claude Code Routine clones this repo each morning, runs a Python script to fetch and filter RSS feeds, uses web search for additional coverage, synthesizes a digest post, commits, and pushes. Cloudflare Pages auto-deploys on push.

No server. No application code to maintain. The pipeline is a Routine prompt backed by a feed script, repo-committed config, and state.

## How It Works

```
Daily scheduled run
  -> Routine clones this repo
  -> Runs scripts/fetch_feeds.py
     -> Fetches RSS/Atom feeds and ArXiv papers
     -> Parses XML, normalizes URLs, computes SHA-256 hashes
     -> Filters ArXiv papers by keyword relevance
     -> Deduplicates against state/seen.json
     -> Drops items older than 48 hours
     -> Outputs structured JSON (new items only)
  -> Routine searches the web for additional AI news
  -> Triages ArXiv papers by significance
  -> Synthesizes a daily digest as Hugo markdown
  -> Writes content/posts/{YYYY-MM-DD}.md
  -> Updates state/seen.json with new URL hashes
  -> Prunes state entries older than 90 days
  -> Commits and pushes to main
  -> Cloudflare Pages builds Hugo and deploys
```

## Repository Structure

```
scripts/fetch_feeds.py     # Feed fetcher: RSS parsing, dedup, filtering
config/feeds.yaml          # RSS/Atom feed URLs, ArXiv keywords
state/seen.json            # Dedup state (URL hashes + dates)
content/posts/             # Generated digest posts (Hugo markdown)
layouts/                   # Hugo templates
static/css/style.css       # Site styles
hugo.toml                  # Hugo configuration
CLAUDE.md                  # Detailed pipeline instructions for the Routine
```

## Architecture Decisions

- **Python for feed processing.** feedparser handles RSS/Atom quirks deterministically. URL normalization and hashing are exact, not LLM-approximate. Claude receives clean JSON instead of raw XML.
- **48-hour recency window.** The script drops items older than 48 hours before Claude sees them. Keeps context small and focused on what's new.
- **JSON state, not SQLite.** Produces readable git diffs. One URL hash per line.
- **URL hashes, not full URLs.** SHA-256 keeps the state file compact.
- **90-day retention.** Caps state at ~5,000-9,000 entries. Pruned each run.
- **No theme dependency.** Templates are self-contained in `layouts/`.
- **No JavaScript.** CSS-only. Progressive enhancement only.

## Setup

1. Connect this repo to Cloudflare Pages (framework: Hugo, output: `public`, env var `HUGO_VERSION=0.147.0`)
2. Create a Claude Code Routine pointed at this repo with a daily schedule
3. Enable unrestricted branch pushes for the Routine
4. In the Routine's cloud environment, add `pip install feedparser pyyaml` to the setup script

The Routine reads `CLAUDE.md` on each run for its full instructions.

## Feeds

Feed sources are configured in `config/feeds.yaml`. Categories include vendor blogs, tech news outlets, newsletters, and ArXiv feeds with keyword-based filtering for papers relevant to agentic AI systems.

## License

MIT
