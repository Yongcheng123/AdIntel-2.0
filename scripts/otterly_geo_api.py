from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import typer
from playwright.async_api import BrowserContext, Page, async_playwright


app = typer.Typer(help="Standalone Otterly GEO audit helper. Keeps AdIntel's existing functions unchanged.")

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "state" / "browser" / "otterly"
OUTPUT_DIR = ROOT / "output" / "otterly"
APP_URL = "https://app.otterly.ai"
LIST_AUDITS_URL = "https://api.otterly.ai/audits/geo/url?version=2"
AUDIT_URL_TEMPLATE = "https://api.otterly.ai/audits/geo/url/{audit_id}"


async def launch_context(*, headless: bool) -> tuple[Any, BrowserContext]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    playwright = await async_playwright().start()
    context = await playwright.chromium.launch_persistent_context(
        str(STATE_DIR),
        channel="chromium",
        headless=headless,
        viewport={"width": 1440, "height": 900},
        args=["--disable-dev-shm-usage"],
    )
    return playwright, context


async def close_context(playwright: Any, context: BrowserContext) -> None:
    await context.close()
    await playwright.stop()


async def ensure_page(context: BrowserContext) -> Page:
    return context.pages[0] if context.pages else await context.new_page()


async def get_session_token(context: BrowserContext) -> str:
    for cookie in await context.cookies():
        if cookie.get("name") == "__session" and cookie.get("domain") == "app.otterly.ai":
            value = cookie.get("value")
            if isinstance(value, str) and value:
                return value
    raise RuntimeError("Could not find Otterly __session token in the saved browser profile. Re-run `login`.")


def normalize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: normalize_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_json(v) for v in value]
    return value


