# AI News Digest

Automated daily AI industry digest. A cron job on a self-hosted machine
invokes headless Claude Code each morning: a Python script fetches and
filters RSS feeds, Claude does web discovery for additional coverage,
synthesizes the digest as Hugo markdown, commits, and pushes. Cloudflare
Pages auto-deploys on push. A summary notification goes out via ntfy.sh.

No server-side application to maintain. The pipeline is a set of
instructions in `CLAUDE.md`, backed by a feed-fetcher script and
repo-committed config and state.

## How It Works

```
Daily cron invocation (11:00 UTC)
  -> Wrapper script pulls latest main
  -> Invokes `claude -p` headless, pointed at CLAUDE.md
     -> Step 1-2: Runs scripts/fetch_feeds.py
        -> Fetches 33 RSS/Atom feeds + 3 ArXiv feeds with per-fetch jitter
        -> Retries once on 5xx/429/timeouts; identifies as a named bot
        -> Detects content-mismatch responses (200 OK where body isn't a feed)
        -> Classifies errors by type (status:*, timeout, content_mismatch, parse_error)
        -> Normalizes URLs, SHA-256 hashes, filters ArXiv by keywords
        -> Deduplicates against state/seen.json
        -> Tracks per-feed consecutive-failure counts in feed_health
        -> Drops items older than 48 hours
        -> Emits structured JSON + disable_candidates list
     -> Step 3-4: Web discovery (+ passive feed-candidate capture)
     -> Step 5: Triages ArXiv papers by significance
     -> Step 6-7: Synthesizes digest, writes content/posts/{YYYY-MM-DD}.md
     -> Step 8: Post-write semantic dedup against last 2 digests
     -> Step 9: Retires feeds past the hard-failure threshold into a
                disabled: section of config/feeds.yaml
     -> Step 10: Updates state/seen.json (seen hashes + feed_health)
     -> Step 11: Commits + pushes to main, recovery-branch fallback
     -> Step 12: Sends summary to ntfy.sh
  -> Cloudflare Pages builds Hugo and deploys
```

## Subscribe

