import asyncio
import json
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import adintel.mcp.server as mcp_server
from adintel.db.models import AdvertiserRecord, Base, ScrapeRunRecord, SensorTowerDownloadRecord
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
        "get_collection_health",
        "get_collection_alerts",
        "get_recent_collection_runs",
        "get_metric_timeseries",
        "get_full_comparison",
        "get_market_top_apps",
        "get_geo_visibility_summary",
        "compare_geo_visibility",
        "get_geo_citation_analysis",
        "get_geo_prompt_insights",
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


def test_get_collection_health_for_advertiser_includes_all_platforms(monkeypatch) -> None:
    session_factory = build_sqlite_session_factory()
    with session_factory() as session:
        session.add_all(
            [
                ScrapeRunRecord(advertiser_name="Chime", platform="sensortower", status="success"),
                ScrapeRunRecord(advertiser_name="Chime", platform="adclarity", status="error", message="boom"),
            ]
        )
        session.commit()

    monkeypatch.setattr(mcp_server, "_session_factory", lambda: session_factory)
    server = create_mcp_server()

    result = asyncio.run(server.call_tool("get_collection_health", {"advertiser_name": "Chime"}))
    payload = decode_tool_result(result)

    assert {row["platform"] for row in payload["collection_health"]} == {"sensortower", "adclarity"}


def test_get_recent_collection_runs_returns_persisted_metadata(monkeypatch) -> None:
    session_factory = build_sqlite_session_factory()
    with session_factory() as session:
        session.add(
            ScrapeRunRecord(
                advertiser_name="Chime",
                platform="sensortower",
                status="success",
                message="Collected SensorTower core metrics.",
                result_metadata={"records_written": 7, "result": {"metric_results": {"downloads/US": "success"}}},
            )
        )
        session.commit()

    monkeypatch.setattr(mcp_server, "_session_factory", lambda: session_factory)
    server = create_mcp_server()

    result = asyncio.run(server.call_tool("get_recent_collection_runs", {"advertiser_name": "Chime"}))
    payload = decode_tool_result(result)

    assert len(payload["runs"]) == 1
    assert payload["runs"][0]["metadata"]["records_written"] == 7
