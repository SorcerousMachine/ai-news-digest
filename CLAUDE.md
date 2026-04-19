# AI News Digest Pipeline

You are running a daily AI industry digest pipeline. Follow these steps
exactly, in order. Do not skip steps. Do not fabricate content.

## Step 1: Load Configuration

Read `config/feeds.yaml` for the list of RSS/Atom feeds, ArXiv feed URLs,
keyword lists, and any other configuration.

Read `state/seen.json` for previously ingested URL hashes and per-feed
health tracking. The file has this shape:
```json
{
  "seen": {
    "<sha256-of-normalized-url>": "<YYYY-MM-DD date first seen>"
  },
  "feed_health": {
    "<feed-url>": {
      "consecutive_hard_failures": 0,
      "last_success": "YYYY-MM-DD",
      "last_error_type": null,
      "last_error_date": null
    }
  }
}
```

If `state/seen.json` does not exist or is empty, treat it as
`{"seen": {}, "feed_health": {}}`. The script manages feed_health
for you — you only write it back in Step 10.

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
    {"feed": "...", "url": "...", "source_url": "...", "type": "status:403", "error": "..."}
  ],
  "disable_candidates": [
    {
      "url": "...",
      "name": "...",
      "category": "...",
      "homepage": "...",
      "section": "feeds",
      "consecutive_hard_failures": 2,
      "last_error_type": "content_mismatch",
      "last_success": "2026-04-10"
    }
  ],
  "feed_health_update": {
    "<feed-url>": {
      "consecutive_hard_failures": 0,
      "last_success": "YYYY-MM-DD",
      "last_error_type": null,
      "last_error_date": null
    }
  },
  "summary": {
    "feeds_attempted": 14,
    "feeds_failed": 0,
    "error_types": {"status:403": 2, "timeout": 1, "parse_error": 0},
    "total_fetched": 2159,
    "skipped_already_seen": 1800,
    "skipped_too_old": 300,
    "new_feed_items": 47,
    "new_arxiv_items": 12,
    "disable_candidates_count": 0
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

**Feed health tracking.** The script tracks `consecutive_hard_failures`
per feed across runs. Hard failures are `status:404`, `status:410`,
and `content_mismatch` (HTTP 200 where the response body does not match
the expected feed format — today that means an HTML body where XML was
expected). Soft failures (5xx,
timeouts, network, parse_error) do not increment the counter. After
three consecutive hard failures a feed appears in `disable_candidates`;
Step 9 moves those feeds out of the active list in `config/feeds.yaml`
so tomorrow's run does not waste attempts on them.

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

**Passive feed discovery.** While reading search results, also note
any author, newsletter, or publisher whose work you'd cite multiple
times across recent digests but which is NOT in the active list in
`config/feeds.yaml`. Record these as **candidate feeds** — name plus
any RSS/Atom URL you can find on the source page. Do NOT add them to
the pipeline yourself; surface them in the commit message under a
"Candidate feeds" line so the user can vet and add them by hand. A
name only rises to candidacy after it has accumulated signal across
multiple days of digests; one-off citations do not qualify. Skip this
when nothing meets the bar — do NOT fabricate candidates.

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
with Steps 6-11 anyway and publish a minimal digest post whose body
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

5. **Sources Unavailable Today** (`##` header): ONLY include this
   section if the Step 2 script output `errors` array contains at
   least one entry whose `type` is a **transient** failure —
   i.e., NOT in {`status:404`, `status:410`, `content_mismatch`}. Omit
   the section entirely if all errors were hard failures (those
   are handled by the Feeds Retired section instead) or if there
   were no errors at all.

   Precede the list with one sentence: "These sources could not be
   fetched today. Links point to their homepages so you can check
   them directly."

   Then one bullet per **transient** failed feed, in the order they
   appear in the `errors` array:

   ```
   - [{feed name}]({source_url}) — *{type}*
   ```

   Use the `source_url` field from each error object (already the
   homepage when available, the RSS URL as fallback). Use the `type`
   field verbatim (e.g., `status:403`, `timeout`, `parse_error`) so
   the failure mode is transparent to readers.

6. **Feeds Retired Today** (`##` header): ONLY include this section
   when the Step 2 output `disable_candidates` array is non-empty.
   Omit entirely otherwise.

   Precede the list with one sentence: "The following feed URLs
   were retired today after repeated hard failures. They have been
   moved out of the active feed list."

   One bullet per entry in `disable_candidates`:

   ```
   - [{name}]({homepage or url}) — *{last_error_type}* (no successful fetch since {last_success or "first run"})
   ```

   Prefer `homepage` when present; fall back to `url` otherwise.

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
title: "AI News Digest — {Month Day, Year}"
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

## Step 8: Post-Write Deduplication

This step catches semantic duplicates — the same story surfaced today
that was already covered in a recent digest under a different URL.
URL-hash dedup (via `state/seen.json`) cannot catch this because web
search commonly finds a different article about the same event.

Read the two most recent prior digest posts in `content/posts/`
(by filename date, excluding today's file). This matches the 48-hour
recency window enforced in Steps 2 and 3 — any item duplicate must
have appeared in one of the two prior days. If fewer than two prior
posts exist, use whatever exists.

For each item in today's digest (any section, any category), ask: is
this covering the same underlying story — same model release, same
funding round, same policy action, same research paper, same
acquisition — as anything in the prior posts? A different article
about the same story IS a duplicate. A follow-up with genuinely new
information (e.g., benchmark numbers released a day after a model
announcement, or a court ruling following a previously-reported
filing) is NOT a duplicate — keep it and lean the analysis into
what is actually new.

For each confirmed duplicate:
- Remove the item (its `###` heading, source attribution, analysis)
- If the Executive Summary or Threads to Watch references the removed
  item, rewrite those sections to stay coherent without it
- If a category section becomes empty after removal, drop the category
  heading entirely and remove its slug from the `tags:` front matter

After edits, verify the `summary` front matter field still matches
the first sentence of the (possibly rewritten) Executive Summary.
Update it if it drifted.

If every item turns out to be a duplicate, replace the body with:
"No significant new AI developments were surfaced in the last 48
hours. See the recent archive for ongoing coverage." Still proceed
to commit and push so the schedule stays consistent.

Overwrite `content/posts/{YYYY-MM-DD}.md` in place with the cleaned
version. This is the ONE exception to the "never modify existing
posts" constraint at the bottom of this document — today's file has
not been committed yet, and the constraint applies to previously-
published posts.

Do NOT add, reword, or reorder items during this step. The only
permitted edits are removals plus the coherence-preserving rewrites
of the executive summary, threads, summary field, and tags.

## Step 9: Process Feed Retirements

If the Step 2 output `disable_candidates` array is empty, skip this
step entirely.

Otherwise, move each candidate out of the active feed list in
`config/feeds.yaml` and into a `disabled:` top-level section. If the
`disabled:` section does not yet exist, create it after `arxiv:` at
the bottom of the file.

For each candidate, the disabled entry should have this shape:

```yaml
disabled:
  - name: "{candidate.name}"
    url: "{candidate.url}"
    homepage: "{candidate.homepage}"   # omit the line if homepage is null
    category: "{candidate.category}"
    disabled_on: "{YYYY-MM-DD today}"
    disabled_reason: "{candidate.last_error_type} ({candidate.consecutive_hard_failures} consecutive hard failures)"
```

Then REMOVE the candidate's original entry from the `feeds:` list (or
from `arxiv.feeds:` if `section` is `arxiv.feeds`). Preserve all other
feed entries, comments, and formatting in `config/feeds.yaml` exactly
as they were. Use `Edit` (not a full rewrite) to minimize diff noise.

The purpose: tomorrow's run skips these URLs entirely. Re-enabling is
a manual action — move the entry back from `disabled:` to the active
list by hand.

## Step 10: Update State

Build an updated `state/seen.json` with two top-level keys:

**`seen`** (URL hash deduplication):
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

**`feed_health`** (per-feed failure tracking):
Use the Step 2 output `feed_health_update` verbatim — the script has
already applied today's outcomes. Write it under the `feed_health`
key. Do not modify it.

Format the JSON with 2-space indentation for readable git diffs.

## Step 11: Commit and Push

IMPORTANT: You MUST commit and push directly to the `main` branch.
Do NOT create a new branch. Do NOT push to a `claude/` prefixed branch.
Cloudflare Pages deploys from `main` — any other branch will not deploy.

Stage all changes:
```bash
git add content/posts/ state/seen.json config/feeds.yaml
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
Do NOT send the ntfy notification (Step 12) in this case —
the user will be alerted by noticing the branch and will
merge it manually.

## Step 12: Send Notification

After a successful commit and push, send a summary notification to ntfy.sh:

```bash
curl -s \
  -H "Title: AI News Digest — {Month Day, Year}" \
  -H "Tags: newspaper" \
  -d "{executive summary from the digest}" \
  https://ntfy.sh/ai-news-digest
```

The message body should be the 2-3 sentence executive summary from
Step 6. Plain text only, no markdown. Do NOT include a Click header
with any URL — the ntfy topic is public and subscribers arrive
through different channels.

If the push failed in Step 11, do NOT send the notification.
If the notification fails, log the error but do not retry — this
is informational, not critical.

## Important Constraints

- Never combine multiple days into one digest. One run = one date.
- Never modify or overwrite previously-committed digest posts in
  content/posts/. Today's in-progress file may be rewritten during
  Step 8 dedup; all prior posts are immutable.
- Never delete files from the repository.
- If a step fails, log the error and continue to subsequent steps where
  possible. The digest should include whatever was successfully collected.
- If digest synthesis fails entirely, do NOT push a broken or empty post.
  Update state/seen.json with what was collected and push only the state.
