#!/usr/bin/env python3
"""
AppFollow full automation: add apps to workspace, discover itemIds, collect, delete, repeat.

Workflow per batch:
  1. Add up to 5 apps to workspace (captures itemId from API response)
  2. Close browser, run collection
  3. Reopen browser, delete apps (UI-based deletion)
  4. Repeat for next batch

Usage:
  python scripts/appfollow_auto.py --test          # Test with 1 advertiser group
  python scripts/appfollow_auto.py                 # Run all remaining uncollected groups
  python scripts/appfollow_auto.py --spy-add       # Show what API call is made when adding an app
  python scripts/appfollow_auto.py --spy-delete    # Intercept delete request (user clicks manually)
"""
import argparse
import asyncio
import difflib
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml
from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = PROJECT_ROOT / "state" / "browser" / "appfollow"
CONFIG_FILE = PROJECT_ROOT / "config" / "appfollow_groups.yaml"
DEBUG_DIR = PROJECT_ROOT / "state" / "debug" / "appfollow"
BASE_URL = "https://watch.appfollow.io"
WORKSPACE = "my-first-workspace"
BATCH_SIZE = 5

WRONG_ID = "639570"  # The initial test/placeholder ID that polluted the config


def is_placeholder(item_id) -> bool:
    """Return True if this itemId is a placeholder and needs to be discovered."""
    if not item_id:
        return True
    s = str(item_id)
    if s == WRONG_ID:
        return True
    # Any 5-char all-uppercase string is a placeholder (e.g. XXXXX, DDDDD, HHHHH)
    if re.fullmatch(r"[A-Z]{5}", s):
        return True
    return False


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config():
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)




def get_uncollected_groups():
    """Return advertiser groups not yet in the DB."""
    from sqlalchemy import create_engine, text
    from adintel.core.settings import get_settings

    db_url = get_settings().database_url
    engine = create_engine(db_url)
    with engine.connect() as conn:
        collected = {
            row[0] for row in conn.execute(
                text("SELECT DISTINCT advertiser_name FROM appfollow_reviews")
            ).fetchall()
        }

    config = load_config()
    uncollected = []
    for group in config.get("groups", []):
        if group.get("advertiser") not in collected:
            uncollected.append(group)
    return uncollected


def get_all_groups():
    """Return all advertiser groups."""
    config = load_config()
    return config.get("groups", [])


def best_match(app_name: str, candidates: list[dict]) -> str | None:
    """Find the best matching itemId from a list of {itemId, title} candidates."""
    if not candidates:
        return None

    app_lower = app_name.lower()

    # 1. Exact match (case-insensitive)
    for c in candidates:
        if c["title"].lower() == app_lower:
            return c["itemId"]

    # 2. App name contained in title
    for c in candidates:
        if app_lower in c["title"].lower():
            return c["itemId"]

    # 3. Title starts with first 5 chars of app name (but guard for short names)
    prefix = app_lower[:min(5, len(app_lower))]
    if len(prefix) >= 4:
        for c in candidates:
            if c["title"].lower().startswith(prefix):
                return c["itemId"]

    # 4. Fuzzy match using difflib (>=0.6 similarity)
    titles = [c["title"].lower() for c in candidates]
    matches = difflib.get_close_matches(app_lower, titles, n=1, cutoff=0.6)
    if matches:
        matched_title = matches[0]
        for c in candidates:
            if c["title"].lower() == matched_title:
                return c["itemId"]

    # 5. Fall back to first result
    return candidates[0]["itemId"]


def update_config_item_id(app_name: str, item_id: str):
    """Update itemId in config for a given app name."""
    config = load_config()
    updated = False

    for group in config.get("groups", []):
        if group.get("advertiser") == app_name:
            group["appfollow_item_id"] = item_id
            updated = True
        for competitor in group.get("competitors", []):
            if competitor.get("name") == app_name:
                competitor["appfollow_item_id"] = item_id
                updated = True

    if updated:
        save_config(config)
    return updated


def run_collection():
    """Run AppFollow collection script."""
    result = subprocess.run(
        ["bash", "scripts/run_appfollow_to_server.sh"],
        cwd=PROJECT_ROOT,
        capture_output=False,
    )
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Browser helpers
# ---------------------------------------------------------------------------

