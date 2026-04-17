#!/usr/bin/env python3
"""
Batch AppFollow review collector.

Reads config/appfollow_groups.yaml, iterates all groups (primary advertiser +
each competitor), and collects app reviews from AppFollow's web UI using
Playwright browser automation.

Reviews are stored in the appfollow_reviews table and queryable via MCP tools:
  get_appfollow_reviews, get_appfollow_sentiment_trend,
  get_appfollow_keyword_analysis, compare_appfollow_reviews.

Usage:
  python scripts/appfollow_collect_batch.py [OPTIONS]

Before running, make sure you have:
  1. Filled in config/appfollow_groups.yaml with your workspace slug and item IDs.
     (Find itemId values in the AppFollow URL: ?itemId=XXXXX)
  2. Logged in: adintel login appfollow
  3. Applied the schema: psql $DATABASE_URL -f sql/migrations/20260416_add_appfollow_tables.sql
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

import typer
import yaml
from playwright.async_api import async_playwright

from adintel.core.browser import BrowserManager
from adintel.core.settings import get_settings
from adintel.db.repositories import ScrapeRunRepository
from adintel.db.session import build_session_factory
from adintel.platforms.appfollow import AppFollowCollector

app = typer.Typer(help="Batch AppFollow review collector.")


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


def _load_groups(config_path: Path) -> tuple[str, list[dict]]:
    """
    Load appfollow_groups.yaml and return (workspace, flat list of app entries).

    Each entry: {"name": str, "item_id": str, "countries": [...], "role": "root"|"competitor"}
    Both the primary advertiser and each competitor are included.
    """
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    workspace = (raw.get("workspace") or "").strip()
    groups = raw.get("groups") or []

    entries: list[dict] = []
    seen_item_ids: set[str] = set()

    for group in groups:
        countries = group.get("countries") or ["US"]
        # Primary advertiser
        name = (group.get("advertiser") or "").strip()
        item_id = str(group.get("appfollow_item_id") or "").strip()
        if name and item_id and item_id not in seen_item_ids and "XXXXX" not in item_id:
            entries.append({
                "name": name,
                "item_id": item_id,
                "countries": countries,
                "role": "root",
            })
            seen_item_ids.add(item_id)

        # Competitors
        for comp in group.get("competitors") or []:
            comp_name = (comp.get("name") or "").strip()
            comp_item_id = str(comp.get("appfollow_item_id") or "").strip()
            if comp_name and comp_item_id and comp_item_id not in seen_item_ids and "YYYYY" not in comp_item_id and "ZZZZZ" not in comp_item_id:
                entries.append({
                    "name": comp_name,
                    "item_id": comp_item_id,
                    "countries": countries,
                    "role": "competitor",
                })
                seen_item_ids.add(comp_item_id)

    return workspace, entries


@app.command()
def main(
    advertiser_name: str | None = typer.Option(
        None, "--advertiser", help="Collect only this advertiser (and its competitors). Defaults to all groups."
    ),
    limit: int | None = typer.Option(
        None, "--limit", help="Collect only the first N advertiser groups (useful for batch processing)."
    ),
    group_config_file: Path | None = typer.Option(
        None, "--config-file", help="AppFollow groups config file. Defaults to config/appfollow_groups.yaml."
    ),
    report_output_file: Path | None = typer.Option(
        None, "--report-output-file", help="Write JSON report to this file."
    ),
    headless: bool = typer.Option(True, help="Run browser headlessly."),
    debug: bool = typer.Option(False, "--debug", help="Write API debug dumps to state/debug/appfollow/."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging."),
) -> None:
    _setup_logging(verbose)
    settings = get_settings()
    config_path = group_config_file or settings.appfollow_group_config_file

    if not config_path.exists():
        typer.echo(f"ERROR: AppFollow config not found: {config_path}", err=True)
        typer.echo("Create it from the example: cp config/appfollow_groups.example.yaml config/appfollow_groups.yaml", err=True)
        raise typer.Exit(code=1)

    workspace, all_entries = _load_groups(config_path)

    # Workspace from config takes priority, then settings, then env
    if not workspace:
        workspace = settings.appfollow_workspace
    if not workspace:
        typer.echo("ERROR: AppFollow workspace is not set.", err=True)
        typer.echo("Add 'workspace: your-slug' to config/appfollow_groups.yaml or set ADINTEL_APPFOLLOW_WORKSPACE=.", err=True)
        raise typer.Exit(code=1)

    if not all_entries:
        typer.echo("No AppFollow entries found in config (check that item IDs are filled in).")
        raise typer.Exit(code=1)

    # Filter to a single advertiser if requested
    if advertiser_name:
        entries = [e for e in all_entries if e["name"].lower() == advertiser_name.lower()]
        if not entries:
            typer.echo(f"Advertiser '{advertiser_name}' not found in AppFollow config.", err=True)
            raise typer.Exit(code=1)
    else:
        entries = all_entries

    typer.echo(f"AppFollow workspace: {workspace}")
    typer.echo(f"Collecting {len(entries)} apps:")
    for e in entries:
        typer.echo(f"  [{e['role']}] {e['name']} (itemId={e['item_id']}, countries={','.join(e['countries'])})")

    session_factory = build_session_factory(settings)
    settings.state_dir.mkdir(parents=True, exist_ok=True)

    report_path = report_output_file
    if report_path is None:
        report_dir = settings.state_dir / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"appfollow-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    else:
        report_path.parent.mkdir(parents=True, exist_ok=True)
    typer.echo(f"Report will be written to: {report_path}")

    run_report: dict = {
        "generated_at": datetime.now().isoformat(),
        "workspace": workspace,
        "headless": headless,
        "debug": debug,
        "targets": [],
    }

    async def _run() -> None:
        typer.echo("Starting AppFollow browser session...")
        state_dir = settings.browser_state_dir / "appfollow"
        state_dir.mkdir(parents=True, exist_ok=True)

        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(
                str(state_dir),
                channel=settings.browser_channel,
                headless=headless,
                viewport={"width": 1440, "height": 900},
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
            )
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                browser_manager = BrowserManager(settings)
                await browser_manager.apply_stealth(page)

                # Validate session once before starting
                typer.echo("Validating AppFollow session...")
                collector_check = AppFollowCollector(settings, browser_manager, None)
                session_ok = await collector_check._validate_session(page, workspace)
                if not session_ok:
                    typer.echo("ERROR: AppFollow session has expired. Run: adintel login appfollow", err=True)
                    raise typer.Exit(code=1)
                typer.echo(f"AppFollow session valid. Starting collection of {len(entries)} apps...")

                total_index = len(entries)
                for idx, entry in enumerate(entries, start=1):
                    name = entry["name"]
                    item_id = entry["item_id"]
                    countries = entry["countries"]
                    role = entry["role"]

                    typer.echo(f"\n[{idx}/{total_index}] Collecting AppFollow {role}: {name} (itemId={item_id})")

                    with session_factory() as session:
                        collector = AppFollowCollector(settings, browser_manager, session)
                        try:
                            result = await collector.collect_app(
                                page=page,
                                advertiser_name=name,
                                item_id=item_id,
                                workspace=workspace,
                                countries=countries,
                                headless=headless,
                                debug=debug,
                            )
                            typer.echo(f"  {result['status']}: {result['message']}")
                            run_report["targets"].append({
                                "advertiser_name": name,
                                "item_id": item_id,
                                "role": role,
                                "countries": countries,
                                "status": result["status"],
                                "records_written": result["records_written"],
                                "message": result["message"],
                            })
                        except Exception as exc:
                            typer.echo(f"  ERROR: {exc}", err=True)
                            run_report["targets"].append({
                                "advertiser_name": name,
                                "item_id": item_id,
                                "role": role,
                                "countries": countries,
                                "status": "error",
                                "error": str(exc),
                            })
            finally:
                await context.close()

        report_path.write_text(json.dumps(run_report, indent=2, ensure_ascii=False), encoding="utf-8")
        typer.echo(f"\nAppFollow report saved to: {report_path}")

        # Summary
        success_count = sum(1 for t in run_report["targets"] if t.get("status") == "success")
        empty_count = sum(1 for t in run_report["targets"] if t.get("status") == "empty")
        error_count = sum(1 for t in run_report["targets"] if t.get("status") == "error")
        total_rows = sum(t.get("records_written", 0) for t in run_report["targets"])
        typer.echo(f"\nSummary: {success_count} success, {empty_count} empty, {error_count} error | {total_rows} total rows")

        if debug:
            debug_dir = settings.debug_dir / "appfollow"
            typer.echo(f"\nDebug dumps written to: {debug_dir}")
            typer.echo("Inspect the JSON files there to see captured API URLs and response shapes.")
            typer.echo("Then update src/adintel/platforms/appfollow_parsers.py to match the real field names.")

    asyncio.run(_run())


if __name__ == "__main__":
    app()
