from __future__ import annotations

from datetime import date


CATEGORY_NAMES = {
    "6000": "Business",
    "6001": "Weather",
    "6002": "Utilities",
    "6003": "Travel",
    "6004": "Sports",
    "6005": "Social Networking",
    "6006": "Reference",
    "6007": "Productivity",
    "6008": "Photo & Video",
    "6009": "News",
    "6010": "Navigation",
    "6011": "Music",
    "6012": "Lifestyle",
    "6013": "Health & Fitness",
    "6014": "Games",
    "6015": "Finance",
    "6016": "Entertainment",
    "6017": "Education",
    "6018": "Books",
    "6020": "Medical",
    "6021": "Magazines & Newspapers",
    "6022": "Catalogs",
    "6023": "Food & Drink",
    "6024": "Shopping",
}


def iso_date(value: str | date | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).split("T")[0])


def _valid_star_rating(value: object) -> float | None:
    if isinstance(value, (int, float)) and 1 <= value <= 5:
        return round(float(value), 1)
    return None


def normalize_array(data: dict | list) -> list:
    if isinstance(data, list):
        return data
    for key in ["data", "apps", "rows", "items", "results", "series"]:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def detect_category(identifiers: set[str], data: dict) -> tuple[str | None, str | None]:
    apps = data.get("apps")
    if not isinstance(apps, list):
        return None, None

    for app in apps:
        app_ids = {
            str(value)
            for value in [
                app.get("app_id"),
                app.get("id"),
                app.get("bundle_id"),
                app.get("unified_app_id"),
            ]
            if value
        }
        if not identifiers.intersection(app_ids):
            continue
        categories = app.get("categories") or []
        if categories:
            category_id = str(categories[0])
            return category_id, CATEGORY_NAMES.get(category_id)
    return None, None


def parse_download_rows(data: dict, advertiser_name: str, country: str) -> tuple[list[dict], list[dict]]:
    if not data.get("apps"):
        return [], []

    timeseries = data["apps"][0].get("timeseries") or []
    download_rows: list[dict] = []
    usage_rows: list[dict] = []

    for point in timeseries:
        period_date = iso_date(point.get("date"))
        if period_date is None:
            continue
        download_rows.append(
            {
                "advertiser_name": advertiser_name,
                "period_date": period_date,
                "granularity": "day",
                "country": country,
                "os": "unified",
                "downloads": point.get("units"),
                "revenue": point.get("revenue"),
            }
        )
        if point.get("dau") is not None:
            usage_rows.append(
                {
                    "advertiser_name": advertiser_name,
                    "period_date": period_date,
                    "country": country,
                    "avg_dau": point.get("dau"),
                    "time_spent_min": None,
                    "sessions_per_day": None,
                }
            )

    return download_rows, usage_rows


def parse_retention_rows(
    data: dict,
    advertiser_name: str,
    country: str,
    default_start: date,
) -> list[dict]:
    rows: list[dict] = []
    for point in data.get("data") or []:
        if point.get("est_retention_d1") is None:
            continue
        rows.append(
            {
                "advertiser_name": advertiser_name,
                "cohort_date": iso_date(point.get("date")) or default_start,
                "country": country,
                "d1": point.get("est_retention_d1"),
                "d3": point.get("est_retention_d3"),
                "d7": point.get("est_retention_d7"),
                "d14": point.get("est_retention_d14"),
                "d30": point.get("est_retention_d30"),
                "d60": point.get("est_retention_d60"),
            }
        )
    return rows


def parse_impression_share_rows(
    data: dict,
    advertiser_name: str,
    country: str,
    *,
    default_network: str | None = None,
) -> list[dict]:
    rows: list[dict] = []
    for point in data.get("data") or []:
        period_date = iso_date(point.get("date"))
        network = (
            default_network
            or point.get("network")
            or point.get("adNetwork")
            or point.get("ad_network")
            or point.get("name")
        )
        if period_date is None or not network or point.get("impressionShareAbsolute") is None:
            continue
        rows.append(
            {
                "advertiser_name": advertiser_name,
                "period_date": period_date,
                "network": network,
                "country": country,
                "sov_pct": point.get("impressionShareAbsolute"),
            }
        )
    return rows