async def add_app_to_workspace(page, app_name: str) -> str | None:
    """
    Search for app on AppFollow add page, intercept API response to get itemId,
    click 'Add app'. Returns itemId or None.
    """
    print(f"  ➕ Adding: {app_name}...", end=" ", flush=True)

    search_term = app_name.lower().replace(" ", "+")
    add_url = f"{BASE_URL}/apps/{WORKSPACE}/app/add?term={search_term}&source=all&country=us"

    captured_items = []
    captured_requests = []  # Also capture request URLs for debugging

    async def capture_response(response):
        if "/client-api/v1/apps/" in response.url and "?" in response.url:
            try:
                body = await response.text()
                data = json.loads(body)
                apps = data.get("apps", [])
                for app in apps:
                    iid = app.get("itemId")
                    title = app.get("title", "")
                    if iid:
                        captured_items.append({"itemId": str(iid), "title": title})
            except Exception:
                pass

    async def capture_request(request):
        if "/client-api/v1/" in request.url and request.method in ("POST", "PUT", "DELETE"):
            captured_requests.append({
                "method": request.method,
                "url": request.url,
                "body": request.post_data,
            })

    page.on("response", capture_response)
    page.on("request", capture_request)

    await page.goto(add_url, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(2000)

    # Click Search button to trigger results
    try:
        btn = page.locator('button:has-text("Search")').first
        if await btn.count() > 0:
            await btn.click()
            await page.wait_for_timeout(3000)
    except Exception:
        pass

    # Save debug info on first run
    if captured_items:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        debug_file = DEBUG_DIR / f"search_{app_name.replace(' ', '_')}.json"
        with open(debug_file, "w") as f:
            json.dump({
                "app_name": app_name,
                "search_results": captured_items,
                "non_get_requests": captured_requests,
            }, f, indent=2)

    page.remove_listener("response", capture_response)
    page.remove_listener("request", capture_request)

    # Find best matching itemId
    item_id = best_match(app_name, captured_items)

    # Click first "Add app" button
    try:
        add_btn = page.locator('button.Z8NjCZG').first
        if await add_btn.count() > 0:
            await add_btn.click(timeout=5000)
            await page.wait_for_timeout(2000)
        else:
            # Try alternative selectors
            for sel in ['button:has-text("Add app")', 'button:has-text("Add to workspace")',
                        '[class*="add"]:not([disabled])']:
                btn = page.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click(timeout=5000)
                    await page.wait_for_timeout(2000)
                    break
    except Exception as e:
        print(f"⚠️  add-click failed: {str(e)[:40]}", end=" ")

    if item_id:
        title = next((c["title"] for c in captured_items if c["itemId"] == item_id), "?")
        print(f"✅ itemId={item_id} ({title})")
    else:
        print(f"⚠️  itemId not found (got {len(captured_items)} results)")

    return item_id


async def delete_app_from_workspace(page, item_id: str, app_name: str) -> bool:
    """
    Remove app from workspace. Tries multiple approaches:
    1. Direct API call (POST to detected delete endpoint)
    2. UI navigation to settings page + button click
    """
    print(f"  🗑️  Removing: {app_name} (id={item_id})...", end=" ", flush=True)

    # --- Strategy 1: Try API-based deletion (several endpoint patterns) ---
    delete_endpoints = [
        f"/client-api/v1/apps/{item_id}/unwatch/",
        f"/client-api/v1/apps/{item_id}/remove/",
        f"/client-api/v1/apps/{item_id}/unsubscribe/",
        f"/client-api/v1/apps/{item_id}/delete/",
    ]

    for endpoint in delete_endpoints:
        try:
            result = await page.evaluate(f"""async () => {{
                const csrfToken = (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || '';
                const resp = await fetch('{endpoint}?appsId=159602', {{
                    method: 'POST',
                    headers: {{
                        'Accept': 'application/json',
                        'Content-Type': 'application/json',
                        'X-CSRFToken': csrfToken,
                    }},
                    body: JSON.stringify({{workspaceId: 159602, itemId: {item_id}}})
                }});
                return {{status: resp.status, body: (await resp.text()).slice(0, 200)}};
            }}""")
            if result["status"] in (200, 201, 204):
                print(f"✅ (API: {endpoint})")
                return True
        except Exception:
            pass

    # --- Strategy 2: DELETE method ---
    try:
        result = await page.evaluate(f"""async () => {{
            const csrfToken = (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || '';
            const resp = await fetch('/client-api/v1/apps/{item_id}/?appsId=159602', {{
                method: 'DELETE',
                headers: {{
                    'Accept': 'application/json',
                    'X-CSRFToken': csrfToken,
                }},
            }});
            return {{status: resp.status, body: (await resp.text()).slice(0, 200)}};
        }}""")
        if result["status"] in (200, 201, 204):
            print(f"✅ (DELETE API)")
            return True
    except Exception:
        pass

    # --- Strategy 3: UI-based deletion via settings page ---
    try:
        settings_url = f"{BASE_URL}/apps/{WORKSPACE}/app/settings?itemId={item_id}"
        await page.goto(settings_url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2000)

        for btn_selector in [
            'button:has-text("Remove app")',
            'button:has-text("Delete app")',
            'button:has-text("Unsubscribe")',
            'button:has-text("Remove")',
            'button:has-text("Delete")',
            '[class*="danger"]',
            '[class*="remove"]',
            '[class*="delete"]',
        ]:
            btn = page.locator(btn_selector).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click(timeout=5000)
                await page.wait_for_timeout(1000)
                # Confirm dialog
                for confirm_sel in ['button:has-text("Confirm")', 'button:has-text("Yes")',
                                    'button:has-text("Remove")', 'button:has-text("Delete")']:
                    confirm = page.locator(confirm_sel).first
                    if await confirm.count() > 0:
                        await confirm.click(timeout=3000)
                        break
                await page.wait_for_timeout(1500)
                print("✅ (UI)")
                return True

        # Screenshot for debugging
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(DEBUG_DIR / f"delete_fail_{app_name.replace(' ', '_')}.png"))
        print(f"⚠️  button not found (screenshot saved to state/debug/appfollow/)")
        return False

    except Exception as e:
        print(f"❌ {str(e)[:50]}")
        return False


