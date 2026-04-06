from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

import typer

from adintel.collectors.service import CollectorService
from adintel.core.catalog import get_catalog_advertiser, load_catalog
from adintel.core.models import AdvertiserProfile, PlatformName
from adintel.core.settings import get_settings
from adintel.db.repositories import AdvertiserRepository, CollectionHealthRepository
from adintel.db.session import build_session_factory, init_db
from adintel.mcp.server import serve_stdio
from adintel.onboarding import load_onboarding_requests, onboard_batch_sync, save_catalog, upsert_catalog_advertiser


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet noisy third-party loggers unless debug
    if not verbose:
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("playwright").setLevel(logging.WARNING)


app = typer.Typer(help="AdIntel operator CLI")
advertisers_app = typer.Typer(help="Manage advertiser profiles")
catalog_app = typer.Typer(help="Work with YAML catalogs")
collect_app = typer.Typer(help="Run collection workflows")

app.add_typer(advertisers_app, name="advertisers")
app.add_typer(catalog_app, name="catalog")
app.add_typer(collect_app, name="collect")


def _session_factory():
    settings = get_settings()
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    return build_session_factory(settings)


def _platforms(value: str) -> list[PlatformName]:
    if value == "all":
        return [PlatformName.ADCLARITY, PlatformName.SENSORTOWER]
    return [PlatformName(value)]


@app.command("init-db")
def init_db_command() -> None:
    settings = get_settings()
    init_db(settings)
    typer.echo("Database schema initialized.")
    typer.echo(f"Canonical SQL schema: {Path('sql/schema.sql')}")


@catalog_app.command("validate")
def validate_catalog(
    path: Path | None = typer.Option(None, help="Path to the advertiser catalog file."),
) -> None:
    settings = get_settings()
    target = path or settings.config_file
    catalog = load_catalog(target)
    typer.echo(f"Catalog valid: {len(catalog.advertisers)} advertisers loaded from {target}.")


@catalog_app.command("bootstrap")
def bootstrap_catalog(
    force: bool = typer.Option(False, help="Overwrite an existing catalog."),
) -> None:
    settings = get_settings()
    target = settings.config_file
    source = Path("config/advertisers.example.yaml")

    if target.exists() and not force:
        raise typer.BadParameter(f"{target} already exists. Use --force to overwrite it.")

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    typer.echo(f"Catalog bootstrapped to {target}.")


@advertisers_app.command("list")
def list_advertisers() -> None:
    with _session_factory()() as session:
        repo = AdvertiserRepository(session)
        advertisers = repo.list()

    if not advertisers:
        typer.echo("No advertisers found.")
        return

    for advertiser in advertisers:
        typer.echo(
            f"{advertiser.name} | {advertiser.category or '-'} | {','.join(advertiser.countries)}"
        )


@advertisers_app.command("sync-catalog")
def sync_catalog() -> None:
    settings = get_settings()
    catalog = load_catalog(settings.config_file)

    with _session_factory()() as session:
        count = AdvertiserRepository(session).sync(catalog.advertisers)

    typer.echo(f"Synchronized {count} advertisers from {settings.config_file}.")


@advertisers_app.command("onboard-batch")
def onboard_batch(
    input_file: Path = typer.Option(..., "--input", help="YAML file containing advertiser names and countries."),
    headless: bool = typer.Option(True, help="Run browser headlessly."),
    use_cdp: bool = typer.Option(False, help="Connect to an existing browser over CDP."),
    write: bool = typer.Option(True, help="Write matched advertisers into the configured catalog file."),
) -> None:
    settings = get_settings()
    requests = load_onboarding_requests(input_file)
    results = onboard_batch_sync(settings, requests, headless=headless, use_cdp=use_cdp)

    catalog = load_catalog(settings.config_file)
    written = 0
    for result in results:
        if result.status == "matched" and result.advertiser is not None:
            typer.echo(f"{result.name}: matched | {result.message}")
            typer.echo(
                f"  uai={result.advertiser.platforms.sensortower.unified_app_id} "
                f"publisher_id={result.advertiser.platforms.sensortower.publisher_id} "
                f"ios_app_id={result.advertiser.platforms.sensortower.ios_app_id} "
                f"android_package={result.advertiser.platforms.sensortower.android_package}"
            )
            if write:
                upsert_catalog_advertiser(catalog, result.advertiser)
                written += 1
        elif result.status == "ambiguous":
            typer.echo(f"{result.name}: ambiguous | {result.message}")
            for candidate in result.candidates or []:
                typer.echo(f"  candidate: {candidate['name']} | publisher={candidate['publisher_name']} | app_id={candidate['app_id']}")
        else:
            typer.echo(f"{result.name}: {result.status} | {result.message}")

    if write and written:
        save_catalog(settings.config_file, catalog)
        typer.echo(f"Wrote {written} matched advertiser(s) to {settings.config_file}.")


