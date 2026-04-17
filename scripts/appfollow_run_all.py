#!/usr/bin/env python3
"""
AppFollow one-shot collector.

Run once — it handles everything automatically:
  1. Opens your saved AppFollow browser session
  2. For each batch of ≤5 apps needing itemIds:
       • Adds them to the workspace
       • Captures real itemIds from the API response
       • Updates config/appfollow_groups.yaml
  3. Collects reviews for every app with a known itemId (same browser session)
  4. Removes the temp-added apps from the workspace
  5. Repeats until all advertiser groups are processed

Usage:
  python scripts/appfollow_run_all.py
  python scripts/appfollow_run_all.py --test       # process first group only
  python scripts/appfollow_run_all.py --headless   # no visible browser
"""
from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, UTC
from pathlib import Path
from urllib.parse import parse_qs, urlparse, unquote_plus

import yaml
from playwright.async_api import async_playwright, Page, BrowserContext

# ── project root & imports ────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from adintel.core.settings import get_settings
from adintel.core.browser import BrowserManager
from adintel.db.session import build_session_factory
from adintel.platforms.appfollow import AppFollowCollector

# ── constants ─────────────────────────────────────────────────────────────
STATE_DIR   = PROJECT_ROOT / "state" / "browser" / "appfollow"
CONFIG_FILE = PROJECT_ROOT / "config" / "appfollow_groups.yaml"
DEBUG_DIR   = PROJECT_ROOT / "state" / "debug" / "appfollow"
BASE_URL    = "https://watch.appfollow.io"
WORKSPACE   = "my-first-workspace"
BATCH_SIZE  = 5          # AppFollow workspace app limit
WRONG_ID    = "639570"   # bogus placeholder ID used earlier
WORKSPACE_APPS_ID: str | None = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("appfollow_run_all")


# ── helpers ───────────────────────────────────────────────────────────────

def is_placeholder(item_id) -> bool:
    """Return True if itemId is still a placeholder needing discovery."""
    if not item_id:
        return True
    s = str(item_id).strip()
    if s == WRONG_ID:
        return True
    if re.fullmatch(r"[A-Z]{5}", s):   # e.g. XXXXX, DDDDD, HHHHH …
        return True
    return False


def load_config() -> dict:
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


def save_config(config: dict) -> None:
    with open(CONFIG_FILE, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def update_config_item_id(app_name: str, item_id: str, platform: str) -> bool:
    """Write a discovered itemId back to the config file. platform: 'ios' or 'android'."""
    field = f"appfollow_{platform}_item_id"
    config = load_config()
    updated = False
    for group in config.get("groups", []):
        if group.get("advertiser") == app_name:
            group[field] = item_id
            updated = True
        for comp in group.get("competitors", []):
            if comp.get("name") == app_name:
                comp[field] = item_id
                updated = True
    if updated:
        save_config(config)
    return updated


def best_match(app_name: str, candidates: list[dict]) -> str | None:
    """Pick the best itemId from a list of {itemId, title} candidates. Returns None if no good match."""
    if not candidates:
        return None
    app_lower = app_name.lower()

    # 1. exact match (case-insensitive)
    for c in candidates:
        if c["title"].lower() == app_lower:
            return c["itemId"]

    # 2. app name contained in result title
    for c in candidates:
        if app_lower in c["title"].lower():
            return c["itemId"]

    # 3. first word of title matches first word of app name
    app_words = app_lower.split()
    for c in candidates:
        title_words = c["title"].lower().split()
        if app_words[0] == title_words[0]:
            return c["itemId"]

    # 4. fuzzy similarity ≥ 0.7 (strict threshold to avoid false matches)
    titles = [c["title"].lower() for c in candidates]
    close = difflib.get_close_matches(app_lower, titles, n=1, cutoff=0.7)
    if close:
        for c in candidates:
            if c["title"].lower() == close[0]:
                return c["itemId"]

    # NO fallback - if we can't confidently match, return None instead of guessing
    log.warning(f"Could not find good match for '{app_name}'. API returned: {[c['title'] for c in candidates[:3]]}")
    return None


def get_collected_advertisers(mode: str = "missing") -> set[str]:
    """Names already in appfollow_reviews table."""
    if mode == "all":
        return set()
    try:
        from sqlalchemy import create_engine, text
        url = get_settings().database_url
        engine = create_engine(url)
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT DISTINCT advertiser_name FROM appfollow_reviews"
            )).fetchall()
        return {r[0] for r in rows}
    except Exception as exc:
        log.warning("Could not query DB for collected advertisers: %s", exc)
        return set()


