from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import typer
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from adintel.platforms.otterlyai import (
    OUTPUT_DIR,
    collect_batch,
    export_citation_rows,
    export_prompt_rows,
    list_reports_payload,
    load_targets,
)
from adintel.platforms.otterlyai_parsers import normalize_engine_label, normalize_engine_service_key


app = typer.Typer(
    help="Standalone Otterly brand reports helper that exports only a lean, MCP-friendly schema."
)


@app.command("list-reports")
def list_reports(
    output: Path | None = typer.Option(None, help="Optional path to save the raw brand reports JSON."),
) -> None:
    """Fetch Otterly brand reports metadata."""

    payload = asyncio.run(list_reports_payload(headless=True))
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        typer.echo(f"Saved raw brand reports JSON to {output}")
    typer.echo(json.dumps(payload, indent=2))


@app.command("export-prompts")
def export_prompts(
    report_id: str = typer.Argument(..., help="Otterly brand report id."),
    country: str = typer.Option(..., "--country", help="Two-letter country code, such as us."),
    start_date: str = typer.Option(..., "--start-date", help="Start date in YYYY-MM-DD format."),
    end_date: str = typer.Option(..., "--end-date", help="End date in YYYY-MM-DD format."),
    service: str | None = typer.Option(
        None,
        "--service",
        help="Optional engine filter, such as chatgpt or perplexity.",
    ),
    output: Path | None = typer.Option(None, help="Optional output path for the refined JSON rows."),
) -> None:
    """Export only the lean prompt fields needed for Postgres/MCP storage."""

    service_key = normalize_engine_service_key(service)
    _, rows = asyncio.run(
        export_prompt_rows(
            report_id=report_id,
            country=country,
            start_date=start_date,
            end_date=end_date,
            service=service_key,
            headless=True,
        )
    )

    service_suffix = f".{service_key}" if service_key else ""
    target = output or (OUTPUT_DIR / f"{report_id}.{country.lower()}.{end_date}{service_suffix}.refined.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    typer.echo(json.dumps(rows, indent=2))
    typer.echo(f"Saved refined prompt JSON to {target}")


@app.command("export-citations")
def export_citations(
    report_id: str = typer.Argument(..., help="Otterly brand report id."),
    country: str = typer.Option(..., "--country", help="Two-letter country code, such as us."),
    start_date: str = typer.Option(..., "--start-date", help="Start date in YYYY-MM-DD format."),
    end_date: str = typer.Option(..., "--end-date", help="End date in YYYY-MM-DD format."),
    service: str | None = typer.Option(
        None,
        "--service",
        help="Optional engine filter, such as chatgpt or perplexity.",
    ),
    page_size: int = typer.Option(100, "--page-size", help="Number of citations to fetch."),
    output: Path | None = typer.Option(None, help="Optional output path for the refined citation JSON rows."),
) -> None:
    """Export lean citation rows from Otterly brand reports."""

    service_key = normalize_engine_service_key(service)
    _, rows = asyncio.run(
        export_citation_rows(
            report_id=report_id,
            country=country,
            start_date=start_date,
            end_date=end_date,
            service=service_key,
            page_size=page_size,
            headless=True,
        )
    )

    service_suffix = f".{service_key}" if service_key else ""
    target = output or (OUTPUT_DIR / f"{report_id}.{country.lower()}.{end_date}{service_suffix}.citations.refined.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    typer.echo(json.dumps(rows, indent=2))
    typer.echo(f"Saved refined citation JSON to {target}")


@app.command("batch-collect")
def batch_collect(
    start_date: str | None = typer.Option(None, "--start-date", help="Start date in YYYY-MM-DD format."),
    end_date: str | None = typer.Option(None, "--end-date", help="End date in YYYY-MM-DD format."),
    services: str | None = typer.Option(None, "--services", help="Comma-separated engines, such as chatgpt,perplexity."),
    config_file: Path | None = typer.Option(None, "--config-file", help="YAML/JSON config file for dates, services, and targets."),
    targets_file: Path | None = typer.Option(None, "--targets-file", help="JSON file containing target objects."),
    targets_json: str | None = typer.Option(None, "--targets-json", help="Inline JSON array of targets."),
    page_size: int = typer.Option(100, "--page-size", help="Number of citations to fetch per target."),
    write_db: bool = typer.Option(True, "--write-db/--no-write-db", help="Upsert Otterly rows into Postgres."),
    save_files: bool = typer.Option(False, "--save-files/--no-save-files", help="Also save refined JSON files under output/otterly."),
) -> None:
    """Collect prompts and citations for multiple targets and optionally upsert into Postgres."""

    config_payload: dict[str, Any] = {}
    if config_file is not None:
        raw_text = config_file.read_text(encoding="utf-8")
        if config_file.suffix.lower() in {".yaml", ".yml"}:
            config_payload = yaml.safe_load(raw_text) or {}
        else:
            config_payload = json.loads(raw_text)

    if start_date is None:
        start_date = config_payload.get("start_date")
    if end_date is None:
        end_date = config_payload.get("end_date")
    if services is None:
        config_services = config_payload.get("services")
        if isinstance(config_services, list):
            services = ",".join(str(value) for value in config_services)
        elif isinstance(config_services, str):
            services = config_services

    sleep_range_seconds: tuple[float, float] | None = None
    raw_sleep_range = config_payload.get("sleep_range_seconds")
    if isinstance(raw_sleep_range, list) and len(raw_sleep_range) == 2:
        try:
            min_seconds = float(raw_sleep_range[0])
            max_seconds = float(raw_sleep_range[1])
        except (TypeError, ValueError) as exc:
            raise typer.BadParameter("`sleep_range_seconds` must contain two numeric values.") from exc
        if min_seconds < 0 or max_seconds < 0 or min_seconds > max_seconds:
            raise typer.BadParameter("`sleep_range_seconds` must be non-negative and ordered as [min, max].")
        sleep_range_seconds = (min_seconds, max_seconds)

    if not isinstance(start_date, str) or not isinstance(end_date, str):
        raise typer.BadParameter("Provide start and end dates via flags or --config-file.")

    if config_file is not None and targets_file is None and targets_json is None:
        try:
            targets = load_targets(config_file, None)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
    else:
        try:
            targets = load_targets(targets_file, targets_json)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc

    service_values = [
        normalize_engine_service_key(value)
        for value in (services or "ChatGPT").split(",")
        if value.strip()
    ]
    summaries = asyncio.run(
        collect_batch(
            targets=targets,
            start_date=start_date,
            end_date=end_date,
            services=service_values,
            page_size=page_size,
            save_files=save_files,
            write_db=write_db,
            headless=True,
            sleep_range_seconds=sleep_range_seconds,
        )
    )
    typer.echo(json.dumps(summaries, indent=2))


@app.command("create-report")
def create_report_cmd(
    domain: str = typer.Argument(..., help="Brand domain, e.g. current.com"),
    brand_name: str | None = typer.Option(None, "--brand-name", help="Display name; defaults to domain"),
    no_headless: bool = typer.Option(False, "--no-headless", help="Show browser window"),
) -> None:
    """Create a brand report in Otterly for the given domain."""
    from adintel.platforms.otterlyai import create_report

    name = brand_name or domain
    typer.echo(f"Creating Otterly report for {domain} ...")
    report_id = asyncio.run(create_report(name, domain, headless=not no_headless))
    typer.echo(f"Created report: {report_id}")


if __name__ == "__main__":
    app()
