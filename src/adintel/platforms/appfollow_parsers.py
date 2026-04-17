"""
AppFollow response parsers.

Designed to be discoverable: the exact AppFollow internal API shape is unknown
until the first browser run. This module probes multiple field name variants and
envelope patterns so data is captured regardless of API version.

After running with --debug, inspect state/debug/appfollow/*.json to see the
real field names returned, then tighten the probing logic here if needed.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _iso_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, (int, float)):
        # Unix timestamp: seconds if < 1e10, else milliseconds
        ts = value / 1000 if value > 1e10 else value
        try:
            return datetime.fromtimestamp(ts, UTC).date()
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.split("T")[0])
        except ValueError:
            return None
    return None


def _valid_star_rating(value) -> float | None:
    try:
        v = float(value)
        if 1.0 <= v <= 5.0:
            return round(v, 1)
    except (TypeError, ValueError):
        pass
    return None


def _normalize_os(raw) -> str:
    if not raw:
        return "unknown"
    lower = str(raw).lower()
    # AppFollow uses "as" for App Store (iOS) and "gp" for Google Play (Android)
    if lower in ("as", "appstore", "app_store") or any(x in lower for x in ("iphone", "ipad", "ios", "apple")):
        return "ios"
    if lower in ("gp", "googleplay", "google_play") or "android" in lower:
        return "android"
    return lower


def _normalize_tags(raw) -> list[str] | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        result = [str(t).strip() for t in raw if t]
        return result or None
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return None


# ---------------------------------------------------------------------------
# Review row extraction
# ---------------------------------------------------------------------------

def _find_items(data: dict | list) -> list:
    """Extract the review item list from a variety of API envelope shapes."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("reviews", "data", "items", "results", "feedback", "list"):
            candidate = data.get(key)
            if isinstance(candidate, list):
                return candidate
    return []


def parse_review_rows(
    data: dict | list,
    advertiser_name: str,
    country: str,
    appfollow_item_id: str | None,
) -> list[dict]:
    """
    Extract individual review rows from an AppFollow API payload.

    Real AppFollow API shape (discovered 2026-04-16):
    - Envelope: {"reviews": [...], "nextCursor": "..."}
    - Per review:
      - reviewId: string (e.g. "13962112461")
      - content: review body text
      - title: review title
      - metaInformation.rating: int 1-5
      - metaInformation.created: ISO datetime string (e.g. "2026-04-15T23:48:28Z")
      - metaInformation.country: lowercase ISO-2 (e.g. "us")
      - metaInformation.version: app version string
      - metaInformation.author: reviewer username
      - metaInformation.store: "as" = iOS App Store, "gp" = Google Play
      - tags.sentiment: "Positive" | "Negative" | "Neutral"
      - tags.tags: list (may be empty)
      - tags.semanticTags: list of tag IDs (integers)
    """
    items = _find_items(data)
    rows: list[dict] = []

    for item in items:
        if not isinstance(item, dict):
            continue

        # Review ID: prefer reviewId (string), fall back to numeric id
        review_id = item.get("reviewId") or item.get("review_id") or item.get("id") or item.get("uid")
        if review_id is None:
            continue

        # Nested metaInformation for core review metadata
        meta = item.get("metaInformation") or {}

        review_date = _iso_date(
            meta.get("created")
            or item.get("date")
            or item.get("created_at")
            or item.get("published_at")
        )
        if review_date is None:
            continue

        # Country from metaInformation, fall back to request country
        item_country = meta.get("country") or item.get("country") or country
        if item_country:
            item_country = str(item_country).upper()[:8]

        # Sentiment and tags are nested under the "tags" object
        tags_obj = item.get("tags") or {}
        if isinstance(tags_obj, dict):
            sentiment = (
                tags_obj.get("sentiment")
                or item.get("sentiment")
                or item.get("sentimentLabel")
            )
            # tags.tags is a list of string labels (may be empty)
            raw_tags = tags_obj.get("tags") or []
        else:
            # Fallback: tags may be a direct list
            sentiment = item.get("sentiment") or item.get("sentimentLabel")
            raw_tags = tags_obj if isinstance(tags_obj, list) else []

        # Normalize sentiment to lowercase for consistency
        if sentiment:
            sentiment = str(sentiment).lower()  # "Positive" → "positive"

        rows.append({
            "advertiser_name":   advertiser_name,
            "review_id":         str(review_id),
            "review_date":       review_date,
            "country":           item_country or "US",
            "star_rating":       _valid_star_rating(
                meta.get("rating") or item.get("rating") or item.get("stars") or item.get("score")
            ),
            "username":          (
                meta.get("author") or item.get("author") or item.get("username") or item.get("reviewer")
            ),
            "title":             item.get("title") or item.get("subject"),
            "body":              (
                item.get("content")  # AppFollow primary field
                or item.get("body")
                or item.get("text")
                or item.get("review")
            ),
            "sentiment":         sentiment,
            "sentiment_score":   (
                item.get("sentimentScore") or item.get("sentiment_score")
            ),
            "tags":              _normalize_tags(raw_tags) if raw_tags else None,
            "app_version":       (
                meta.get("version") or item.get("version") or item.get("appVersion") or item.get("app_version")
            ),
            "os":                _normalize_os(
                meta.get("store") or item.get("os") or item.get("platform") or item.get("store")
            ),
            "appfollow_item_id": appfollow_item_id,
        })

    return rows