def find_matching_audits(payload: Any, url: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            node_url = node.get("url") or node.get("target_url") or node.get("page_url")
            if isinstance(node_url, str) and node_url.rstrip("/") == url.rstrip("/"):
                matches.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return matches


def summarize_audit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    structural = payload.get("structuralAnalysis") or {}
    dynamic = payload.get("dynamicContent") or {}
    content = payload.get("contentAnalysis") or {}
    analysis = content.get("analysis") or {}

    content_scores = {
        key: value.get("score")
        for key, value in analysis.items()
        if isinstance(value, dict) and isinstance(value.get("score"), (int, float))
    }
    weakest_dimensions = [
        {"name": name, "score": score}
        for name, score in sorted(content_scores.items(), key=lambda item: item[1])[:5]
    ]

    useful_for = [
        "content quality benchmarking",
        "AI/crawlability QA",
        "page structure monitoring",
    ]
    not_useful_for = [
        "DMA or regional geo targeting",
        "country or market segmentation",
        "publisher or campaign analysis",
    ]

    return {
        "url": payload.get("link"),
        "status": payload.get("status"),
        "created_at": payload.get("createdAt"),
        "structural_overall_score": structural.get("overallScore"),
        "structural_category_scores": structural.get("categoryScores"),
        "dynamic_content_score": dynamic.get("score"),
        "dynamic_content_match": dynamic.get("differenceDescription"),
        "dynamic_content_lengths": {
            "dynamic": dynamic.get("dynamicLength"),
            "static": dynamic.get("staticLength"),
        },
        "content_overall_score": content.get("overallScore"),
        "weakest_content_dimensions": weakest_dimensions,
        "geo_signal_present": False,
        "geo_signal_note": "No country, region, city, locale, market, or DMA fields were found in this audit payload.",
        "useful_for": useful_for,
        "not_useful_for": not_useful_for,
    }


@app.command()
def login(
    headless: bool = typer.Option(False, help="Open the login browser headlessly. Usually keep this false."),
) -> None:
    """Open Otterly in a persistent profile so you can log in once manually."""

    async def _run() -> None:
        playwright, context = await launch_context(headless=headless)
        try:
            page = await ensure_page(context)
            await page.goto(f"{APP_URL}/sign-in", wait_until="domcontentloaded")
            await asyncio.to_thread(
                input,
                "Complete the Otterly login in the browser, then press Enter here to save the session.",
            )
        finally:
            await close_context(playwright, context)

    asyncio.run(_run())
    typer.echo(f"Saved Otterly browser state under: {STATE_DIR}")


@app.command("list-audits")
def list_audits(
    limit: int = typer.Option(10, help="Max number of top-level audit entries to print."),
    output: Path | None = typer.Option(None, help="Optional path to save the raw audits JSON."),
) -> None:
    """Fetch the raw GEO audit list JSON from Otterly using the saved browser session."""

    async def _run() -> Any:
        playwright, context = await launch_context(headless=True)
        try:
            page = await ensure_page(context)
            await page.goto(f"{APP_URL}/geo-audit/content", wait_until="domcontentloaded")
            session_token = await get_session_token(context)
            response = await page.request.get(
                LIST_AUDITS_URL,
                timeout=30_000,
                headers={"Authorization": f"Bearer {session_token}"},
            )
            if response.status != 200:
                raise RuntimeError(f"Otterly list audits returned HTTP {response.status}")
            return await response.json()
        finally:
            await close_context(playwright, context)

    payload = normalize_json(asyncio.run(_run()))
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        typer.echo(f"Saved raw audit list JSON to {output}")

    if isinstance(payload, list):
        preview = payload[:limit]
    elif isinstance(payload, dict):
        preview = payload
    else:
        preview = payload
    typer.echo(json.dumps(preview, indent=2))


@app.command("find-audit")
def find_audit(
    url: str = typer.Argument(..., help="Exact page URL to match, such as https://www.scopely.com/."),
) -> None:
    """Find matching GEO audits for a URL in the Otterly audit list."""

    async def _run() -> Any:
        playwright, context = await launch_context(headless=True)
        try:
            page = await ensure_page(context)
            await page.goto(f"{APP_URL}/geo-audit/content", wait_until="domcontentloaded")
            session_token = await get_session_token(context)
            response = await page.request.get(
                LIST_AUDITS_URL,
                timeout=30_000,
                headers={"Authorization": f"Bearer {session_token}"},
            )
            if response.status != 200:
                raise RuntimeError(f"Otterly list audits returned HTTP {response.status}")
            return await response.json()
        finally:
            await close_context(playwright, context)

    payload = normalize_json(asyncio.run(_run()))
    matches = find_matching_audits(payload, url)
    typer.echo(json.dumps(matches, indent=2))


@app.command("get-audit")
def get_audit(
    audit_id: str = typer.Argument(..., help="Otterly GEO audit id."),
    output: Path | None = typer.Option(None, help="Optional output path. Defaults to output/otterly/<audit_id>.json"),
) -> None:
    """Fetch one GEO audit as raw JSON from api.otterly.ai."""

    async def _run() -> Any:
        playwright, context = await launch_context(headless=True)
        try:
            page = await ensure_page(context)
            await page.goto(f"{APP_URL}/geo-audit/content/{audit_id}", wait_until="domcontentloaded")
            session_token = await get_session_token(context)
            response = await page.request.get(
                AUDIT_URL_TEMPLATE.format(audit_id=audit_id),
                timeout=30_000,
                headers={"Authorization": f"Bearer {session_token}"},
            )
            if response.status != 200:
                raise RuntimeError(f"Otterly get audit returned HTTP {response.status}")
            return await response.json()
        finally:
            await close_context(playwright, context)

    payload = normalize_json(asyncio.run(_run()))
    target = output or (OUTPUT_DIR / f"{audit_id}.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    typer.echo(f"Saved raw audit JSON to {target}")


@app.command("summarize-audit")
def summarize_audit(
    input_path: Path = typer.Argument(..., help="Path to a raw Otterly audit JSON file."),
    output: Path | None = typer.Option(None, help="Optional output path for the compact summary JSON."),
) -> None:
    """Create a compact, high-signal summary from a raw Otterly audit JSON file."""

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    summary = summarize_audit_payload(payload)
    target = output or input_path.with_name(f"{input_path.stem}.summary.json")
    target.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    typer.echo(json.dumps(summary, indent=2))
    typer.echo(f"Saved audit summary JSON to {target}")


if __name__ == "__main__":
    app()
