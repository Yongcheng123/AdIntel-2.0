import asyncio
import json
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import adintel.mcp.server as mcp_server
from adintel.db.models import (
    AdvertiserRecord,
    Base,
    OtterlyCitationRecord,
    OtterlyPromptRecord,
    ScrapeRunRecord,
    SensorTowerDownloadRecord,
    SensorTowerMarketTopAppRecord,
    SocialPetaCreativeChannelRecord,
    SocialPetaCreativeRecord,
    SocialPetaCreativeTagRecord,
)
from adintel.mcp.server import create_mcp_server


def test_mcp_server_registers_expected_tools() -> None:
    server = create_mcp_server()
    tool_names = [tool.name for tool in server._tool_manager.list_tools()]
    assert tool_names == [
        "list_advertisers",
        "get_advertiser_summary",
        "get_socialpeta_summary",
        "get_socialpeta_comparison",
        "request_advertiser",
        "list_requested_advertisers",
        "read_schema_text",
        "run_query",
        "get_collection_status",
        "get_metric_timeseries",
        "get_full_comparison",
        "get_market_top_apps",
        "get_geo_summary",
        "compare_geo_visibility",
        "get_geo_data_availability",
        "get_appfollow_reviews",
        "get_appfollow_sentiment_trend",
        "get_appfollow_keyword_analysis",
        "compare_appfollow_reviews",
    ]


def build_sqlite_session_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def decode_tool_result(result) -> dict:
    return json.loads(result[0][0].text)


def test_get_metric_timeseries_returns_latest_window(monkeypatch) -> None:
    session_factory = build_sqlite_session_factory()
    with session_factory() as session:
        session.add(AdvertiserRecord(name="Chime", countries_csv="US"))
        for day in range(1, 6):
            session.add(
                SensorTowerDownloadRecord(
                    advertiser_name="Chime",
                    period_date=date(2026, 3, day),
                    country="US",
                    granularity="day",
                    os="unified",
                    downloads=day,
                )
            )
        session.commit()

    monkeypatch.setattr(mcp_server, "_session_factory", lambda: session_factory)
    server = create_mcp_server()

    result = asyncio.run(
        server.call_tool(
            "get_metric_timeseries",
            {"advertiser_name": "Chime", "metric": "downloads", "country": "US", "days": 2},
        )
    )
    payload = decode_tool_result(result)

    assert [point["date"] for point in payload["data"]] == ["2026-03-04", "2026-03-05"]


def test_get_collection_status_for_advertiser_includes_health_and_recent_runs(monkeypatch) -> None:
    session_factory = build_sqlite_session_factory()
    with session_factory() as session:
        session.add(AdvertiserRecord(name="Chime", countries_csv="US"))
        session.add_all(
            [
                ScrapeRunRecord(advertiser_name="Chime", platform="sensortower", status="success"),
            ]
        )
        session.commit()

    monkeypatch.setattr(mcp_server, "_session_factory", lambda: session_factory)
    server = create_mcp_server()

    result = asyncio.run(server.call_tool("get_collection_status", {"advertiser_name": "Chime"}))
    payload = decode_tool_result(result)

    assert {row["platform"] for row in payload["health"]} == {"sensortower"}
    assert len(payload["recent_runs"]) == 1
    assert payload["data_availability"]["advertisers"][0]["advertiser_name"] == "Chime"
    assert payload["data_availability"]["advertisers"][0]["sensortower"]["has_data"] is True
    assert payload["alerts"]["thresholds"]["stale_hours"] == 48


def test_get_collection_status_filters_recent_runs_by_platform(monkeypatch) -> None:
    session_factory = build_sqlite_session_factory()
    with session_factory() as session:
        session.add(AdvertiserRecord(name="Chime", countries_csv="US"))
        session.add_all(
            [
                ScrapeRunRecord(
                    advertiser_name="Chime",
                    platform="sensortower",
                    status="success",
                    message="Collected SensorTower core metrics.",
                    result_metadata={"records_written": 7},
                ),
            ]
        )
        session.commit()

    monkeypatch.setattr(mcp_server, "_session_factory", lambda: session_factory)
    server = create_mcp_server()

    result = asyncio.run(
        server.call_tool(
            "get_collection_status",
            {"advertiser_name": "Chime", "platform": "sensortower"},
        )
    )
    payload = decode_tool_result(result)

    assert len(payload["recent_runs"]) == 1
    assert payload["recent_runs"][0]["platform"] == "sensortower"
    assert payload["recent_runs"][0]["metadata"]["records_written"] == 7