def expand_group_to_entries(group: dict) -> list[dict]:
    """
    Expand one config group into individual work entries — one per (app, platform).
    Each entry: {name, platform, search_term, source, item_id, countries, needs_id}
    """
    entries = []
    countries = group.get("countries", ["US"])

    apps = [(group.get("advertiser"), group)] + [
        (c["name"], c) for c in group.get("competitors", [])
    ]

    for name, node in apps:
        ios_id  = str(node.get("ios_app_id") or "").strip()
        apk     = str(node.get("android_package") or "").strip()
        ios_iid = str(node.get("appfollow_ios_item_id") or "").strip()
        apk_iid = str(node.get("appfollow_android_item_id") or "").strip()

        if ios_id:
            entries.append({
                "name": name, "platform": "ios",
                "search_term": ios_id, "source": "itunes",
                "item_id": ios_iid, "countries": countries,
                "needs_id": is_placeholder(ios_iid),
            })
        if apk:
            entries.append({
                "name": name, "platform": "android",
                "search_term": apk, "source": "googleplay",
                "item_id": apk_iid, "countries": countries,
                "needs_id": is_placeholder(apk_iid),
            })

    return entries


def build_work_plan(mode: str = "missing") -> list[dict]:
    """
    Return advertiser groups that still need collection, ordered so groups
    whose apps all already have real itemIds come first (quick wins).
    """
    collected = get_collected_advertisers(mode=mode)
    config = load_config()
    plan = []
    for group in config.get("groups", []):
        adv = group.get("advertiser", "")
        if adv in collected:
            log.info("  ✓ Already collected: %s — skipping", adv)
            continue
        plan.append(group)

    # Sort: groups with all real IDs first
    def has_all_ids(g):
        entries = expand_group_to_entries(g)
        return all(not e["needs_id"] for e in entries)

    plan.sort(key=lambda g: 0 if has_all_ids(g) else 1)
    return plan


# ── browser automation ────────────────────────────────────────────────────

