#!/usr/bin/env python3
"""
Full AppFollow automation: manage workspace, discover itemIds, and collect data in batches.

Workflow:
1. Delete all apps from workspace
2. Add 5 apps to workspace
3. Discover their itemIds
4. Update config
5. Collect reviews
6. Repeat for next batch

Usage:
  python scripts/appfollow_full_automation.py
"""
import asyncio
import subprocess
import sys
from pathlib import Path

import yaml
from playwright.async_api import async_playwright

from adintel.core.settings import get_settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = PROJECT_ROOT / "config" / "appfollow_groups.yaml"
BASE_URL = "https://watch.appfollow.io"
BATCH_SIZE = 5


def load_config():
    """Load AppFollow config."""
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


def get_app_list():
    """Get all advertiser groups from config."""
    config = load_config()
    apps = []

    for group in config.get("groups", []):
        advertiser = group.get("advertiser")
        countries = group.get("countries", ["US"])
        if advertiser:
            apps.append({
                "name": advertiser,
                "type": "advertiser",
                "countries": countries,
                "competitors": [c.get("name") for c in group.get("competitors", [])]
            })

    return apps


async def delete_all_apps(page, workspace):
    """Delete all apps from the workspace."""
    print(f"\n🗑️  Deleting all apps from workspace '{workspace}'...")

    try:
        # Navigate to workspace
        await page.goto(f"{BASE_URL}/apps/{workspace}/home", wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2000)

        # Find delete buttons and delete all apps
        # This is tricky because AppFollow's UI may vary
        # For now, we'll navigate to settings or use the API
        print("  ⚠️  Manual deletion required: go to workspace settings and remove apps")
        print(f"  URL: {BASE_URL}/apps/{workspace}/settings")

    except Exception as e:
        print(f"  ❌ Error: {e}")


async def add_apps_to_workspace(page, workspace, app_names):
    """Add apps to the workspace via the web UI."""
    print(f"\n➕ Adding {len(app_names)} apps to workspace...")

    added = []
    for app_name in app_names:
        try:
            print(f"  Adding {app_name}...", end=" ", flush=True)

            # Navigate to add app page
            await page.goto(
                f"{BASE_URL}/apps/{workspace}/app/add?term={app_name.lower().replace(' ', '+')}&source=all&country=us",
                wait_until="domcontentloaded",
                timeout=30_000
            )
            await page.wait_for_timeout(1500)

            # Try to find and click the first app result
            try:
                app_link = page.locator(f'a:has-text("{app_name}")').first
                if await app_link.count() > 0:
                    await app_link.click(timeout=5000)
                    await page.wait_for_timeout(1000)

                    # Click "Add to workspace" button if available
                    add_button = page.locator('button:has-text("Add"), button:has-text("add"), a:has-text("Add")').first
                    if await add_button.count() > 0:
                        await add_button.click(timeout=5000)
                        await page.wait_for_timeout(2000)

                    added.append(app_name)
                    print("✅")
                else:
                    print("⚠️  not found")
            except Exception:
                print("❌ click failed")

        except Exception as e:
            print(f"❌ {str(e)[:30]}")

    return added


async def run_full_automation():
    """Main automation loop."""
    config = load_config()
    workspace = config.get("workspace", "my-first-workspace")
    all_apps = get_app_list()

    print(f"""
╔════════════════════════════════════════════════════════════════╗
║         AppFollow Full Automation                              ║
║         Process all {len(all_apps)} advertisers in batches of {BATCH_SIZE}          ║
╚════════════════════════════════════════════════════════════════╝
""")

    # Confirm with user
    print(f"This will:")
    print(f"  1. Delete all apps from '{workspace}'")
    print(f"  2. Add {BATCH_SIZE} apps at a time")
    print(f"  3. Discover itemIds")
    print(f"  4. Collect reviews")
    print(f"  5. Repeat {len(all_apps) // BATCH_SIZE} times")
    print()

    # Open browser
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        # Navigate to AppFollow workspace
        print(f"\n🌐 Opening AppFollow workspace: {BASE_URL}/apps/{workspace}/home")
        await page.goto(f"{BASE_URL}/apps/{workspace}/home", wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2000)

        # Process batches
        for batch_num, i in enumerate(range(0, len(all_apps), BATCH_SIZE), 1):
            batch = all_apps[i:i+BATCH_SIZE]
            batch_apps = [a["name"] for a in batch]

            print(f"\n{'='*70}")
            print(f"📦 BATCH {batch_num}/{(len(all_apps)-1)//BATCH_SIZE + 1}")
            print(f"{'='*70}")

            # Step 1: Delete all (only first batch)
            if batch_num == 1:
                input("Press Enter after manually deleting all apps from workspace...")

            # Step 2: Add apps
            print(f"\nApps to add:")
            for app in batch_apps:
                print(f"  • {app}")

            input("\nPress Enter to add apps to workspace...")
            await add_apps_to_workspace(page, workspace, batch_apps)

            # Step 3: Discover itemIds
            print(f"\n🔍 Running discovery...")
            result = subprocess.run(
                [sys.executable, "scripts/appfollow_discover_itemids.py"],
                cwd=PROJECT_ROOT
            )

            if result.returncode != 0:
                print(f"❌ Discovery failed for batch {batch_num}")
                break

            # Step 4: Collect reviews
            print(f"\n📊 Collecting reviews...")
            result = subprocess.run(
                ["bash", "scripts/run_appfollow_to_server.sh"],
                cwd=PROJECT_ROOT
            )

            if result.returncode != 0:
                print(f"❌ Collection failed for batch {batch_num}")
                break

            print(f"\n✅ Batch {batch_num} complete!")

            if batch_num < (len(all_apps) // BATCH_SIZE + 1):
                input(f"\nDelete these apps and add next batch, then press Enter...")

        print(f"\n✅ All batches complete!")


if __name__ == "__main__":
    asyncio.run(run_full_automation())
