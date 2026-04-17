# AI Digest Pipeline

You are running a daily AI industry digest pipeline. Follow these steps
exactly, in order. Do not skip steps. Do not fabricate content.

## Step 1: Load Configuration

Read `config/feeds.yaml` for the list of RSS/Atom feeds, ArXiv feed URLs,
keyword lists, and any other configuration.

Read `state/seen.json` for the set of previously ingested URL hashes.
This file contains a JSON object:
```json
{
  "seen": {
    "<sha256-of-normalized-url>": "<YYYY-MM-DD date first seen>",
    ...
  }
}
```

If `state/seen.json` does not exist or is empty, treat it as `{"seen": {}}`.

## Step 2: Fetch and Parse All Feeds

Run the feed fetcher script:

```bash
python3 scripts/fetch_feeds.py --config config/feeds.yaml --state state/seen.json
```

This script handles all feed fetching, XML parsing, URL normalization,
SHA-256 hashing, ArXiv keyword filtering, deduplication against
seen.json, and 48-hour recency filtering. It outputs **only new,
recent items** as a JSON object to stdout:

```json
{
  "feeds": [
    {
      "title": "...",
      "url": "...",
      "url_hash": "<sha256>",
      "published": "...",
      "description": "...",
      "authors": "...",
      "source": "Feed Name",
      "category": "vendor",
      "thin_description": true
    }
  ],
  "arxiv": [
    {
      "title": "...",
      "url": "...",
      "url_hash": "<sha256>",
      "authors": "...",
      "abstract": "...",
      "matched_keywords": ["agent", "planning"],
      "source": "arxiv:cs.AI",
      "category": "research"
    }
  ],
  "errors": [
    {"feed": "...", "url": "...", "type": "status:403", "error": "..."}
  ],
  "summary": {
    "feeds_attempted": 14,
    "feeds_failed": 0,
    "error_types": {"status:403": 2, "timeout": 1, "parse_error": 0},
    "total_fetched": 2159,
    "skipped_already_seen": 1800,
    "skipped_too_old": 300,
    "new_feed_items": 47,
    "new_arxiv_items": 12
  }
}
```

All items in `feeds` and `arxiv` are already deduplicated and recent.
You do not need to check them against seen.json again.

The `thin_description` flag (feed items only) marks items whose RSS
description has less than 200 characters of visible text — you will
need to fetch the article body to analyze them. See Step 6.

Save the full JSON output. You will need it for subsequent steps.

If the script fails entirely, log the error and continue to Step 3
(web discovery) so the digest still has content. If it succeeds but
`errors` is non-empty, note the failed feeds in the commit message so
feed rot is visible. Group them by `type` (e.g., `status:403`,
`timeout`, `parse_error`) using the `error_types` breakdown in
`summary` — this makes persistent blockers distinguishable from
transient blips run-over-run.

## Step 3: Web Discovery

Use web search to find significant AI industry developments from the last
48 hours that would not be covered by the RSS feeds. The 48-hour window
exists to catch anything yesterday's digest might have missed. Search for:

- New model releases or major updates
- API changes or developer tools
- Regulatory actions or policy developments
- Major funding rounds (Series B+)
- Significant benchmark results or breakthroughs
- Notable open source releases
- Industry partnerships or acquisitions
- Infrastructure developments (chips, compute, cloud)

Run 3-5 targeted web searches with specific queries like:
- "AI model release today"
- "AI regulation policy news today"
- "AI startup funding round today"
- "AI open source release today"
- "AI benchmark results today"

For each significant finding, record: title, URL, a 2-3 sentence summary
of why it matters, and a category.

Do NOT include: routine product updates, opinion pieces, rumors, or
content older than 48 hours.

## Step 4: Deduplicate Web Discoveries

Feed and ArXiv items from Step 2 are already deduplicated by the script.

For web-discovered items from Step 3, check each URL hash against
`state/seen.json`:
```bash
python3 -c "from scripts.fetch_feeds import hash_url; print(hash_url('THE_URL'))"
```

- If the hash exists in `seen`: skip the item (already ingested)
- If the hash does not exist: keep the item as new

If no new items exist from any source after deduplication, continue
with Steps 6-9 anyway and publish a minimal digest post whose body
is a single sentence: "No significant AI developments were surfaced
in the last 48 hours." Still update seen.json and push. This case
should be rare but not silent.

## Step 5: Triage ArXiv Papers

For the ArXiv papers that passed keyword filtering AND are new (not in
seen.json), assess each paper:

- Evaluate relevance to someone building agentic AI systems with
  structured decomposition, validation, and orchestration
- Assign significance: "high", "medium", or "low"
- Write a one-sentence note on why it matters or doesn't (for all papers)
- Write a 2-3 sentence abstract summary ONLY for "high" significance papers

Drop papers assessed as not relevant.

## Step 6: Synthesize Daily Digest

Before writing, for every feed item with `thin_description: true`,
WebFetch the item's URL and read the article body. The RSS teaser
alone is not enough to write useful analysis. If the fetch fails,
note the failure and work from the title alone rather than
fabricating detail. ArXiv items are exempt — abstracts are sufficient.

