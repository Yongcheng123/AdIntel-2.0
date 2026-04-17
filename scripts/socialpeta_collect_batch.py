#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import logging
import random
from collections import Counter
from datetime import datetime
from pathlib import Path

import typer
from sqlalchemy import select

from adintel.core.browser import BrowserManager
from adintel.core.catalog import get_catalog_advertiser, load_catalog
from adintel.core.competitor_groups import build_competitor_run_plan, load_competitor_groups
from adintel.core.settings import get_settings
from adintel.db.models import (
    ScrapeRunRecord,
    SocialPetaCreativeChannelRecord,
    SocialPetaCreativeRecord,
    SocialPetaCreativeTagRecord,
)
from adintel.db.repositories import AdvertiserRepository, ScrapeRunRepository
from adintel.db.session import build_session_factory
from adintel.platforms.socialpeta import (
    APP_URL,
    SocialPetaCollector,
    close_context,
    ensure_display_ads_page,
    ensure_page,
    launch_context,
)

app = typer.Typer(help="Batch SocialPeta display-ads collector.")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    if not verbose:
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("playwright").setLevel(logging.WARNING)


def _session_factory():
    settings = get_settings()
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    return build_session_factory(settings)


def _get_collected_advertisers(session) -> set[str]:
    rows = session.execute(
        select(ScrapeRunRecord.advertiser_name)
        .where(ScrapeRunRecord.platform == "socialpeta")
        .where(ScrapeRunRecord.status.in_(["success", "partial"]))
        .distinct()
    ).all()
    return {row[0] for row in rows if row and row[0]}


def _resolve_query(name: str, catalog, repo: AdvertiserRepository) -> tuple[str, str, str]:
    advertiser = get_catalog_advertiser(catalog, name) or repo.get(name)
    if advertiser is None:
        return name, name, "US"
    query = advertiser.domain or advertiser.name
    country = advertiser.countries[0] if advertiser.countries else "US"
    return advertiser.name, query, country


def _score_rows(rows: list[SocialPetaCreativeRecord]) -> dict[str, object]:
    total = len(rows)
    if total == 0:
        return {
            "creatives": 0,
            "video_share": None,
            "image_share": None,
            "avg_active_days": None,
            "long_running_share": None,
            "top_type": None,
            "top_channel": None,
        }

    type_counter = Counter((row.creative_type or "unknown") for row in rows)
    channel_counter = Counter((row.primary_channel or "unknown") for row in rows)
    video_count = sum(1 for row in rows if (row.creative_type or "").casefold() == "video")
    image_count = sum(1 for row in rows if (row.creative_type or "").casefold() == "image")
    active_days = [row.active_days for row in rows if row.active_days is not None]
    long_running_count = sum(1 for row in rows if (row.active_days or 0) >= 30)

    return {
        "creatives": total,
        "video_share": round(video_count / total, 3),
        "image_share": round(image_count / total, 3),
        "avg_active_days": round(sum(active_days) / len(active_days), 1) if active_days else None,
        "long_running_share": round(long_running_count / total, 3),
        "top_type": type_counter.most_common(1)[0][0] if type_counter else None,
        "top_channel": channel_counter.most_common(1)[0][0] if channel_counter else None,
    }