def test_get_collection_status_includes_otterly_availability_matrix(monkeypatch) -> None:
    session_factory = build_sqlite_session_factory()
    with session_factory() as session:
        session.add(AdvertiserRecord(name="Chime", domain="chime.com", countries_csv="US"))
        session.add(
            OtterlyPromptRecord(
                target_brand_or_domain_name="chime.com",
                country_code="us",
                query_window_start_date=date(2026, 3, 5),
                query_window_end_date=date(2026, 4, 6),
                prompt_text="Best online checking accounts",
                ai_engine="ChatGPT",
                domain_cited=True,
            )
        )
        session.commit()

    monkeypatch.setattr(mcp_server, "_session_factory", lambda: session_factory)
    server = create_mcp_server()

    result = asyncio.run(server.call_tool("get_collection_status", {"advertiser_name": "Chime"}))
    payload = decode_tool_result(result)

    availability = payload["data_availability"]["advertisers"][0]
    assert availability["advertiser_name"] == "Chime"
    assert availability["geo_otterly"]["has_data"] is True
    assert availability["geo_otterly"]["prompt_rows"] == 1


def test_get_collection_status_includes_socialpeta_availability_matrix(monkeypatch) -> None:
    session_factory = build_sqlite_session_factory()
    with session_factory() as session:
        session.add(AdvertiserRecord(name="Chime", countries_csv="US"))
        session.add(
            SocialPetaCreativeRecord(
                advertiser_name="Chime",
                country="US",
                creative_id="creative-1",
                creative_title="Save more today",
                creative_type="Video",
                primary_channel="TikTok",
            )
        )
        session.add(
            SocialPetaCreativeChannelRecord(
                advertiser_name="Chime",
                country="US",
                creative_id="creative-1",
                channel="TikTok",
            )
        )
        session.add(
            SocialPetaCreativeTagRecord(
                advertiser_name="Chime",
                country="US",
                creative_id="creative-1",
                tag_category="creative_type",
                tag_value="UGC",
            )
        )
        session.commit()

    monkeypatch.setattr(mcp_server, "_session_factory", lambda: session_factory)
    server = create_mcp_server()

    result = asyncio.run(server.call_tool("get_collection_status", {"advertiser_name": "Chime"}))
    payload = decode_tool_result(result)

    availability = payload["data_availability"]["advertisers"][0]
    assert availability["socialpeta"]["has_data"] is True
    assert availability["socialpeta"]["creative_rows"] == 1
    assert payload["data_availability"]["summary"]["socialpeta_available"] == 1


def test_get_geo_summary_returns_combined_visibility_citation_and_prompt_views(monkeypatch) -> None:
    session_factory = build_sqlite_session_factory()
    with session_factory() as session:
        session.add(
            AdvertiserRecord(name="Chime", domain="chime.com", countries_csv="US")
        )
        session.add_all(
            [
                OtterlyPromptRecord(
                    target_brand_or_domain_name="chime.com",
                    country_code="us",
                    query_window_start_date=date(2026, 3, 5),
                    query_window_end_date=date(2026, 4, 6),
                    prompt_text="Best online checking accounts",
                    prompt_volume=500,
                    target_rank=2,
                    ai_engine="ChatGPT",
                    domain_cited=True,
                    sentiment_score=0.7,
                    sentiment_label="positive",
                    competitors=["Current", "SoFi"],
                ),
                OtterlyPromptRecord(
                    target_brand_or_domain_name="chime.com",
                    country_code="us",
                    query_window_start_date=date(2026, 3, 5),
                    query_window_end_date=date(2026, 4, 6),
                    prompt_text="Best app for overdraft protection",
                    prompt_volume=350,
                    target_rank=None,
                    ai_engine="Perplexity",
                    domain_cited=False,
                    sentiment_score=-0.4,
                    sentiment_label="negative",
                    competitors=["Current"],
                ),
                OtterlyCitationRecord(
                    target_brand_or_domain_name="chime.com",
                    country_code="us",
                    query_window_start_date=date(2026, 3, 5),
                    query_window_end_date=date(2026, 4, 6),
                    ai_engine="ChatGPT",
                    cited_url="https://www.chime.com/blog/checking/",
                    cited_domain="chime.com",
                    citation_count=3,
                    brand_mentioned=True,
                    domain_category="brand-owned",
                    competitors=["Current"],
                ),
            ]
        )
        session.commit()

    monkeypatch.setattr(mcp_server, "_session_factory", lambda: session_factory)
    server = create_mcp_server()

    result = asyncio.run(server.call_tool("get_geo_summary", {"advertiser_name": "Chime"}))
    payload = decode_tool_result(result)

    assert payload["advertiser_name"] == "chime.com"
    assert payload["visibility"]["total_prompts_tracked"] == 2
    assert payload["visibility"]["prompts_where_cited"] == 1
    assert payload["citations"]["total_citation_rows"] == 1
    assert payload["citations"]["top_cited_domains"][0]["domain"] == "chime.com"
    assert payload["prompts"]["top_by_volume"][0]["prompt"] == "Best online checking accounts"
    assert payload["prompts"]["blind_spots"][0]["prompt"] == "Best app for overdraft protection"


