#!/usr/bin/env python3
"""Fetch and parse RSS/Atom feeds, output structured JSON.

Reads config/feeds.yaml, fetches all feeds, parses with atoma,
normalizes URLs, computes SHA-256 dedup hashes, applies ArXiv keyword
filtering, deduplicates against state/seen.json, and drops items older
than 48 hours. Outputs only new, recent items as JSON to stdout.

Usage:
    python3 scripts/fetch_feeds.py [--config config/feeds.yaml] [--state state/seen.json]
"""

import argparse
import hashlib
import json
import random
import re
import socket
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import atoma
import yaml

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "source", "fbclid", "gclid",
}
USER_AGENT = "Mozilla/5.0 (compatible; AIDigestBot/1.0; +https://digest.sorcerousmachine.com)"
FETCH_TIMEOUT = 30
MAX_AGE_HOURS = 48
THIN_DESCRIPTION_THRESHOLD = 200
INTER_FETCH_JITTER_RANGE = (0.2, 0.5)
RETRY_BACKOFF_BASE = 2.0
RETRY_BACKOFF_JITTER = 1.0
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
HARD_FAILURE_TYPES = frozenset({"status:404", "status:410", "content_mismatch"})
DISABLE_THRESHOLD = 3


def visible_text_length(text: str) -> int:
    """Length of the description after stripping HTML tags and whitespace."""
    if not text:
        return 0
    stripped = re.sub(r"<[^>]+>", "", text)
    return len(stripped.strip())


def looks_like_feed(data: bytes) -> bool:
    """Heuristic: does this response body look like an XML feed?

    Returns False for clearly-HTML responses (body does not match the
    expected feed format — typically a publisher that moved to an SPA
    and left the feed URL serving the site HTML). Returns True otherwise
    — lets the parser decide edge cases.
    """
    if not data:
        return False
    head = data.lstrip(b"\xef\xbb\xbf").lstrip()[:256].lower()
    if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
        return False
    return True


def parse_published_date(date_str: str) -> datetime | None:
    """Try to parse a published date string into a timezone-aware datetime."""
    if not date_str:
        return None

    # Try email-style dates first (RFC 2822, common in RSS)
    try:
        return parsedate_to_datetime(date_str)
    except (ValueError, TypeError):
        pass

    # Try ISO 8601 formats
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue

    return None


def datetime_to_str(dt: datetime | None) -> str:
    """Convert a datetime to an RFC 2822 string, or empty string if None."""
    if dt is None:
        return ""
    return dt.strftime("%a, %d %b %Y %H:%M:%S %z").strip()


def is_recent(published: datetime | None, cutoff: datetime) -> bool:
    """Return True if the item's date is recent enough, or unknown (keep it)."""
    if published is None:
        return True
    return published >= cutoff


def load_state(path: Path) -> tuple[dict[str, str], dict]:
    """Load seen hashes and feed_health from state file. Empty structs if missing."""
    if not path.exists():
        return {}, {}
    try:
        data = json.loads(path.read_text())
        return data.get("seen", {}), data.get("feed_health", {})
    except (json.JSONDecodeError, KeyError):
        return {}, {}


def load_seen(path: Path) -> dict[str, str]:
    """Legacy shim — some callers import just load_seen."""
    seen, _ = load_state(path)
    return seen


def update_feed_health(
    prior: dict,
    outcomes: dict[str, str],
    today: str,
) -> tuple[dict, list[str]]:
    """Apply today's outcomes to feed_health, return (new_health, urls_to_disable).

    outcomes: {feed_url: "success" | hard_failure_type | "soft"}
      - "success" resets the consecutive_hard_failures counter.
      - A hard-failure type (e.g. "status:404", "content_mismatch") increments it.
      - "soft" leaves the counter unchanged (transient failure — unknown state).
    """
    new_health = {k: dict(v) for k, v in prior.items()}
    urls_to_disable: list[str] = []

    for url, outcome in outcomes.items():
        entry = new_health.get(url, {
            "consecutive_hard_failures": 0,
            "last_success": None,
            "last_error_type": None,
            "last_error_date": None,
        })

        if outcome == "success":
            entry["consecutive_hard_failures"] = 0
            entry["last_success"] = today
        elif outcome == "soft":
            pass  # transient — preserve prior counter state
        else:
            entry["consecutive_hard_failures"] = entry.get("consecutive_hard_failures", 0) + 1
            entry["last_error_type"] = outcome
            entry["last_error_date"] = today
            if entry["consecutive_hard_failures"] >= DISABLE_THRESHOLD:
                urls_to_disable.append(url)

        new_health[url] = entry

    return new_health, urls_to_disable