# ---------------------------------------------------------------------------
# Spy modes (for discovering API endpoints)
# ---------------------------------------------------------------------------

async def spy_add_endpoint():
    """Open browser and capture ALL requests made when adding an app."""
    print("🔍 SPY MODE: Add an app manually and I'll capture the API request.\n")

    captured = []

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(STATE_DIR), headless=False,
            viewport={"width": 1440, "height": 900},
        )
        page = context.pages[0] if context.pages else await context.new_page()

        async def on_req(req):
            if req.method != "GET" and "/client-api/" in req.url:
                entry = {"method": req.method, "url": req.url, "body": req.post_data}
                captured.append(entry)
                print(f"  📡 {req.method} {req.url}")
                if req.post_data:
                    print(f"      Body: {req.post_data[:200]}")

        page.on("request", on_req)

        await page.goto(f"{BASE_URL}/apps/{WORKSPACE}/app/add", wait_until="domcontentloaded")
        print("👆 Search for an app and click 'Add app'. Press Ctrl+C when done.\n")

        try:
            await asyncio.sleep(120)
        except KeyboardInterrupt:
            pass

        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        out = DEBUG_DIR / "spy_add_requests.json"
        with open(out, "w") as f:
            json.dump(captured, f, indent=2)
        print(f"\n💾 Saved to {out}")
        await context.close()


async def spy_delete_endpoint(item_id: str):
    """Navigate to app settings and capture the delete request."""
    print(f"🔍 SPY MODE: Delete app {item_id} manually and I'll capture the API request.\n")

    captured = []

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(STATE_DIR), headless=False,
            viewport={"width": 1440, "height": 900},
        )
        page = context.pages[0] if context.pages else await context.new_page()

        async def on_req(req):
            if req.method != "GET":
                entry = {"method": req.method, "url": req.url, "body": req.post_data,
                         "headers": {k: v for k, v in req.headers.items()
                                     if k.lower() in ('content-type', 'x-csrftoken', 'authorization')}}
                captured.append(entry)
                print(f"  📡 {req.method} {req.url}")
                if req.post_data:
                    print(f"      Body: {req.post_data[:200]}")

        page.on("request", on_req)

        settings_url = f"{BASE_URL}/apps/{WORKSPACE}/app/settings?itemId={item_id}"
        await page.goto(settings_url, wait_until="domcontentloaded", timeout=30_000)
        print(f"👆 Click the delete/remove button for this app. Press Ctrl+C when done.\n")

        try:
            await asyncio.sleep(120)
        except KeyboardInterrupt:
            pass

        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        out = DEBUG_DIR / "spy_delete_requests.json"
        with open(out, "w") as f:
            json.dump(captured, f, indent=2)
        print(f"\n💾 Saved to {out}")
        print(f"\n📋 Summary of {len(captured)} non-GET requests:")
        for r in captured:
            print(f"  {r['method']} {r['url']}")
        await context.close()


# ---------------------------------------------------------------------------
# Main automation loop
# ---------------------------------------------------------------------------