def parse_demographic_rows(data: dict, advertiser_name: str, country: str) -> list[dict]:
    app_data = data.get("app_data") or []
    if not app_data:
        return []

    normalized = app_data[0].get("normalized_demographics") or {}
    rows = [
        {
            "advertiser_name": advertiser_name,
            "country": country,
            "age_bracket": "18-24",
            "male_pct": normalized.get("male_18"),
            "female_pct": normalized.get("female_18"),
        },
        {
            "advertiser_name": advertiser_name,
            "country": country,
            "age_bracket": "25-34",
            "male_pct": normalized.get("male_25"),
            "female_pct": normalized.get("female_25"),
        },
        {
            "advertiser_name": advertiser_name,
            "country": country,
            "age_bracket": "35-44",
            "male_pct": normalized.get("male_35"),
            "female_pct": normalized.get("female_35"),
        },
        {
            "advertiser_name": advertiser_name,
            "country": country,
            "age_bracket": "45-54",
            "male_pct": normalized.get("male_45"),
            "female_pct": normalized.get("female_45"),
        },
        {
            "advertiser_name": advertiser_name,
            "country": country,
            "age_bracket": "55+",
            "male_pct": normalized.get("male_55"),
            "female_pct": normalized.get("female_55"),
        },
    ]
    return [row for row in rows if row["male_pct"] is not None or row["female_pct"] is not None]


def merge_engagement_rows(
    values: list[dict],
    advertiser_name: str,
    country: str,
    existing: dict[tuple[str, date, str], dict] | None = None,
) -> dict[tuple[str, date, str], dict]:
    rows_by_key = existing or {}
    for value in values:
        period_date = iso_date(value.get("date") or value.get("period_date"))
        if period_date is None:
            continue
        key = (advertiser_name, period_date, country)
        row = rows_by_key.setdefault(
            key,
            {
                "advertiser_name": advertiser_name,
                "period_date": period_date,
                "country": country,
                "avg_dau": None,
                "time_spent_min": None,
                "sessions_per_day": None,
            },
        )
        time_spent = value.get("time_spent") or value.get("timeSpent") or value.get("avg_time_spent")
        sessions = (
            value.get("session_count")
            or value.get("sessionCount")
            or value.get("sessions_per_day")
            or value.get("avg_sessions")
        )
        if time_spent is not None:
            row["time_spent_min"] = time_spent
        if sessions is not None:
            row["sessions_per_day"] = sessions
    return rows_by_key


def parse_ranking_row(
    data: dict,
    advertiser_name: str,
    country: str,
    target_id: str,
    rank_date: date,
    category: str,
) -> dict | None:
    apps = data.get("apps") or []
    for index, app in enumerate(apps):
        app_id = app.get("app_id") or app.get("id") or app.get("unified_app_id")
        if str(app_id) != str(target_id):
            continue
        return {
            "advertiser_name": advertiser_name,
            "rank_date": rank_date,
            "country": country,
            "category": category,
            "chart_type": "ad_sov",
            "rank": index + 1,
            "is_featured": False,
        }
    return None


def parse_review_rows(data: dict, advertiser_name: str, country: str) -> list[dict]:
    by_date: dict[date, dict] = {}
    for item in data.get("data") or []:
        period_date = iso_date(item.get("date"))
        if period_date is None:
            continue
        row = by_date.setdefault(
            period_date,
            {
                "advertiser_name": advertiser_name,
                "period_date": period_date,
                "country": country,
                "avg_rating": item.get("review_rating_average"),
                "rating_count": item.get("review_rating_total_count"),
                "star_1_count": None,
                "star_2_count": None,
                "star_3_count": None,
                "star_4_count": None,
                "star_5_count": None,
            },
        )
        rating = item.get("review_rating")
        count = item.get("review_rating_count")
        if rating in {1, 2, 3, 4, 5}:
            row[f"star_{rating}_count"] = count
    return list(by_date.values())


