from __future__ import annotations

import json
import os
from datetime import date, datetime
from decimal import Decimal
from collections import Counter

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import case, desc, func, or_, select

from adintel.core.competitor_groups import build_competitor_run_plan, load_competitor_groups
from adintel.core.settings import ROOT_DIR, get_settings
from adintel.db.models import (
    AppFollowReviewRecord,
    OtterlyCitationRecord,
    OtterlyPromptRecord,
    RequestedAdvertiserRecord,
    ScrapeRunRecord,
    SensorTowerAsoKeywordRecord,
    SensorTowerCreativeRecord,
    SensorTowerDemographicRecord,
    SensorTowerDownloadRecord,
    SensorTowerImpressionShareRecord,
    SensorTowerMarketTopAppRecord,
    SensorTowerRankingRecord,
    SensorTowerReviewRecord,
    SensorTowerReviewTextRecord,
    SensorTowerRetentionRecord,
    SensorTowerUsageRecord,
    SocialPetaCreativeChannelRecord,
    SocialPetaCreativeRecord,
    SocialPetaCreativeTagRecord,
)
from adintel.db.repositories import AdvertiserRepository, CollectionHealthRepository, RequestedAdvertiserRepository
from adintel.db.repositories import ScrapeRunRepository
from adintel.db.session import build_session_factory


SCHEMA_PATH = ROOT_DIR / "sql" / "schema.sql"


def _session_factory():
    return build_session_factory(get_settings())


def _schema_text() -> str:
    if not SCHEMA_PATH.exists():
        return "Schema file not found."
    return SCHEMA_PATH.read_text(encoding="utf-8")


