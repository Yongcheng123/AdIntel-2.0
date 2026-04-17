#!/usr/bin/env python3
"""
Discover AppFollow itemIds for all apps in config and auto-update the config file.

Uses the existing AppFollow browser session (from 'adintel login appfollow').
Navigates to each app and extracts the itemId from the URL, then updates
config/appfollow_groups.yaml automatically.
"""
import asyncio
import sys
from pathlib import Path

import yaml
from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = PROJECT_ROOT / "state" / "browser" / "appfollow"
CONFIG_FILE = PROJECT_ROOT / "config" / "appfollow_groups.yaml"
BASE_URL = "https://watch.appfollow.io"


async def discover_and_update_config():
    """
    Discover itemIds for all advertisers and competitors in config,
    then auto-update the config file.
    """
    # Load current config
    if not CONFIG_FILE.exists():
        print(f"❌ Config file not found: {CONFIG_FILE}")
        return

    with open(CONFIG_FILE, "r") as f:
        config = yaml.safe_load(f)

    workspace = config.get("workspace", "")
    if not workspace:
        print("❌ No workspace configured in appfollow_groups.yaml")
        return

    # Collect all apps to discover
    apps_to_find = {}
    for group in config.get("groups", []):
        advertiser = group.get("advertiser")
        if advertiser:
            apps_to_find[advertiser] = "advertiser"
        for competitor in group.get("competitors", []):
            comp_name = competitor.get("name")
            if comp_name:
                apps_to_find[comp_name] = "competitor"

    print(f"\n🔍 Discovering itemIds for {len(apps_to_find)} apps from AppFollow workspace: {workspace}")
    print("=" * 60)

    discovered = {}
    async with async_playwright() as pw:
        # Use the existing persistent context (reuses login cookies)
        context = await pw.chromium.launch_persistent_context(
            str(STATE_DIR),
            headless=False,
            viewport={"width": 1440, "height": 900},
        )
        page = context.pages[0] if context.pages else await context.new_page()

        for app_name in apps_to_find.keys():
            try:
                print(f"🔍 {app_name}...", end=" ", flush=True)

                # Navigate to workspace home
                await page.goto(
                    f"{BASE_URL}/apps/{workspace}/home",
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
                await page.wait_for_timeout(1_000)

                # Try to find app via search or navigation
                try:
                    search_input = page.locator('input[placeholder*="Search"], input[type="search"]')
                    if await search_input.is_visible():
                        await search_input.fill(app_name)
                        await page.wait_for_timeout(1_000)
                except Exception:
                    pass

                # Look for app link by name
                try:
                    app_link = page.locator(f'a:has-text("{app_name}")')
                    if await app_link.count() > 0:
                        await app_link.first.click()
                except Exception:
                    pass

                # Wait and extract itemId from URL
                await page.wait_for_timeout(2_000)
                current_url = page.url

                if "itemId=" in current_url:
                    item_id = current_url.split("itemId=")[1].split("&")[0]
                    discovered[app_name] = item_id
                    print(f"✅ {item_id}")
                else:
                    print(f"⚠️  not found")

            except Exception as exc:
                print(f"❌ {exc}")

        await context.close()

    # Update config with discovered IDs
    if not discovered:
        print("\n⚠️  No apps discovered. Make sure they're in your AppFollow workspace.")
        return

    print("\n" + "=" * 60)
    print(f"✅ Found {len(discovered)} apps. Updating config file...")
    print("=" * 60)

    for group in config.get("groups", []):
        advertiser = group.get("advertiser")
        if advertiser in discovered:
            group["appfollow_item_id"] = discovered[advertiser]
            print(f"  {advertiser}: {discovered[advertiser]}")

        for competitor in group.get("competitors", []):
            comp_name = competitor.get("name")
            if comp_name in discovered:
                competitor["appfollow_item_id"] = discovered[comp_name]
                print(f"    └─ {comp_name}: {discovered[comp_name]}")

    # Write updated config back
    with open(CONFIG_FILE, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    print("\n✅ Config file updated: " + str(CONFIG_FILE))
    print("\nNow ready to run:")
    print("  bash scripts/run_appfollow_to_server.sh")


if __name__ == "__main__":
    asyncio.run(discover_and_update_config())