def test_get_socialpeta_summary_returns_creative_and_gap_views(monkeypatch) -> None:
    session_factory = build_sqlite_session_factory()
    with session_factory() as session:
        session.add_all(
            [
                AdvertiserRecord(name="Chime", countries_csv="US"),
                AdvertiserRecord(name="Current", countries_csv="US"),
            ]
        )
        session.add_all(
            [
                SocialPetaCreativeRecord(
                    advertiser_name="Chime",
                    country="US",
                    creative_id="chime-1",
                    creative_title="Chime UGC video",
                    creative_type="Video",
                    primary_channel="TikTok",
                    active_days=42,
                    impression=5000,
                    creative_score=88,
                    first_seen=date(2026, 3, 1),
                    last_seen=date(2026, 4, 12),
                ),
                SocialPetaCreativeRecord(
                    advertiser_name="Current",
                    country="US",
                    creative_id="current-1",
                    creative_title="Current static",
                    creative_type="Image",
                    primary_channel="Facebook",
                    active_days=12,
                    impression=900,
                    creative_score=52,
                    first_seen=date(2026, 4, 1),
                    last_seen=date(2026, 4, 13),
                ),
                SocialPetaCreativeChannelRecord(
                    advertiser_name="Chime",
                    country="US",
                    creative_id="chime-1",
                    channel="TikTok",
                ),
                SocialPetaCreativeChannelRecord(
                    advertiser_name="Current",
                    country="US",
                    creative_id="current-1",
                    channel="Facebook",
                ),
                SocialPetaCreativeTagRecord(
                    advertiser_name="Chime",
                    country="US",
                    creative_id="chime-1",
                    tag_category="creative_type",
                    tag_value="UGC",
                ),
                SocialPetaCreativeTagRecord(
                    advertiser_name="Current",
                    country="US",
                    creative_id="current-1",
                    tag_category="creative_type",
                    tag_value="Feature demo",
                ),
            ]
        )
        session.commit()

    monkeypatch.setattr(mcp_server, "_session_factory", lambda: session_factory)
    server = create_mcp_server()

    result = asyncio.run(server.call_tool("get_socialpeta_summary", {"advertiser_name": "Chime"}))
    payload = decode_tool_result(result)

    assert payload["found"] is True
    assert payload["advertiser_name"] == "Chime"
    assert payload["summary"]["creatives"] == 1
    assert payload["summary"]["top_primary_channel"] == "TikTok"
    assert payload["comparison"]["gap_analysis"]["root"] == "Chime"
    assert payload["comparison"]["gap_analysis"]["competitors"] == ["Current"]
    assert payload["comparison"]["gap_analysis"]["video_share_gap"]["root"] == 1.0
    assert payload["comparison"]["gap_analysis"]["video_share_gap"]["competitor_average"] == 0.0


def test_compute_category_benchmarks_matches_multi_category_catalog_values(monkeypatch) -> None:
    session_factory = build_sqlite_session_factory()
    with session_factory() as session:
        session.add_all(
            [
                SensorTowerMarketTopAppRecord(
                    scrape_month=date(2026, 4, 1),
                    country="US",
                    category="Overall",
                    os="unified",
                    rank=1,
                    app_name="Stash",
                    primary_category="Finance",
                    downloads=1000,
                    dau=500,
                    impression_share=0.2,
                    ad_on_tiktok=True,
                ),
                SensorTowerMarketTopAppRecord(
                    scrape_month=date(2026, 4, 1),
                    country="US",
                    category="Overall",
                    os="unified",
                    rank=2,
                    app_name="Shopback",
                    primary_category="Lifestyle",
                    downloads=800,
                    dau=300,
                    impression_share=0.1,
                    ad_on_facebook=True,
                ),
            ]
        )
        session.commit()

        payload = mcp_server._compute_category_benchmarks(
            session,
            category="Finance, Education",
            country="US",
            client_downloads=900,
            client_dau=400,
            client_sov=0.15,
            client_networks={"facebook"},
        )

    assert payload is not None
    assert payload["total_apps_in_category"] == 1
    assert payload["medians"]["downloads"] == 1000
    assert payload["signals"][0]["metric"] == "downloads"