async def run_automation(test_mode: bool = False):
    uncollected = get_uncollected_groups()

    if not uncollected:
        print("✅ All advertisers already collected!")
        return

    if test_mode:
        uncollected = uncollected[:1]
        print(f"\n🧪 TEST MODE: processing 1 advertiser: {uncollected[0]['advertiser']}\n")
    else:
        print(f"\n🚀 Processing {len(uncollected)} remaining advertiser groups in batches of {BATCH_SIZE}\n")

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(STATE_DIR),
            headless=False,
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else await context.new_page()

        # Validate session
        print("🔑 Validating AppFollow session...")
        await page.goto(f"{BASE_URL}/apps/{WORKSPACE}/home", wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2000)

        if "login" in page.url or "signin" in page.url:
            print("❌ Session expired. Run: adintel login appfollow")
            await context.close()
            return

        print("✅ Session valid\n")

        # Process in batches
        for batch_start in range(0, len(uncollected), BATCH_SIZE):
            batch = uncollected[batch_start:batch_start + BATCH_SIZE]
            batch_num = batch_start // BATCH_SIZE + 1
            total_batches = (len(uncollected) - 1) // BATCH_SIZE + 1

            print(f"\n{'='*70}")
            print(f"📦 BATCH {batch_num}/{total_batches}: {[g['advertiser'] for g in batch]}")
            print(f"{'='*70}")

            # Collect unique apps in this batch (advertiser + competitors)
            all_apps_in_batch = []
            seen = set()
            for group in batch:
                advertiser = group["advertiser"]
                apps = [(advertiser, group.get("appfollow_item_id"))] + [
                    (c["name"], c.get("appfollow_item_id"))
                    for c in group.get("competitors", [])
                ]
                for name, iid in apps:
                    if name not in seen:
                        all_apps_in_batch.append({"name": name, "existing_id": iid})
                        seen.add(name)

            print(f"\n📋 {len(all_apps_in_batch)} unique apps to process:")
            for a in all_apps_in_batch:
                status = "has ID" if not is_placeholder(a["existing_id"]) else "needs ID"
                print(f"    • {a['name']} ({status})")

            # Step 1: Add apps that don't have IDs yet (in sub-batches of 5)
            print(f"\n➕ Adding apps to workspace (5 at a time)...")
            batch_item_ids = {}

            # First, collect existing real IDs
            for app in all_apps_in_batch:
                if not is_placeholder(app["existing_id"]):
                    batch_item_ids[app["name"]] = str(app["existing_id"])

            # Then add apps without IDs
            apps_needing_ids = [a for a in all_apps_in_batch if is_placeholder(a["existing_id"])]

            for sub_start in range(0, len(apps_needing_ids), 5):
                sub_batch = apps_needing_ids[sub_start:sub_start + 5]

                for app in sub_batch:
                    item_id = await add_app_to_workspace(page, app["name"])
                    if item_id:
                        batch_item_ids[app["name"]] = item_id
                        update_config_item_id(app["name"], item_id)
                    await asyncio.sleep(1.5)

                if sub_start + 5 < len(apps_needing_ids):
                    print(f"\n  ⏸️  Cleaning workspace before next sub-batch...")
                    # Delete the apps we just added before adding more
                    for app in sub_batch:
                        iid = batch_item_ids.get(app["name"])
                        if iid:
                            await delete_app_from_workspace(page, iid, app["name"])
                            await asyncio.sleep(0.5)

            print(f"\n📊 ItemIds collected: {len(batch_item_ids)}/{len(all_apps_in_batch)}")
            for name, iid in batch_item_ids.items():
                print(f"    {name}: {iid}")

            # Step 2: Close browser, run collection
            print(f"\n📥 Closing browser to run collection...")
            await context.close()

            print(f"🔄 Running collection (this may take several minutes)...")
            success = run_collection()
            if success:
                print(f"✅ Collection complete for batch {batch_num}")
            else:
                print(f"⚠️  Collection had errors for batch {batch_num}")

            # Step 3: Reopen browser, delete added apps
            print(f"\n🔓 Reopening browser to clean up workspace...")
            context = await pw.chromium.launch_persistent_context(
                str(STATE_DIR), headless=False,
                viewport={"width": 1440, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = context.pages[0] if context.pages else await context.new_page()

            await page.goto(f"{BASE_URL}/apps/{WORKSPACE}/home", wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(1500)

            print(f"\n🗑️  Removing apps from workspace...")
            for app in apps_needing_ids:
                iid = batch_item_ids.get(app["name"])
                if iid:
                    await delete_app_from_workspace(page, iid, app["name"])
                    await asyncio.sleep(0.5)
                else:
                    print(f"  ⏭️  Skipping {app['name']} (no itemId)")

            print(f"\n✅ Batch {batch_num} complete!")

            if test_mode:
                break

        await context.close()
        print("\n✅ All done!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Test with 1 advertiser group only")
    parser.add_argument("--spy-add", action="store_true", help="Capture API request made when adding an app")
    parser.add_argument("--spy-delete", metavar="ITEM_ID", help="Capture delete request for given itemId")
    args = parser.parse_args()

    if args.spy_add:
        asyncio.run(spy_add_endpoint())
    elif args.spy_delete:
        asyncio.run(spy_delete_endpoint(args.spy_delete))
    else:
        asyncio.run(run_automation(test_mode=args.test))