def _print_group_analysis(
    session,
    root_name: str,
    root_query: str,
    member_targets: list[tuple[str, str]],
) -> dict | None:
    report = _build_group_report(session, root_name, root_query, member_targets)
    if report is None:
        return None

    if not report["found"]:
        typer.echo(f"SocialPeta analysis for {root_name}: {report['message']}")
        return report

    root_stats = report["root"]
    competitor_stats = report["competitors"]
    typer.echo(f"SocialPeta analysis for {root_name}")
    typer.echo(
        f"  coverage: {report['coverage']['brands']} brands, {report['coverage']['creatives']} creatives, "
        f"{report['coverage']['channel_rows']} channel rows"
    )
    typer.echo(
        f"  root: creatives={root_stats['creatives']} video_share={root_stats['video_share']} "
        f"image_share={root_stats['image_share']} avg_active_days={root_stats['avg_active_days']} "
        f"long_running_share={root_stats['long_running_share']} top_type={root_stats['top_type']} "
        f"top_channel={root_stats['top_channel']}"
    )
    if competitor_stats["creatives"]:
        typer.echo(
            f"  competitors: creatives={competitor_stats['creatives']} "
            f"video_share={competitor_stats['video_share']} image_share={competitor_stats['image_share']} "
            f"avg_active_days={competitor_stats['avg_active_days']} "
            f"long_running_share={competitor_stats['long_running_share']} "
            f"top_type={competitor_stats['top_type']} top_channel={competitor_stats['top_channel']}"
        )
        if root_stats["video_share"] is not None and competitor_stats["video_share"] is not None:
            gap = round(float(root_stats["video_share"]) - float(competitor_stats["video_share"]), 3)
            if gap < -0.15:
                gap_note = f"{root_name} trails competitors on video creatives by {abs(gap):.1%}."
            elif gap > 0.15:
                gap_note = f"{root_name} leads competitors on video creatives by {gap:.1%}."
            else:
                gap_note = "Video mix looks broadly aligned with competitors."
            typer.echo(f"  gap: {gap_note}")
        if root_stats["long_running_share"] is not None and competitor_stats["long_running_share"] is not None:
            duration_gap = round(
                float(root_stats["long_running_share"]) - float(competitor_stats["long_running_share"]), 3
            )
            if duration_gap < -0.15:
                typer.echo("  duration gap: root has fewer long-running creatives than competitors.")
            elif duration_gap > 0.15:
                typer.echo("  duration gap: root has more long-running creatives than competitors.")
    if report["top_channels"]:
        top_channels = ", ".join(f"{row['channel']} ({row['count']})" for row in report["top_channels"])
        typer.echo(f"  top channels: {top_channels}")
    if report["top_tags"]:
        top_tags = ", ".join(f"{row['tag']} ({row['count']})" for row in report["top_tags"])
        typer.echo(f"  tags: {top_tags}")
    return report


def _build_group_report(
    session,
    root_name: str,
    root_query: str,
    member_targets: list[tuple[str, str]],
) -> dict | None:
    if not member_targets:
        return None
    member_names = [name for name, _ in member_targets]
    member_queries = [query for _, query in member_targets]
    competitor_queries = [query for _, query in member_targets if query != root_query]

    creative_rows = session.scalars(
        select(SocialPetaCreativeRecord).where(SocialPetaCreativeRecord.target_query.in_(member_queries))
    ).all()
    if not creative_rows:
        return {
            "advertiser": root_name,
            "members": member_names,
            "found": False,
            "message": "No collected creatives yet.",
        }

    rows_by_advertiser: dict[str, list[SocialPetaCreativeRecord]] = {name: [] for name, _ in member_targets}
    query_to_name = {query: name for name, query in member_targets}
    for row in creative_rows:
        label = query_to_name.get(row.target_query or "")
        if label:
            rows_by_advertiser.setdefault(label, []).append(row)

    root_rows = [row for row in creative_rows if row.target_query == root_query]
    competitor_rows = [row for row in creative_rows if (row.target_query or "") in competitor_queries]

    root_stats = _score_rows(root_rows)
    competitor_stats = _score_rows(competitor_rows)

    channel_rows = session.scalars(
        select(SocialPetaCreativeChannelRecord).where(
            SocialPetaCreativeChannelRecord.advertiser_name.in_(member_names)
        )
    ).all()
    tag_rows = session.scalars(
        select(SocialPetaCreativeTagRecord).where(SocialPetaCreativeTagRecord.advertiser_name.in_(member_names))
    ).all()

    channel_counter = Counter(row.channel for row in channel_rows if row.channel)
    tag_counter = Counter(f"{row.tag_category}:{row.tag_value}" for row in tag_rows if row.tag_value)

    return {
        "advertiser": root_name,
        "members": member_names,
        "found": True,
        "coverage": {
            "brands": len(rows_by_advertiser),
            "creatives": len(creative_rows),
            "channel_rows": len(channel_rows),
        },
        "root": root_stats,
        "competitors": competitor_stats,
        "gap": {
            "video_share": (
                round(float(root_stats["video_share"]) - float(competitor_stats["video_share"]), 3)
                if root_stats["video_share"] is not None and competitor_stats["video_share"] is not None
                else None
            ),
            "long_running_share": (
                round(float(root_stats["long_running_share"]) - float(competitor_stats["long_running_share"]), 3)
                if root_stats["long_running_share"] is not None and competitor_stats["long_running_share"] is not None
                else None
            ),
        },
        "top_channels": [
            {"channel": name, "count": count}
            for name, count in channel_counter.most_common(3)
        ],
        "top_tags": [
            {"tag": name, "count": count}
            for name, count in tag_counter.most_common(5)
        ],
    }


