# AI News Digest

Automated daily AI industry digest. A Claude Code Routine clones this repo each morning, runs a Python script to fetch and filter RSS feeds, uses web search for additional coverage, synthesizes a digest post, commits, and pushes. Cloudflare Pages auto-deploys on push. A summary notification goes out via ntfy.sh.

No server. No application code to maintain. The pipeline is a Routine prompt backed by a feed script, repo-committed config, and state.

## How It Works

```
Daily scheduled run
  -> Routine clones this repo
  -> Runs scripts/fetch_feeds.py
     -> Fetches 33 RSS/Atom feeds + 3 ArXiv feeds
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
  -> Sends summary notification to ntfy.sh
```

## Subscribe

- **Web:** [digest.sorcerousmachine.com](https://digest.sorcerousmachine.com)
- **RSS:** [digest.sorcerousmachine.com/feed.xml](https://digest.sorcerousmachine.com/feed.xml)
- **Notifications:** [ntfy.sh/ai-news-digest](https://ntfy.sh/ai-news-digest)

## Repository Structure

```
scripts/fetch_feeds.py     # Feed fetcher: RSS parsing, dedup, filtering
config/feeds.yaml          # 33 RSS/Atom feeds + 3 ArXiv feeds, 7 categories
state/seen.json            # Dedup state (URL hashes + dates)
content/posts/             # Generated digest posts (Hugo markdown)
layouts/                   # Hugo templates
static/css/style.css       # Site styles
hugo.toml                  # Hugo configuration
CLAUDE.md                  # Detailed pipeline instructions for the Routine
```

## Feed Sources

33 feeds across 7 categories, plus 3 ArXiv feeds with keyword filtering:

- **Vendor** -- OpenAI, Google DeepMind, Anthropic, Hugging Face
- **News** -- TechCrunch, Ars Technica, MIT Technology Review, The Register, Hacker News, Lobsters
- **Newsletters** -- Simon Willison, Nathan Lambert, Jack Clark, Ethan Mollick, Lilian Weng, Sebastian Raschka, Andrej Karpathy, Zvi Mowshowitz, SemiAnalysis, and more
- **Open Source** -- LangChain, Weights & Biases, PyTorch
- **Research** -- ArXiv (cs.AI, cs.CL, cs.LG), Google Research, AI Alignment Forum, HF Daily Papers
- **Regulatory** -- Stanford HAI, NIST
- **Infrastructure** -- NVIDIA, Semiconductor Engineering, AWS ML

Configured in `config/feeds.yaml`.

## Architecture Decisions

- **Python for feed processing.** atoma (pure-Python, defusedxml-based) handles RSS/Atom parsing deterministically. URL normalization and hashing are exact, not LLM-approximate. Claude receives clean JSON instead of raw XML.
- **48-hour recency window.** The script drops items older than 48 hours before Claude sees them. Keeps context small and focused on what's new.
- **JSON state, not SQLite.** Produces readable git diffs. One URL hash per line.
- **URL hashes, not full URLs.** SHA-256 keeps the state file compact.
- **90-day retention.** Caps state at ~5,000-9,000 entries. Pruned each run.
- **No theme dependency.** Templates are self-contained in `layouts/`.
- **No JavaScript.** CSS-only. Progressive enhancement only.

## Setup

1. Connect this repo to Cloudflare Pages (framework: Hugo, output: `public`, env var `HUGO_VERSION=0.147.0`)
2. Set build watch paths to `content/**`, `layouts/**`, `static/**`, `hugo.toml`
3. Create a Claude Code Routine pointed at this repo with a daily schedule
4. Enable unrestricted branch pushes for the Routine
5. In the Routine's cloud environment, add `pip install atoma pyyaml` to the setup script

The Routine reads `CLAUDE.md` on each run for its full instructions.

## Build Parameters

A couple of Hugo site params can be set via environment variables at build time:

- `HUGO_PARAMS_GITHUBREPO` — if set (e.g. `owner/repo`), renders a GitHub icon link in the header pointing to that repo. Omit to hide the link.
- `HUGO_PARAMS_NOINDEX` — if set to `true`, emits a restrictive `robots.txt` and a `<meta name="robots" content="noindex, nofollow">` tag on every page. Use for builds that should stay out of search indexes.

## License

MIT
