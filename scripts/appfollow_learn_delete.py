#!/usr/bin/env python3
"""
Intercept the AppFollow delete-app network request.

Run this script, then manually click the delete/remove button for an app.
All non-GET requests will be printed so we can identify the correct endpoint.

Usage:
  python scripts/appfollow_learn_delete.py [itemId]
  python scripts/appfollow_learn_delete.py 639808   # Binance
"""
import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = PROJECT_ROOT / "state" / "browser" / "appfollow"
BASE_URL = "https://watch.appfollow.io"
WORKSPACE = "my-first-workspace"


async def learn_delete(item_id: str):
    print(f"""
╔════════════════════════════════════════════════════════════════╗
║         AppFollow Delete Endpoint Discovery                    ║
║                                                                ║
║  I'll open the app settings page and capture all network      ║
║  requests while you delete the app.                           ║
╚════════════════════════════════════════════════════════════════╝

App itemId: {item_id}
""")

    captured_requests = []

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(STATE_DIR),
            headless=False,
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else await context.new_page()

        # Capture all non-GET requests
        async def on_request(request):
            if request.method != "GET":
                data = {
                    "method": request.method,
                    "url": request.url,
                    "headers": dict(request.headers),
                    "post_data": request.post_data,
                }
                captured_requests.append(data)
                print(f"\n📡 {request.method} {request.url}")
                if request.post_data:
                    print(f"   Body: {request.post_data[:200]}")

        page.on("request", on_request)

        # Navigate to app settings page
        settings_url = f"{BASE_URL}/apps/{WORKSPACE}/app/settings?itemId={item_id}"
        print(f"🌐 Opening: {settings_url}")
        await page.goto(settings_url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2000)

        print(f"\n👆 The app settings page is now open.")
        print(f"   Please click the 'Remove app', 'Delete', or 'Unsubscribe' button.")
        print(f"   Then confirm if a dialog appears.")
        print(f"\n   Watching for network requests...")
        print(f"   Press Ctrl+C when done.\n")

        # Wait indefinitely while user performs the action
        try:
            await asyncio.sleep(120)  # Wait up to 2 minutes
        except KeyboardInterrupt:
            pass

        page.remove_listener("request", on_request)

        # Print summary
        print(f"\n{'='*70}")
        print(f"📊 CAPTURED {len(captured_requests)} non-GET requests:")
        print(f"{'='*70}")
        for i, req in enumerate(captured_requests, 1):
            print(f"\n[{i}] {req['method']} {req['url']}")
            if req['post_data']:
                print(f"    Body: {req['post_data'][:300]}")
            # Print relevant headers
            for h in ['content-type', 'x-csrftoken', 'authorization']:
                if h in req['headers']:
                    print(f"    {h}: {req['headers'][h]}")

        # Save to file
        output_file = PROJECT_ROOT / "state" / "debug" / "appfollow_delete_requests.json"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w") as f:
            json.dump(captured_requests, f, indent=2)
        print(f"\n💾 Full request data saved to: {output_file}")

        await context.close()


if __name__ == "__main__":
    item_id = sys.argv[1] if len(sys.argv) > 1 else "639808"
    asyncio.run(learn_delete(item_id))
