#!/usr/bin/env python3
"""
Interactive learning mode: Open AppFollow, prompt user for each step,
observe the page state to learn the automation pattern.
"""
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = PROJECT_ROOT / "state" / "browser" / "appfollow"
BASE_URL = "https://watch.appfollow.io"


async def interactive_learn():
    """
    Open AppFollow in your persistent session and guide through adding apps.
    """
    print("""
╔════════════════════════════════════════════════════════════════╗
║         AppFollow Interactive Learning Mode                    ║
║                                                                ║
║  I'll open AppFollow, prompt you for each action,             ║
║  and learn the exact steps to automate later.                 ║
╚════════════════════════════════════════════════════════════════╝
""")

    async with async_playwright() as pw:
        # Use persistent context (your saved login session)
        context = await pw.chromium.launch_persistent_context(
            str(STATE_DIR),
            headless=False,
            viewport={"width": 1440, "height": 900},
        )

        page = context.pages[0] if context.pages else await context.new_page()

        print("🌐 Opening AppFollow workspace...")
        await page.goto(f"{BASE_URL}/apps/my-first-workspace/home", wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2000)

        print("✅ AppFollow is now open in the browser window\n")

        # Step 1: Add App button
        print("=" * 70)
        print("STEP 1: Click 'Add App' button")
        print("=" * 70)
        print("Action: Look for and click the 'Add App' or '+' button")
        print("        (Usually in the top right or center of the page)")
        input("\nPress Enter once you've clicked 'Add App'...")

        current_url = page.url
        print(f"📍 Current URL: {current_url}\n")

        # Step 2: Search for app
        print("=" * 70)
        print("STEP 2: Search for an app")
        print("=" * 70)
        print("Action: Type an app name (e.g., 'Chime', 'Dave', 'Pokemon GO')")
        print("        in the search box")
        app_name = input("Enter the app name you'll search for: ").strip()

        print(f"  You should now search for: {app_name}")
        input("\nPress Enter once you've typed the app name in search...")

        await page.wait_for_timeout(1500)
        current_url = page.url
        print(f"📍 Current URL: {current_url}\n")

        # Step 3: Select app from results
        print("=" * 70)
        print("STEP 3: Select app from search results")
        print("=" * 70)
        print(f"Action: Click on '{app_name}' in the search results")
        input("\nPress Enter once you've clicked the app result...")

        await page.wait_for_timeout(2000)
        current_url = page.url
        print(f"📍 Current URL: {current_url}\n")

        # Step 4: Add to workspace
        print("=" * 70)
        print("STEP 4: Add to workspace")
        print("=" * 70)
        print("Action: Click the 'Add' or 'Add to Workspace' button")
        input("\nPress Enter once you've clicked the add button...")

        await page.wait_for_timeout(2000)
        current_url = page.url
        print(f"📍 Current URL: {current_url}\n")

        # Step 5: Verify added
        print("=" * 70)
        print("STEP 5: Verify app was added")
        print("=" * 70)
        print("You should see a success message or be redirected")
        print("The app should now appear in your workspace")
        input("\nPress Enter once you've confirmed the app was added...")

        # Check page title and content
        title = await page.title()
        print(f"📍 Page title: {title}")
        print(f"📍 Current URL: {page.url}\n")

        # Step 6: Extract itemId
        print("=" * 70)
        print("STEP 6: Get the app's itemId")
        print("=" * 70)
        print("Action: Navigate to the app details page (click on the app)")
        input("\nPress Enter once you're on the app's detail page...")

        await page.wait_for_timeout(1500)
        detail_url = page.url
        print(f"📍 App details URL: {detail_url}")

        # Try to extract itemId
        if "itemId=" in detail_url:
            item_id = detail_url.split("itemId=")[1].split("&")[0]
            print(f"✅ Found itemId: {item_id}\n")
        else:
            print(f"⚠️  Could not extract itemId from URL\n")
            item_id = input("Enter the itemId manually from the URL (after ?itemId=): ").strip()

        # Step 7: Delete app (for learning purposes)
        print("=" * 70)
        print("STEP 7: Delete the app (to reset for automation)")
        print("=" * 70)
        print("Action: Find and click the delete/remove button for this app")
        print("        (Usually a trash icon or 'Remove' option)")
        input("\nPress Enter once you've deleted the app...")

        await page.wait_for_timeout(1500)
        print(f"✅ App deleted\n")

        # Summary
        print("=" * 70)
        print("✅ AUTOMATION LEARNING COMPLETE")
        print("=" * 70)
        print(f"\n📊 Recorded information:")
        print(f"  App name: {app_name}")
        print(f"  Item ID: {item_id}")
        print(f"  Workspace: my-first-workspace")
        print(f"\n✨ I've learned the process! Now I can automate this for all apps.\n")

        input("Press Enter to close the browser...")
        await context.close()


if __name__ == "__main__":
    asyncio.run(interactive_learn())
