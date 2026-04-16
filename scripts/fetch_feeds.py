#!/usr/bin/env python3
"""Fetch and parse RSS/Atom feeds, output structured JSON.

Reads config/feeds.yaml, fetches all feeds, parses with feedparser,
normalizes URLs, computes SHA-256 dedup hashes, applies ArXiv keyword
filtering, deduplicates against state/seen.json, and drops items older
than 48 hours. Outputs only new, recent items as JSON to stdout.

Usage:
    python3 scripts/fetch_feeds.py [--config config/feeds.yaml] [--state state/seen.json]
"""

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import feedparser
import yaml

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "source", "fbclid", "gclid",
}
REQUEST_HEADERS = {"User-Agent": "AIDigest/1.0"}
FETCH_TIMEOUT = 30
MAX_AGE_HOURS = 48


def parse_published_date(date_str: str) -> datetime | None:
    """Try to parse a published date string into a timezone-aware datetime."""
    if not date_str:
        return None

    # feedparser's parsed time tuple
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


def is_recent(date_str: str, cutoff: datetime) -> bool:
    """Return True if the item's date is recent enough, or unparseable (keep it)."""
    parsed = parse_published_date(date_str)
    if parsed is None:
        # Can't determine age — keep it rather than drop it
        return True
    return parsed >= cutoff


def load_seen(path: Path) -> dict[str, str]:
    """Load seen.json, returning the 'seen' dict. Empty dict if missing."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data.get("seen", {})
    except (json.JSONDecodeError, KeyError):
        return {}


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


def parse_feed_items(feed_data, feed_name: str, category: str) -> list[dict]:
    """Extract items from a parsed feed."""
    items = []
    for entry in feed_data.entries:
        link = entry.get("link", "")
        if not link:
            continue

        title = entry.get("title", "").strip()
        if not title:
            continue

        # Extract published date
        published = ""
        for date_field in ("published", "updated", "created"):
            if entry.get(date_field):
                published = entry[date_field]
                break

        # Extract description/summary, truncate to 500 chars
        description = ""
        for desc_field in ("summary", "description", "content"):
            val = entry.get(desc_field)
            if isinstance(val, list) and val:
                val = val[0].get("value", "")
            if val:
                description = str(val)[:500]
                break

        # Extract authors for ArXiv papers
        authors = ""
        if entry.get("authors"):
            authors = ", ".join(
                a.get("name", "") for a in entry["authors"] if a.get("name")
            )
        elif entry.get("author"):
            authors = entry["author"]

        url_hash = hash_url(link)

        items.append({
            "title": title,
            "url": link,
            "url_hash": url_hash,
            "published": published,
            "description": description,
            "authors": authors,
            "source": feed_name,
            "category": category,
        })

    return items


def matches_keywords(title: str, keywords: list[str]) -> list[str]:
    """Return list of keywords that match the title (case-insensitive)."""
    title_lower = title.lower()
    return [kw for kw in keywords if kw.lower() in title_lower]


def fetch_feed(url: str) -> tuple[feedparser.FeedParserDict | None, str | None]:
    """Fetch and parse a single feed. Returns (feed_data, error)."""
    try:
        feed = feedparser.parse(
            url,
            request_headers=REQUEST_HEADERS,
        )
        if feed.bozo and not feed.entries:
            return None, f"Parse error: {feed.bozo_exception}"
        return feed, None
    except Exception as e:
        return None, str(e)


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

    seen = load_seen(Path(args.state))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)

    result = {
        "feeds": [],
        "arxiv": [],
        "errors": [],
    }

    all_feed_items = []
    all_arxiv_items = []
    skipped_seen = 0
    skipped_old = 0

    # Fetch regular feeds
    for feed_cfg in config.get("feeds", []):
        name = feed_cfg["name"]
        url = feed_cfg["url"]
        category = feed_cfg.get("category", "other")

        feed_data, error = fetch_feed(url)
        if error:
            result["errors"].append({"feed": name, "url": url, "error": error})
            continue

        items = parse_feed_items(feed_data, name, category)
        all_feed_items.extend(items)

    # Filter: drop already-seen and older than 48 hours
    for item in all_feed_items:
        if item["url_hash"] in seen:
            skipped_seen += 1
            continue
        if not is_recent(item["published"], cutoff):
            skipped_old += 1
            continue
        result["feeds"].append(item)

    # Fetch ArXiv feeds with keyword filtering
    arxiv_cfg = config.get("arxiv", {})
    high_signal = arxiv_cfg.get("keywords", {}).get("high_signal", [])
    moderate_signal = arxiv_cfg.get("keywords", {}).get("moderate_signal", [])

    for url in arxiv_cfg.get("feeds", []):
        feed_name = url.split("/")[-1] if "/" in url else url

        feed_data, error = fetch_feed(url)
        if error:
            result["errors"].append({"feed": f"arxiv:{feed_name}", "url": url, "error": error})
            continue

        for entry in feed_data.entries:
            link = entry.get("link", "")
            title = entry.get("title", "").strip()
            if not link or not title:
                continue

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

            # Extract abstract/summary
            abstract = ""
            for desc_field in ("summary", "description"):
                val = entry.get(desc_field)
                if val:
                    abstract = str(val)
                    break

            authors = ""
            if entry.get("authors"):
                authors = ", ".join(
                    a.get("name", "") for a in entry["authors"] if a.get("name")
                )
            elif entry.get("author"):
                authors = entry["author"]

            # Extract published date
            published = ""
            for date_field in ("published", "updated", "created"):
                if entry.get(date_field):
                    published = entry[date_field]
                    break

            url_hash = hash_url(link)

            all_arxiv_items.append({
                "title": title,
                "url": link,
                "url_hash": url_hash,
                "published": published,
                "authors": authors,
                "abstract": abstract,
                "matched_keywords": matched_keywords,
                "source": f"arxiv:{feed_name}",
                "category": "research",
            })

    # Filter ArXiv: drop already-seen and older than 48 hours
    for item in all_arxiv_items:
        if item["url_hash"] in seen:
            skipped_seen += 1
            continue
        if not is_recent(item["published"], cutoff):
            skipped_old += 1
            continue
        result["arxiv"].append(item)

    # Summary counts
    result["summary"] = {
        "feeds_attempted": len(config.get("feeds", [])) + len(arxiv_cfg.get("feeds", [])),
        "feeds_failed": len(result["errors"]),
        "total_fetched": len(all_feed_items) + len(all_arxiv_items),
        "skipped_already_seen": skipped_seen,
        "skipped_too_old": skipped_old,
        "new_feed_items": len(result["feeds"]),
        "new_arxiv_items": len(result["arxiv"]),
    }

    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