Write a daily digest in markdown with this exact structure:

1. **Executive summary** (2-3 sentences, no header): What are the most
   important developments today?

2. **Categorized sections** using `##` headers, ordered by significance:
   - Model Releases
   - Developer Tools
   - Research & Papers
   - Regulatory & Policy
   - Funding & Business
   - Open Source
   - Infrastructure
   - Other

   OMIT any category with zero items.

3. **Within each category**, items ordered by significance. Each item:
   - `###` title as a markdown link to the source URL
   - Source attribution in italics
   - 2-3 sentence analysis of why this matters

4. **Threads to Watch** (`##` header): 2-3 emerging patterns or threads
   connecting multiple items from today's digest.

Style rules:
- Direct, analytical tone. No hype, no filler.
- No emoji anywhere.
- Write for a technical audience building production AI systems.
- For research papers, focus on practical implications.
- Flag skepticism-warranting claims (benchmarks without code, extraordinary
  claims without strong evidence).
- Do NOT fabricate items. Only include items you actually found.

## Step 7: Write the Digest File

Create the file `content/posts/{YYYY-MM-DD}.md` where the date is today.

The file must begin with Hugo front matter. Use a naive datetime
(no timezone offset) — Hugo applies the site's timeZone setting
from hugo.toml (UTC). Use noon UTC as the canonical publish time:

```yaml
---
title: "AI Digest — {Month Day, Year}"
date: {YYYY-MM-DD}T12:00:00
draft: false
summary: "{first sentence of executive summary, max 200 chars}"
tags: [{list of category slugs that appear in the digest}]
---
```

"Today" throughout this document means today in UTC. The filename,
title, and date should all use the UTC calendar date.

Tag slugs should be lowercase-hyphenated versions of the category names:
model-releases, developer-tools, research-papers, regulatory-policy,
funding-business, open-source, infrastructure, other.

The `summary` is the first sentence of the executive summary, truncated
to 200 characters if needed.

Follow the front matter with the full digest markdown from Step 6.

## Step 8: Update State

Build an updated seen.json:

1. Start with the existing `seen` entries
2. Add URL hashes with today's date for EVERY item the pipeline
   processed, regardless of whether it made it into the digest:
   - All feed items from the Step 2 script output (`feeds` array)
   - All ArXiv items from the Step 2 script output (`arxiv` array),
     including those Claude dropped in Step 5 as not relevant
   - All web items from Step 3 that passed Step 4 dedup, including
     those Claude chose not to include in the digest
   The goal: nothing the pipeline has already evaluated should be
   re-evaluated tomorrow. The 90-day prune (below) is the safety
   valve — items eventually get re-considered.
3. Remove any entries with dates older than 90 days from today
4. Write the result to `state/seen.json`

Format the JSON with 2-space indentation for readable git diffs.

## Step 9: Commit and Push

IMPORTANT: You MUST commit and push directly to the `main` branch.
Do NOT create a new branch. Do NOT push to a `claude/` prefixed branch.
Cloudflare Pages deploys from `main` — any other branch will not deploy.

Stage all changes:
```bash
git add content/posts/ state/seen.json
```

Check if anything is staged:
```bash
git diff --cached --quiet
```

If nothing is staged, exit cleanly (this happens if all items were
duplicates and state didn't change).

If changes exist, commit and push directly to main:
```bash
git commit -m "digest: {YYYY-MM-DD}"
git push origin HEAD:main
```

If the push fails because the remote has advanced (non-fast-forward),
rebase onto the latest main and try once more:
```bash
git pull --rebase origin main
git push origin HEAD:main
```

If the retry also fails, fall back to pushing the commit to a
recovery branch so the digest isn't lost:
```bash
git push origin HEAD:recovery/digest-{YYYY-MM-DD}
```

Log that the main push failed and the recovery branch name.
Do NOT send the ntfy notification (Step 10) in this case —
the user will be alerted by noticing the branch and will
merge it manually.

## Step 10: Send Notification

After a successful commit and push, send a summary notification to ntfy.sh:

```bash
curl -s \
  -H "Title: AI Digest — {Month Day, Year}" \
  -H "Tags: newspaper" \
  -d "{executive summary from the digest}" \
  https://ntfy.sh/ai-news-digest
```

The message body should be the 2-3 sentence executive summary from
Step 6. Plain text only, no markdown. Do NOT include a Click header
with any URL — the ntfy topic is public and subscribers arrive
through different channels.

If the push failed in Step 9, do NOT send the notification.
If the notification fails, log the error but do not retry — this
is informational, not critical.

## Important Constraints

- Never combine multiple days into one digest. One run = one date.
- Never modify or overwrite existing digest posts in content/posts/.
- Never delete files from the repository.
- If a step fails, log the error and continue to subsequent steps where
  possible. The digest should include whatever was successfully collected.
- If digest synthesis fails entirely, do NOT push a broken or empty post.
  Update state/seen.json with what was collected and push only the state.