def parse_review_text_rows(data: dict, advertiser_name: str) -> list[dict]:
    rows: list[dict] = []
    for item in data.get("feedback") or []:
        review_date = iso_date(item.get("date"))
        review_id = item.get("id")
        if review_date is None or review_id is None:
            continue
        rows.append(
            {
                "advertiser_name": advertiser_name,
                "review_id": int(review_id),
                "review_date": review_date,
                "country": item.get("country") or "US",
                "star_rating": _valid_star_rating(item.get("rating") or item.get("stars")),
                "username": item.get("username") or item.get("author"),
                "title": item.get("title"),
                "body": item.get("content") or item.get("body") or item.get("text"),
                "sentiment": item.get("sentiment"),
                "tags": item.get("tags") or [],
                "app_version": item.get("version") or item.get("app_version"),
                "os": item.get("os") or item.get("platform") or item.get("device_type") or "Unknown",
            }
        )
    return rows


def build_creative_metadata_map(captured: list[dict]) -> dict[str, dict]:
    metadata: dict[str, dict] = {}
    for item in captured:
        if "creatives/metadata" not in item["url"]:
            continue
        for creative in item["data"].get("creatives") or []:
            creative_id = creative.get("id")
            if creative_id is None:
                continue
            metadata[str(creative_id)] = {
                "thumbnail_url": creative.get("media_urls", {}).get("thumb_url")
                or creative.get("media_urls", {}).get("preview_url"),
                "creative_type": creative.get("ad_type"),
                "duration_seconds": creative.get("video", {}).get("duration"),
            }
    return metadata


def _duration_bucket(duration_seconds: float | int | None) -> str | None:
    if duration_seconds is None:
        return None
    if duration_seconds < 6:
        return "<6s"
    if duration_seconds <= 10:
        return "6-10s"
    if duration_seconds <= 15:
        return "10-15s"
    if duration_seconds <= 30:
        return "15-30s"
    return "30s+"


def parse_creative_rows(
    data: dict,
    advertiser_name: str,
    metadata_map: dict[str, dict],
) -> list[dict]:
    rows: list[dict] = []
    for item in normalize_array(data):
        creative_id = item.get("grouped_creative_id") or item.get("id")
        if creative_id is None:
            continue
        first_child_id = (item.get("creative_ids") or [None])[0]
        metadata = metadata_map.get(str(first_child_id)) if first_child_id is not None else None
        ad_formats = item.get("grouped_creative_ad_formats") or []
        duration_seconds = item.get("grouped_creative_duration") or (metadata or {}).get("duration_seconds")
        rows.append(
            {
                "advertiser_name": advertiser_name,
                "creative_id": str(creative_id),
                "creative_type": (metadata or {}).get("creative_type") or (ad_formats[0] if ad_formats else None),
                "network": item.get("network"),
                "thumbnail_url": (metadata or {}).get("thumbnail_url"),
                "duration_bucket": _duration_bucket(duration_seconds),
                "first_seen": iso_date(item.get("grouped_creative_first_seen_at")),
            }
        )
    return rows


def parse_aso_keyword_rows(data: dict, advertiser_name: str, country: str, *, device: str = "iphone") -> list[dict]:
    rows: list[dict] = []
    for item in normalize_array(data):
        keyword = item.get("keyword") or item.get("term")
        if not keyword:
            continue
        rows.append(
            {
                "advertiser_name": advertiser_name,
                "keyword": keyword,
                "keyword_type": item.get("keyword_type") or item.get("keywordType") or item.get("type"),
                "rank": item.get("keyword_rank") or item.get("rank") or item.get("position"),
                "traffic_score": item.get("keyword_traffic") or item.get("traffic_score") or item.get("trafficScore"),
                "opportunity_score": item.get("keyword_opportunity_score")
                or item.get("opportunity_score")
                or item.get("opportunityScore"),
                "country": country,
                "device": device,
            }
        )
    return rows