@advertisers_app.command("upsert")
def upsert_advertiser(
    name: str = typer.Option(..., help="Advertiser name."),
    domain: str | None = typer.Option(None, help="Company domain."),
    category: str | None = typer.Option(None, help="Category label."),
    countries: str = typer.Option("US", help="Comma-separated country codes."),
    adclarity_advertiser_id: str | None = typer.Option(None, help="AdClarity advertiser ID."),
    adclarity_brand_id: str | None = typer.Option(None, help="AdClarity brand ID."),
    sensortower_unified_app_id: str | None = typer.Option(None, help="SensorTower unified app ID."),
    sensortower_publisher_id: str | None = typer.Option(None, help="SensorTower publisher ID."),
    sensortower_ios_app_id: str | None = typer.Option(None, help="SensorTower iOS app ID."),
    sensortower_android_package: str | None = typer.Option(None, help="SensorTower Android package."),
) -> None:
    advertiser = AdvertiserProfile.model_validate(
        {
            "name": name,
            "domain": domain,
            "category": category,
            "countries": [country.strip().upper() for country in countries.split(",") if country.strip()],
            "platforms": {
                "adclarity": {
                    "advertiser_id": adclarity_advertiser_id,
                    "brand_id": adclarity_brand_id,
                },
                "sensortower": {
                    "unified_app_id": sensortower_unified_app_id,
                    "publisher_id": sensortower_publisher_id,
                    "ios_app_id": sensortower_ios_app_id,
                    "android_package": sensortower_android_package,
                },
            },
        }
    )

    with _session_factory()() as session:
        stored = AdvertiserRepository(session).upsert(advertiser)

    typer.echo(f"Upserted advertiser {stored.name}.")


@app.command("login")
def login_command(
    platform: PlatformName,
    headless: bool = typer.Option(False, help="Launch a headless browser session."),
    use_cdp: bool = typer.Option(False, help="Connect to an existing browser over CDP."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging (DEBUG level)."),
) -> None:
    _setup_logging(verbose)
    with _session_factory()() as session:
        service = CollectorService(get_settings(), session)
        asyncio.run(service.login(platform, headless=headless, use_cdp=use_cdp))


@app.command("mcp")
def mcp_command() -> None:
    serve_stdio()


@collect_app.command("advertiser")
def collect_advertiser(
    advertiser_name: str,
    platform: str = typer.Option("all", help="Platform name or 'all'."),
    countries: str | None = typer.Option(None, help="Comma-separated country codes."),
    metrics: str | None = typer.Option(
        None,
        help=(
            "Comma-separated metric names to collect. "
            "For SensorTower: downloads, retention, impression_share, demographics, "
            "engagement, reviews, rankings, creatives, aso_keywords."
        ),
    ),
    headless: bool = typer.Option(False, help="Run browser headlessly."),
    debug: bool = typer.Option(False, help="Enable debug mode."),
    use_cdp: bool = typer.Option(False, help="Connect to an existing browser over CDP."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging (DEBUG level)."),
) -> None:
    _setup_logging(verbose)
    settings = get_settings()
    catalog = load_catalog(settings.config_file)
    with _session_factory()() as session:
        advertisers = AdvertiserRepository(session)
        advertiser = get_catalog_advertiser(catalog, advertiser_name) or advertisers.get(advertiser_name)
        if advertiser is None:
            raise typer.BadParameter(
                f'Advertiser "{advertiser_name}" is not in the database. Run "adintel advertisers sync-catalog" or "adintel advertisers upsert".'
            )

        service = CollectorService(settings, session)
        results = asyncio.run(
            service.collect_many(
                advertiser,
                platforms=_platforms(platform),
                countries=[c.strip().upper() for c in countries.split(",")] if countries else None,
                metrics=[m.strip().lower() for m in metrics.split(",") if m.strip()] if metrics else None,
                headless=headless,
                debug=debug,
                use_cdp=use_cdp,
            )
        )

    for result in results:
        typer.echo(f"{result.platform}: {result.status} | {result.message}")


@collect_app.command("stale")
def collect_stale(
    platform: str = typer.Option("sensortower", help="Platform name."),
    stale_hours: float = typer.Option(48, help="Hours since last success to consider stale."),
    headless: bool = typer.Option(False, help="Run browser headlessly."),
    debug: bool = typer.Option(False, help="Enable debug mode."),
    use_cdp: bool = typer.Option(False, help="Connect to an existing browser over CDP."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging (DEBUG level)."),
) -> None:
    """Collect data for all advertisers whose data is stale or has never been collected."""
    _setup_logging(verbose)
    settings = get_settings()
    catalog = load_catalog(settings.config_file)
    with _session_factory()() as session:
        health_repo = CollectionHealthRepository(session)
        stale_names = health_repo.get_stale_advertisers(platform, stale_hours=stale_hours)

        if not stale_names:
            typer.echo(f"All advertisers are fresh (within {stale_hours}h).")
            return

        typer.echo(f"Found {len(stale_names)} stale advertiser(s): {', '.join(stale_names)}")
        advertisers_repo = AdvertiserRepository(session)
        service = CollectorService(settings, session)

        async def _run_stale() -> None:
            for name in stale_names:
                advertiser = get_catalog_advertiser(catalog, name) or advertisers_repo.get(name)
                if advertiser is None:
                    typer.echo(f"  {name}: skipped (not in database)")
                    continue

                typer.echo(f"  Collecting {name}...")
                try:
                    results = await service.collect_many(
                        advertiser,
                        platforms=[PlatformName(platform)],
                        headless=headless,
                        debug=debug,
                        use_cdp=use_cdp,
                    )
                    for result in results:
                        typer.echo(f"    {result.platform}: {result.status} | {result.message}")
                except Exception as exc:
                    typer.echo(f"    {name}: error — {exc}")

        asyncio.run(_run_stale())