@app.command()
def main(
    advertiser_name: str | None = typer.Option(None, "--advertiser-name", help="Run one advertiser and its competitor set."),
    config_file: Path | None = typer.Option(None, "--config-file", help="Advertiser catalog file. Defaults to settings config."),
    group_config_file: Path | None = typer.Option(None, "--group-config-file", help="Competitor group config file."),
    report_output_file: Path | None = typer.Option(
        None,
        "--report-output-file",
        help="Write a JSON report for the run to this file. Defaults to state/reports/socialpeta-<timestamp>.json.",
    ),
    pages: int = typer.Option(3, min=1, max=20, help="Number of SocialPeta result pages to fetch per query."),
    mode: str = typer.Option("missing", "--mode", help="Run mode: 'missing' (default) or 'all'."),
    headless: bool = typer.Option(False, help="Run browser headlessly."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging."),
) -> None:
    _setup_logging(verbose)
    settings = get_settings()
    advertiser_config = config_file or settings.config_file
    group_config = group_config_file or settings.socialpeta_group_config_file

    catalog = load_catalog(advertiser_config)
    groups = load_competitor_groups(group_config)

    with _session_factory()() as session:
        advertiser_repo = AdvertiserRepository(session)
        run_repo = ScrapeRunRepository(session)
        collector = SocialPetaCollector(settings=settings, browser=BrowserManager(settings), session=session)
        mode = mode.lower().strip()
        if mode not in {"missing", "all"}:
            typer.echo("Invalid --mode. Use 'missing' or 'all'.", err=True)
            raise typer.Exit(code=2)
        collected = _get_collected_advertisers(session) if mode == "missing" and advertiser_name is None else set()

        roots = [advertiser_name] if advertiser_name else [advertiser.name for advertiser in catalog.advertisers]
        ordered_targets: list[tuple[str, str, str, bool]] = []
        seen_queries: set[tuple[str, str]] = set()
        run_plans: list[tuple[str, list[str]]] = []
        analysis_plans: list[tuple[str, str, list[tuple[str, str]]]] = []

        for root_name in roots:
            canonical_name, query, country = _resolve_query(root_name, catalog, advertiser_repo)
            if mode == "missing" and advertiser_name is None and canonical_name in collected:
                continue
            plan = build_competitor_run_plan(groups, canonical_name)
            run_plans.append((canonical_name, list(plan.competitors)))
            member_targets: list[tuple[str, str]] = [(canonical_name, query)]
            candidates = [(canonical_name, query, plan.country or country, False)]
            for competitor_name in plan.competitors:
                comp_name, comp_query, comp_country = _resolve_query(competitor_name, catalog, advertiser_repo)
                candidates.append((comp_name, comp_query, plan.country or comp_country, True))
                member_targets.append((comp_name, comp_query))
            analysis_plans.append((canonical_name, query, member_targets))
            for item in candidates:
                key = (item[1], item[2])
                if key in seen_queries:
                    continue
                seen_queries.add(key)
                ordered_targets.append(item)

        if not ordered_targets:
            if mode == "missing" and advertiser_name is None:
                typer.echo("All configured advertisers already have SocialPeta success runs. Nothing to do.")
                raise typer.Exit(code=0)
            typer.echo("No SocialPeta targets resolved from config.")
            raise typer.Exit(code=1)

        typer.echo(f"Resolved SocialPeta run plan (mode={mode}):")
        for advertiser_label, competitors in run_plans:
            if competitors:
                typer.echo(f"  - {advertiser_label}: {len(competitors)} competitors -> {', '.join(competitors)}")
            else:
                typer.echo(f"  - {advertiser_label}: no competitors configured")

        report_path = report_output_file
        if report_path is None:
            report_dir = settings.state_dir / "reports"
            report_dir.mkdir(parents=True, exist_ok=True)
            report_path = report_dir / f"socialpeta-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        else:
            report_path.parent.mkdir(parents=True, exist_ok=True)
        typer.echo(f"SocialPeta report will be written to: {report_path}")

        run_report: dict = {
            "generated_at": datetime.now().isoformat(),
            "pages": pages,
            "headless": headless,
            "targets": [],
            "analysis": [],
        }

        async def _run() -> None:
            typer.echo("Starting SocialPeta browser session...")
            playwright, context = await launch_context(headless=headless)
            try:
                page = await ensure_page(context)
                typer.echo(f"SocialPeta browser ready on page: {page.url or '(new page)'}")
                if APP_URL not in page.url:
                    typer.echo("Navigating to SocialPeta display-ads page...")
                    await ensure_display_ads_page(page, timeout_ms=60_000)
                    typer.echo(f"SocialPeta page loaded: {page.url}")
                if "login" in page.url or "sign" in page.url:
                    raise RuntimeError("SocialPeta session has expired. Run `adintel login socialpeta`.")

                total_targets = len(ordered_targets)
                for index, (canonical_name, query, country, is_competitor) in enumerate(ordered_targets, start=1):
                    role = "competitor" if is_competitor else "root"
                    typer.echo(
                        f"[{index}/{total_targets}] Collecting SocialPeta {role}: "
                        f"{canonical_name} -> query={query} country={country}"
                    )
                    run = run_repo.start(canonical_name, "socialpeta")
                    try:
                        typer.echo(f"  fetching {role} creatives...")
                        outcome = await collector.collect_query(
                            page,
                            advertiser_name=canonical_name,
                            target_query=query,
                            country=country.upper(),
                            pages=pages,
                        )
                        status = "success" if outcome["records_written"] else "empty"
                        run_repo.finish(
                            run,
                            status=status,
                            message=f"Collected {outcome['records_written']} SocialPeta creative rows.",
                            metadata=outcome["metadata"],
                        )
                        typer.echo(
                            f"  {status}: {role} creatives={outcome['records_written']} "
                            f"channels={outcome['metadata']['channels_captured']}"
                        )
                        run_report["targets"].append(
                            {
                                "advertiser_name": canonical_name,
                                "query": query,
                                "country": country,
                                "role": role,
                                "status": status,
                                "records_written": outcome["records_written"],
                                "metadata": outcome["metadata"],
                            }
                        )
                    except Exception as exc:
                        run_repo.finish(run, status="error", message=str(exc))
                        typer.echo(f"  error: {exc}")
                        run_report["targets"].append(
                            {
                                "advertiser_name": canonical_name,
                                "query": query,
                                "country": country,
                                "role": role,
                                "status": "error",
                                "error": str(exc),
                            }
                        )
                    if index < total_targets and settings.socialpeta_jitter_enabled:
                        lo = settings.socialpeta_target_jitter_min_s
                        hi = settings.socialpeta_target_jitter_max_s
                        if hi < lo:
                            lo, hi = hi, lo
                        delay = random.uniform(lo, hi)
                        typer.echo(f"  waiting {delay:.2f}s jitter before next target...")
                        await asyncio.sleep(delay)

                typer.echo()
                typer.echo("SocialPeta analysis summary:")
                for root_name, root_query, member_targets in analysis_plans:
                    analysis = _print_group_analysis(session, root_name, root_query, member_targets)
                    if analysis is not None:
                        run_report["analysis"].append(analysis)

                report_path.write_text(json.dumps(run_report, indent=2), encoding="utf-8")
                typer.echo(f"\nSaved SocialPeta JSON report to: {report_path}")
            finally:
                await close_context(playwright, context)

        asyncio.run(_run())


if __name__ == "__main__":
    app()
