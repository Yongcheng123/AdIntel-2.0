from datetime import date

from adintel.platforms.sensortower_parsers import (
    build_creative_metadata_map,
    detect_category,
    merge_engagement_rows,
    parse_aso_keyword_rows,
    parse_creative_rows,
    parse_demographic_rows,
    parse_download_rows,
    parse_impression_share_rows,
    parse_ranking_row,
    parse_review_rows,
    parse_review_text_rows,
    parse_retention_rows,
)


def test_detect_category_matches_known_identifier() -> None:
    category_id, category_name = detect_category(
        {"55d93e4402ac645ad20fc82a", "836215269"},
        {
            "apps": [
                {
                    "unified_app_id": "55d93e4402ac645ad20fc82a",
                    "categories": [6015, 6012],
                }
            ]
        },
    )
    assert category_id == "6015"
    assert category_name == "Finance"


def test_parse_download_rows_extracts_downloads_and_dau() -> None:
    downloads, usage = parse_download_rows(
        {
            "apps": [
                {
                    "timeseries": [
                        {"date": "2026-03-01T00:00:00Z", "units": 120, "revenue": 0, "dau": 5000},
                        {"date": "2026-03-02T00:00:00Z", "units": 140, "revenue": 3.5},
                    ]
                }
            ]
        },
        "Chime",
        "US",
    )
    assert len(downloads) == 2
    assert downloads[0]["downloads"] == 120
    assert len(usage) == 1
    assert usage[0]["avg_dau"] == 5000


def test_parse_retention_rows_uses_default_date_when_missing() -> None:
    rows = parse_retention_rows(
        {"data": [{"est_retention_d1": 0.4, "est_retention_d7": 0.2}]},
        "Chime",
        "US",
        date(2026, 3, 1),
    )
    assert len(rows) == 1
    assert rows[0]["cohort_date"] == date(2026, 3, 1)
    assert rows[0]["d7"] == 0.2


def test_parse_impression_share_rows_honors_default_network() -> None:
    rows = parse_impression_share_rows(
        {"data": [{"date": "2026-03-02T00:00:00Z", "impressionShareAbsolute": 0.12}]},
        "Chime",
        "US",
        default_network="all",
    )
    assert rows == [
        {
            "advertiser_name": "Chime",
            "period_date": date(2026, 3, 2),
            "network": "all",
            "country": "US",
            "sov_pct": 0.12,
        }
    ]


def test_parse_demographic_rows_filters_empty_buckets() -> None:
    rows = parse_demographic_rows(
        {
            "app_data": [
                {
                    "normalized_demographics": {
                        "male_18": 0.1,
                        "female_18": 0.2,
                        "male_25": 0.3,
                        "female_25": 0.4,
                    }
                }
            ]
        },
        "Chime",
        "US",
    )
    assert len(rows) == 2
    assert rows[0]["age_bracket"] == "18-24"
    assert rows[1]["age_bracket"] == "25-34"


def test_merge_engagement_rows_merges_two_measures() -> None:
    rows = merge_engagement_rows(
        [{"date": "2026-03-01T00:00:00Z", "timeSpent": 7.5}],
        "Chime",
        "US",
    )
    rows = merge_engagement_rows(
        [{"date": "2026-03-01T00:00:00Z", "sessionCount": 3}],
        "Chime",
        "US",
        existing=rows,
    )
    merged = list(rows.values())[0]
    assert merged["time_spent_min"] == 7.5
    assert merged["sessions_per_day"] == 3


def test_parse_ranking_row_finds_matching_app() -> None:
    row = parse_ranking_row(
        {
            "apps": [
                {"unified_app_id": "other"},
                {"unified_app_id": "target"},
            ]
        },
        "Chime",
        "US",
        "target",
        date(2026, 4, 3),
        "Finance",
    )
    assert row is not None
    assert row["rank"] == 2


