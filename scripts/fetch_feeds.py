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
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from urllib.request import Request, urlopen
from urllib.error import URLError

import atoma
import yaml

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "source", "fbclid", "gclid",
}
USER_AGENT = "AIDigest/1.0"
FETCH_TIMEOUT = 30
MAX_AGE_HOURS = 48


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


def fetch_bytes(url: str) -> bytes:
    """Fetch URL content as bytes."""
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        return resp.read()


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
    except atoma.FeedParseError:
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

    seen = load_seen(Path(args.state))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)

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

    # Fetch regular feeds
    for feed_cfg in config.get("feeds", []):
        name = feed_cfg["name"]
        url = feed_cfg["url"]
        category = feed_cfg.get("category", "other")

        try:
            data = fetch_bytes(url)
            items, fmt = parse_feed(data)
        except (URLError, OSError) as e:
            result["errors"].append({"feed": name, "url": url, "error": f"Fetch error: {e}"})
            continue
        except (atoma.FeedParseError, Exception) as e:
            result["errors"].append({"feed": name, "url": url, "error": f"Parse error: {e}"})
            continue

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
        result["feeds"].append(item)

    # Fetch ArXiv feeds with keyword filtering
    arxiv_cfg = config.get("arxiv", {})
    high_signal = arxiv_cfg.get("keywords", {}).get("high_signal", [])
    moderate_signal = arxiv_cfg.get("keywords", {}).get("moderate_signal", [])

    for url in arxiv_cfg.get("feeds", []):
        feed_name = url.split("/")[-1] if "/" in url else url

        try:
            data = fetch_bytes(url)
            items, fmt = parse_feed(data)
        except (URLError, OSError) as e:
            result["errors"].append({"feed": f"arxiv:{feed_name}", "url": url, "error": f"Fetch error: {e}"})
            continue
        except (atoma.FeedParseError, Exception) as e:
            result["errors"].append({"feed": f"arxiv:{feed_name}", "url": url, "error": f"Parse error: {e}"})
            continue

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