def normalize_url(url: str) -> str:
    """Normalize a URL for consistent hashing."""
    parsed = urlparse(url)

    scheme = parsed.scheme.lower()
    host = parsed.netloc.lower()

    # Strip tracking parameters
    params = parse_qs(parsed.query, keep_blank_values=True)
    filtered = {
        k: v for k, v in params.items()
        if k.lower() not in TRACKING_PARAMS
    }
    # Sort remaining parameters alphabetically
    sorted_query = urlencode(sorted(filtered.items()), doseq=True)

    # Strip trailing slash from path
    path = parsed.path.rstrip("/")

    # Reassemble without fragment
    return urlunparse((scheme, host, path, parsed.params, sorted_query, ""))


def hash_url(url: str) -> str:
    """SHA-256 hash of the normalized URL."""
    normalized = normalize_url(url)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def classify_fetch_error(exc: Exception) -> str:
    """Bucket a fetch/parse exception into a stable category string."""
    if isinstance(exc, (atoma.FeedParseError, atoma.FeedXMLError)):
        return "parse_error"
    if isinstance(exc, HTTPError):
        return f"status:{exc.code}"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return "timeout"
        return "network"
    if isinstance(exc, OSError):
        return "network"
    return "other"


def _single_fetch(req: Request) -> bytes:
    with urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        return resp.read()


def fetch_bytes(url: str) -> bytes:
    """Fetch URL content as bytes. Retries once on 5xx/429/timeouts/network errors."""
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        return _single_fetch(req)
    except HTTPError as e:
        if e.code not in RETRYABLE_STATUS_CODES:
            raise
    except (URLError, socket.timeout, TimeoutError, OSError):
        pass

    time.sleep(RETRY_BACKOFF_BASE + random.uniform(0, RETRY_BACKOFF_JITTER))
    return _single_fetch(req)


def parse_feed(data: bytes) -> tuple[list[dict], str]:
    """Parse feed bytes, trying RSS then Atom. Returns (items, format)."""
    try:
        feed = atoma.parse_rss_bytes(data)
        items = []
        for item in feed.items:
            link = item.link or (item.guid if item.guid else "")
            title = (item.title or "").strip()
            if not link or not title:
                continue

            published = item.pub_date
            description = (item.description or "")[:500]

            # Extract author
            author = item.author or ""

            items.append({
                "title": title,
                "link": link,
                "published": published,
                "description": description,
                "author": author,
            })
        return items, "rss"
    except (atoma.FeedParseError, atoma.FeedXMLError):
        pass

    feed = atoma.parse_atom_bytes(data)
    items = []
    for entry in feed.entries:
        # Atom entries can have multiple links; prefer rel="alternate"
        link = ""
        for lnk in (entry.links or []):
            if lnk.rel in (None, "alternate"):
                link = lnk.href or ""
                break
        if not link and entry.links:
            link = entry.links[0].href or ""
        if not link and entry.id_:
            link = entry.id_

        title = (entry.title.value if entry.title else "").strip()
        if not link or not title:
            continue

        published = entry.published or entry.updated

        # Atom summary/content
        description = ""
        if entry.summary and entry.summary.value:
            description = entry.summary.value[:500]
        elif entry.content and entry.content.value:
            description = entry.content.value[:500]

        # Authors
        author = ""
        if entry.authors:
            author = ", ".join(a.name for a in entry.authors if a.name)

        items.append({
            "title": title,
            "link": link,
            "published": published,
            "description": description,
            "author": author,
        })
    return items, "atom"


def matches_keywords(title: str, keywords: list[str]) -> list[str]:
    """Return list of keywords that match the title (case-insensitive)."""
    title_lower = title.lower()
    return [kw for kw in keywords if kw.lower() in title_lower]