- **Web:** [digest.sorcerousmachine.com](https://digest.sorcerousmachine.com)
- **RSS:** [digest.sorcerousmachine.com/feed.xml](https://digest.sorcerousmachine.com/feed.xml)
- **Notifications:** [ntfy.sh/ai-news-digest](https://ntfy.sh/ai-news-digest)

## Repository Structure

```
scripts/fetch_feeds.py     # Feed fetcher: RSS parsing, dedup, filtering, health tracking
config/feeds.yaml          # Active feeds + disabled: section for retired entries
state/seen.json            # Dedup state (URL hashes) + per-feed health
content/posts/             # Generated digest posts (Hugo markdown)
layouts/                   # Hugo templates
static/css/style.css       # Site styles
hugo.toml                  # Hugo configuration
CLAUDE.md                  # Pipeline instructions loaded by each run
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

Configured in `config/feeds.yaml`. Feeds that produce two consecutive
hard failures (404, 410, or content_mismatch — currently detected as
HTML-served-where-XML-expected) are moved
to a `disabled:` section of the same file with a reason and date, and
are skipped on subsequent runs. Manual re-enable by moving the entry
back into the active list.

## Architecture Decisions

- **Python for feed processing.** atoma (pure-Python, defusedxml-based)
  handles RSS/Atom parsing deterministically. URL normalization and
  hashing are exact, not LLM-approximate. Claude receives clean JSON
  instead of raw XML.
- **48-hour recency window.** The script drops items older than 48
  hours before Claude sees them. Keeps context small and focused on
  what's new.
- **JSON state, not SQLite.** Produces readable git diffs. One URL
  hash per line.
- **URL hashes, not full URLs.** SHA-256 keeps the state file compact.
- **90-day retention.** Caps state at ~5,000-9,000 entries. Pruned
  each run.
- **Per-feed health tracking with auto-retirement.** Hard failures
  (status:404, status:410, content_mismatch) increment a consecutive-failure
  counter; soft failures (5xx, timeouts, parse errors on XML-ish
  bodies) preserve it. Two consecutive hard failures retires the feed
  automatically. Prevents the error log from being dominated by feeds
  that have permanently moved or gone dark.
- **Post-write semantic deduplication.** URL-hash dedup can't catch
  the same story surfaced from a different URL day-to-day. After the
  digest is written, Claude reads the last two digests — matching the
  48-hour recency window upstream — and removes items that cover
  stories already reported, keeping the synthesis context clean of
  prior-post bias.
- **Passive feed discovery.** During web search, recurring authors
  and publishers not already in feeds.yaml are surfaced as candidates
  in the commit message for manual review. No auto-addition — web
  search ranks for traffic, not insight, so the curation stays human.
- **No theme dependency.** Templates are self-contained in `layouts/`.
- **No JavaScript.** CSS-only. Progressive enhancement only.

## Setup

Two parts: Cloudflare Pages for hosting, and a cron job on a machine
you control for the daily pipeline. No part of the pipeline runs in
a managed service — anything that can run `claude` and `git` on a
schedule will do.

### Hosting (Cloudflare Pages)

1. Connect this repo to Cloudflare Pages. Framework: Hugo. Output
   directory: `public`. Set `HUGO_VERSION=0.147.0` as a build env var.
2. Set build watch paths to `content/**`, `layouts/**`, `static/**`,
   `hugo.toml` — config and state changes shouldn't trigger rebuilds.
3. Point a custom domain at the Pages project.

### Pipeline (cron)

Requirements: a long-running host (small VPS or homelab machine) with
git, Python 3.10+, and network access to GitHub + Anthropic + RSS
origins.

1. Install the Claude Code CLI and log in — this creates
   `~/.claude/.credentials.json`.
2. Install `gh` (GitHub CLI), authenticate with `repo` scope, and
   enable the git credential helper so pushes work over HTTPS.
3. `pip install atoma pyyaml` (use `--user` or a venv depending on
   your distribution's Python policy).
4. Clone this repo locally.
5. Write a wrapper script that `cd`s into the repo, pulls, and
   invokes:
   ```
   claude -p --permission-mode bypassPermissions \
     "Run the daily AI digest pipeline per CLAUDE.md. Follow every step in order."
   ```
   Redirect stdout+stderr to a per-day log file so stream interruptions
   are visible after the fact.
6. Add to crontab with `TZ=UTC`:
   ```
   0 11 * * * /path/to/wrapper.sh
   ```
   11:00 UTC lands after ArXiv's nightly update and before most US
   readers wake up.

Claude loads `CLAUDE.md` at the start of each run for its full
pipeline spec.

## Build Parameters

A handful of Hugo site params can be set via environment variables at
build time:

- `HUGO_PARAMS_GITHUBREPO` — if set (e.g. `owner/repo`), renders a
  GitHub icon link in the header pointing to that repo. Omit to hide
  the link.
- `HUGO_PARAMS_NOINDEX` — if set to `true`, emits a restrictive
  `robots.txt` and a `<meta name="robots" content="noindex, nofollow">`
  tag on every page. Use for builds that should stay out of search
  indexes.
- `HUGO_PARAMS_BUILTBY` — if set to an organization or individual name
  (e.g. `Acme Corp`), renders a "Built by {name}." line in the footer
  and emits schema.org JSON-LD marking the site as a `Blog` published
  by that `Organization`. Search engines pick up the parent/child
  relationship for sitelinks and knowledge-panel purposes. Omit to
  hide both the footer attribution and the structured data.
- `HUGO_PARAMS_BUILTBYURL` — if set alongside `HUGO_PARAMS_BUILTBY`,
  wraps the footer name in a link and adds the `url` field to the
  JSON-LD `Organization` object. Ignored when `HUGO_PARAMS_BUILTBY` is
  unset.

## License

MIT