async def add_app_and_get_id(page: Page, app_name: str,
                             search_term: str, source: str) -> str | None:
    """
    Add an app to the workspace and return its AppFollow itemId.
    source: 'itunes' (iOS), 'googleplay' (Android), or 'all' (name search).
    When source is itunes/googleplay, the first result is always the correct app.
    """
    platform_label = {"itunes": "iOS", "googleplay": "Android"}.get(source, "name")
    print(f"    ➕ {app_name} [{platform_label}] ...", end=" ", flush=True)

    candidates: list[dict] = []
    seen_candidates: set[tuple[str, str, str]] = set()
    matched_api_urls: list[str] = []
    search_term_norm = re.sub(r"\s+", "", str(search_term).lower())

    async def on_response(resp):
        # Capture only search-like app endpoints matching this term/source.
        if "watch.appfollow.io/client-api" not in resp.url:
            return
        parsed = urlparse(resp.url)
        path = parsed.path.lower()
        if "/client-api/v1/apps/" not in path:
            return
        if "search" not in path and "autocomplete" not in path and "suggest" not in path:
            return

        query = parse_qs(parsed.query)
        query_source = (query.get("source", [""])[0] or "").lower()
        if query_source and query_source != source.lower():
            return

        query_term_raw = (
            query.get("term", [""])[0]
            or query.get("q", [""])[0]
            or query.get("query", [""])[0]
        )
        # Some search requests put query in URL, some in request body.
        # If URL query is present, verify it. If absent, still accept based on
        # search endpoint path (already filtered above).
        if query_term_raw:
            query_term_norm = re.sub(r"\s+", "", unquote_plus(query_term_raw).lower())
            if query_term_norm != search_term_norm:
                return

        try:
            body = await resp.text()
            data = json.loads(body)
            apps = data.get("apps", [])
            if not isinstance(apps, list):
                return
            matched_api_urls.append(resp.url)
            for app in apps:
                item = {
                    "itemId": str(app.get("itemId") or ""),
                    "title": app.get("title", ""),
                    "extId": str(app.get("extId") or app.get("ext_id") or ""),
                }
                key = (item["itemId"], item["extId"], item["title"])
                if key in seen_candidates:
                    continue
                seen_candidates.add(key)
                candidates.append(item)
        except Exception:
            pass

    add_url = f"{BASE_URL}/apps/{WORKSPACE}/app/add?term={search_term}&source={source}&country=us"
    page.on("response", on_response)
    await page.goto(add_url, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(2_000)

    # Wait until add-page controls are rendered (can be slow in headless mode).
    search_btn = page.locator('button:has-text("Search")').first
    try:
        await search_btn.wait_for(state="visible", timeout=12_000)
    except Exception:
        pass

    # The add page is source-driven; selecting the source tile and filling
    # the search box mirrors the real UI flow and reliably triggers search API.
    try:
        source_label = {"itunes": "App Store", "googleplay": "Google Play"}.get(source)
        if source_label:
            src_btn = page.locator(f'button:has-text("{source_label}")').first
            if await src_btn.count() > 0 and await src_btn.is_visible():
                await src_btn.click()
                await page.wait_for_timeout(250)
    except Exception:
        pass

    try:
        search_input = page.locator(
            'input[placeholder*="Enter app name"], '
            'input[placeholder*="developer"], '
            'input[placeholder*="store link"]'
        ).first
        if await search_input.count() > 0 and await search_input.is_visible():
            await search_input.fill(search_term)
            await page.wait_for_timeout(250)
    except Exception:
        pass

    try:
        btn = page.locator('button:has-text("Search")').first
        if await btn.count() > 0:
            for _ in range(20):
                if not await btn.is_disabled():
                    break
                await page.wait_for_timeout(300)
            if not await btn.is_disabled():
                await btn.click()
                await page.wait_for_timeout(5_000)
    except Exception:
        pass

    page.remove_listener("response", on_response)

    if not candidates:
        print(f"⚠️  no results")
        return None

    # iOS/Android search by unique ID → match by extId, no fallback
    target_ext_id = ""
    if source in ("itunes", "googleplay"):
        matched = next(
            (c for c in candidates if c["extId"] == search_term),
            None,
        )
        if not matched:
            # extId didn't match — log what we got for debugging
            got = [(c["extId"], c["title"]) for c in candidates[:3]]
            print(f"⚠️  no extId match for {search_term} (got: {got})")
            return None
        target_ext_id = matched["extId"]
        item_id = matched["itemId"] or ""
        if item_id:
            print(f"✅  id={item_id}  ({matched['title']})")
        else:
            print(f"✅  found app  ({matched['title']})")
    else:
        item_id = best_match(app_name, candidates)
        if not item_id:
            print(f"⚠️  no confident name match ({[c['title'][:25] for c in candidates[:3]]})")
            return None
        matched_title = next((c["title"] for c in candidates if c["itemId"] == item_id), "")
        print(f"✅  id={item_id}  ({matched_title})  [name]")

    # Save debug snapshot
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    (DEBUG_DIR / f"search_{app_name.replace(' ', '_')}_{source}.json").write_text(
        json.dumps({"app": app_name, "term": search_term, "source": source,
                    "matched_api_urls": matched_api_urls[:10],
                    "results": candidates[:5]}, indent=2)
    )

    # Click "Add app" button
    clicked_add = False
    for sel in ['button.Z8NjCZG', 'button:has-text("Add app")',
                'button:has-text("Add to workspace")', 'button:has-text("Add")']:
        try:
            btn = page.locator(sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click(timeout=5_000)
                await page.wait_for_timeout(2_000)
                clicked_add = True
                break
        except Exception:
            pass

    # Search payload often omits itemId until app is added. After add, poll the
    # workspace apps API and resolve itemId by extId.
    if source in ("itunes", "googleplay") and target_ext_id and clicked_add and not item_id and WORKSPACE_APPS_ID:
        for _ in range(12):
            try:
                resp = await page.request.get(
                    f"{BASE_URL}/client-api/v1/apps/?appsId={WORKSPACE_APPS_ID}&isArchiveIncluded=false"
                )
                if resp.status == 200:
                    data = json.loads(await resp.text())
                    apps = data.get("apps", []) if isinstance(data, dict) else []
                    found = next(
                        (
                            a for a in apps
                            if str(a.get("extId") or a.get("ext_id") or "") == target_ext_id
                            and a.get("itemId")
                        ),
                        None,
                    )
                    if found:
                        item_id = str(found.get("itemId"))
                        print(f"       ↳ resolved itemId after add: {item_id}")
                        break
            except Exception:
                pass
            await page.wait_for_timeout(500)

    if source in ("itunes", "googleplay") and not item_id:
        print(f"⚠️  app found but itemId not resolved after add ({target_ext_id})")
        return None

    return item_id


async def get_workspace_app_ids(page: Page) -> list[str]:
    """
    Return itemIds of all apps currently in the workspace by intercepting the
    API response on the workspace home page.
    """
    found_ids: list[str] = []

    async def on_response(resp):
        if "/client-api/v1/apps/" in resp.url:
            try:
                body = await resp.text()
                data = json.loads(body)
                # Workspace home returns apps in various envelope shapes
                for key in ("apps", "data", "items", "results"):
                    items = data.get(key, [])
                    if isinstance(items, list):
                        for app in items:
                            iid = app.get("itemId") or app.get("item_id")
                            if iid:
                                found_ids.append(str(iid))
                        if found_ids:
                            break
            except Exception:
                pass

    page.on("response", on_response)
    await page.goto(f"{BASE_URL}/apps/{WORKSPACE}/home",
                    wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(3_000)
    page.remove_listener("response", on_response)

    # Also scrape itemIds from hrefs in the page (fallback)
    if not found_ids:
        hrefs = await page.eval_on_selector_all(
            '[href*="itemId="]',
            "els => els.map(e => new URL(e.href, location.href).searchParams.get('itemId')).filter(Boolean)"
        )
        found_ids = list(dict.fromkeys(hrefs))  # deduplicate preserving order

    return list(dict.fromkeys(found_ids))


async def clear_workspace(page: Page) -> None:
    """
    Remove every app currently in the workspace so we start with a clean slate.
    This ensures there's room to add new apps (workspace limit = 5).
    Failures are logged but don't block progress.
    """
    print("🧹  Checking workspace for existing apps...")
    app_ids = await get_workspace_app_ids(page)

    if not app_ids:
        print("    Workspace is empty — nothing to clear.")
        return

    print(f"    Found {len(app_ids)} app(s) in workspace: {app_ids}")
    print("    Attempting to remove them...")
    deleted = 0
    for iid in app_ids:
        try:
            success = await delete_app(page, iid, f"workspace-app-{iid}")
            if success:
                deleted += 1
        except Exception as exc:
            print(f"      ⚠️  Exception during delete: {exc}")
        await asyncio.sleep(0.5)
    print(f"    Cleared {deleted}/{len(app_ids)} apps. (Some may need manual removal.)\n")


async def delete_app(page: Page, item_id: str, app_name: str) -> bool:
    """
    Remove app from workspace using multiple strategies (most reliable first).
    Returns True if deletion succeeded. Aborts after 10 seconds if no success.
    """
    print(f"    🗑  {app_name} (id={item_id}) ...", end=" ", flush=True)

    try:
        return await asyncio.wait_for(_delete_impl(page, item_id, app_name), timeout=10.0)
    except asyncio.TimeoutError:
        print(f"⏱️  timeout (took >10s)")
        return False
    except Exception as exc:
        print(f"❌  {str(exc)[:40]}")
        return False


async def _delete_impl(page: Page, item_id: str, app_name: str) -> bool:
    """Implementation of delete with multiple strategies."""
    # ── Strategy 1: API — try common endpoint patterns ────────────────────
    apps_id_suffixes = [f"?appsId={WORKSPACE_APPS_ID}"] if WORKSPACE_APPS_ID else ["",]
    api_attempts = []
    for suffix in apps_id_suffixes:
        api_attempts.extend([
            ("POST",   f"/client-api/v1/apps/{item_id}/unwatch/{suffix}",     "{}"),
            ("POST",   f"/client-api/v1/apps/{item_id}/remove/{suffix}",       "{}"),
            ("POST",   f"/client-api/v1/apps/{item_id}/unsubscribe/{suffix}",  "{}"),
            ("POST",   f"/client-api/v1/apps/{item_id}/delete/{suffix}",       "{}"),
            ("DELETE", f"/client-api/v1/apps/{item_id}/{suffix}",              None),
            ("POST",   f"/client-api/v1/workspace-apps/{item_id}/{suffix}",    '{"action":"remove"}'),
        ])
    for method, path, body in api_attempts:
        try:
            js_body = f"JSON.stringify({body})" if body else "undefined"
            ct = "'Content-Type': 'application/json'" if body else ""
            result = await page.evaluate(f"""async () => {{
                const csrf = (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || '';
                const r = await fetch('{path}', {{
                    method: '{method}',
                    credentials: 'include',
                    headers: {{ 'Accept': 'application/json', {ct}
                                'X-CSRFToken': csrf }},
                    {f'body: {js_body},' if body else ''}
                }});
                return {{status: r.status, body: (await r.text()).slice(0,120)}};
            }}""")
            if result["status"] in (200, 201, 204):
                print(f"✅  (API {method} {path.split('?')[0]})")
                return True
        except Exception:
            pass

    # ── Strategy 2: UI on "My apps" grid — open card menu and confirm remove ─
    try:
        my_apps_url = (
            f"{BASE_URL}/apps/{WORKSPACE}"
            "?appType=apps&appStore=all&search=&sortType=added_asc"
        )
        await page.goto(my_apps_url, wait_until="domcontentloaded", timeout=20_000)
        # The page can render slowly; wait for cards/icons.
        await page.wait_for_timeout(4_000)

        # Resolve card index from the workspace apps API so we remove the correct app.
        if WORKSPACE_APPS_ID:
            api_resp = await page.request.get(
                f"{BASE_URL}/client-api/v1/apps/?appsId={WORKSPACE_APPS_ID}&isArchiveIncluded=false"
            )
        else:
            api_resp = None
        if api_resp and api_resp.status == 200:
            data = json.loads(await api_resp.text())
            apps = data.get("apps", []) if isinstance(data, dict) else []
            idx = next(
                (
                    i for i, a in enumerate(apps)
                    if str(a.get("itemId") or a.get("item_id") or "") == str(item_id)
                ),
                None,
            )
            if idx is not None:
                icons = page.locator('button.ucE4oQl.wEOj6nq.CsgOPCt.l2rYh9W')
                # Three icons per card; the 3rd one opens "Remove app" modal.
                target_icon_idx = idx * 3 + 2
                for _ in range(20):
                    if await icons.count() > target_icon_idx:
                        break
                    await page.wait_for_timeout(500)

                if await icons.count() > target_icon_idx:
                    await icons.nth(target_icon_idx).click(timeout=4_000)
                    await page.wait_for_timeout(500)

                    confirm_txt = page.locator("text=Are you sure you want to remove this app?")
                    if await confirm_txt.count() > 0:
                        yes_btn = page.locator('button:has-text("Yes")').last
                        if await yes_btn.count() > 0:
                            await yes_btn.click(timeout=4_000)
                            await page.wait_for_timeout(1_500)
                            print("✅  (UI my apps)")
                            return True
    except Exception:
        pass

    # ── Strategy 3: UI on workspace home — hover card → kebab/gear/trash ─
    try:
        await page.goto(f"{BASE_URL}/apps/{WORKSPACE}/home",
                        wait_until="domcontentloaded", timeout=15_000)
        await page.wait_for_timeout(1_000)

        # Hover the app card to reveal action icons
        card = page.locator(f'[href*="itemId={item_id}"], [data-item-id="{item_id}"]').first
        if await card.count() > 0:
            await card.hover()
            await page.wait_for_timeout(400)
        # Look for trash / gear / kebab icons near this card
        for icon_sel in ['[class*="trash"]', '[class*="delete"]', '[class*="remove"]']:
            btn = page.locator(icon_sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click(timeout=3_000)
                await page.wait_for_timeout(600)
                for conf in ['button:has-text("Confirm")', 'button:has-text("Yes")',
                             'button:has-text("Remove")']:
                    c = page.locator(conf).first
                    if await c.count() > 0:
                        await c.click(timeout=3_000)
                        break
                print("✅  (UI home)")
                return True
    except Exception:
        pass

    # ── Strategy 4: Settings page — scroll and click any remove-ish button ─
    try:
        await page.goto(
            f"{BASE_URL}/apps/{WORKSPACE}/app/settings?itemId={item_id}",
            wait_until="domcontentloaded", timeout=15_000
        )
        await page.wait_for_timeout(1_500)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(600)

        for sel in ['button:has-text("Remove")', 'button:has-text("Delete")',
                    'button[class*="danger"]', 'button[class*="remove"]']:
            btn = page.locator(sel).first
            if await btn.count() > 0:
                await btn.click(timeout=4_000)
                await page.wait_for_timeout(800)
                for conf in ['button:has-text("Yes")', 'button:has-text("Remove")']:
                    c = page.locator(conf).first
                    if await c.count() > 0:
                        await c.click(timeout=2_000)
                        break
                print("✅  (UI settings)")
                return True
    except Exception:
        pass

    print(f"⚠️  no delete method worked")
    return False


async def resolve_workspace_apps_id(page: Page) -> str | None:
    """Resolve numeric appsId for current workspace from network traffic."""
    found: list[str] = []

    async def on_response(resp):
        if "/client-api/v1/apps/" not in resp.url:
            return
        parsed = urlparse(resp.url)
        apps_id = parse_qs(parsed.query).get("appsId", [None])[0]
        if apps_id and apps_id not in found:
            found.append(apps_id)

    page.on("response", on_response)
    try:
        await page.goto(f"{BASE_URL}/apps/{WORKSPACE}/home", wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2_000)
    finally:
        page.remove_listener("response", on_response)

    if found:
        return found[0]

    if WORKSPACE_APPS_ID:
        return WORKSPACE_APPS_ID

    env_value = str(os.environ.get("ADINTEL_APPFOLLOW_WORKSPACE_APPS_ID", "")).strip()
    return env_value or None


# ── collection ────────────────────────────────────────────────────────────

async def collect_apps(page: Page, entries: list[dict],
                       settings, session_factory) -> None:
    """Run AppFollowCollector.collect_app() for each entry in-process."""
    total = len(entries)
    log.info("Collecting %d entries...", total)
    for i, entry in enumerate(entries, 1):
        name      = entry["name"]
        item_id   = entry["item_id"]
        countries = entry["countries"]
        platform  = entry.get("platform", "ios")  # "ios" or "android"
        print(f"\n  [{i}/{total}] {name} [{platform}]  (id={item_id}, countries={','.join(countries)})")
        try:
            with session_factory() as session:
                browser_mgr = BrowserManager(settings)
                collector   = AppFollowCollector(settings, browser_mgr, session)
                result = await collector.collect_app(
                    page=page,
                    advertiser_name=name,
                    item_id=item_id,
                    workspace=WORKSPACE,
                    countries=countries,
                    headless=False,
                    debug=False,
                )
            status = result.get("status", "?")
            rows   = result.get("records_written", 0)
            msg    = result.get("message", "")
            print(f"    → {status}  ({rows} rows)  {msg}")
        except Exception as exc:
            print(f"    → ERROR: {exc}")


# ── main loop ─────────────────────────────────────────────────────────────

async def run_all(test_mode: bool = False, headless: bool = False, mode: str = "missing") -> None:
    global WORKSPACE, WORKSPACE_APPS_ID
    cfg_workspace = str((load_config() or {}).get("workspace") or "").strip()
    if cfg_workspace:
        WORKSPACE = cfg_workspace

    settings = get_settings()
    session_factory = build_session_factory(settings)

    work_plan = build_work_plan(mode=mode)
    if not work_plan:
        print("✅  All advertiser groups already collected!")
        return

    if test_mode:
        work_plan = work_plan[:1]
        print(f"\n🧪  TEST MODE — processing 1 group: {work_plan[0]['advertiser']}\n")
    else:
        print(f"\n🚀  {len(work_plan)} advertiser groups to process\n")
    print(f"📌  Run mode: {mode} ({'only missing advertisers' if mode == 'missing' else 'refresh all advertisers'})\n")

    async with async_playwright() as pw:
        context: BrowserContext = await pw.chromium.launch_persistent_context(
            str(STATE_DIR),
            headless=headless,
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled",
                  "--disable-dev-shm-usage"],
        )
        page = context.pages[0] if context.pages else await context.new_page()

        # ── Validate session ──────────────────────────────────────────────
        print("🔑  Validating AppFollow session...")
        await page.goto(f"{BASE_URL}/apps/{WORKSPACE}/home",
                        wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2_000)
        if "login" in page.url or "signin" in page.url:
            print("❌  Session expired — run: adintel login appfollow")
            await context.close()
            return
        WORKSPACE_APPS_ID = await resolve_workspace_apps_id(page)
        if WORKSPACE_APPS_ID:
            print(f"✅  Session valid (workspace appsId={WORKSPACE_APPS_ID})\n")
        else:
            print("✅  Session valid (workspace appsId unresolved; UI fallback delete mode)\n")

        # ── Clear any leftover apps from previous runs ────────────────────
        await clear_workspace(page)

        # Flatten all groups into (name, platform) work entries, deduplicated
        all_entries: list[dict] = []
        seen_key: set[tuple] = set()
        for group in work_plan:
            for entry in expand_group_to_entries(group):
                key = (entry["name"], entry["platform"])
                if key not in seen_key:
                    seen_key.add(key)
                    all_entries.append(entry)

        need_discovery = [e for e in all_entries if e["needs_id"]]
        have_id        = [e for e in all_entries if not e["needs_id"]]

        total_entries = len(all_entries)
        print(f"  {total_entries} total (app, platform) pairs")
        print(f"  {len(have_id)} already have itemIds, {len(need_discovery)} need discovery\n")

        # ── Step 1: Collect entries that already have real itemIds ────────
        if have_id:
            print(f"📥  Collecting {len(have_id)} pre-known entries...")
            await collect_apps(page, have_id, settings, session_factory)

        # ── Step 2: Discover + collect in batches of BATCH_SIZE ──────────
        total_batches = (len(need_discovery) - 1) // BATCH_SIZE + 1 if need_discovery else 0

        for batch_idx in range(0, len(need_discovery), BATCH_SIZE):
            sub = need_discovery[batch_idx : batch_idx + BATCH_SIZE]
            batch_num = batch_idx // BATCH_SIZE + 1

            labels = [f"{e['name']}[{e['platform']}]" for e in sub]
            print(f"\n{'='*70}")
            print(f"📦  DISCOVERY BATCH {batch_num}/{total_batches}: {labels}")
            print(f"{'='*70}\n")

            # Add each app to workspace, capture itemId
            added: list[dict] = []
            for entry in sub:
                item_id = await add_app_and_get_id(
                    page, entry["name"],
                    search_term=entry["search_term"],
                    source=entry["source"],
                )
                if item_id:
                    entry["item_id"]  = item_id
                    entry["needs_id"] = False
                    update_config_item_id(entry["name"], item_id, entry["platform"])
                    added.append(entry)
                await asyncio.sleep(1.5)

            # Collect reviews for confirmed entries
            collectible = [e for e in sub if not e["needs_id"]]
            if collectible:
                print(f"\n  📥 Collecting {len(collectible)} entries...")
                await collect_apps(page, collectible, settings, session_factory)

            # Remove from workspace (make room for next batch)
            if batch_idx + BATCH_SIZE < len(need_discovery):
                print(f"\n  🗑  Clearing workspace for next batch...")
                for entry in added:
                    await delete_app(page, entry["item_id"],
                                     f"{entry['name']}[{entry['platform']}]")
                    await asyncio.sleep(0.5)

            print(f"\n✅  Discovery batch {batch_num} complete!")
            if test_mode:
                break

        await context.close()

    # ── Final summary ─────────────────────────────────────────────────────
    print("\n" + "="*70)
    print("✅  All done!")
    try:
        from sqlalchemy import create_engine, text
        url = get_settings().database_url
        engine = create_engine(url)
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT advertiser_name,
                       count(*)                                         AS reviews,
                       min(review_date)                                 AS earliest,
                       max(review_date)                                 AS latest,
                       round(avg(star_rating)::numeric, 2)             AS avg_rating,
                       count(*) FILTER (WHERE sentiment='positive')    AS pos,
                       count(*) FILTER (WHERE sentiment='negative')    AS neg
                FROM appfollow_reviews
                GROUP BY advertiser_name
                ORDER BY advertiser_name
            """)).fetchall()
        print(f"\n{'Advertiser':<25} {'Reviews':>8} {'Earliest':<12} {'Latest':<12} {'Avg':>5} {'Pos':>6} {'Neg':>6}")
        print("-" * 76)
        for r in rows:
            print(f"{r[0]:<25} {r[1]:>8} {str(r[2]):<12} {str(r[3]):<12} {str(r[4]):>5} {r[5]:>6} {r[6]:>6}")
    except Exception as exc:
        print(f"(Could not query summary: {exc})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AppFollow one-shot collector")
    parser.add_argument("--test",     action="store_true", help="Process only the first group")
    parser.add_argument("--headless", action="store_true", help="Run browser without GUI")
    parser.add_argument(
        "--mode",
        choices=["missing", "all"],
        default="missing",
        help="Collection mode: 'missing' skips advertisers already in appfollow_reviews, 'all' refreshes all groups.",
    )
    args = parser.parse_args()

    asyncio.run(run_all(test_mode=args.test, headless=args.headless, mode=args.mode))