def main():
    parser = argparse.ArgumentParser(description="Fetch and parse RSS/Atom feeds")
    parser.add_argument(
        "--config",
        default="config/feeds.yaml",
        help="Path to feeds config file",
    )
    parser.add_argument(
        "--state",
        default="state/seen.json",
        help="Path to seen.json state file for deduplication",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(json.dumps({"error": f"Config file not found: {config_path}"}))
        sys.exit(1)

    with open(config_path) as f:
        config = yaml.safe_load(f)

    seen, feed_health = load_state(Path(args.state))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    result = {
        "feeds": [],
        "arxiv": [],
        "errors": [],
        "warnings": [],
    }

    all_feed_items = []
    all_arxiv_items = []
    skipped_seen = 0
    skipped_old = 0
    feed_outcomes: dict[str, str] = {}

    def record_outcome(url: str, err_type: str | None) -> None:
        """Map an error type to a health outcome: success / hard-failure / soft."""
        if err_type is None:
            feed_outcomes[url] = "success"
        elif err_type in HARD_FAILURE_TYPES:
            feed_outcomes[url] = err_type
        else:
            feed_outcomes[url] = "soft"

    # Fetch regular feeds
    for feed_cfg in config.get("feeds", []):
        name = feed_cfg["name"]
        url = feed_cfg["url"]
        category = feed_cfg.get("category", "other")
        source_url = feed_cfg.get("homepage") or url

        time.sleep(random.uniform(*INTER_FETCH_JITTER_RANGE))

        try:
            data = fetch_bytes(url)
        except (HTTPError, URLError, socket.timeout, TimeoutError, OSError) as e:
            err_type = classify_fetch_error(e)
            result["errors"].append({
                "feed": name,
                "url": url,
                "source_url": source_url,
                "type": err_type,
                "error": str(e)[:200],
            })
            record_outcome(url, err_type)
            continue

        if not looks_like_feed(data):
            result["errors"].append({
                "feed": name,
                "url": url,
                "source_url": source_url,
                "type": "content_mismatch",
                "error": "Response body is HTML, not RSS/Atom — content type does not match expected feed format",
            })
            record_outcome(url, "content_mismatch")
            continue

        try:
            items, fmt = parse_feed(data)
        except (atoma.FeedParseError, atoma.FeedXMLError) as e:
            result["errors"].append({
                "feed": name,
                "url": url,
                "source_url": source_url,
                "type": "parse_error",
                "error": str(e)[:200],
            })
            record_outcome(url, "parse_error")
            continue

        record_outcome(url, None)
        for item in items:
            url_hash = hash_url(item["link"])
            published_str = datetime_to_str(item["published"])
            all_feed_items.append({
                "title": item["title"],
                "url": item["link"],
                "url_hash": url_hash,
                "published": published_str,
                "description": item["description"],
                "authors": item["author"],
                "source": name,
                "category": category,
                "_published_dt": item["published"],
            })

    # Filter: drop already-seen and older than 48 hours
    for item in all_feed_items:
        if item["url_hash"] in seen:
            skipped_seen += 1
            continue
        if not is_recent(item["_published_dt"], cutoff):
            skipped_old += 1
            continue
        # Remove internal field before output
        del item["_published_dt"]
        if visible_text_length(item["description"]) < THIN_DESCRIPTION_THRESHOLD:
            item["thin_description"] = True
        result["feeds"].append(item)

    # Fetch ArXiv feeds with keyword filtering
    arxiv_cfg = config.get("arxiv", {})
    high_signal = arxiv_cfg.get("keywords", {}).get("high_signal", [])
    moderate_signal = arxiv_cfg.get("keywords", {}).get("moderate_signal", [])

    for url in arxiv_cfg.get("feeds", []):
        feed_name = url.split("/")[-1] if "/" in url else url
        arxiv_source = f"arxiv:{feed_name}"
        source_url = f"https://arxiv.org/list/{feed_name}/recent"

        time.sleep(random.uniform(*INTER_FETCH_JITTER_RANGE))

        try:
            data = fetch_bytes(url)
        except (HTTPError, URLError, socket.timeout, TimeoutError, OSError) as e:
            err_type = classify_fetch_error(e)
            result["errors"].append({
                "feed": arxiv_source,
                "url": url,
                "source_url": source_url,
                "type": err_type,
                "error": str(e)[:200],
            })
            record_outcome(url, err_type)
            continue

        if not looks_like_feed(data):
            result["errors"].append({
                "feed": arxiv_source,
                "url": url,
                "source_url": source_url,
                "type": "content_mismatch",
                "error": "Response body is HTML, not RSS/Atom — content type does not match expected feed format",
            })
            record_outcome(url, "content_mismatch")
            continue

        try:
            items, fmt = parse_feed(data)
        except (atoma.FeedParseError, atoma.FeedXMLError) as e:
            result["errors"].append({
                "feed": arxiv_source,
                "url": url,
                "source_url": source_url,
                "type": "parse_error",
                "error": str(e)[:200],
            })
            record_outcome(url, "parse_error")
            continue

        record_outcome(url, None)
        for item in items:
            title = item["title"]

            # Apply keyword filtering
            high_matches = matches_keywords(title, high_signal)
            moderate_matches = matches_keywords(title, moderate_signal)

            passed = False
            matched_keywords = []
            if high_matches:
                passed = True
                matched_keywords = high_matches
            elif len(moderate_matches) >= 2:
                passed = True
                matched_keywords = moderate_matches

            if not passed:
                continue

            url_hash = hash_url(item["link"])
            published_str = datetime_to_str(item["published"])

            all_arxiv_items.append({
                "title": title,
                "url": item["link"],
                "url_hash": url_hash,
                "published": published_str,
                "authors": item["author"],
                "abstract": item["description"],
                "matched_keywords": matched_keywords,
                "source": f"arxiv:{feed_name}",
                "category": "research",
                "_published_dt": item["published"],
            })

    # Filter ArXiv: drop already-seen and older than 48 hours
    for item in all_arxiv_items:
        if item["url_hash"] in seen:
            skipped_seen += 1
            continue
        if not is_recent(item["_published_dt"], cutoff):
            skipped_old += 1
            continue
        del item["_published_dt"]
        result["arxiv"].append(item)

    # Apply today's outcomes to feed_health and identify disable candidates
    new_health, urls_to_disable = update_feed_health(feed_health, feed_outcomes, today_utc)

    # Enrich disable_candidates with config metadata so Claude can move entries
    feed_lookup = {f["url"]: f for f in config.get("feeds", [])}
    arxiv_urls = set(arxiv_cfg.get("feeds", []))
    disable_candidates = []
    for url in urls_to_disable:
        health = new_health.get(url, {})
        candidate = {
            "url": url,
            "consecutive_hard_failures": health.get("consecutive_hard_failures"),
            "last_error_type": health.get("last_error_type"),
            "last_success": health.get("last_success"),
        }
        if url in feed_lookup:
            cfg = feed_lookup[url]
            candidate["name"] = cfg.get("name", url)
            candidate["category"] = cfg.get("category", "other")
            candidate["homepage"] = cfg.get("homepage")
            candidate["section"] = "feeds"
        elif url in arxiv_urls:
            slug = url.rsplit("/", 1)[-1]
            candidate["name"] = f"arxiv:{slug}"
            candidate["category"] = "research"
            candidate["section"] = "arxiv.feeds"
        else:
            candidate["name"] = url
            candidate["section"] = "unknown"
        disable_candidates.append(candidate)

    result["feed_health_update"] = new_health
    result["disable_candidates"] = disable_candidates

    # Summary counts
    error_types = Counter(e.get("type", "other") for e in result["errors"])
    result["summary"] = {
        "feeds_attempted": len(config.get("feeds", [])) + len(arxiv_cfg.get("feeds", [])),
        "feeds_failed": len(result["errors"]),
        "error_types": dict(error_types),
        "total_fetched": len(all_feed_items) + len(all_arxiv_items),
        "skipped_already_seen": skipped_seen,
        "skipped_too_old": skipped_old,
        "new_feed_items": len(result["feeds"]),
        "new_arxiv_items": len(result["arxiv"]),
        "disable_candidates_count": len(disable_candidates),
    }

    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