# ---------------------------------------------------------------------------
# Pagination cursor extraction
# ---------------------------------------------------------------------------

def extract_next_page_cursor(data: dict | list) -> str | int | None:
    """
    Try to find a pagination cursor or next-page indicator in the API response.

    Returns the cursor/offset/page-number to use for the next request,
    or None if this is the last page.
    """
    if not isinstance(data, dict):
        return None

    # Explicit "no more pages" flags
    has_more = data.get("has_more") or data.get("hasMore") or data.get("has_next")
    if has_more is False:
        return None

    # Cursor-based pagination
    for key in ("next_cursor", "nextCursor", "cursor", "next_page_token", "nextPageToken"):
        val = data.get(key)
        if val is not None and val != "" and val is not False:
            return val

    # Page-number pagination: current page + total pages
    page = data.get("page") or data.get("current_page") or data.get("currentPage")
    total_pages = data.get("total_pages") or data.get("totalPages") or data.get("pages")
    if page is not None and total_pages is not None:
        try:
            if int(page) < int(total_pages):
                return int(page) + 1
        except (TypeError, ValueError):
            pass

    # Offset-based pagination
    offset = data.get("offset")
    limit = data.get("limit") or data.get("per_page") or data.get("perPage")
    total_count = data.get("total") or data.get("total_count") or data.get("totalCount")
    if offset is not None and limit and total_count:
        try:
            if int(offset) + int(limit) < int(total_count):
                return int(offset) + int(limit)
        except (TypeError, ValueError):
            pass

    return None


# ---------------------------------------------------------------------------
# Debug dump helper
# ---------------------------------------------------------------------------

def write_debug_dump(label: str, captured: list[dict], debug_dir: Path) -> None:
    """
    Write captured API responses to a JSON file for inspection.

    Each entry includes the URL, top-level response keys, and a truncated
    sample so you can quickly identify which endpoint returns review data.
    """
    dump_dir = debug_dir / "appfollow"
    dump_dir.mkdir(parents=True, exist_ok=True)

    dump = []
    for item in captured:
        data = item.get("data", {})
        if isinstance(data, dict):
            keys = list(data.keys())
            sample = json.dumps(data)[:1000]
        elif isinstance(data, list):
            keys = [f"[list of {len(data)} items]"]
            sample = json.dumps(data[:2])[:1000]
        else:
            keys = [type(data).__name__]
            sample = str(data)[:1000]

        dump.append({
            "url":    item["url"],
            "keys":   keys,
            "sample": sample,
        })

    ts = int(datetime.now(UTC).timestamp())
    target = dump_dir / f"{label}-{ts}.json"
    target.write_text(json.dumps(dump, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("AppFollow debug dump written: %s", target)