def _serialize_requested(row: RequestedAdvertiserRecord) -> dict:
    return {
        "name": row.name,
        "requested_by": row.requested_by,
        "context": row.context,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _serialize_scrape_run(row) -> dict:
    return {
        "id": row.id,
        "advertiser_name": row.advertiser_name,
        "platform": row.platform,
        "status": row.status,
        "message": row.message,
        "metadata": row.result_metadata,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


def _to_float(val):
    """Safely convert Decimal or numeric values to float for JSON serialization."""
    if val is None:
        return None
    if isinstance(val, Decimal):
        return float(val)
    if hasattr(val, "__float__"):
        return float(val)
    return val


def _build_data_availability(session, advertiser_name: str | None = None) -> dict:
    advertisers = AdvertiserRepository(session).list()
    if advertiser_name:
        advertisers = [adv for adv in advertisers if adv.name == advertiser_name]

    st_freshness_q = (
        select(
            ScrapeRunRecord.advertiser_name,
            func.max(ScrapeRunRecord.finished_at).label("last_scraped"),
            func.count().label("total_runs"),
        )
        .where(ScrapeRunRecord.platform == "sensortower")
        .where(ScrapeRunRecord.status.in_(["success", "partial"]))
        .group_by(ScrapeRunRecord.advertiser_name)
    )
    st_freshness = {
        row.advertiser_name: {
            "last_scraped": row.last_scraped.isoformat() if row.last_scraped else None,
            "total_runs": row.total_runs,
        }
        for row in session.execute(st_freshness_q).all()
    }

    st_download_counts = {
        row.advertiser_name: row.row_count
        for row in session.execute(
            select(
                SensorTowerDownloadRecord.advertiser_name,
                func.count().label("row_count"),
            ).group_by(SensorTowerDownloadRecord.advertiser_name)
        ).all()
    }

    geo_prompt_stats = {
        row.target_brand_or_domain_name: {
            "prompt_rows": row.prompt_rows,
            "last_scraped": row.last_scraped.isoformat() if row.last_scraped else None,
        }
        for row in session.execute(
            select(
                OtterlyPromptRecord.target_brand_or_domain_name,
                func.count().label("prompt_rows"),
                func.max(OtterlyPromptRecord.scraped_at).label("last_scraped"),
            ).group_by(OtterlyPromptRecord.target_brand_or_domain_name)
        ).all()
    }
    geo_citation_counts = {
        row.target_brand_or_domain_name: row.citation_rows
        for row in session.execute(
            select(
                OtterlyCitationRecord.target_brand_or_domain_name,
                func.count().label("citation_rows"),
            ).group_by(OtterlyCitationRecord.target_brand_or_domain_name)
        ).all()
    }
    socialpeta_freshness = {
        row.advertiser_name: {
            "last_scraped": row.last_scraped.isoformat() if row.last_scraped else None,
            "creative_rows": row.creative_rows,
        }
        for row in session.execute(
            select(
                SocialPetaCreativeRecord.advertiser_name,
                func.max(SocialPetaCreativeRecord.scraped_at).label("last_scraped"),
                func.count().label("creative_rows"),
            ).group_by(SocialPetaCreativeRecord.advertiser_name)
        ).all()
    }
    socialpeta_channel_counts = {
        row.advertiser_name: row.channel_rows
        for row in session.execute(
            select(
                SocialPetaCreativeChannelRecord.advertiser_name,
                func.count().label("channel_rows"),
            ).group_by(SocialPetaCreativeChannelRecord.advertiser_name)
        ).all()
    }
    socialpeta_tag_counts = {
        row.advertiser_name: row.tag_rows
        for row in session.execute(
            select(
                SocialPetaCreativeTagRecord.advertiser_name,
                func.count().label("tag_rows"),
            ).group_by(SocialPetaCreativeTagRecord.advertiser_name)
        ).all()
    }
    appfollow_freshness = {
        row.advertiser_name: {
            "last_scraped": row.last_scraped.isoformat() if row.last_scraped else None,
            "review_rows": row.review_rows,
        }
        for row in session.execute(
            select(
                AppFollowReviewRecord.advertiser_name,
                func.max(AppFollowReviewRecord.scraped_at).label("last_scraped"),
                func.count().label("review_rows"),
            ).group_by(AppFollowReviewRecord.advertiser_name)
        ).all()
    }
    socialpeta_freshness_ci = {k.casefold(): v for k, v in socialpeta_freshness.items()}
    socialpeta_channel_counts_ci = {k.casefold(): v for k, v in socialpeta_channel_counts.items()}
    socialpeta_tag_counts_ci = {k.casefold(): v for k, v in socialpeta_tag_counts.items()}
    appfollow_freshness_ci = {k.casefold(): v for k, v in appfollow_freshness.items()}

    rows: list[dict] = []
    for advertiser in advertisers:
        geo_key = advertiser.domain or advertiser.name
        st_run = st_freshness.get(advertiser.name, {})
        st_download_rows = st_download_counts.get(advertiser.name, 0)
        geo_prompt = geo_prompt_stats.get(geo_key, {})
        geo_citation_rows = geo_citation_counts.get(geo_key, 0)
        name_ci = advertiser.name.casefold()
        socialpeta_run = socialpeta_freshness.get(advertiser.name) or socialpeta_freshness_ci.get(name_ci, {})
        socialpeta_creative_rows = socialpeta_run.get("creative_rows", 0)
        socialpeta_channel_rows = socialpeta_channel_counts.get(advertiser.name) or socialpeta_channel_counts_ci.get(
            name_ci, 0
        )
        socialpeta_tag_rows = socialpeta_tag_counts.get(advertiser.name) or socialpeta_tag_counts_ci.get(name_ci, 0)
        af_run = appfollow_freshness.get(advertiser.name) or appfollow_freshness_ci.get(name_ci, {})
        af_review_rows = af_run.get("review_rows", 0)

        rows.append({
            "advertiser_name": advertiser.name,
            "domain": advertiser.domain,
            "sensortower": {
                "has_data": bool(st_run.get("total_runs") or st_download_rows),
                "last_scraped": st_run.get("last_scraped"),
                "successful_runs": st_run.get("total_runs", 0),
                "download_rows": st_download_rows,
            },
            "geo_otterly": {
                "has_data": bool(geo_prompt.get("prompt_rows") or geo_citation_rows),
                "last_scraped": geo_prompt.get("last_scraped"),
                "prompt_rows": geo_prompt.get("prompt_rows", 0),
                "citation_rows": geo_citation_rows,
            },
            "socialpeta": {
                "has_data": bool(socialpeta_creative_rows or socialpeta_channel_rows or socialpeta_tag_rows),
                "last_scraped": socialpeta_run.get("last_scraped"),
                "creative_rows": socialpeta_creative_rows,
                "channel_rows": socialpeta_channel_rows,
                "tag_rows": socialpeta_tag_rows,
            },
            "appfollow": {
                "has_data": bool(af_review_rows),
                "last_scraped": af_run.get("last_scraped"),
                "review_rows": af_review_rows,
            },
        })

    return {
        "summary": {
            "tracked_advertisers": len(rows),
            "sensortower_available": sum(1 for row in rows if row["sensortower"]["has_data"]),
            "geo_otterly_available": sum(1 for row in rows if row["geo_otterly"]["has_data"]),
            "socialpeta_available": sum(1 for row in rows if row["socialpeta"]["has_data"]),
            "appfollow_available": sum(1 for row in rows if row["appfollow"]["has_data"]),
            "missing_sensortower": [row["advertiser_name"] for row in rows if not row["sensortower"]["has_data"]],
            "missing_geo_otterly": [row["advertiser_name"] for row in rows if not row["geo_otterly"]["has_data"]],
            "missing_socialpeta": [row["advertiser_name"] for row in rows if not row["socialpeta"]["has_data"]],
            "missing_appfollow": [row["advertiser_name"] for row in rows if not row["appfollow"]["has_data"]],
        },
        "advertisers": rows,
    }


# Maps metric names to (model, date_column, value_columns) for time-series queries
_METRIC_MAP = {
    "downloads": (
        SensorTowerDownloadRecord,
        "period_date",
        ["downloads", "revenue", "os"],
    ),
    "usage": (
        SensorTowerUsageRecord,
        "period_date",
        ["avg_dau", "time_spent_min", "sessions_per_day"],
    ),
    "retention": (
        SensorTowerRetentionRecord,
        "cohort_date",
        ["d1", "d3", "d7", "d14", "d30", "d60"],
    ),
    "impression_share": (
        SensorTowerImpressionShareRecord,
        "period_date",
        ["network", "sov_pct"],
    ),
    "rankings": (
        SensorTowerRankingRecord,
        "rank_date",
        ["category", "chart_type", "rank", "is_featured"],
    ),
    "reviews": (
        SensorTowerReviewRecord,
        "period_date",
        ["avg_rating", "rating_count", "star_1_count", "star_2_count", "star_3_count", "star_4_count", "star_5_count"],
    ),
}


def _build_timeseries(session, advertiser_name: str, metric: str, country: str, days: int) -> list[dict]:
    """Return chronological list of data-point dicts for a metric."""
    model, date_col_name, value_cols = _METRIC_MAP[metric]
    date_col = getattr(model, date_col_name)

    q = select(model).where(model.advertiser_name == advertiser_name)
    if hasattr(model, "country"):
        q = q.where(model.country == country)

    rows = session.scalars(
        q.order_by(desc(date_col)).limit(days)
    ).all()

    points = []
    for row in reversed(rows):
        point = {"date": getattr(row, date_col_name).isoformat()}
        if hasattr(row, "country"):
            point["country"] = row.country
        for col in value_cols:
            point[col] = _to_float(getattr(row, col, None))
        points.append(point)
    return points


def _resolved_info(advertiser) -> dict:
    """Return resolution metadata if fuzzy auto-resolve was used."""
    resolved_from = getattr(advertiser, "_resolved_from", None)
    if resolved_from:
        return {"resolved_from": resolved_from, "resolved_to": advertiser.name}
    return {}


def _median(lst: list) -> float | int | None:
    if not lst:
        return None
    n = len(lst)
    s = sorted(lst)
    return (s[n // 2 - 1] + s[n // 2]) / 2 if n % 2 == 0 else s[n // 2]


def _percentile_rank(lst: list, val) -> int | None:
    """What percentile does `val` fall at in the sorted list?"""
    if not lst or val is None:
        return None
    below = sum(1 for x in lst if x < val)
    return round(below / len(lst) * 100)


def _compute_category_benchmarks(
    session,
    category: str,
    country: str,
    client_downloads: int | None = None,
    client_dau: int | None = None,
    client_sov: float | None = None,
    client_networks: set[str] | None = None,
) -> dict | None:
    """Compute category-level benchmarks from market_top_apps and produce signals."""
    category_tokens = [token.strip() for token in (category or "").split(",") if token.strip()]
    if not category_tokens and category:
        category_tokens = [category]

    if not category_tokens:
        return None

    category_filter = or_(*[
        or_(
            SensorTowerMarketTopAppRecord.category.ilike(token),
            SensorTowerMarketTopAppRecord.primary_category.ilike(f"%{token}%"),
        )
        for token in category_tokens
    ])

    latest_month = session.scalar(
        select(func.max(SensorTowerMarketTopAppRecord.scrape_month))
        .where(SensorTowerMarketTopAppRecord.country == country)
        .where(category_filter)
    )
    if not latest_month:
        return None

    rows = session.scalars(
        select(SensorTowerMarketTopAppRecord)
        .where(SensorTowerMarketTopAppRecord.country == country)
        .where(category_filter)
        .where(SensorTowerMarketTopAppRecord.scrape_month == latest_month)
    ).all()
    if not rows:
        return None

    total = len(rows)
    dl_list = [r.downloads for r in rows if r.downloads is not None]
    dau_list = [r.dau for r in rows if r.dau is not None]
    sov_list = [_to_float(r.impression_share) or 0 for r in rows if r.impression_share is not None]

    # Network adoption: % of category apps advertising on each network
    _NET_COLS = {
        "admob": "ad_on_admob", "facebook": "ad_on_facebook",
        "instagram": "ad_on_instagram", "tiktok": "ad_on_tiktok",
        "youtube": "ad_on_youtube", "snapchat": "ad_on_snapchat",
        "applovin": "ad_on_applovin", "unity": "ad_on_unity",
        "mintegral": "ad_on_mintegral",
    }
    network_adoption: dict[str, float] = {}
    for net, col in _NET_COLS.items():
        count = sum(1 for r in rows if getattr(r, col, False))
        network_adoption[net] = round(count / total, 3)

    # Build signals when client metrics are provided
    signals: list[dict] = []
    if client_downloads is not None and dl_list:
        pct = _percentile_rank(dl_list, client_downloads)
        med = _median(dl_list)
        if pct is not None and pct < 50:
            signals.append({
                "type": "below_median",
                "metric": "downloads",
                "client_value": client_downloads,
                "category_median": med,
                "percentile": pct,
                "signal": f"Downloads at {pct}th percentile in {category} (median {med:,})",
            })

    if client_dau is not None and dau_list:
        pct = _percentile_rank(dau_list, client_dau)
        med = _median(dau_list)
        if pct is not None and pct < 50:
            signals.append({
                "type": "below_median",
                "metric": "dau",
                "client_value": client_dau,
                "category_median": med,
                "percentile": pct,
                "signal": f"DAU at {pct}th percentile in {category} (median {med:,})",
            })

    if client_sov is not None and sov_list:
        pct = _percentile_rank(sov_list, client_sov)
        med = _median(sov_list)
        if pct is not None and pct < 50:
            signals.append({
                "type": "below_median",
                "metric": "impression_share",
                "client_value": round(client_sov, 6),
                "category_median": round(med, 6) if med else None,
                "percentile": pct,
                "signal": f"SOV at {pct}th percentile in {category}",
            })

    if client_networks is not None:
        for net, rate in network_adoption.items():
            if net not in client_networks and rate >= 0.25:
                signals.append({
                    "type": "missing_popular_network",
                    "network": net,
                    "category_adoption_rate": rate,
                    "signal": f"{rate:.0%} of {category} apps advertise on {net}, but this advertiser does not",
                })

    return {
        "category": category,
        "scrape_month": latest_month.isoformat(),
        "total_apps_in_category": total,
        "medians": {
            "downloads": _median(dl_list),
            "dau": _median(dau_list),
            "impression_share": round(_median(sov_list), 6) if sov_list else None,
        },
        "network_adoption": network_adoption,
        "signals": signals,
    }


def _compute_geo_snapshot(
    session,
    advertiser_name: str,
    domain: str | None,
    country: str | None,
) -> dict | None:
    """Lightweight GEO visibility snapshot for embedding in advertiser summaries."""
    from adintel.db.models import AdvertiserRecord

    # Resolve to the otterly target key (usually the domain)
    target = None
    candidates = [domain, advertiser_name] if domain else [advertiser_name]
    for candidate in candidates:
        if candidate:
            exists = session.scalar(
                select(OtterlyPromptRecord.target_brand_or_domain_name)
                .where(OtterlyPromptRecord.target_brand_or_domain_name == candidate)
                .limit(1)
            )
            if exists:
                target = candidate
                break
    if not target:
        # Try fuzzy domain match
        target = session.scalar(
            select(OtterlyPromptRecord.target_brand_or_domain_name)
            .where(OtterlyPromptRecord.target_brand_or_domain_name.ilike(f"%{advertiser_name}%"))
            .limit(1)
        )
    if not target:
        return None

    # Aggregate visibility stats via SQL
    overview_q = select(
        func.count().label("total"),
        func.sum(case((OtterlyPromptRecord.domain_cited.is_(True), 1), else_=0)).label("cited"),
    ).where(OtterlyPromptRecord.target_brand_or_domain_name == target)
    if country:
        overview_q = overview_q.where(OtterlyPromptRecord.country_code == country.lower())

    stats = session.execute(overview_q).one()
    total = stats.total or 0
    if total == 0:
        return None
    cited = stats.cited or 0

    # Per-engine breakdown
    engine_q = (
        select(
            OtterlyPromptRecord.ai_engine,
            func.count().label("total_prompts"),
            func.sum(case((OtterlyPromptRecord.domain_cited.is_(True), 1), else_=0)).label("cited_prompts"),
        )
        .where(OtterlyPromptRecord.target_brand_or_domain_name == target)
        .group_by(OtterlyPromptRecord.ai_engine)
    )
    if country:
        engine_q = engine_q.where(OtterlyPromptRecord.country_code == country.lower())
    engine_rows = session.execute(engine_q).all()
    engines = []
    for row in engine_rows:
        t, c = row.total_prompts or 0, row.cited_prompts or 0
        engines.append({
            "engine": row.ai_engine,
            "total": t,
            "cited": c,
            "visibility": round(c / t, 4) if t > 0 else 0,
        })
    engines.sort(key=lambda e: e["total"], reverse=True)

    # Top blind-spot prompts (high volume, not cited, competitors present)
    blind_q = (
        select(
            OtterlyPromptRecord.prompt_text,
            OtterlyPromptRecord.prompt_volume,
            OtterlyPromptRecord.ai_engine,
            OtterlyPromptRecord.competitors,
        )
        .where(OtterlyPromptRecord.target_brand_or_domain_name == target)
        .where(OtterlyPromptRecord.domain_cited.is_(False))
        .where(OtterlyPromptRecord.competitors.isnot(None))
        .order_by(OtterlyPromptRecord.prompt_volume.desc().nulls_last())
        .limit(5)
    )
    if country:
        blind_q = blind_q.where(OtterlyPromptRecord.country_code == country.lower())
    blind_rows = session.execute(blind_q).all()
    blind_spots = [
        {
            "prompt": row.prompt_text,
            "volume": row.prompt_volume,
            "engine": row.ai_engine,
            "competitors_present": row.competitors,
        }
        for row in blind_rows
    ]

    # Signals
    signals: list[dict] = []
    visibility_rate = round(cited / total, 4)
    if visibility_rate < 0.15:
        signals.append({
            "type": "low_overall_visibility",
            "visibility_rate": visibility_rate,
            "signal": f"Only {visibility_rate:.0%} AI search visibility — low presence across AI engines",
        })
    for eng in engines:
        if eng["total"] >= 5 and eng["visibility"] == 0:
            signals.append({
                "type": "zero_visibility_engine",
                "engine": eng["engine"],
                "signal": f"Zero citations on {eng['engine']} ({eng['total']} prompts tracked)",
            })
        elif eng["total"] >= 5 and eng["visibility"] < 0.10:
            signals.append({
                "type": "low_visibility_engine",
                "engine": eng["engine"],
                "visibility": eng["visibility"],
                "signal": f"Only {eng['visibility']:.0%} visibility on {eng['engine']}",
            })
    if blind_spots:
        signals.append({
            "type": "blind_spots_found",
            "count": len(blind_spots),
            "signal": f"{len(blind_spots)} high-volume prompts where competitors appear but brand does not",
        })

    return {
        "target": target,
        "visibility_rate": visibility_rate,
        "total_prompts": total,
        "cited_prompts": cited,
        "engine_breakdown": engines,
        "top_blind_spots": blind_spots,
        "signals": signals,
    }


def _socialpeta_resolve_target(session, advertiser_name: str) -> str | None:
    advertiser_repo = AdvertiserRepository(session)
    resolved = advertiser_repo.resolve(advertiser_name)
    if resolved is not None:
        return resolved.name

    exact = session.scalar(
        select(SocialPetaCreativeRecord.advertiser_name)
        .where(func.lower(SocialPetaCreativeRecord.advertiser_name) == advertiser_name.lower())
        .limit(1)
    )
    if exact:
        return exact

    fuzzy = session.scalar(
        select(SocialPetaCreativeRecord.advertiser_name)
        .where(SocialPetaCreativeRecord.advertiser_name.ilike(f"%{advertiser_name}%"))
        .limit(1)
    )
    return fuzzy


def _socialpeta_snapshot(session, advertiser_name: str, country: str | None = None) -> dict | None:
    rows_q = select(SocialPetaCreativeRecord).where(SocialPetaCreativeRecord.advertiser_name == advertiser_name)
    if country:
        rows_q = rows_q.where(SocialPetaCreativeRecord.country == country)
    rows = session.scalars(rows_q).all()
    if not rows:
        return None

    channel_q = select(SocialPetaCreativeChannelRecord).where(
        SocialPetaCreativeChannelRecord.advertiser_name == advertiser_name
    )
    tag_q = select(SocialPetaCreativeTagRecord).where(SocialPetaCreativeTagRecord.advertiser_name == advertiser_name)
    if country:
        channel_q = channel_q.where(SocialPetaCreativeChannelRecord.country == country)
        tag_q = tag_q.where(SocialPetaCreativeTagRecord.country == country)
    channels = session.scalars(channel_q).all()
    tags = session.scalars(tag_q).all()

    type_counts = Counter((row.creative_type or "unknown") for row in rows)
    primary_channel_counts = Counter((row.primary_channel or "unknown") for row in rows)
    channel_counts = Counter(row.channel for row in channels if row.channel)
    tag_counts = Counter(f"{row.tag_category}:{row.tag_value}" for row in tags if row.tag_value)
    video_count = sum(1 for row in rows if (row.creative_type or "").casefold() == "video")
    image_count = sum(1 for row in rows if (row.creative_type or "").casefold() == "image")
    long_running_count = sum(1 for row in rows if (row.active_days or 0) >= 30)
    active_days = [row.active_days for row in rows if row.active_days is not None]
    impressions = [row.impression for row in rows if row.impression is not None]
    scores = [row.creative_score for row in rows if row.creative_score is not None]
    first_seen = min((row.first_seen for row in rows if row.first_seen is not None), default=None)
    last_seen = max((row.last_seen for row in rows if row.last_seen is not None), default=None)

    return {
        "advertiser_name": advertiser_name,
        "country": country,
        "creatives": len(rows),
        "creative_type_distribution": dict(type_counts),
        "video_share": round(video_count / len(rows), 4) if rows else None,
        "image_share": round(image_count / len(rows), 4) if rows else None,
        "avg_active_days": round(sum(active_days) / len(active_days), 1) if active_days else None,
        "long_running_share": round(long_running_count / len(rows), 4) if rows else None,
        "avg_impression": round(sum(impressions) / len(impressions), 1) if impressions else None,
        "avg_creative_score": round(sum(scores) / len(scores), 1) if scores else None,
        "first_seen": first_seen.isoformat() if first_seen else None,
        "last_seen": last_seen.isoformat() if last_seen else None,
        "primary_channel_distribution": dict(primary_channel_counts),
        "channel_distribution": dict(channel_counts),
        "tag_distribution": dict(tag_counts),
        "top_primary_channel": primary_channel_counts.most_common(1)[0][0] if primary_channel_counts else None,
        "top_channel": channel_counts.most_common(1)[0][0] if channel_counts else None,
        "top_tag": tag_counts.most_common(1)[0][0] if tag_counts else None,
        "top_pages": [
            {"page_name": name, "count": count}
            for name, count in Counter((row.page_name or "unknown") for row in rows).most_common(5)
        ],
        "sample_creatives": [
            {
                "creative_id": row.creative_id,
                "title": row.creative_title,
                "creative_type": row.creative_type,
                "primary_channel": row.primary_channel,
                "first_seen": row.first_seen.isoformat() if row.first_seen else None,
                "last_seen": row.last_seen.isoformat() if row.last_seen else None,
                "active_days": row.active_days,
                "impression": row.impression,
                "preview_image_url": row.preview_image_url,
            }
            for row in sorted(
                rows,
                key=lambda r: (
                    r.active_days if r.active_days is not None else -1,
                    r.impression if r.impression is not None else -1,
                ),
                reverse=True,
            )[:5]
        ],
    }


def _socialpeta_comparison_snapshot(
    session,
    advertiser_names: list[str],
    country: str | None = None,
) -> dict:
    snapshots: dict[str, dict] = {}
    for name in advertiser_names:
        snap = _socialpeta_snapshot(session, name, country=country)
        if snap is not None:
            snapshots[name] = snap

    if not snapshots:
        return {
            "found": False,
            "advertiser_names": advertiser_names,
            "message": "No SocialPeta creative data found for the requested advertisers.",
        }

    found_names = list(snapshots.keys())
    comparison: dict[str, object] = {
        "found": True,
        "country": country,
        "advertiser_names": found_names,
        "brands": snapshots,
    }

    if len(found_names) >= 2:
        root = found_names[0]
        competitors = found_names[1:]
        competitor_rows = [snapshots[name] for name in competitors]
        competitor_totals = [snap["creatives"] for snap in competitor_rows]
        competitor_video_shares = [snap["video_share"] for snap in competitor_rows if snap["video_share"] is not None]
        competitor_long_running = [snap["long_running_share"] for snap in competitor_rows if snap["long_running_share"] is not None]
        competitor_channels = set()
        competitor_tags = set()
        for snap in competitor_rows:
            competitor_channels.update(snap["channel_distribution"].keys())
            competitor_tags.update(snap["tag_distribution"].keys())

        root_snap = snapshots[root]
        comparison["gap_analysis"] = {
            "root": root,
            "competitors": competitors,
            "creative_volume_gap": {
                "root": root_snap["creatives"],
                "competitor_average": round(sum(competitor_totals) / len(competitor_totals), 1) if competitor_totals else None,
            },
            "video_share_gap": {
                "root": root_snap["video_share"],
                "competitor_average": round(sum(competitor_video_shares) / len(competitor_video_shares), 4)
                if competitor_video_shares else None,
            },
            "long_running_share_gap": {
                "root": root_snap["long_running_share"],
                "competitor_average": round(sum(competitor_long_running) / len(competitor_long_running), 4)
                if competitor_long_running else None,
            },
            "channel_blind_spots": sorted(competitor_channels - set(root_snap["channel_distribution"].keys())),
            "tag_blind_spots": sorted(competitor_tags - set(root_snap["tag_distribution"].keys())),
        }

    return comparison


def _build_summary(advertiser_name: str, country: str | None = None) -> dict:
    with _session_factory()() as session:
        advertisers = AdvertiserRepository(session)
        advertiser = advertisers.resolve(advertiser_name)
        if advertiser is None:
            suggestions = advertisers.suggest(advertiser_name)
            return {
                "found": False,
                "advertiser_name": advertiser_name,
                "message": (
                    f"No advertiser found for '{advertiser_name}'. Did you mean: "
                    + ", ".join(f"'{s}'" for s in suggestions)
                    + "?"
                ) if suggestions else f"No advertiser found for '{advertiser_name}'.",
                "did_you_mean": suggestions,
            }

        # Use the canonical name from the database for all queries
        canonical_name = advertiser.name

        def _q(model):
            """Build a base query with advertiser + optional country filter."""
            q = select(model).where(model.advertiser_name == canonical_name)
            if country and hasattr(model, "country"):
                q = q.where(model.country == country)
            return q

        latest_download = session.scalar(
            _q(SensorTowerDownloadRecord)
            .order_by(desc(SensorTowerDownloadRecord.period_date))
        )
        latest_usage = session.scalar(
            _q(SensorTowerUsageRecord)
            .order_by(desc(SensorTowerUsageRecord.period_date))
        )
        latest_retention = session.scalar(
            _q(SensorTowerRetentionRecord)
            .order_by(desc(SensorTowerRetentionRecord.cohort_date))
        )
        latest_ranking = session.scalar(
            _q(SensorTowerRankingRecord)
            .order_by(desc(SensorTowerRankingRecord.rank_date))
        )
        # Total SOV ("all" network aggregate)
        q_imp_all = _q(SensorTowerImpressionShareRecord).where(
            SensorTowerImpressionShareRecord.network == "all",
        )
        latest_impression_share = session.scalar(
            q_imp_all.order_by(desc(SensorTowerImpressionShareRecord.period_date))
        )
        # Per-network SOV breakdown: top networks by latest SOV
        latest_imp_date = (
            latest_impression_share.period_date if latest_impression_share else None
        )
        top_networks: list[dict] = []
        if latest_imp_date:
            net_rows = session.scalars(
                select(SensorTowerImpressionShareRecord)
                .where(SensorTowerImpressionShareRecord.advertiser_name == canonical_name)
                .where(SensorTowerImpressionShareRecord.country == (country or "US"))
                .where(SensorTowerImpressionShareRecord.period_date == latest_imp_date)
                .where(SensorTowerImpressionShareRecord.network != "all")
                .where(SensorTowerImpressionShareRecord.network != "other")
                .where(SensorTowerImpressionShareRecord.sov_pct > 0)
                .order_by(desc(SensorTowerImpressionShareRecord.sov_pct))
                .limit(8)
            ).all()
            top_networks = [
                {"network": r.network, "sov_pct": _to_float(r.sov_pct)}
                for r in net_rows
            ]

        demographics = session.scalars(
            _q(SensorTowerDemographicRecord)
            .order_by(SensorTowerDemographicRecord.age_bracket)
        ).all()
        latest_reviews = session.scalar(
            _q(SensorTowerReviewRecord)
            .order_by(desc(SensorTowerReviewRecord.period_date))
        )

        # Review sentiment distribution from review_texts table
        review_sentiment_q = (
            select(
                SensorTowerReviewTextRecord.sentiment,
                func.count().label("cnt"),
            )
            .where(SensorTowerReviewTextRecord.advertiser_name == canonical_name)
            .where(SensorTowerReviewTextRecord.sentiment.isnot(None))
            .group_by(SensorTowerReviewTextRecord.sentiment)
        )
        if country:
            review_sentiment_q = review_sentiment_q.where(
                SensorTowerReviewTextRecord.country == country
            )
        sentiment_dist = {
            row.sentiment: row.cnt
            for row in session.execute(review_sentiment_q).all()
        }

        # Top review tags with frequency counts
        all_tags_q = (
            select(SensorTowerReviewTextRecord.tags)
            .where(SensorTowerReviewTextRecord.advertiser_name == canonical_name)
            .where(SensorTowerReviewTextRecord.tags.isnot(None))
        )
        if country:
            all_tags_q = all_tags_q.where(
                SensorTowerReviewTextRecord.country == country
            )
        tag_counts: dict[str, int] = {}
        for (tags,) in session.execute(all_tags_q).all():
            for tag in (tags or []):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        top_tags = [
            {"tag": t, "count": c}
            for t, c in sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        ]

        # Version breakdown: avg rating per app_version (last 5 versions by recency)
        version_q = (
            select(
                SensorTowerReviewTextRecord.app_version,
                func.avg(SensorTowerReviewTextRecord.star_rating).label("avg_rating"),
                func.count().label("review_count"),
                func.max(SensorTowerReviewTextRecord.review_date).label("latest_date"),
            )
            .where(SensorTowerReviewTextRecord.advertiser_name == canonical_name)
            .where(SensorTowerReviewTextRecord.app_version.isnot(None))
            .group_by(SensorTowerReviewTextRecord.app_version)
            .order_by(func.max(SensorTowerReviewTextRecord.review_date).desc())
            .limit(5)
        )
        if country:
            version_q = version_q.where(
                SensorTowerReviewTextRecord.country == country
            )
        version_breakdown = [
            {
                "app_version": row.app_version,
                "avg_rating": round(float(row.avg_rating), 2) if row.avg_rating else None,
                "review_count": row.review_count,
                "latest_review_date": row.latest_date.isoformat() if row.latest_date else None,
            }
            for row in session.execute(version_q).all()
        ]

        # 5 most recent reviews (kept for quick reading)
        recent_review_texts = session.scalars(
            _q(SensorTowerReviewTextRecord)
            .order_by(desc(SensorTowerReviewTextRecord.review_date))
            .limit(5)
        ).all()

        # Creative strategy breakdown
        creative_type_q = (
            select(
                SensorTowerCreativeRecord.creative_type,
                func.count().label("cnt"),
            )
            .where(SensorTowerCreativeRecord.advertiser_name == canonical_name)
            .group_by(SensorTowerCreativeRecord.creative_type)
        )
        creative_type_dist = {
            row.creative_type: row.cnt
            for row in session.execute(creative_type_q).all()
        }

        creative_network_q = (
            select(
                SensorTowerCreativeRecord.network,
                func.count().label("cnt"),
            )
            .where(SensorTowerCreativeRecord.advertiser_name == canonical_name)
            .where(SensorTowerCreativeRecord.network.isnot(None))
            .group_by(SensorTowerCreativeRecord.network)
            .order_by(func.count().desc())
        )
        creative_network_dist = {
            row.network: row.cnt
            for row in session.execute(creative_network_q).all()
        }

        creative_duration_q = (
            select(
                SensorTowerCreativeRecord.duration_bucket,
                func.count().label("cnt"),
            )
            .where(SensorTowerCreativeRecord.advertiser_name == canonical_name)
            .where(SensorTowerCreativeRecord.duration_bucket.isnot(None))
            .group_by(SensorTowerCreativeRecord.duration_bucket)
            .order_by(func.count().desc())
        )
        creative_duration_dist = {
            row.duration_bucket: row.cnt
            for row in session.execute(creative_duration_q).all()
        }

        # Monthly creative launch cadence (last 6 months)
        creative_cadence_q = (
            select(
                func.date_trunc("month", SensorTowerCreativeRecord.first_seen).label("month"),
                func.count().label("cnt"),
            )
            .where(SensorTowerCreativeRecord.advertiser_name == canonical_name)
            .where(SensorTowerCreativeRecord.first_seen.isnot(None))
            .group_by(func.date_trunc("month", SensorTowerCreativeRecord.first_seen))
            .order_by(func.date_trunc("month", SensorTowerCreativeRecord.first_seen).desc())
            .limit(6)
        )
        creative_cadence = [
            {"month": row.month.strftime("%Y-%m") if row.month else None, "new_creatives": row.cnt}
            for row in session.execute(creative_cadence_q).all()
        ]
        creative_cadence.reverse()  # chronological order

        recent_creatives = session.scalars(
            _q(SensorTowerCreativeRecord)
            .order_by(desc(SensorTowerCreativeRecord.first_seen))
            .limit(5)
        ).all()

        # ASO: richer stats (top 20, plus summary counts)
        aso_keywords = session.scalars(
            _q(SensorTowerAsoKeywordRecord)
            .order_by(SensorTowerAsoKeywordRecord.rank)
            .limit(20)
        ).all()

        aso_total_q = (
            select(
                func.count().label("total"),
                func.avg(SensorTowerAsoKeywordRecord.rank).label("avg_rank"),
            )
            .where(SensorTowerAsoKeywordRecord.advertiser_name == canonical_name)
        )
        if country:
            aso_total_q = aso_total_q.where(
                SensorTowerAsoKeywordRecord.country == country
            )
        aso_stats = session.execute(aso_total_q).one()

        aso_type_q = (
            select(
                SensorTowerAsoKeywordRecord.keyword_type,
                func.count().label("cnt"),
            )
            .where(SensorTowerAsoKeywordRecord.advertiser_name == canonical_name)
            .where(SensorTowerAsoKeywordRecord.keyword_type.isnot(None))
            .group_by(SensorTowerAsoKeywordRecord.keyword_type)
        )
        if country:
            aso_type_q = aso_type_q.where(
                SensorTowerAsoKeywordRecord.country == country
            )
        aso_type_dist = {
            row.keyword_type: row.cnt
            for row in session.execute(aso_type_q).all()
        }

        # ── Category benchmarks ──────────────────────────────────────
        _country = country or "US"
        category_benchmarks = None
        if advertiser.category:
            # Get ALL client networks (not just top 8)
            client_network_set: set[str] = set()
            if latest_imp_date:
                all_net_names = session.scalars(
                    select(SensorTowerImpressionShareRecord.network)
                    .where(SensorTowerImpressionShareRecord.advertiser_name == canonical_name)
                    .where(SensorTowerImpressionShareRecord.country == _country)
                    .where(SensorTowerImpressionShareRecord.period_date == latest_imp_date)
                    .where(SensorTowerImpressionShareRecord.network.notin_(["all", "other"]))
                    .where(SensorTowerImpressionShareRecord.sov_pct > 0)
                ).all()
                client_network_set = set(all_net_names)

            category_benchmarks = _compute_category_benchmarks(
                session,
                category=advertiser.category,
                country=_country,
                client_downloads=latest_download.downloads if latest_download else None,
                client_dau=_to_float(latest_usage.avg_dau) if latest_usage else None,
                client_sov=_to_float(latest_impression_share.sov_pct) if latest_impression_share else None,
                client_networks=client_network_set,
            )

        # ── GEO snapshot ─────────────────────────────────────────────
        geo_snapshot = _compute_geo_snapshot(
            session,
            advertiser_name=canonical_name,
            domain=advertiser.domain,
            country=country,
        )

    return {
        "found": True,
        **_resolved_info(advertiser),
        "advertiser": advertiser.model_dump(),
        "category_benchmarks": category_benchmarks,
        "geo_snapshot": geo_snapshot,
        "sensortower": {
            "latest_download": (
                {
                    "period_date": latest_download.period_date.isoformat(),
                    "downloads": latest_download.downloads,
                    "revenue": _to_float(latest_download.revenue),
                    "country": latest_download.country,
                    "os": latest_download.os,
                }
                if latest_download
                else None
            ),
            "latest_usage": (
                {
                    "period_date": latest_usage.period_date.isoformat(),
                    "avg_dau": _to_float(latest_usage.avg_dau),
                    "time_spent_min": _to_float(latest_usage.time_spent_min),
                    "sessions_per_day": _to_float(latest_usage.sessions_per_day),
                    "country": latest_usage.country,
                }
                if latest_usage
                else None
            ),
            "latest_retention": (
                {
                    "cohort_date": latest_retention.cohort_date.isoformat(),
                    "d1": _to_float(latest_retention.d1),
                    "d7": _to_float(latest_retention.d7),
                    "d30": _to_float(latest_retention.d30),
                    "d60": _to_float(latest_retention.d60),
                    "country": latest_retention.country,
                }
                if latest_retention
                else None
            ),
            "impression_share": {
                "total_sov": (
                    {
                        "period_date": latest_impression_share.period_date.isoformat(),
                        "sov_pct": _to_float(latest_impression_share.sov_pct),
                        "country": latest_impression_share.country,
                    }
                    if latest_impression_share
                    else None
                ),
                "top_networks": top_networks,
            },
            "latest_ranking": (
                {
                    "rank_date": latest_ranking.rank_date.isoformat(),
                    "category": latest_ranking.category,
                    "chart_type": latest_ranking.chart_type,
                    "rank": latest_ranking.rank,
                    "country": latest_ranking.country,
                }
                if latest_ranking
                else None
            ),
            "demographics": [
                {
                    "age_bracket": row.age_bracket,
                    "male_pct": _to_float(row.male_pct),
                    "female_pct": _to_float(row.female_pct),
                    "country": row.country,
                }
                for row in demographics
            ],
            "reviews": {
                "latest_aggregate": (
                    {
                        "period_date": latest_reviews.period_date.isoformat(),
                        "avg_rating": _to_float(latest_reviews.avg_rating),
                        "rating_count": latest_reviews.rating_count,
                        "star_1_count": latest_reviews.star_1_count,
                        "star_2_count": latest_reviews.star_2_count,
                        "star_3_count": latest_reviews.star_3_count,
                        "star_4_count": latest_reviews.star_4_count,
                        "star_5_count": latest_reviews.star_5_count,
                        "country": latest_reviews.country,
                    }
                    if latest_reviews
                    else None
                ),
                "sentiment_distribution": sentiment_dist,
                "top_tags": top_tags,
                "version_breakdown": version_breakdown,
                "recent_reviews": [
                    {
                        "review_id": row.review_id,
                        "review_date": row.review_date.isoformat(),
                        "country": row.country,
                        "star_rating": _to_float(row.star_rating),
                        "title": row.title,
                        "body": row.body,
                        "sentiment": row.sentiment,
                        "tags": row.tags,
                        "app_version": row.app_version,
                    }
                    for row in recent_review_texts
                ],
            },
            "creatives": {
                "strategy_summary": {
                    "by_type": creative_type_dist,
                    "by_network": creative_network_dist,
                    "by_duration": creative_duration_dist,
                    "monthly_cadence": creative_cadence,
                },
                "recent": [
                    {
                        "creative_id": row.creative_id,
                        "creative_type": row.creative_type,
                        "network": row.network,
                        "thumbnail_url": row.thumbnail_url,
                        "duration_bucket": row.duration_bucket,
                        "first_seen": row.first_seen.isoformat() if row.first_seen else None,
                    }
                    for row in recent_creatives
                ],
            },
            "aso_keywords": {
                "summary": {
                    "total_tracked": aso_stats.total or 0,
                    "avg_rank": round(float(aso_stats.avg_rank), 1) if aso_stats.avg_rank else None,
                    "by_type": aso_type_dist,
                },
                "top_20": [
                    {
                        "keyword": row.keyword,
                        "keyword_type": row.keyword_type,
                        "rank": row.rank,
                        "traffic_score": _to_float(row.traffic_score),
                        "opportunity_score": _to_float(row.opportunity_score),
                        "country": row.country,
                        "device": row.device,
                    }
                    for row in aso_keywords
                ],
            },
        },
    }


def create_mcp_server() -> FastMCP:
    # For the public Vercel deployment, serve Streamable HTTP at the function
    # root and disable localhost-only host validation. Local stdio usage is
    # unaffected because these HTTP settings are only relevant for HTTP
    # transports.
    is_vercel = bool(os.getenv("VERCEL"))
    is_hf_space = bool(os.getenv("SPACE_AUTHOR_NAME") or os.getenv("SPACE_ID"))
    server = FastMCP(
        name="AdIntel",
        instructions=(
            "AdIntel provides competitive intelligence for mobile advertisers via SensorTower "
            "and GEO (Generative Engine Optimization) analysis via Otterly.AI.\n\n"
            "DATA COLLECTED FROM SENSORTOWER (per tracked advertiser):\n"
            "- Downloads & revenue: daily timeseries, up to 90 days, by country and OS.\n"
            "- DAU, time spent, sessions per day: daily engagement metrics.\n"
            "- Retention cohorts: D1/D3/D7/D14/D30/D60 by month.\n"
            "- Impression share (Share of Voice): per ad network (23 networks), daily.\n"
            "- Demographics: age brackets (18-24, 25-34, 35-44, 45-54, 55+) with male/female %.\n"
            "- Rankings: monthly position in category chart by ad SOV.\n"
            "- Reviews: daily aggregate ratings and individual review text with sentiment.\n"
            "- Ad creatives: type, network, thumbnail URL, duration bucket, first seen date.\n"
            "- ASO keywords: rank, traffic score, opportunity score per device and country.\n"
            "- Market-wide top apps: category rankings with downloads, revenue, DAU, and "
            "per-network ad presence flags for ALL apps (not just tracked ones). "
            "Populated by running 'adintel collect market-top-apps'.\n\n"
            "DATA COLLECTED FROM OTTERLY.AI (GEO):\n"
            "- AI search visibility: citation rate per prompt across ChatGPT, Perplexity, "
            "Google AI Overview, Google Gemini, Microsoft Copilot.\n"
            "- Prompt-level data: volume, brand rank, sentiment, competitor overlap.\n"
            "- Citation data: cited URLs and domains, brand vs third-party, domain category.\n\n"
            "ADVERTISER NAME RESOLUTION:\n"
            "- Names are fuzzy-matched: 'chime', 'Chme', or 'chim' all resolve to 'Chime'.\n"
            "- When auto-resolved, the response includes resolved_from and resolved_to fields.\n"
            "- Use list_advertisers to see all tracked advertisers with data freshness metadata.\n\n"
            "SENSORTOWER WORKFLOW:\n"
            "- Competitive comparison (2+ advertisers) → call get_full_comparison.\n"
            "  Returns 30-day timeseries for downloads and DAU, per-network SOV trends,\n"
            "  a server-computed gap_analysis (exclusive networks, SOV ratios, efficiency,\n"
            "  retention gaps, review rating gaps, demographics comparison),\n"
            "  PLUS per-advertiser category_benchmarks (percentile vs category median,\n"
            "  missing popular networks) and geo_snapshot (AI search visibility per brand).\n"
            "  The gap_analysis also includes geo_gaps: overall AI visibility leader,\n"
            "  per-engine comparison, and GEO-specific opportunities.\n\n"
            "- Single advertiser deep dive → call get_advertiser_summary.\n"
            "  Returns all latest SensorTower data PLUS:\n"
            "  · category_benchmarks: downloads/DAU/SOV percentile vs category median,\n"
            "    network adoption rates, and signals (below_median, missing_popular_network)\n"
            "  · geo_snapshot: AI search visibility rate, engine breakdown, top blind-spot\n"
            "    prompts, and signals (low_visibility, zero_visibility_engine)\n"
            "  · impression_share: total SOV + top networks breakdown\n"
            "  · reviews: aggregate ratings, sentiment distribution, top tags, version breakdown\n"
            "  · creatives: strategy summary (by type/network/duration, monthly cadence)\n"
            "  · aso_keywords: summary (total tracked, avg rank, by type) + top 20 keywords\n\n"
            "- Custom date range or individual metric → use get_metric_timeseries.\n"
            "  Metrics: downloads, usage, retention, impression_share, rankings, reviews.\n\n"
            "- Market-wide category rankings → call get_market_top_apps.\n"
            "  Sort by: downloads, revenue, dau, impression_share, rank.\n"
            "  Filters: network_filter, min_downloads, app_category.\n\n"
            "- Custom analysis → call run_query with a SELECT statement.\n"
            "  Call read_schema_text first. Limit up to 500 rows; read-only (SELECT/WITH).\n\n"
            "GEO (AI SEARCH VISIBILITY) WORKFLOW:\n"
            "- Single brand comprehensive GEO analysis → call get_geo_summary.\n"
            "  Returns everything in one call: visibility rate + engine breakdown,\n"
            "  sentiment distribution, citation analysis (top domains, categories, per-engine),\n"
            "  prompt insights (top by volume, best ranked, blind spots, negative sentiment).\n\n"
            "- Compare 2+ brands in AI search → call compare_geo_visibility.\n"
            "  Shows blind spots, engine gaps, competitor overlap, opportunities.\n\n"
            "NOTE: get_advertiser_summary and get_full_comparison already include a GEO\n"
            "snapshot with visibility rate, engine breakdown, blind spots, and signals.\n"
            "Use get_geo_summary only when a deeper, standalone GEO analysis is needed.\n\n"
            "COLLECTION HEALTH:\n"
            "- get_collection_status: health + active alerts + recent run history in one call.\n"
            "  Pass advertiser_name to scope to one brand; omit for all brands.\n"
            "  include_run_history (default True) adds per-metric scrape run outcomes.\n"
            "  Filter runs by platform (e.g. 'sensortower'). Adjust run_history_limit (default 20).\n"
            "  Tune alert thresholds: stale_hours (default 48), max_consecutive_failures (default 3).\n"
            "- list_advertisers: shows st_last_scraped, st_download_rows, geo_last_scraped per brand.\n\n"
            "COMPETITIVE GAP ANALYSIS REPORT FORMAT:\n"
            "1. Executive summary (who leads, by how much)\n"
            "2. Side-by-side metrics table (downloads, DAU, revenue, total SOV, avg rating)\n"
            "3. Ad placement gap: networks each advertiser uses exclusively\n"
            "4. Network efficiency: downloads-per-SOV-point comparison\n"
            "5. Retention gap: d1/d7/d30/d60 cohort comparison with leader and gap size\n"
            "6. Review rating gap: rating difference and leader\n"
            "7. Demographics comparison: audience gender/age skew per advertiser\n"
            "8. Opportunities: untapped networks, underweight channels, strategic moves\n\n"
            "GEO ANALYSIS REPORT FORMAT:\n"
            "1. Visibility overview: cited rate across AI engines\n"
            "2. Engine-by-engine breakdown with sentiment and rank\n"
            "3. Blind spots: engines/prompts where competitors appear but brand doesn't\n"
            "4. Citation landscape: top domains, brand-owned vs third-party\n"
            "5. Opportunities: uncovered engines, high-volume uncited prompts, negative sentiment areas\n\n"
            "DATA COLLECTED FROM APPFOLLOW (per tracked app):\n"
            "- Individual reviews: text body, star rating, username, title, country, OS (iOS/Android).\n"
            "- Sentiment: positive/negative/neutral label per review, optional numeric score.\n"
            "- Keyword tags: topic/keyword tags attached to each review by AppFollow.\n"
            "- Collection: requires config/appfollow_groups.yaml with workspace slug and app item IDs.\n"
            "  Run: bash scripts/run_appfollow_to_server.sh\n\n"
            "APPFOLLOW WORKFLOW:\n"
            "- Single advertiser review analysis → get_appfollow_reviews (filterable list) or\n"
            "  get_appfollow_sentiment_trend (daily positive/negative/neutral breakdown).\n"
            "- Find root causes → get_appfollow_keyword_analysis (top keyword/topic counts).\n"
            "  Filter by sentiment='negative' to see what users complain about.\n"
            "  Filter by sentiment='positive' to see what competitors are praised for.\n"
            "- Competitor comparison → compare_appfollow_reviews (comma-separated names).\n"
            "  Returns rating gap, sentiment leader, top keywords per brand.\n"
            "  Example: 'Chime, Current, Dave' reveals which neobank has the best user sentiment."
        ),
        streamable_http_path="/",
        host="0.0.0.0",
        stateless_http=is_vercel or is_hf_space,
        transport_security=(
            TransportSecuritySettings(enable_dns_rebinding_protection=False)
            if is_vercel or is_hf_space
            else None
        ),
    )

    @server.resource(
        "schema://adintel",
        name="adintel_schema",
        description="Canonical SQL schema for the AdIntel rewrite.",
        mime_type="text/sql",
    )
    def schema_resource() -> str:
        return _schema_text()

    @server.tool(
        name="list_advertisers",
        description=(
            "List advertisers currently stored in AdIntel with data freshness metadata "
            "(last scraped timestamps and row counts)."
        ),
    )
    def list_advertisers() -> str:
        from adintel.db.models import AdvertiserRecord, ScrapeRunRecord

        with _session_factory()() as session:
            advertisers = AdvertiserRepository(session).list()

            # SensorTower freshness: latest successful run per advertiser
            st_freshness_q = (
                select(
                    ScrapeRunRecord.advertiser_name,
                    func.max(ScrapeRunRecord.finished_at).label("last_scraped"),
                    func.count().label("total_runs"),
                )
                .where(ScrapeRunRecord.platform == "sensortower")
                .where(ScrapeRunRecord.status.in_(["success", "partial"]))
                .group_by(ScrapeRunRecord.advertiser_name)
            )
            st_freshness = {
                row.advertiser_name: {
                    "last_scraped": row.last_scraped.isoformat() if row.last_scraped else None,
                    "total_runs": row.total_runs,
                }
                for row in session.execute(st_freshness_q).all()
            }

            # Download row counts per advertiser
            dl_counts_q = (
                select(
                    SensorTowerDownloadRecord.advertiser_name,
                    func.count().label("row_count"),
                )
                .group_by(SensorTowerDownloadRecord.advertiser_name)
            )
            dl_counts = {
                row.advertiser_name: row.row_count
                for row in session.execute(dl_counts_q).all()
            }

            # GEO freshness: latest scraped_at from otterly prompts per target
            geo_freshness_q = (
                select(
                    OtterlyPromptRecord.target_brand_or_domain_name,
                    func.max(OtterlyPromptRecord.scraped_at).label("last_scraped"),
                )
                .group_by(OtterlyPromptRecord.target_brand_or_domain_name)
            )
            geo_freshness = {
                row.target_brand_or_domain_name: row.last_scraped.isoformat() if row.last_scraped else None
                for row in session.execute(geo_freshness_q).all()
            }

        result = []
        for adv in advertisers:
            d = adv.model_dump()
            name = adv.name
            domain = adv.domain

            st = st_freshness.get(name, {})
            geo_last = geo_freshness.get(domain) if domain else geo_freshness.get(name)

            d["data_freshness"] = {
                "st_last_scraped": st.get("last_scraped"),
                "st_total_runs": st.get("total_runs", 0),
                "st_download_rows": dl_counts.get(name, 0),
                "geo_last_scraped": geo_last,
            }
            result.append(d)

        return json.dumps({"advertisers": result}, indent=2)

    @server.tool(
        name="get_advertiser_summary",
        description=(
            "Get a comprehensive SensorTower summary for a specific advertiser. "
            "Returns: latest downloads/revenue, DAU/engagement, retention cohorts (d1/d7/d30/d60), "
            "total SOV + top 8 networks, demographics, review sentiment distribution + top tags + "
            "version breakdown + recent reviews, creative strategy (by type/network/duration/cadence) + "
            "recent creatives, and top 20 ASO keywords with summary stats. "
            "Optionally filter by country code (e.g., 'US', 'BR')."
        ),
    )
    def get_advertiser_summary(advertiser_name: str, country: str | None = None) -> str:
        return json.dumps(_build_summary(advertiser_name, country=country), indent=2)

    @server.tool(
        name="get_socialpeta_summary",
        description=(
            "Get a comprehensive SocialPeta summary for a specific advertiser from collected display-ad data. "
            "Returns creative mix, active duration, channel distribution, tags, and a comparison against the "
            "advertiser's configured competitor group when available."
        ),
    )
    def get_socialpeta_summary(advertiser_name: str, country: str | None = None) -> str:
        with _session_factory()() as session:
            canonical_name = _socialpeta_resolve_target(session, advertiser_name)
            if canonical_name is None:
                return json.dumps({
                    "found": False,
                    "advertiser_name": advertiser_name,
                    "message": "No SocialPeta creative data found for this advertiser.",
                })

            snapshot = _socialpeta_snapshot(session, canonical_name, country=country)
            if snapshot is None:
                return json.dumps({
                    "found": False,
                    "advertiser_name": canonical_name,
                    "country": country,
                    "message": "No SocialPeta creative data found for this advertiser.",
                })

            groups = load_competitor_groups(get_settings().socialpeta_group_config_file)
            plan = build_competitor_run_plan(groups, canonical_name)
            comparison = _socialpeta_comparison_snapshot(
                session,
                [canonical_name, *plan.competitors],
                country=country,
            )

        return json.dumps(
            {
                "found": True,
                "advertiser_name": canonical_name,
                "country": country,
                "configured_competitors": plan.competitors,
                "summary": snapshot,
                "comparison": comparison,
            },
            indent=2,
        )

    @server.tool(
        name="get_socialpeta_comparison",
        description=(
            "Compare multiple advertisers using SocialPeta display-ad creative data. "
            "The comparison includes creative volume, video/image mix, average active duration, "
            "channel blind spots, and tag blind spots. "
            "Pass advertiser_names as a comma-separated list."
        ),
    )
    def get_socialpeta_comparison(advertiser_names: str, country: str | None = None) -> str:
        names = [name.strip() for name in advertiser_names.split(",") if name.strip()]
        if not names:
            return json.dumps({"found": False, "message": "No advertiser names were provided."})

        with _session_factory()() as session:
            if len(names) == 1:
                groups = load_competitor_groups(get_settings().socialpeta_group_config_file)
                plan = build_competitor_run_plan(groups, names[0])
                names = [names[0], *plan.competitors]
            comparison = _socialpeta_comparison_snapshot(session, names, country=country)

        return json.dumps(comparison, indent=2)

    @server.tool(
        name="request_advertiser",
        description="Log a missing advertiser request for later onboarding.",
    )
    def request_advertiser(
        name: str,
        requested_by: str | None = None,
        context: str | None = None,
    ) -> str:
        with _session_factory()() as session:
            RequestedAdvertiserRepository(session).request(
                name=name,
                requested_by=requested_by,
                context=context,
            )
        return json.dumps(
            {
                "status": "logged",
                "name": name,
                "requested_by": requested_by,
                "context": context,
            },
            indent=2,
        )

    @server.tool(
        name="list_requested_advertisers",
        description="List advertisers that have been requested but not yet onboarded.",
    )
    def list_requested_advertisers() -> str:
        with _session_factory()() as session:
            rows = session.scalars(
                select(RequestedAdvertiserRecord).order_by(desc(RequestedAdvertiserRecord.created_at))
            ).all()
        return json.dumps(
            {"requested_advertisers": [_serialize_requested(row) for row in rows]},
            indent=2,
        )

    @server.tool(
        name="read_schema_text",
        description="Return the canonical SQL schema as text when the client needs raw schema content.",
    )
    def read_schema_text() -> str:
        return _schema_text()

    @server.tool(
        name="run_query",
        description=(
            "Execute a read-only SQL query against the AdIntel database. "
            "Only SELECT statements are allowed. Read schema://adintel first to understand the table structure. "
            "Returns up to max_rows rows (default 100, max 500)."
        ),
    )
    def run_query(sql: str, max_rows: int = 100) -> str:
        import re
        from sqlalchemy import text

        stripped = sql.strip()
        upper = stripped.upper()

        # Must start with SELECT or WITH (CTE)
        if not (upper.startswith("SELECT") or upper.startswith("WITH")):
            return json.dumps({"error": "Only SELECT queries are allowed."})

        # Reject DML/DDL keywords as standalone words
        forbidden = r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE)\b"
        if re.search(forbidden, upper):
            return json.dumps({"error": "Only SELECT queries are allowed. Detected forbidden keyword."})

        max_rows = max(1, min(max_rows, 500))
        try:
            with _session_factory()() as session:
                conn = session.connection()
                conn.execute(text("SET TRANSACTION READ ONLY"))
                result = conn.execute(text(stripped))
                columns = list(result.keys())
                rows = []
                truncated = False
                for i, row in enumerate(result):
                    if i >= max_rows:
                        truncated = True
                        break
                    rows.append({
                        col: (
                            _to_float(val) if isinstance(val, Decimal) else
                            val.isoformat() if isinstance(val, (date, datetime)) else
                            val
                        )
                        for col, val in zip(columns, row)
                    })
                return json.dumps({
                    "columns": columns,
                    "rows": rows,
                    "row_count": len(rows),
                    "truncated": truncated,
                }, indent=2)
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    @server.tool(
        name="get_collection_status",
        description=(
            "Check collection health, active alerts, recent run history, and platform data availability in one call. "
            "Returns: per-advertiser health (last success, consecutive failures, data staleness), "
            "active alerts (stale data, consecutive failures, never-collected advertisers), "
            "a per-advertiser availability matrix for SensorTower and Otterly GEO, "
            "and recent scrape runs with per-metric outcomes (set include_run_history=False to skip). "
            "Scope to one advertiser with advertiser_name, or omit for all brands. "
            "Filter runs by platform (e.g. 'sensortower' or 'otterlyai'). "
            "Tune alert thresholds with stale_hours and max_consecutive_failures."
        ),
    )
    def get_collection_status(
        advertiser_name: str | None = None,
        stale_hours: float = 48,
        max_consecutive_failures: int = 3,
        include_run_history: bool = True,
        platform: str | None = None,
        run_history_limit: int = 20,
    ) -> str:
        with _session_factory()() as session:
            repo = CollectionHealthRepository(session)
            if advertiser_name:
                health = repo.get_health_for_advertiser(advertiser_name)
            else:
                health = repo.get_all_health()
            alerts = repo.get_alerts(
                stale_hours=stale_hours,
                max_consecutive_failures=max_consecutive_failures,
            )
            if advertiser_name:
                alerts = [
                    a for a in alerts
                    if a.get("advertiser_name") == advertiser_name
                ]

            result: dict = {
                "alerts": {
                    "active": alerts,
                    "alert_count": len(alerts),
                    "thresholds": {
                        "stale_hours": stale_hours,
                        "max_consecutive_failures": max_consecutive_failures,
                    },
                },
                "data_availability": _build_data_availability(session, advertiser_name=advertiser_name),
                "health": health,
            }

            if include_run_history:
                runs = ScrapeRunRepository(session).list_recent(
                    advertiser_name=advertiser_name,
                    platform=platform,
                    limit=max(1, min(run_history_limit, 100)),
                )
                result["recent_runs"] = [_serialize_scrape_run(row) for row in runs]

        return json.dumps(result, indent=2)

    @server.tool(
        name="get_metric_timeseries",
        description=(
            "Get daily time-series data for a specific metric and advertiser. "
            "Available metrics: downloads, usage, retention, impression_share, rankings, reviews. "
            "Returns up to 90 days of daily data points for trend analysis."
        ),
    )
    def get_metric_timeseries(
        advertiser_name: str,
        metric: str,
        country: str = "US",
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 90,
    ) -> str:
        if metric not in _METRIC_MAP:
            return json.dumps({
                "error": f"Unknown metric '{metric}'. Available: {', '.join(_METRIC_MAP.keys())}",
            })

        model, date_col_name, value_cols = _METRIC_MAP[metric]
        date_col = getattr(model, date_col_name)

        with _session_factory()() as session:
            repo = AdvertiserRepository(session)
            resolved = repo.resolve(advertiser_name)
            if resolved is None:
                suggestions = repo.suggest(advertiser_name)
                return json.dumps({
                    "found": False,
                    "advertiser_name": advertiser_name,
                    "message": (
                        f"No advertiser found for '{advertiser_name}'. Did you mean: "
                        + ", ".join(f"'{s}'" for s in suggestions) + "?"
                    ) if suggestions else f"No advertiser found for '{advertiser_name}'.",
                    "did_you_mean": suggestions,
                }, indent=2)
            canonical_name = resolved.name
            q = (
                select(model)
                .where(model.advertiser_name == canonical_name)
            )
            if hasattr(model, "country"):
                q = q.where(model.country == country)
            if start_date:
                q = q.where(date_col >= date.fromisoformat(start_date))
            if end_date:
                q = q.where(date_col <= date.fromisoformat(end_date))

            rows = session.scalars(
                q.order_by(desc(date_col)).limit(limit)
            ).all()

        data_points = []
        for row in reversed(rows):
            point = {"date": getattr(row, date_col_name).isoformat()}
            if hasattr(row, "country"):
                point["country"] = row.country
            for col in value_cols:
                point[col] = _to_float(getattr(row, col, None))
            data_points.append(point)

        return json.dumps({
            **_resolved_info(resolved),
            "advertiser_name": canonical_name,
            "metric": metric,
            "country": country,
            "count": len(data_points),
            "data": data_points,
        }, indent=2)

    @server.tool(
        name="get_full_comparison",
        description=(
            "Full competitive comparison for two or more advertisers. "
            "Returns 30-day timeseries for downloads and DAU, per-network ad impression share with trends, "
            "and a server-computed gap_analysis with exclusive networks, SOV ratios, efficiency indicators, "
            "and opportunity bullets. Use this for any competitive analysis task."
        ),
    )
    def get_full_comparison(
        advertiser_names: str,
        country: str = "US",
        days: int = 30,
    ) -> str:
        names = [n.strip() for n in advertiser_names.split(",") if n.strip()]
        if not names:
            return json.dumps({"error": "No advertiser names provided."})
        days = max(1, min(days, 90))

        advertiser_data: dict[str, dict] = {}

        with _session_factory()() as session:
            for name in names:
                # ── Snapshot ──────────────────────────────────────────
                repo = AdvertiserRepository(session)
                advertiser = repo.resolve(name)
                if advertiser is None:
                    suggestions = repo.suggest(name)
                    advertiser_data[name] = {
                        "found": False,
                        "message": (
                            f"No advertiser found for '{name}'. Did you mean: "
                            + ", ".join(f"'{s}'" for s in suggestions) + "?"
                        ) if suggestions else f"No advertiser found for '{name}'.",
                        "did_you_mean": suggestions,
                    }
                    continue

                canonical_name = advertiser.name

                def _q(model, adv_name=canonical_name):
                    q = select(model).where(model.advertiser_name == adv_name)
                    if hasattr(model, "country"):
                        q = q.where(model.country == country)
                    return q

                latest_dl = session.scalar(
                    _q(SensorTowerDownloadRecord)
                    .order_by(desc(SensorTowerDownloadRecord.period_date))
                )
                latest_usage = session.scalar(
                    _q(SensorTowerUsageRecord)
                    .order_by(desc(SensorTowerUsageRecord.period_date))
                )
                latest_imp = session.scalar(
                    _q(SensorTowerImpressionShareRecord)
                    .where(SensorTowerImpressionShareRecord.network == "all")
                    .order_by(desc(SensorTowerImpressionShareRecord.period_date))
                )
                latest_retention = session.scalar(
                    _q(SensorTowerRetentionRecord)
                    .order_by(desc(SensorTowerRetentionRecord.cohort_date))
                )
                latest_review = session.scalar(
                    _q(SensorTowerReviewRecord)
                    .order_by(desc(SensorTowerReviewRecord.period_date))
                )
                demographics = session.scalars(
                    _q(SensorTowerDemographicRecord)
                    .order_by(SensorTowerDemographicRecord.age_bracket)
                ).all()

                snapshot = {
                    "downloads": latest_dl.downloads if latest_dl else None,
                    "revenue": _to_float(latest_dl.revenue) if latest_dl else None,
                    "downloads_date": latest_dl.period_date.isoformat() if latest_dl else None,
                    "avg_dau": _to_float(latest_usage.avg_dau) if latest_usage else None,
                    "sessions_per_day": _to_float(latest_usage.sessions_per_day) if latest_usage else None,
                    "usage_date": latest_usage.period_date.isoformat() if latest_usage else None,
                    "total_sov": _to_float(latest_imp.sov_pct) if latest_imp else None,
                    "sov_date": latest_imp.period_date.isoformat() if latest_imp else None,
                    "retention": (
                        {
                            "cohort_date": latest_retention.cohort_date.isoformat(),
                            "d1": _to_float(latest_retention.d1),
                            "d7": _to_float(latest_retention.d7),
                            "d30": _to_float(latest_retention.d30),
                            "d60": _to_float(latest_retention.d60),
                        }
                        if latest_retention else None
                    ),
                    "avg_rating": _to_float(latest_review.avg_rating) if latest_review else None,
                    "rating_count": latest_review.rating_count if latest_review else None,
                    "reviews_date": latest_review.period_date.isoformat() if latest_review else None,
                    "demographics": [
                        {
                            "age_bracket": row.age_bracket,
                            "male_pct": _to_float(row.male_pct),
                            "female_pct": _to_float(row.female_pct),
                        }
                        for row in demographics
                    ],
                }

                # ── Timeseries: downloads & usage ─────────────────────
                dl_series = _build_timeseries(session, canonical_name, "downloads", country, days)
                usage_series = _build_timeseries(session, canonical_name, "usage", country, days)

                # ── Per-network impression share ───────────────────────
                imp_rows = session.scalars(
                    select(SensorTowerImpressionShareRecord)
                    .where(SensorTowerImpressionShareRecord.advertiser_name == canonical_name)
                    .where(SensorTowerImpressionShareRecord.country == country)
                    .where(SensorTowerImpressionShareRecord.network != "all")
                    .where(SensorTowerImpressionShareRecord.network != "other")
                    .order_by(desc(SensorTowerImpressionShareRecord.period_date))
                    .limit(days * 20)  # enough rows for all networks × days
                ).all()

                # Group by network → latest SOV + recent trend
                networks_map: dict[str, dict] = {}
                for row in imp_rows:
                    net = row.network
                    sov = _to_float(row.sov_pct) or 0.0
                    dt = row.period_date.isoformat()
                    if net not in networks_map:
                        networks_map[net] = {"latest_sov": sov, "latest_date": dt, "trend": []}
                    networks_map[net]["trend"].append({"date": dt, "sov_pct": sov})

                # Trim trend to `days` entries and sort chronologically
                for net in networks_map:
                    networks_map[net]["trend"] = sorted(
                        networks_map[net]["trend"], key=lambda x: x["date"]
                    )[-days:]

                # ── GEO snapshot for this advertiser ──────────────────
                geo_snap = _compute_geo_snapshot(
                    session, canonical_name, advertiser.domain, country
                )

                # ── Category benchmarks for this advertiser ───────────
                client_net_set = {
                    net for net, v in networks_map.items() if v["latest_sov"] > 0
                }
                cat_bench = None
                if advertiser.category:
                    cat_bench = _compute_category_benchmarks(
                        session,
                        category=advertiser.category,
                        country=country,
                        client_downloads=snapshot["downloads"],
                        client_dau=snapshot["avg_dau"],
                        client_sov=snapshot["total_sov"],
                        client_networks=client_net_set,
                    )

                advertiser_data[canonical_name] = {
                    "found": True,
                    **_resolved_info(advertiser),
                    "snapshot": snapshot,
                    "timeseries": {
                        "downloads": dl_series,
                        "usage": usage_series,
                    },
                    "ad_placement": {
                        "networks": networks_map,
                        "total_sov": snapshot["total_sov"],
                        "network_count": len(networks_map),
                    },
                    "geo_snapshot": geo_snap,
                    "category_benchmarks": cat_bench,
                }

        # ── Gap analysis (computed across all advertisers) ─────────────
        found = {n: d for n, d in advertiser_data.items() if d.get("found")}

        gap_analysis: dict = {}
        if len(found) >= 2:
            # Network coverage sets
            network_sets: dict[str, set] = {
                n: {net for net, v in d["ad_placement"]["networks"].items() if v["latest_sov"] > 0}
                for n, d in found.items()
            }
            all_nets = set().union(*network_sets.values())
            shared = set.intersection(*network_sets.values()) if network_sets else set()
            exclusive: dict[str, list] = {
                n: sorted(network_sets[n] - shared) for n in found
            }

            # SOV comparison
            sov_totals = {n: d["snapshot"]["total_sov"] or 0.0 for n, d in found.items()}
            sov_leader = max(sov_totals, key=lambda n: sov_totals[n])
            sov_follower = min(sov_totals, key=lambda n: sov_totals[n])
            sov_ratio = (
                round(sov_totals[sov_leader] / sov_totals[sov_follower], 1)
                if sov_totals[sov_follower] > 0 else None
            )

            # Efficiency: downloads per SOV point
            efficiency: dict[str, float | None] = {}
            for n, d in found.items():
                dl = d["snapshot"]["downloads"]
                sov = d["snapshot"]["total_sov"]
                efficiency[n] = round(dl / sov, 1) if dl and sov and sov > 0 else None

            # Opportunity bullets
            opportunities: list[str] = []
            for n, d in found.items():
                others = [o for o in found if o != n]
                for other in others:
                    for net in exclusive.get(other, []):
                        other_sov = found[other]["ad_placement"]["networks"].get(net, {}).get("latest_sov", 0)
                        if other_sov and other_sov > 0:
                            opportunities.append(
                                f"{n} has no presence on {net} — {other}'s active network (SOV {other_sov:.4%})"
                            )

            # Top network for each advertiser
            for n, d in found.items():
                nets = d["ad_placement"]["networks"]
                if nets:
                    top_net = max(nets, key=lambda x: nets[x]["latest_sov"])
                    top_sov = nets[top_net]["latest_sov"]
                    opportunities.append(
                        f"{n}'s strongest network is {top_net} (SOV {top_sov:.4%})"
                    )

            # Retention gap analysis
            retention_gap: dict = {}
            cohort_days = ["d1", "d7", "d30", "d60"]
            advertisers_with_retention = {
                n: d["snapshot"]["retention"]
                for n, d in found.items()
                if d["snapshot"].get("retention")
            }
            if len(advertisers_with_retention) >= 2:
                for cohort in cohort_days:
                    cohort_vals = {
                        n: r[cohort]
                        for n, r in advertisers_with_retention.items()
                        if r.get(cohort) is not None
                    }
                    if len(cohort_vals) >= 2:
                        best = max(cohort_vals, key=lambda n: cohort_vals[n])
                        worst = min(cohort_vals, key=lambda n: cohort_vals[n])
                        gap = cohort_vals[best] - cohort_vals[worst]
                        retention_gap[cohort] = {
                            "leader": best,
                            "values": cohort_vals,
                            "gap": round(gap, 4),
                            "insight": f"{best} retains {gap:.1%} more users at {cohort}",
                        }

            # Review rating gap analysis
            rating_gap: dict = {}
            advertisers_with_ratings = {
                n: d["snapshot"]["avg_rating"]
                for n, d in found.items()
                if d["snapshot"].get("avg_rating") is not None
            }
            if len(advertisers_with_ratings) >= 2:
                best_rated = max(advertisers_with_ratings, key=lambda n: advertisers_with_ratings[n])
                worst_rated = min(advertisers_with_ratings, key=lambda n: advertisers_with_ratings[n])
                rating_diff = advertisers_with_ratings[best_rated] - advertisers_with_ratings[worst_rated]
                rating_gap = {
                    "leader": best_rated,
                    "ratings": {n: round(v, 2) for n, v in advertisers_with_ratings.items()},
                    "gap": round(rating_diff, 2),
                    "insight": f"{best_rated} has a {rating_diff:.2f}-star rating advantage over {worst_rated}",
                }

            # Demographics gap analysis (age skew)
            demographics_gap: list[dict] = []
            for n, d in found.items():
                demo = d["snapshot"].get("demographics", [])
                if demo:
                    total_male = sum(row["male_pct"] or 0 for row in demo)
                    total_female = sum(row["female_pct"] or 0 for row in demo)
                    count = len(demo)
                    if count > 0:
                        demographics_gap.append({
                            "advertiser": n,
                            "avg_male_pct": round(total_male / count, 1),
                            "avg_female_pct": round(total_female / count, 1),
                            "skew": "male-skewed" if total_male > total_female else "female-skewed",
                        })

            # GEO visibility gap analysis
            geo_gaps: dict = {}
            geo_data = {
                n: d["geo_snapshot"]
                for n, d in found.items()
                if d.get("geo_snapshot")
            }
            if len(geo_data) >= 2:
                vis_rates = {n: g["visibility_rate"] for n, g in geo_data.items()}
                geo_leader = max(vis_rates, key=lambda n: vis_rates[n])
                geo_follower = min(vis_rates, key=lambda n: vis_rates[n])
                gap = vis_rates[geo_leader] - vis_rates[geo_follower]

                # Per-engine comparison: who leads on each engine?
                all_engines: set[str] = set()
                for g in geo_data.values():
                    for eng in g["engine_breakdown"]:
                        all_engines.add(eng["engine"])

                engine_comparison: dict[str, dict] = {}
                for engine in sorted(all_engines):
                    engine_vals: dict[str, float] = {}
                    for n, g in geo_data.items():
                        for eng in g["engine_breakdown"]:
                            if eng["engine"] == engine:
                                engine_vals[n] = eng["visibility"]
                    if len(engine_vals) >= 2:
                        leader = max(engine_vals, key=lambda x: engine_vals[x])
                        engine_comparison[engine] = {
                            "leader": leader,
                            "rates": engine_vals,
                        }

                # GEO-specific opportunities
                geo_opportunities: list[str] = []
                for n, g in geo_data.items():
                    for eng in g["engine_breakdown"]:
                        if eng["total"] >= 5 and eng["visibility"] == 0:
                            geo_opportunities.append(
                                f"{n} has zero AI visibility on {eng['engine']} ({eng['total']} prompts tracked)"
                            )
                    if g.get("top_blind_spots"):
                        geo_opportunities.append(
                            f"{n} has {len(g['top_blind_spots'])} high-volume blind-spot prompts where competitors appear"
                        )

                geo_gaps = {
                    "overall": {
                        "leader": geo_leader,
                        "rates": vis_rates,
                        "gap": round(gap, 4),
                        "insight": f"{geo_leader} leads AI visibility by {gap:.0%} over {geo_follower}",
                    },
                    "engine_comparison": engine_comparison,
                    "opportunities": geo_opportunities,
                }

            gap_analysis = {
                "network_coverage": {
                    n: {"exclusive": exclusive[n], "shared_with_others": sorted(shared)}
                    for n in found
                },
                "all_networks_seen": sorted(all_nets),
                "sov_comparison": {
                    "leader": sov_leader,
                    "totals": {n: round(v, 6) for n, v in sov_totals.items()},
                    "ratio": sov_ratio,
                    "insight": (
                        f"{sov_leader} commands {sov_ratio}x the total impression share of {sov_follower}."
                        if sov_ratio else "SOV data unavailable for comparison."
                    ),
                },
                "efficiency_indicators": {
                    n: {"downloads_per_sov_point": efficiency[n]} for n in found
                },
                "retention_gap": retention_gap,
                "review_rating_gap": rating_gap,
                "demographics_comparison": demographics_gap,
                "geo_gaps": geo_gaps,
                "opportunities": opportunities,
            }

        return json.dumps(
            {
                "comparison_date": date.today().isoformat(),
                "country": country,
                "days": days,
                "advertisers": advertiser_data,
                "gap_analysis": gap_analysis,
            },
            indent=2,
        )

    # ── Market-wide tools ──────────────────────────────────────────────

    # Network name → ad_on_* column mapping for market top apps
    _MARKET_NETWORK_COLS = {
        "admob": "ad_on_admob",
        "facebook": "ad_on_facebook",
        "instagram": "ad_on_instagram",
        "tiktok": "ad_on_tiktok",
        "youtube": "ad_on_youtube",
        "snapchat": "ad_on_snapchat",
        "applovin": "ad_on_applovin",
        "unity": "ad_on_unity",
        "mintegral": "ad_on_mintegral",
    }

    @server.tool(
        name="get_market_top_apps",
        description=(
            "Get market-wide app rankings from SensorTower. "
            "Returns top apps by downloads, revenue, DAU, or impression share. "
            "Use this for questions like 'which apps have the most downloads?', 'top Finance apps by DAU', "
            "or 'Finance apps advertising on TikTok'. "
            "Filters: network_filter (e.g. 'tiktok', 'admob') to show only apps advertising on that network; "
            "min_downloads to exclude small apps; app_category for cross-category searches (e.g. find 'Finance' "
            "apps within the Overall ranking)."
        ),
    )
    def get_market_top_apps(
        category: str = "Finance",
        country: str = "US",
        sort_by: str = "downloads",
        limit: int = 20,
        scrape_month: str | None = None,
        network_filter: str | None = None,
        min_downloads: int | None = None,
        app_category: str | None = None,
    ) -> str:
        from adintel.db.models import SensorTowerMarketTopAppRecord
        from adintel.platforms.sensortower_parsers import CATEGORY_NAMES

        valid_sort = {"downloads", "revenue", "dau", "impression_share", "rank"}
        if sort_by not in valid_sort:
            return json.dumps({"error": f"Invalid sort_by. Options: {', '.join(sorted(valid_sort))}"})

        # Validate network_filter if provided
        network_col: str | None = None
        if network_filter:
            network_col = _MARKET_NETWORK_COLS.get(network_filter.lower())
            if network_col is None:
                return json.dumps({
                    "error": f"Unknown network_filter '{network_filter}'. "
                             f"Options: {', '.join(sorted(_MARKET_NETWORK_COLS.keys()))}"
                })

        # Resolve category name to ID or use as-is
        reverse = {v.lower(): k for k, v in CATEGORY_NAMES.items()}
        category_name = category
        if category.lower() in reverse:
            category_name = CATEGORY_NAMES[reverse[category.lower()]]
        elif category in CATEGORY_NAMES:
            category_name = CATEGORY_NAMES[category]

        limit = max(1, min(limit, 100))

        with _session_factory()() as session:
            q = select(SensorTowerMarketTopAppRecord).where(
                SensorTowerMarketTopAppRecord.country == country,
            )

            # Match category flexibly (exact or case-insensitive)
            q = q.where(
                SensorTowerMarketTopAppRecord.category.ilike(category_name)
            )

            if scrape_month:
                q = q.where(SensorTowerMarketTopAppRecord.scrape_month == date.fromisoformat(scrape_month))
            else:
                # Latest available month
                latest = session.scalar(
                    select(func.max(SensorTowerMarketTopAppRecord.scrape_month))
                    .where(SensorTowerMarketTopAppRecord.country == country)
                    .where(SensorTowerMarketTopAppRecord.category.ilike(category_name))
                )
                if latest is None:
                    return json.dumps({
                        "error": f"No market data found for category='{category}', country='{country}'.",
                        "hint": "Run 'adintel collect market-top-apps' to collect market data first.",
                    })
                q = q.where(SensorTowerMarketTopAppRecord.scrape_month == latest)

            # Optional filters
            if network_col:
                col = getattr(SensorTowerMarketTopAppRecord, network_col)
                q = q.where(col.is_(True))
            if min_downloads is not None:
                q = q.where(SensorTowerMarketTopAppRecord.downloads >= min_downloads)
            if app_category:
                q = q.where(SensorTowerMarketTopAppRecord.primary_category.ilike(f"%{app_category}%"))

            # Sort
            sort_col = getattr(SensorTowerMarketTopAppRecord, sort_by)
            if sort_by == "rank":
                q = q.order_by(sort_col.asc())
            else:
                q = q.order_by(sort_col.desc().nulls_last())

            rows = session.scalars(q.limit(limit)).all()

        results = []
        for row in rows:
            results.append({
                "rank": row.rank,
                "app_name": row.app_name,
                "publisher_name": row.publisher_name,
                "unified_app_id": row.unified_app_id,
                "primary_category": row.primary_category,
                "downloads": row.downloads,
                "revenue": _to_float(row.revenue),
                "dau": row.dau,
                "impression_share": _to_float(row.impression_share),
                "ad_on_admob": row.ad_on_admob,
                "ad_on_facebook": row.ad_on_facebook,
                "ad_on_tiktok": row.ad_on_tiktok,
                "ad_on_youtube": row.ad_on_youtube,
                "ad_on_applovin": row.ad_on_applovin,
                "ad_on_unity": row.ad_on_unity,
                "scrape_month": row.scrape_month.isoformat(),
                "country": row.country,
            })

        active_filters: dict = {}
        if network_filter:
            active_filters["network_filter"] = network_filter
        if min_downloads is not None:
            active_filters["min_downloads"] = min_downloads
        if app_category:
            active_filters["app_category"] = app_category

        return json.dumps({
            "category": category_name,
            "country": country,
            "sort_by": sort_by,
            "filters": active_filters,
            "count": len(results),
            "data": results,
        }, indent=2)

    # ── GEO (Generative Engine Optimization) Analysis Tools ─────────────

    def _resolve_geo_target(session, advertiser_name: str) -> str:
        """Resolve a display name like 'Chime' to its otterly domain like 'chime.com'.

        Resolution order:
        1. Exact match in otterlyai_prompts (already a domain key).
        2. Advertiser catalog domain match (display name → domain).
        3. Case-insensitive partial name match in catalog.
        4. Fall back to the original string (let callers handle missing data).
        """
        from sqlalchemy import func as _func
        from adintel.db.models import AdvertiserRecord

        # 1. Exact match already in otterly data
        exists = session.scalar(
            select(OtterlyPromptRecord.target_brand_or_domain_name)
            .where(OtterlyPromptRecord.target_brand_or_domain_name == advertiser_name)
            .limit(1)
        )
        if exists:
            return advertiser_name

        # 2 & 3. Look up via advertiser catalog
        row = session.scalar(
            select(AdvertiserRecord).where(
                _func.lower(AdvertiserRecord.name) == advertiser_name.lower()
            )
        )
        if row is None:
            row = session.scalar(
                select(AdvertiserRecord).where(
                    _func.lower(AdvertiserRecord.name).contains(advertiser_name.lower())
                )
            )
        if row is not None and row.domain:
            # Confirm the domain exists in otterly data
            domain_exists = session.scalar(
                select(OtterlyPromptRecord.target_brand_or_domain_name)
                .where(OtterlyPromptRecord.target_brand_or_domain_name == row.domain)
                .limit(1)
            )
            if domain_exists:
                return row.domain

        # 4. Fuzzy domain match directly in otterly data (e.g. "Dave" → "dave.com")
        fuzzy = session.scalar(
            select(OtterlyPromptRecord.target_brand_or_domain_name)
            .where(OtterlyPromptRecord.target_brand_or_domain_name.ilike(f"%{advertiser_name}%"))
            .limit(1)
        )
        if fuzzy:
            return fuzzy

        return advertiser_name

    def _geo_engine_breakdown(session, target: str, country: str | None) -> list[dict]:
        q = (
            select(
                OtterlyPromptRecord.ai_engine,
                func.count().label("total_prompts"),
                func.sum(case((OtterlyPromptRecord.domain_cited.is_(True), 1), else_=0)).label("cited_prompts"),
                func.avg(OtterlyPromptRecord.sentiment_score).label("avg_sentiment"),
                func.avg(OtterlyPromptRecord.target_rank).label("avg_rank"),
            )
            .where(OtterlyPromptRecord.target_brand_or_domain_name == target)
            .group_by(OtterlyPromptRecord.ai_engine)
        )
        if country:
            q = q.where(OtterlyPromptRecord.country_code == country.lower())
        rows = session.execute(q).all()
        result = []
        for row in rows:
            total = row.total_prompts or 0
            cited = row.cited_prompts or 0
            result.append({
                "ai_engine": row.ai_engine,
                "total_prompts": total,
                "cited_prompts": cited,
                "visibility_rate": round(cited / total, 4) if total > 0 else 0,
                "avg_sentiment": round(float(row.avg_sentiment), 2) if row.avg_sentiment is not None else None,
                "avg_rank": round(float(row.avg_rank), 1) if row.avg_rank is not None else None,
            })
        return sorted(result, key=lambda x: x["total_prompts"], reverse=True)

    def _geo_sentiment_distribution(session, target: str, country: str | None) -> dict:
        q = (
            select(
                OtterlyPromptRecord.sentiment_label,
                func.count().label("cnt"),
            )
            .where(OtterlyPromptRecord.target_brand_or_domain_name == target)
            .where(OtterlyPromptRecord.sentiment_label.isnot(None))
            .group_by(OtterlyPromptRecord.sentiment_label)
        )
        if country:
            q = q.where(OtterlyPromptRecord.country_code == country.lower())
        rows = session.execute(q).all()
        return {row.sentiment_label: row.cnt for row in rows}

    def _geo_top_cited_domains(session, target: str, country: str | None, limit: int = 15) -> list[dict]:
        q = (
            select(
                OtterlyCitationRecord.cited_domain,
                func.sum(OtterlyCitationRecord.citation_count).label("total_citations"),
                func.count().label("appearances"),
                func.sum(case((OtterlyCitationRecord.brand_mentioned.is_(True), 1), else_=0)).label("brand_mentions"),
            )
            .where(OtterlyCitationRecord.target_brand_or_domain_name == target)
            .where(OtterlyCitationRecord.cited_domain.isnot(None))
            .group_by(OtterlyCitationRecord.cited_domain)
            .order_by(func.sum(OtterlyCitationRecord.citation_count).desc())
            .limit(limit)
        )
        if country:
            q = q.where(OtterlyCitationRecord.country_code == country.lower())
        rows = session.execute(q).all()
        return [
            {
                "domain": row.cited_domain,
                "total_citations": _to_float(row.total_citations) or 0,
                "appearances": row.appearances,
                "brand_mentioned_count": _to_float(row.brand_mentions) or 0,
            }
            for row in rows
        ]

    @server.tool(
        name="get_geo_summary",
        description=(
            "Comprehensive single-advertiser GEO (Generative Engine Optimization) analysis using Otterly.AI-backed GEO data. "
            "Returns everything about a brand's AI search presence in one call: "
            "visibility rate and engine breakdown (ChatGPT, Perplexity, Gemini, etc.), "
            "sentiment distribution, top cited domains with categories, "
            "per-engine citation patterns, top prompts by volume, "
            "blind-spot prompts (where competitors appear but brand doesn't), "
            "and negative-sentiment prompts. Use for any single-brand GEO analysis."
        ),
    )
    def get_geo_summary(
        advertiser_name: str,
        country: str | None = None,
        limit: int = 20,
    ) -> str:
        limit = max(1, min(limit, 100))
        with _session_factory()() as session:
            advertiser_name = _resolve_geo_target(session, advertiser_name)

            # ── Visibility overview ──────────────────────────────────
            overview_q = (
                select(
                    func.count().label("total"),
                    func.sum(case((OtterlyPromptRecord.domain_cited.is_(True), 1), else_=0)).label("cited"),
                    func.min(OtterlyPromptRecord.query_window_start_date).label("earliest"),
                    func.max(OtterlyPromptRecord.query_window_end_date).label("latest"),
                )
                .where(OtterlyPromptRecord.target_brand_or_domain_name == advertiser_name)
            )
            if country:
                overview_q = overview_q.where(OtterlyPromptRecord.country_code == country.lower())

            stats = session.execute(overview_q).one()
            total = stats.total or 0
            if total == 0:
                return json.dumps({
                    "found": False,
                    "advertiser_name": advertiser_name,
                    "message": "No Otterly GEO data found for this advertiser.",
                })

            cited = stats.cited or 0
            engine_breakdown = _geo_engine_breakdown(session, advertiser_name, country)
            sentiment_dist = _geo_sentiment_distribution(session, advertiser_name, country)
            top_domains = _geo_top_cited_domains(session, advertiser_name, country, limit=limit)

            # ── Citation analysis ────────────────────────────────────
            cit_overview_q = (
                select(
                    func.count().label("total"),
                    func.sum(case((OtterlyCitationRecord.brand_mentioned.is_(True), 1), else_=0)).label("brand_mentioned"),
                )
                .where(OtterlyCitationRecord.target_brand_or_domain_name == advertiser_name)
            )
            if country:
                cit_overview_q = cit_overview_q.where(OtterlyCitationRecord.country_code == country.lower())
            cit_stats = session.execute(cit_overview_q).one()
            cit_total = cit_stats.total or 0
            brand_mentioned_count = cit_stats.brand_mentioned or 0

            # Per-engine citation counts
            cit_engine_breakdown: list[dict] = []
            if cit_total > 0:
                cit_engine_q = (
                    select(
                        OtterlyCitationRecord.ai_engine,
                        func.count().label("citation_rows"),
                        func.sum(OtterlyCitationRecord.citation_count).label("total_citations"),
                        func.sum(case((OtterlyCitationRecord.brand_mentioned.is_(True), 1), else_=0)).label("brand_mentions"),
                    )
                    .where(OtterlyCitationRecord.target_brand_or_domain_name == advertiser_name)
                    .group_by(OtterlyCitationRecord.ai_engine)
                )
                if country:
                    cit_engine_q = cit_engine_q.where(OtterlyCitationRecord.country_code == country.lower())
                cit_engine_rows = session.execute(cit_engine_q).all()
                cit_engine_breakdown = [
                    {
                        "ai_engine": row.ai_engine,
                        "citation_rows": row.citation_rows,
                        "total_citations": _to_float(row.total_citations) or 0,
                        "brand_mentioned_count": _to_float(row.brand_mentions) or 0,
                    }
                    for row in sorted(cit_engine_rows, key=lambda r: r.total_citations or 0, reverse=True)
                ]

            # Domain category distribution
            cat_q = (
                select(
                    OtterlyCitationRecord.domain_category,
                    func.count().label("cnt"),
                    func.sum(OtterlyCitationRecord.citation_count).label("total_citations"),
                )
                .where(OtterlyCitationRecord.target_brand_or_domain_name == advertiser_name)
                .where(OtterlyCitationRecord.domain_category.isnot(None))
                .group_by(OtterlyCitationRecord.domain_category)
                .order_by(func.sum(OtterlyCitationRecord.citation_count).desc())
                .limit(15)
            )
            if country:
                cat_q = cat_q.where(OtterlyCitationRecord.country_code == country.lower())
            category_distribution = [
                {"category": row.domain_category, "count": row.cnt, "total_citations": _to_float(row.total_citations) or 0}
                for row in session.execute(cat_q).all()
            ]

            # Competitors appearing in citations
            comp_q = (
                select(OtterlyCitationRecord.competitors)
                .where(OtterlyCitationRecord.target_brand_or_domain_name == advertiser_name)
                .where(OtterlyCitationRecord.competitors.isnot(None))
            )
            if country:
                comp_q = comp_q.where(OtterlyCitationRecord.country_code == country.lower())
            all_competitors: dict[str, int] = {}
            for comp_list in session.scalars(comp_q).all():
                for comp in (comp_list or []):
                    all_competitors[comp] = all_competitors.get(comp, 0) + 1

            # ── Prompt insights ──────────────────────────────────────
            prompt_q = (
                select(OtterlyPromptRecord)
                .where(OtterlyPromptRecord.target_brand_or_domain_name == advertiser_name)
            )
            if country:
                prompt_q = prompt_q.where(OtterlyPromptRecord.country_code == country.lower())
            all_prompts = session.scalars(prompt_q).all()

            # Top prompts by volume
            with_volume = [p for p in all_prompts if p.prompt_volume is not None]
            top_prompts = [
                {
                    "prompt": p.prompt_text,
                    "volume": p.prompt_volume,
                    "rank": p.target_rank,
                    "cited": p.domain_cited,
                    "ai_engine": p.ai_engine,
                    "sentiment": p.sentiment_label,
                }
                for p in sorted(with_volume, key=lambda p: p.prompt_volume or 0, reverse=True)[:limit]
            ]

            # Best ranked prompts
            with_rank = [p for p in all_prompts if p.target_rank is not None and p.target_rank > 0]
            best_ranked_prompts = [
                {
                    "prompt": p.prompt_text,
                    "rank": p.target_rank,
                    "volume": p.prompt_volume,
                    "ai_engine": p.ai_engine,
                    "cited": p.domain_cited,
                }
                for p in sorted(with_rank, key=lambda p: p.target_rank)[:limit]
            ]

            # Blind spots: not cited but competitors present
            blind_spots = [p for p in all_prompts if not p.domain_cited and (p.competitors or [])]
            blind_spot_prompts = [
                {
                    "prompt": p.prompt_text,
                    "volume": p.prompt_volume,
                    "ai_engine": p.ai_engine,
                    "competitors_present": p.competitors,
                    "sentiment": p.sentiment_label,
                }
                for p in sorted(blind_spots, key=lambda p: p.prompt_volume or 0, reverse=True)[:limit]
            ]

            # Negative sentiment prompts
            negative = [p for p in all_prompts if p.sentiment_label == "negative"]
            negative_prompts = [
                {
                    "prompt": p.prompt_text,
                    "volume": p.prompt_volume,
                    "ai_engine": p.ai_engine,
                    "sentiment_score": _to_float(p.sentiment_score),
                }
                for p in sorted(negative, key=lambda p: p.prompt_volume or 0, reverse=True)[:10]
            ]

        return json.dumps({
            "advertiser_name": advertiser_name,
            "country": country,
            "visibility": {
                "date_range": {
                    "earliest": stats.earliest.isoformat() if stats.earliest else None,
                    "latest": stats.latest.isoformat() if stats.latest else None,
                },
                "total_prompts_tracked": total,
                "prompts_where_cited": cited,
                "visibility_rate": round(cited / total, 4) if total > 0 else 0,
            },
            "engine_breakdown": engine_breakdown,
            "sentiment_distribution": sentiment_dist,
            "citations": {
                "total_citation_rows": cit_total,
                "brand_mentioned_count": brand_mentioned_count,
                "brand_mention_rate": round(brand_mentioned_count / cit_total, 4) if cit_total > 0 else 0,
                "top_cited_domains": top_domains,
                "per_engine": cit_engine_breakdown,
                "category_distribution": category_distribution,
                "competitors_in_citations": dict(
                    sorted(all_competitors.items(), key=lambda x: x[1], reverse=True)[:10]
                ),
            },
            "prompts": {
                "total": len(all_prompts),
                "top_by_volume": top_prompts,
                "best_ranked": best_ranked_prompts,
                "blind_spots": blind_spot_prompts,
                "negative_sentiment": negative_prompts,
            },
        }, indent=2)

    @server.tool(
        name="compare_geo_visibility",
        description=(
            "Side-by-side GEO visibility comparison for 2+ brands. "
            "Shows who is winning in AI search, engine-by-engine gap analysis, "
            "sentiment comparison, competitor overlap, and blind spots. "
            "Use for competitive GEO analysis."
        ),
    )
    def compare_geo_visibility(
        advertiser_names: str,
        country: str | None = None,
    ) -> str:
        names = [n.strip() for n in advertiser_names.split(",") if n.strip()]
        if len(names) < 2:
            return json.dumps({"error": "Provide at least 2 comma-separated advertiser names."})

        with _session_factory()() as _resolve_session:
            names = [_resolve_geo_target(_resolve_session, n) for n in names]

        advertiser_data: dict[str, dict] = {}

        with _session_factory()() as session:
            for name in names:
                # Aggregate counts via SQL
                overview_q = (
                    select(
                        func.count().label("total"),
                        func.sum(case((OtterlyPromptRecord.domain_cited.is_(True), 1), else_=0)).label("cited"),
                    )
                    .where(OtterlyPromptRecord.target_brand_or_domain_name == name)
                )
                if country:
                    overview_q = overview_q.where(OtterlyPromptRecord.country_code == country.lower())

                stats = session.execute(overview_q).one()
                total = stats.total or 0
                if total == 0:
                    advertiser_data[name] = {"found": False}
                    continue

                cited = stats.cited or 0
                engines = _geo_engine_breakdown(session, name, country)
                sentiment = _geo_sentiment_distribution(session, name, country)

                # Load only the competitors JSON column for aggregation
                comp_q = (
                    select(OtterlyPromptRecord.competitors)
                    .where(OtterlyPromptRecord.target_brand_or_domain_name == name)
                    .where(OtterlyPromptRecord.competitors.isnot(None))
                )
                if country:
                    comp_q = comp_q.where(OtterlyPromptRecord.country_code == country.lower())
                comp_rows = session.scalars(comp_q).all()

                all_competitors: dict[str, int] = {}
                for comp_list in comp_rows:
                    for comp in (comp_list or []):
                        all_competitors[comp] = all_competitors.get(comp, 0) + 1

                advertiser_data[name] = {
                    "found": True,
                    "total_prompts": total,
                    "cited_prompts": cited,
                    "visibility_rate": round(cited / total, 4) if total > 0 else 0,
                    "engine_breakdown": engines,
                    "sentiment_distribution": sentiment,
                    "top_competitors_mentioned": dict(
                        sorted(all_competitors.items(), key=lambda x: x[1], reverse=True)[:10]
                    ),
                }

            # Gap analysis across advertisers
            found = {n: d for n, d in advertiser_data.items() if d.get("found")}
            gap_analysis: dict = {}

            if len(found) >= 2:
                # Engine coverage: which engines cite each brand
                engine_sets: dict[str, set] = {}
                for n, d in found.items():
                    engine_sets[n] = {
                        e["ai_engine"] for e in d["engine_breakdown"] if e["cited_prompts"] > 0
                    }

                all_engines = set().union(*engine_sets.values())
                blind_spots: dict[str, list] = {}
                for n in found:
                    others_engines = set().union(*(engine_sets[o] for o in found if o != n))
                    missing = sorted(others_engines - engine_sets[n])
                    if missing:
                        blind_spots[n] = missing

                # Visibility ranking
                vis_ranking = sorted(
                    [(n, d["visibility_rate"]) for n, d in found.items()],
                    key=lambda x: x[1],
                    reverse=True,
                )

                # Competitor overlap
                competitor_sets = {
                    n: set(d.get("top_competitors_mentioned", {}).keys())
                    for n, d in found.items()
                }
                shared_competitors = sorted(set.intersection(*competitor_sets.values())) if competitor_sets else []

                opportunities: list[str] = []
                leader_name, leader_rate = vis_ranking[0]
                for n, rate in vis_ranking[1:]:
                    gap = leader_rate - rate
                    if gap > 0:
                        opportunities.append(
                            f"{n} trails {leader_name} by {gap:.1%} visibility rate"
                        )
                for n, engines in blind_spots.items():
                    for eng in engines:
                        opportunities.append(
                            f"{n} has zero citations on {eng} — competitors are active there"
                        )

                gap_analysis = {
                    "visibility_ranking": [{"name": n, "rate": r} for n, r in vis_ranking],
                    "engine_blind_spots": blind_spots,
                    "shared_competitors": shared_competitors,
                    "opportunities": opportunities,
                }

        return json.dumps({
            "country": country,
            "advertisers": advertiser_data,
            "gap_analysis": gap_analysis,
        }, indent=2)


    @server.tool(
        name="get_geo_data_availability",
        description=(
            "Check what GEO (Otterly.AI-backed) data is available in the database. "
            "Shows which brands have data, which AI engines are covered, date range, "
            "row counts, and field completeness gaps. Use this before running any GEO "
            "analysis to understand data coverage."
        ),
    )
    def get_geo_data_availability(advertiser_name: str | None = None) -> str:
        with _session_factory()() as session:
            if advertiser_name:
                advertiser_name = _resolve_geo_target(session, advertiser_name)

            def _prompt_filter(q):
                if advertiser_name:
                    q = q.where(OtterlyPromptRecord.target_brand_or_domain_name == advertiser_name)
                return q

            def _citation_filter(q):
                if advertiser_name:
                    q = q.where(OtterlyCitationRecord.target_brand_or_domain_name == advertiser_name)
                return q

            # Overall prompt stats
            p_total = session.scalar(_prompt_filter(select(func.count()).select_from(OtterlyPromptRecord))) or 0
            c_total = session.scalar(_citation_filter(select(func.count()).select_from(OtterlyCitationRecord))) or 0

            if p_total == 0 and c_total == 0:
                return json.dumps({
                    "found": False,
                    "advertiser_name": advertiser_name,
                    "message": "No GEO data found." + (f" '{advertiser_name}' is not tracked in Otterly." if advertiser_name else ""),
                })

            # Date range
            date_q = _prompt_filter(
                select(
                    func.min(OtterlyPromptRecord.query_window_start_date).label("earliest"),
                    func.max(OtterlyPromptRecord.query_window_end_date).label("latest"),
                    func.count(OtterlyPromptRecord.query_window_end_date.distinct()).label("snapshots"),
                ).select_from(OtterlyPromptRecord)
            )
            dates = session.execute(date_q).one()

            # Per-brand prompt coverage
            brand_q = _prompt_filter(
                select(
                    OtterlyPromptRecord.target_brand_or_domain_name,
                    OtterlyPromptRecord.ai_engine,
                    func.count().label("prompt_rows"),
                    func.sum(case((OtterlyPromptRecord.domain_cited.is_(True), 1), else_=0)).label("cited"),
                ).select_from(OtterlyPromptRecord)
                .group_by(OtterlyPromptRecord.target_brand_or_domain_name, OtterlyPromptRecord.ai_engine)
                .order_by(OtterlyPromptRecord.target_brand_or_domain_name, OtterlyPromptRecord.ai_engine)
            )
            brand_rows = session.execute(brand_q).all()

            # Aggregate by brand
            brands_summary: dict[str, dict] = {}
            for row in brand_rows:
                b = row.target_brand_or_domain_name
                if b not in brands_summary:
                    brands_summary[b] = {"engines": [], "total_prompts": 0, "total_cited": 0}
                rate = round(row.cited / row.prompt_rows, 3) if row.prompt_rows else 0
                brands_summary[b]["engines"].append({
                    "engine": row.ai_engine,
                    "prompts": row.prompt_rows,
                    "cited": row.cited,
                    "visibility_rate": rate,
                })
                brands_summary[b]["total_prompts"] += row.prompt_rows
                brands_summary[b]["total_cited"] += row.cited

            # Citation coverage per brand
            cit_q = _citation_filter(
                select(
                    OtterlyCitationRecord.target_brand_or_domain_name,
                    func.count().label("citation_rows"),
                ).select_from(OtterlyCitationRecord)
                .group_by(OtterlyCitationRecord.target_brand_or_domain_name)
            )
            for row in session.execute(cit_q).all():
                if row.target_brand_or_domain_name in brands_summary:
                    brands_summary[row.target_brand_or_domain_name]["citation_rows"] = row.citation_rows

            # Field completeness gaps (on full table or filtered)
            null_sentiment = session.scalar(
                _prompt_filter(select(func.count()).select_from(OtterlyPromptRecord).where(OtterlyPromptRecord.sentiment_label.is_(None)))
            ) or 0
            gap_pct = round(null_sentiment / p_total, 3) if p_total else 0
            field_gaps = []
            if gap_pct > 0.05:
                field_gaps.append({
                    "field": "sentiment_label",
                    "null_rows": null_sentiment,
                    "null_pct": gap_pct,
                    "impact": "sentiment analysis and negative-prompt detection will be incomplete",
                })

            # Engines present
            engines = [r[0] for r in session.execute(
                _prompt_filter(select(OtterlyPromptRecord.ai_engine).distinct().select_from(OtterlyPromptRecord))
            ).all()]
            countries = [r[0] for r in session.execute(
                _prompt_filter(select(OtterlyPromptRecord.country_code).distinct().select_from(OtterlyPromptRecord))
            ).all()]

        return json.dumps({
            "scope": advertiser_name or "all brands",
            "summary": {
                "brands_tracked": len(brands_summary),
                "ai_engines": sorted(engines),
                "countries": sorted(countries),
                "date_range": {
                    "start": dates.earliest.isoformat() if dates.earliest else None,
                    "end": dates.latest.isoformat() if dates.latest else None,
                    "snapshots": dates.snapshots,
                },
                "total_prompt_rows": p_total,
                "total_citation_rows": c_total,
            },
            "brands": {
                b: {
                    **d,
                    "visibility_rate": round(d["total_cited"] / d["total_prompts"], 3) if d["total_prompts"] else 0,
                }
                for b, d in brands_summary.items()
            },
            "field_gaps": field_gaps,
            "ready_for_analysis": len(field_gaps) == 0,
        }, indent=2)

    # ------------------------------------------------------------------
    # AppFollow tools
    # ------------------------------------------------------------------

    @server.tool(
        name="get_appfollow_reviews",
        description=(
            "List individual AppFollow reviews for an advertiser with optional filters. "
            "Filters: country (ISO-2), os ('ios'/'android'), sentiment ('positive'/'negative'/'neutral'), "
            "min_rating (1-5), days lookback (default 90). "
            "Returns up to limit reviews (default 50, max 200) ordered by review_date desc. "
            "Useful for reading actual user complaints and praise verbatim."
        ),
    )
    def get_appfollow_reviews(
        advertiser_name: str,
        country: str | None = None,
        os: str | None = None,
        sentiment: str | None = None,
        min_rating: float | None = None,
        days: int = 90,
        limit: int = 50,
    ) -> str:
        from datetime import timedelta
        limit = max(1, min(limit, 200))
        cutoff = date.today() - timedelta(days=days)

        with _session_factory()() as session:
            repo = AdvertiserRepository(session)
            advertiser = repo.resolve(advertiser_name)
            resolved_name = advertiser.name if advertiser else advertiser_name

            q = (
                select(AppFollowReviewRecord)
                .where(AppFollowReviewRecord.advertiser_name == resolved_name)
                .where(AppFollowReviewRecord.review_date >= cutoff)
            )
            if country:
                q = q.where(AppFollowReviewRecord.country == country.upper())
            if os:
                q = q.where(AppFollowReviewRecord.os == os.lower())
            if sentiment:
                q = q.where(AppFollowReviewRecord.sentiment == sentiment.lower())
            if min_rating is not None:
                q = q.where(AppFollowReviewRecord.star_rating >= min_rating)

            rows = session.scalars(
                q.order_by(desc(AppFollowReviewRecord.review_date)).limit(limit)
            ).all()

        return json.dumps({
            "found": bool(rows),
            "resolved_name": resolved_name,
            "filters": {
                "country": country,
                "os": os,
                "sentiment": sentiment,
                "min_rating": min_rating,
                "days": days,
            },
            "count": len(rows),
            "reviews": [
                {
                    "review_id":       row.review_id,
                    "review_date":     row.review_date.isoformat(),
                    "country":         row.country,
                    "os":              row.os,
                    "star_rating":     _to_float(row.star_rating),
                    "username":        row.username,
                    "title":           row.title,
                    "body":            row.body,
                    "sentiment":       row.sentiment,
                    "sentiment_score": _to_float(row.sentiment_score),
                    "tags":            row.tags,
                    "app_version":     row.app_version,
                }
                for row in rows
            ],
        }, indent=2)

    @server.tool(
        name="get_appfollow_sentiment_trend",
        description=(
            "Return daily sentiment breakdown (positive/negative/neutral counts and avg rating) "
            "for an advertiser's AppFollow reviews. "
            "Useful for tracking when sentiment improved or degraded — e.g. after a product launch or competitor move. "
            "Filter by country or os. days default: 90."
        ),
    )
    def get_appfollow_sentiment_trend(
        advertiser_name: str,
        country: str | None = None,
        os: str | None = None,
        days: int = 90,
    ) -> str:
        from datetime import timedelta
        cutoff = date.today() - timedelta(days=days)

        with _session_factory()() as session:
            repo = AdvertiserRepository(session)
            advertiser = repo.resolve(advertiser_name)
            resolved_name = advertiser.name if advertiser else advertiser_name

            q = (
                select(
                    AppFollowReviewRecord.review_date,
                    AppFollowReviewRecord.sentiment,
                    func.count().label("count"),
                    func.avg(AppFollowReviewRecord.star_rating).label("avg_rating"),
                )
                .where(AppFollowReviewRecord.advertiser_name == resolved_name)
                .where(AppFollowReviewRecord.review_date >= cutoff)
            )
            if country:
                q = q.where(AppFollowReviewRecord.country == country.upper())
            if os:
                q = q.where(AppFollowReviewRecord.os == os.lower())

            q = q.group_by(
                AppFollowReviewRecord.review_date,
                AppFollowReviewRecord.sentiment,
            ).order_by(AppFollowReviewRecord.review_date)

            result_rows = session.execute(q).all()

        # Pivot: date → {positive: N, negative: N, neutral: N, total: N, avg_rating: F}
        by_date: dict[str, dict] = {}
        for row in result_rows:
            d = row.review_date.isoformat()
            entry = by_date.setdefault(d, {
                "date": d, "positive": 0, "negative": 0, "neutral": 0,
                "unknown": 0, "avg_rating": None, "total": 0,
            })
            label = (row.sentiment or "unknown").lower()
            entry[label] = entry.get(label, 0) + int(row.count)
            entry["total"] += int(row.count)
            if row.avg_rating is not None:
                entry["avg_rating"] = round(float(row.avg_rating), 2)

        trend = sorted(by_date.values(), key=lambda x: x["date"])
        return json.dumps({
            "found": bool(trend),
            "resolved_name": resolved_name,
            "filters": {"country": country, "os": os, "days": days},
            "trend": trend,
        }, indent=2)

    @server.tool(
        name="get_appfollow_keyword_analysis",
        description=(
            "Return top keywords and topics extracted from AppFollow review tags. "
            "Filter by sentiment='negative' to see what users complain about; "
            "filter by sentiment='positive' to see what users praise. "
            "Returns top_n keywords (default 20) each with count, avg_rating, and sentiment_split. "
            "Example: '35% of negative reviews mention slow registration' — the kind of insight "
            "that explains why conversion rates are poor."
        ),
    )
    def get_appfollow_keyword_analysis(
        advertiser_name: str,
        country: str | None = None,
        sentiment: str | None = None,
        days: int = 90,
        top_n: int = 20,
    ) -> str:
        from datetime import timedelta
        cutoff = date.today() - timedelta(days=days)

        with _session_factory()() as session:
            repo = AdvertiserRepository(session)
            advertiser = repo.resolve(advertiser_name)
            resolved_name = advertiser.name if advertiser else advertiser_name

            q = (
                select(
                    AppFollowReviewRecord.tags,
                    AppFollowReviewRecord.sentiment,
                    AppFollowReviewRecord.star_rating,
                )
                .where(AppFollowReviewRecord.advertiser_name == resolved_name)
                .where(AppFollowReviewRecord.review_date >= cutoff)
                .where(AppFollowReviewRecord.tags.isnot(None))
            )
            if country:
                q = q.where(AppFollowReviewRecord.country == country.upper())
            if sentiment:
                q = q.where(AppFollowReviewRecord.sentiment == sentiment.lower())

            result_rows = session.execute(q).all()

        tag_counts: dict[str, int] = {}
        tag_ratings: dict[str, list[float]] = {}
        tag_sentiments: dict[str, Counter] = {}

        for row in result_rows:
            for tag in (row.tags or []):
                if not tag:
                    continue
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
                if row.star_rating is not None:
                    tag_ratings.setdefault(tag, []).append(float(row.star_rating))
                s = (row.sentiment or "unknown").lower()
                tag_sentiments.setdefault(tag, Counter())[s] += 1

        top_keywords = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:top_n]
        keywords = [
            {
                "keyword":         kw,
                "count":           count,
                "avg_rating":      (
                    round(sum(tag_ratings[kw]) / len(tag_ratings[kw]), 2)
                    if kw in tag_ratings and tag_ratings[kw] else None
                ),
                "sentiment_split": dict(tag_sentiments.get(kw, Counter())),
            }
            for kw, count in top_keywords
        ]

        return json.dumps({
            "found": bool(keywords),
            "resolved_name": resolved_name,
            "filters": {"country": country, "sentiment": sentiment, "days": days},
            "total_reviews_with_tags": len(result_rows),
            "top_keywords": keywords,
        }, indent=2)

    @server.tool(
        name="compare_appfollow_reviews",
        description=(
            "Compare AppFollow review sentiment and ratings across multiple advertisers. "
            "Pass advertiser_names as a comma-separated list (e.g. 'Chime, Current, Dave'). "
            "Returns per-brand: total reviews, avg rating, sentiment distribution, top keywords. "
            "gap_analysis identifies which competitor has the highest rating and best sentiment. "
            "Use this to answer: 'What are competitors praised for that we are criticized for?'"
        ),
    )
    def compare_appfollow_reviews(
        advertiser_names: str,
        country: str | None = None,
        days: int = 90,
    ) -> str:
        from datetime import timedelta
        names = [n.strip() for n in advertiser_names.split(",") if n.strip()]
        if not names:
            return json.dumps({"found": False, "message": "No advertiser names provided."})

        cutoff = date.today() - timedelta(days=days)
        results: dict[str, dict] = {}

        with _session_factory()() as session:
            repo = AdvertiserRepository(session)

            for raw_name in names:
                advertiser = repo.resolve(raw_name)
                resolved = advertiser.name if advertiser else raw_name

                q = (
                    select(
                        AppFollowReviewRecord.sentiment,
                        func.count().label("count"),
                        func.avg(AppFollowReviewRecord.star_rating).label("avg_rating"),
                    )
                    .where(AppFollowReviewRecord.advertiser_name == resolved)
                    .where(AppFollowReviewRecord.review_date >= cutoff)
                )
                if country:
                    q = q.where(AppFollowReviewRecord.country == country.upper())
                q = q.group_by(AppFollowReviewRecord.sentiment)
                sentiment_rows = session.execute(q).all()

                if not sentiment_rows:
                    results[resolved] = {"found": False, "message": "No AppFollow data collected yet."}
                    continue

                tag_q = (
                    select(AppFollowReviewRecord.tags)
                    .where(AppFollowReviewRecord.advertiser_name == resolved)
                    .where(AppFollowReviewRecord.review_date >= cutoff)
                    .where(AppFollowReviewRecord.tags.isnot(None))
                )
                if country:
                    tag_q = tag_q.where(AppFollowReviewRecord.country == country.upper())
                tag_counts: dict[str, int] = {}
                for (tags,) in session.execute(tag_q).all():
                    for t in (tags or []):
                        if t:
                            tag_counts[t] = tag_counts.get(t, 0) + 1
                top_tags = [k for k, _ in sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:10]]

                sentiment_dist: dict[str, int] = {}
                total_reviews = 0
                avg_ratings: list[float] = []
                for row in sentiment_rows:
                    label = (row.sentiment or "unknown").lower()
                    sentiment_dist[label] = int(row.count)
                    total_reviews += int(row.count)
                    if row.avg_rating is not None:
                        avg_ratings.append(float(row.avg_rating))

                results[resolved] = {
                    "found":                 True,
                    "total_reviews":         total_reviews,
                    "avg_rating":            round(sum(avg_ratings) / len(avg_ratings), 2) if avg_ratings else None,
                    "sentiment_distribution": sentiment_dist,
                    "top_keywords":          top_tags,
                }

        found_names = [n for n, d in results.items() if d.get("found")]
        gap_analysis = None
        if len(found_names) >= 2:
            root = found_names[0]
            competitors = found_names[1:]
            root_data = results[root]

            def _positive_rate(d: dict) -> float:
                total = d.get("total_reviews") or 1
                return d.get("sentiment_distribution", {}).get("positive", 0) / total

            gap_analysis = {
                "root":             root,
                "competitors":      competitors,
                "rating_gaps": {
                    comp: {
                        "root_avg_rating":       root_data.get("avg_rating"),
                        "competitor_avg_rating": results[comp].get("avg_rating"),
                        "delta":                 (
                            round(
                                (results[comp].get("avg_rating") or 0)
                                - (root_data.get("avg_rating") or 0),
                                2,
                            )
                            if root_data.get("avg_rating") and results[comp].get("avg_rating")
                            else None
                        ),
                    }
                    for comp in competitors
                    if results[comp].get("found")
                },
                "positive_rate_gaps": {
                    comp: {
                        "root_positive_rate":       round(_positive_rate(root_data), 3),
                        "competitor_positive_rate": round(_positive_rate(results[comp]), 3),
                        "delta":                    round(
                            _positive_rate(results[comp]) - _positive_rate(root_data), 3
                        ),
                    }
                    for comp in competitors
                    if results[comp].get("found")
                },
                "sentiment_leader": max(
                    found_names,
                    key=lambda n: _positive_rate(results[n]),
                    default=None,
                ),
                "rating_leader": max(
                    (n for n in found_names if results[n].get("avg_rating") is not None),
                    key=lambda n: results[n].get("avg_rating", 0),
                    default=None,
                ),
            }

        return json.dumps({
            "found":        bool(found_names),
            "country":      country,
            "days":         days,
            "brands":       results,
            "gap_analysis": gap_analysis,
        }, indent=2)

    return server


def serve_stdio() -> None:
    create_mcp_server().run("stdio")


def main() -> None:
    serve_stdio()


if __name__ == "__main__":
    main()
