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
)
from adintel.mcp.server import create_mcp_server


def test_mcp_server_registers_expected_tools() -> None:
    server = create_mcp_server()
    tool_names = [tool.name for tool in server._tool_manager.list_tools()]
    assert tool_names == [
        "list_advertisers",
        "get_advertiser_summary",
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
            {"advertiser_name": "Chime", "metric": "downloads", "country": "US", "limit": 2},
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
                ScrapeRunRecord(advertiser_name="Chime", platform="adclarity", status="error", message="boom"),
            ]
        )
        session.commit()

    monkeypatch.setattr(mcp_server, "_session_factory", lambda: session_factory)
    server = create_mcp_server()

    result = asyncio.run(server.call_tool("get_collection_status", {"advertiser_name": "Chime"}))
    payload = decode_tool_result(result)

    assert {row["platform"] for row in payload["health"]} == {"sensortower", "adclarity"}
    assert len(payload["recent_runs"]) == 2
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
                ScrapeRunRecord(
                    advertiser_name="Chime",
                    platform="adclarity",
                    status="success",
                    message="Collected AdClarity data.",
                    result_metadata={"records_written": 3},
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