def test_parse_review_rows_groups_daily_stars() -> None:
    rows = parse_review_rows(
        {
            "data": [
                {
                    "date": "2026-03-01T00:00:00Z",
                    "review_rating": 5,
                    "review_rating_count": 10,
                    "review_rating_average": 4.6,
                    "review_rating_total_count": 18,
                },
                {
                    "date": "2026-03-01T00:00:00Z",
                    "review_rating": 1,
                    "review_rating_count": 2,
                    "review_rating_average": 4.6,
                    "review_rating_total_count": 18,
                },
            ]
        },
        "Chime",
        "US",
    )
    assert len(rows) == 1
    assert rows[0]["star_5_count"] == 10
    assert rows[0]["star_1_count"] == 2
    assert rows[0]["rating_count"] == 18


def test_parse_review_text_rows_extracts_feedback() -> None:
    rows = parse_review_text_rows(
        {
            "feedback": [
                {
                    "id": 42,
                    "date": "2026-03-01T00:00:00Z",
                    "country": "US",
                    "rating": 2,
                    "username": "tester",
                    "content": "bad experience",
                    "tags": ["support"],
                    "version": "1.0.0",
                    "os": "android",
                }
            ]
        },
        "Chime",
    )
    assert len(rows) == 1
    assert rows[0]["review_id"] == 42
    assert rows[0]["body"] == "bad experience"
    assert rows[0]["tags"] == ["support"]
    assert rows[0]["os"] == "android"


def test_parse_review_text_rows_os_defaults_to_none() -> None:
    rows = parse_review_text_rows(
        {
            "feedback": [
                {
                    "id": 99,
                    "date": "2026-03-15T00:00:00Z",
                    "country": "BR",
                    "rating": 5,
                    "content": "great app",
                }
            ]
        },
        "Chime",
    )
    assert len(rows) == 1
    assert rows[0]["os"] is None


def test_parse_creative_rows_uses_metadata_lookup() -> None:
    metadata = build_creative_metadata_map(
        [
            {
                "url": "https://example.com/api/creatives/metadata",
                "data": {
                    "creatives": [
                        {
                            "id": 9,
                            "media_urls": {"thumb_url": "https://img"},
                            "ad_type": "Video",
                            "video": {"duration": 12},
                        }
                    ]
                },
            }
        ]
    )
    rows = parse_creative_rows(
        {
            "items": [
                {
                    "grouped_creative_id": "group-1",
                    "creative_ids": [9],
                    "grouped_creative_ad_formats": ["Playable"],
                    "network": "Admob",
                    "grouped_creative_first_seen_at": "2026-03-05T00:00:00Z",
                }
            ]
        },
        "Chime",
        metadata,
    )
    assert len(rows) == 1
    assert rows[0]["creative_type"] == "Video"
    assert rows[0]["thumbnail_url"] == "https://img"
    assert rows[0]["duration_bucket"] == "10-15s"


def test_parse_aso_keyword_rows_extracts_keywords() -> None:
    rows = parse_aso_keyword_rows(
        {
            "items": [
                {
                    "keyword": "mobile banking",
                    "keywordType": "Generic",
                    "rank": 3,
                    "trafficScore": 7.5,
                    "opportunityScore": 4.2,
                }
            ]
        },
        "Chime",
        "US",
    )
    assert rows == [
        {
            "advertiser_name": "Chime",
            "keyword": "mobile banking",
            "keyword_type": "Generic",
            "rank": 3,
            "traffic_score": 7.5,
            "opportunity_score": 4.2,
            "country": "US",
            "device": "iphone",
        }
    ]


def test_parse_aso_keyword_rows_accepts_device_parameter() -> None:
    rows = parse_aso_keyword_rows(
        {"items": [{"keyword": "crypto wallet", "rank": 5}]},
        "Coinbase",
        "US",
        device="android",
    )
    assert len(rows) == 1
    assert rows[0]["device"] == "android"
