#!/usr/bin/env python3
"""
Find AppFollow itemIds for missing apps by searching AppFollow's public data.

Usage: python scripts/appfollow_find_missing_itemids.py
"""
import asyncio
from pathlib import Path

import yaml
from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = PROJECT_ROOT / "config" / "appfollow_groups.yaml"
BASE_URL = "https://watch.appfollow.io"


async def find_missing_itemids():
    """
    For each app with a placeholder itemId, search AppFollow directly
    and try to extract the real itemId.
    """
    with open(CONFIG_FILE) as f:
        config = yaml.safe_load(f)

    # Find all apps with placeholder IDs
    missing = []
    for group in config.get("groups", []):
        advertiser = group.get("advertiser")
        item_id = group.get("appfollow_item_id")
        if str(item_id).isupper() and len(str(item_id)) == 5:
            missing.append({"name": advertiser, "type": "advertiser"})

        for competitor in group.get("competitors", []):
            comp_name = competitor.get("name")
            comp_id = competitor.get("appfollow_item_id")
            if str(comp_id).isupper() and len(str(comp_id)) == 5:
                missing.append({"name": comp_name, "type": "competitor"})

    if not missing:
        print("✓ No missing itemIds found!")
        return

    print(f"\n🔍 Searching for {len(missing)} missing apps on AppFollow...\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        page = await browser.new_page()

        found_ids = {}

        for i, app in enumerate(missing, 1):
            name = app["name"]
            print(f"[{i}/{len(missing)}] Searching: {name}...", end=" ", flush=True)

            try:
                # Navigate to AppFollow homepage
                await page.goto(f"{BASE_URL}/apps/all/home", wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(1000)

                # Use search if available
                try:
                    search_box = page.locator('input[placeholder*="Search"], input[type="search"]').first
                    if await search_box.is_visible():
                        await search_box.clear()
                        await search_box.fill(name)
                        await page.wait_for_timeout(1500)
                except Exception:
                    pass

                # Try to find and click the app link
                try:
                    app_link = page.locator(f'a:has-text("{name}")').first
                    if await app_link.count() > 0:
                        await app_link.click(timeout=5000)
                        await page.wait_for_timeout(2000)
                except Exception:
                    pass

                # Extract itemId from URL
                current_url = page.url
                if "itemId=" in current_url:
                    item_id = current_url.split("itemId=")[1].split("&")[0]
                    found_ids[name] = item_id
                    print(f"✅ {item_id}")
                else:
                    print(f"⚠️  not found in URL")

            except Exception as e:
                print(f"❌ {str(e)[:40]}")

        await browser.close()

    # Display results
    if found_ids:
        print(f"\n✅ Found {len(found_ids)} itemIds:\n")
        for name, item_id in sorted(found_ids.items()):
            print(f"  {name:30s}: {item_id}")

        print(f"\n📝 Update config/appfollow_groups.yaml with these itemIds manually.")
    else:
        print(f"\n❌ Could not find any itemIds. Make sure apps are in your AppFollow workspace.")


if __name__ == "__main__":
    asyncio.run(find_missing_itemids())
